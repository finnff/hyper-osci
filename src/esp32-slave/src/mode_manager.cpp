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
// Why the radio is currently off ("button" / "console" / "battery" /
// "boot-hold"), or nullptr while it is on. Diagnostics only — the radio state
// itself is never persisted.
const char* g_radio_off_reason = nullptr;
Preferences prefs;

int16_t mix_buf[AUDIO_BLOCK_FRAMES * 2];

// Concealment ladder state (NETWORK mode, docs/protocol.md §6): on a brief
// stream hiccup, hold the last frame ≤20 ms, ramp to center over ~10 ms, then
// hold center; only fall back to the mic when the stream is gone ≥1 s.
int16_t conceal_l = 0, conceal_r = 0;
uint32_t conceal_blocks = 0;
constexpr uint32_t CONCEAL_HOLD_BLOCKS = 4;  // 20 ms
constexpr uint32_t CONCEAL_RAMP_BLOCKS = 2;  // 10 ms

// Has a network block played since this mode stint (or stream) began? Until
// it has, the slave is in pre-roll: the controller's blocks are due LEAD_US
// after it sends them, so a fresh join sees a head that is "too early" for
// ~0.45 s. That wait must show the local render, not the concealment ladder
// (whose centre dot is for hiccups inside a playing stream). Cleared on a
// real mode change and when the stream goes away (WiFi reconnect, unmute
// after silence, controller restart all rejoin like a fresh start).
volatile bool g_net_played = false;
// Audio task's view of stream_active(), to catch its edges. Reset on mode
// change so each stint logs its own STREAM_UP.
volatile bool g_stream_seen = false;
volatile uint32_t g_entry_ms = 0;  // millis() of the last mode change
volatile uint32_t g_last_preroll_ms = 0;

// Event ring: any task produces, loop task consumes (ui.cpp prints).
constexpr uint32_t EV_RING = 16;
mode_manager::Event ev_ring[EV_RING];
uint32_t ev_head = 0, ev_tail = 0, ev_dropped = 0;
portMUX_TYPE ev_mux = portMUX_INITIALIZER_UNLOCKED;

void log_event(mode_manager::EventKind kind, uint32_t arg) {
  const uint32_t now = millis();
  portENTER_CRITICAL(&ev_mux);
  if (ev_head - ev_tail < EV_RING) {
    ev_ring[ev_head % EV_RING] = {now, arg, kind, g_mode};
    ev_head++;
  } else {
    ev_dropped++;
  }
  portEXIT_CRITICAL(&ev_mux);
}

uint32_t since_entry() { return millis() - g_entry_ms; }

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

bool init(bool force_local) {
  prefs.begin("hyperosci", false);
  // The radio state is deliberately NOT persisted: a power cut, brownout or
  // reset must always come back with WiFi ON. Only the 2 s MODE press (or the
  // console) turns it off, and only until the next boot. `wifi_off` was an
  // earlier persisted latch — remove it so units already carrying that key
  // don't come up silent.
  prefs.remove("wifi_off");
  g_mode = force_local ? Mode::LOCAL : Mode::NETWORK;
  net_rx::set_accept_audio(!force_local);
  g_entry_ms = millis();
  if (force_local) g_radio_off_reason = "boot-hold";
  uint8_t mac[6] = {0};
  esp_read_mac(mac, ESP_MAC_WIFI_STA);  // works before WiFi init
  g_slave_id = prefs.getUChar("slave_id", mac[5]);  // default: MAC last octet
  audio_out::set_gain(prefs.getFloat("gain", 1.0f));
  return !force_local;
}

void set_wifi_enabled(bool on, const char* reason) {
  net_rx::set_radio(on);
  // Mode follows the radio: with the radio stopped, NETWORK/HYBRID would
  // render the mic anyway while the LEDs claimed a stream was expected. LOCAL
  // also lands in the next STATUS, which is what makes the controller drop
  // this unit from its audio fan-out (mode != LOCAL filter) on its next tick.
  set_mode(on ? Mode::NETWORK : Mode::LOCAL);
  if (on) {
    g_radio_off_reason = nullptr;
  } else {
    // Mic-only means the mic: drop whatever bring-up test pattern was
    // selected, so the gesture always lands on the mic pattern.
    renderer_local::set_pattern(renderer_local::Pattern::MIC);
    g_radio_off_reason = reason;
  }
}

bool wifi_enabled() { return net_rx::radio_enabled(); }

const char* radio_off_reason() { return g_radio_off_reason; }

void set_mode(Mode m) {
  // Gate (and flush) before the mode flips: a LOCAL slave buffers nothing, so
  // the next join can't start on a stale tail (net_rx::set_accept_audio).
  net_rx::set_accept_audio(m != Mode::LOCAL);
  if (m == g_mode) return;
  // Flags before g_mode, so the audio task never sees the new mode with the
  // old stint's state. (Single core: the only race is preemption, and the
  // audio task touches these only in the mode branch it is leaving.)
  g_net_played = false;
  g_stream_seen = false;
  g_entry_ms = millis();
  g_mode = m;
  log_event(EventKind::MODE, (uint32_t)m);
}

Mode mode() { return g_mode; }

void cycle_mode() {
  // NETWORK -> LOCAL -> HYBRID -> NETWORK (DESIGN.md §7 button spec), via
  // set_mode() so entering LOCAL flushes the jitter buffer exactly as the
  // console and network paths do.
  if (!net_rx::radio_enabled()) {
    set_mode(Mode::LOCAL);  // radio is off: nothing else would be honest
    return;
  }
  switch (g_mode) {
    case Mode::NETWORK:
      set_mode(Mode::LOCAL);
      break;
    case Mode::LOCAL:
      set_mode(Mode::HYBRID);
      break;
    default:
      set_mode(Mode::NETWORK);
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

    case Mode::NETWORK: {
      const net_rx::Pull r = net_rx::pull_block(frames, frame_count);
      if (r == net_rx::Pull::PLAYED) {
        source = 1;
        g_last_net_ms = millis();
        if (!g_net_played) {
          g_net_played = true;
          g_last_preroll_ms = since_entry();
          log_event(EventKind::FIRST_PLAY, g_last_preroll_ms);
        }
        g_stream_seen = true;
        conceal_blocks = 0;
        conceal_l = frames[(frame_count - 1) * 2];
        conceal_r = frames[(frame_count - 1) * 2 + 1];
        mic_in::drain();  // pump ADC DMA (anti-wedge); vbat/pot stay fresh
        break;
      }
      if (r == net_rx::Pull::UNDERRUN) {
        log_event(EventKind::UNDERRUN, since_entry());
      }
      if (net_rx::stream_active()) {
        if (!g_stream_seen) {
          g_stream_seen = true;
          log_event(EventKind::STREAM_UP, since_entry());
        }
        if (g_net_played) {
          // Brief hiccup inside a playing stream (rebuffer / lost packets):
          // conceal instead of flapping to the mic for 5 ms at a time.
          fill_conceal(frames, frame_count);
          mic_in::drain();
        } else {
          // Pre-roll: keep drawing the mic until the first block is due.
          renderer_local::render(frames, frame_count, 1.0f);
        }
      } else {
        if (g_stream_seen) {
          g_stream_seen = false;
          g_net_played = false;  // the next audio is a fresh join
          log_event(EventKind::STREAM_LOST, since_entry());
        }
        // Stream gone ≥ STREAM_TIMEOUT_MS — auto-fallback (v3.1 hard
        // requirement): keep the show running on the mic.
        renderer_local::render(frames, frame_count, 1.0f);
      }
      break;
    }

    case Mode::HYBRID: {
      const net_rx::Pull r = net_rx::pull_block(frames, frame_count);
      if (r == net_rx::Pull::PLAYED) {
        source = 1;
        g_last_net_ms = millis();
        renderer_local::render(mix_buf, frame_count, HYBRID_MIC_GAIN);
        for (size_t i = 0; i < frame_count * 2; i++) {
          frames[i] = sat_add(frames[i], mix_buf[i]);
        }
      } else {
        if (r == net_rx::Pull::UNDERRUN) {
          log_event(EventKind::UNDERRUN, since_entry());
        }
        renderer_local::render(frames, frame_count, 1.0f);
      }
      break;
    }
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

bool pop_event(Event& out) {
  bool got = false;
  portENTER_CRITICAL(&ev_mux);
  if (ev_tail != ev_head) {
    out = ev_ring[ev_tail % EV_RING];
    ev_tail++;
    got = true;
  }
  portEXIT_CRITICAL(&ev_mux);
  return got;
}

uint32_t events_dropped() { return ev_dropped; }

uint32_t last_preroll_ms() { return g_last_preroll_ms; }

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
