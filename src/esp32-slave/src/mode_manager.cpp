#include "mode_manager.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <string.h>

#include "audio_out.h"
#include "config.h"
#include "esp_mac.h"
#include "mic_in.h"
#include "net_rx.h"
#include "renderer_local.h"
#include "ui.h"

namespace {

volatile mode_manager::Mode g_mode = mode_manager::Mode::NETWORK;
volatile uint8_t g_active_source = 0;
// Last time a network block actually played. STATUS samples the source once
// a second; without this, a single 5 ms concealment block landing on that
// instant reports "local" for a whole second (UI flaps to "stream lost").
volatile uint32_t g_last_net_ms = 0;
uint8_t g_slave_id = 0;
Preferences prefs;

int16_t mix_buf[AUDIO_BLOCK_FRAMES * 2];

// Concealment ladder state (NETWORK mode, docs/protocol.md §6): on a brief
// stream hiccup, hold the last frame ≤20 ms, ramp to center over ~10 ms, then
// hold center; only fall back to the mic when the stream is gone ≥1 s.
int16_t conceal_l = 0, conceal_r = 0;
uint32_t conceal_blocks = 0;
constexpr uint32_t CONCEAL_HOLD_BLOCKS = 4;  // 20 ms
constexpr uint32_t CONCEAL_RAMP_BLOCKS = 2;  // 10 ms

inline int16_t sat_add(int16_t a, int16_t b) {
  int32_t v = (int32_t)a + (int32_t)b;
  if (v > 32767) return 32767;
  if (v < -32768) return -32768;
  return (int16_t)v;
}

void fill_conceal(int16_t* frames, size_t frame_count) {
  conceal_blocks++;
  if (conceal_blocks <= CONCEAL_HOLD_BLOCKS) {
    for (size_t i = 0; i < frame_count; i++) {
      frames[i * 2] = conceal_l;
      frames[i * 2 + 1] = conceal_r;
    }
  } else if (conceal_blocks <= CONCEAL_HOLD_BLOCKS + CONCEAL_RAMP_BLOCKS) {
    // Linear ramp of the held value toward 0 across the ramp blocks.
    const uint32_t step = conceal_blocks - CONCEAL_HOLD_BLOCKS;
    const float g0 =
        1.0f - (float)(step - 1) / (float)CONCEAL_RAMP_BLOCKS;
    const float g1 = 1.0f - (float)step / (float)CONCEAL_RAMP_BLOCKS;
    for (size_t i = 0; i < frame_count; i++) {
      const float t = (float)i / (float)frame_count;
      const float g = g0 + (g1 - g0) * t;
      frames[i * 2] = (int16_t)((float)conceal_l * g);
      frames[i * 2 + 1] = (int16_t)((float)conceal_r * g);
    }
  } else {
    memset(frames, 0, frame_count * 2 * sizeof(int16_t));  // center dot
  }
}

}  // namespace

namespace mode_manager {

void init(Mode boot_mode) {
  g_mode = boot_mode;
  prefs.begin("hyperosci", false);
  uint8_t mac[6] = {0};
  esp_read_mac(mac, ESP_MAC_WIFI_STA);  // works before WiFi init
  g_slave_id = prefs.getUChar("slave_id", mac[5]);  // default: MAC last octet
  audio_out::set_gain(prefs.getFloat("gain", 1.0f));
}

void set_mode(Mode m) {
  // Entering LOCAL: drop any buffered stream audio. The controller stops
  // sending to LOCAL slaves, so whatever is queued would only rot into a
  // stale-drop burst on the next switch back to NETWORK/HYBRID.
  if (m == Mode::LOCAL && g_mode != Mode::LOCAL) net_rx::flush();
  g_mode = m;
}

Mode mode() { return g_mode; }

void cycle_mode() {
  // NETWORK -> LOCAL -> HYBRID -> NETWORK (DESIGN.md §7 button spec)
  switch (g_mode) {
    case Mode::NETWORK:
      g_mode = Mode::LOCAL;
      break;
    case Mode::LOCAL:
      g_mode = Mode::HYBRID;
      break;
    default:
      g_mode = Mode::NETWORK;
      break;
  }
}

const char* mode_name() {
  switch (g_mode) {
    case Mode::LOCAL:
      return "local";
    case Mode::HYBRID:
      return "hybrid";
    default:
      return "network";
  }
}

uint8_t fill_block(int16_t* frames, size_t frame_count) {
  uint8_t source = 0;
  switch (g_mode) {
    case Mode::LOCAL:
      renderer_local::render(frames, frame_count, 1.0f);
      break;

    case Mode::NETWORK:
      if (net_rx::pull_block(frames, frame_count)) {
        source = 1;
        g_last_net_ms = millis();
        conceal_blocks = 0;
        conceal_l = frames[(frame_count - 1) * 2];
        conceal_r = frames[(frame_count - 1) * 2 + 1];
        mic_in::drain();  // keep vbat/pot smoothers alive, mic ring fresh
      } else if (net_rx::stream_active()) {
        // Brief hiccup (rebuffer / lost packets / early block): conceal
        // instead of flapping to the mic for 5 ms at a time.
        fill_conceal(frames, frame_count);
        mic_in::drain();
      } else {
        // Stream gone ≥ STREAM_TIMEOUT_MS — auto-fallback (v3.1 hard
        // requirement): keep the show running on the mic.
        renderer_local::render(frames, frame_count, 1.0f);
      }
      break;

    case Mode::HYBRID:
      if (net_rx::pull_block(frames, frame_count)) {
        source = 1;
        g_last_net_ms = millis();
        renderer_local::render(mix_buf, frame_count, HYBRID_MIC_GAIN);
        for (size_t i = 0; i < frame_count * 2; i++) {
          frames[i] = sat_add(frames[i], mix_buf[i]);
        }
      } else {
        renderer_local::render(frames, frame_count, 1.0f);
      }
      break;
  }
  g_active_source = source;
  return source;
}

uint8_t active_source() {
  if (g_active_source) return 1;
  // Bridge sub-150 ms concealment gaps so the 1 Hz STATUS sample doesn't
  // flap; a real fallback (rebuffer/timeout) exceeds this and reports 0.
  const uint32_t last = g_last_net_ms;
  return (last != 0 && millis() - last < 150) ? 1 : 0;
}

void handle_command(const char* json, size_t len) {
  JsonDocument doc;
  if (deserializeJson(doc, json, len) != DeserializationError::Ok) return;
  const char* cmd = doc["cmd"];
  if (cmd == nullptr) return;

  if (strcmp(cmd, "set_mode") == 0) {
    const char* m = doc["mode"];
    if (m == nullptr) return;
    if (strcmp(m, "local") == 0) set_mode(Mode::LOCAL);
    else if (strcmp(m, "network") == 0) set_mode(Mode::NETWORK);
    else if (strcmp(m, "hybrid") == 0) set_mode(Mode::HYBRID);
  } else if (strcmp(cmd, "set_gain") == 0) {
    float g = doc["gain"] | 1.0f;
    audio_out::set_gain(g);
    prefs.putFloat("gain", audio_out::gain());
  } else if (strcmp(cmd, "set_pattern") == 0) {
    // Selects the LOCAL renderer's pattern (played in LOCAL mode and as the
    // NETWORK-mode fallback). {"cmd":"set_pattern","pattern":"mic"|...}
    const char* p = doc["pattern"];
    if (p == nullptr) return;
    using renderer_local::Pattern;
    if (strcmp(p, "mic") == 0) renderer_local::set_pattern(Pattern::MIC);
    else if (strcmp(p, "circle") == 0) renderer_local::set_pattern(Pattern::CIRCLE);
    else if (strcmp(p, "lissajous") == 0) renderer_local::set_pattern(Pattern::LISSAJOUS);
    else if (strcmp(p, "ramp") == 0) renderer_local::set_pattern(Pattern::RAMP);
    else if (strcmp(p, "square") == 0) renderer_local::set_pattern(Pattern::SQUARE);
  } else if (strcmp(cmd, "identify") == 0) {
    ui::identify();
  } else if (strcmp(cmd, "reboot") == 0) {
    ESP.restart();
  }
}

uint8_t slave_id() { return g_slave_id; }

void set_slave_id(uint8_t id) {
  g_slave_id = id;
  prefs.putUChar("slave_id", id);
}

}  // namespace mode_manager
