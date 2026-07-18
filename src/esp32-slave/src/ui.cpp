#include "ui.h"

#include <Arduino.h>
#include <WiFi.h>
#include <string.h>

#include "audio_out.h"
#include "config.h"

namespace app {
uint32_t audio_stack_hwm();  // main.cpp — audio-task stack headroom (words)
}
#include "esp_intr_alloc.h"
#include "esp_sleep.h"
#include "mic_in.h"
#include "mode_manager.h"
#include "net_rx.h"
#include "renderer_local.h"
#include "timesync.h"

namespace {

bool g_boot_local = false;
bool identify_active = false;
uint32_t identify_started_ms = 0;
bool g_log_periodic = false;  // `log on` — print stat every second
uint32_t last_log_ms = 0;

// --- Buttons -----------------------------------------------------------------
struct Button {
  uint8_t pin;
  bool stable_state = true;  // pulled up, true = released
  bool last_read = true;
  uint32_t last_change_ms = 0;
  uint32_t press_started_ms = 0;
  bool long_fired = false;
};
Button btn_mode{PIN_BTN_MODE};
Button btn_boot{PIN_BTN_BOOT};

// Returns: 0 = nothing, 1 = short press (on release), 2 = long press (fires
// once while still held).
int button_poll(Button& b, bool long_press_enabled) {
  const uint32_t now = millis();
  const bool read = digitalRead(b.pin);
  if (read != b.last_read) {
    b.last_read = read;
    b.last_change_ms = now;
  }
  if (now - b.last_change_ms < BTN_DEBOUNCE_MS) return 0;
  if (read == b.stable_state) {
    // Held long enough for a long press?
    if (!read && long_press_enabled && !b.long_fired &&
        now - b.press_started_ms >= BTN_LONGPRESS_MS) {
      b.long_fired = true;
      return 2;
    }
    return 0;
  }
  b.stable_state = read;
  if (!read) {  // pressed
    b.press_started_ms = now;
    b.long_fired = false;
    return 0;
  }
  // released
  if (b.long_fired) return 0;
  return 1;
}

// --- LEDs --------------------------------------------------------------------
void update_leds() {
  const uint32_t now = millis();

  if (identify_active) {  // identify overrides everything
    if (now - identify_started_ms >= 3000) {
      identify_active = false;
    } else {
      const bool on = (now / 125) % 2;
      digitalWrite(PIN_LED_NET, on);
      digitalWrite(PIN_LED_MODE, on);
      digitalWrite(PIN_LED_ONBOARD, on ? LOW : HIGH);  // active low
      return;
    }
  }

  // NET (green) — DESIGN.md §7 semantics: 1 Hz = connecting OR connected but
  // never streamed; solid = streaming; 5 Hz = had a stream and lost it.
  bool net_on = false;
  if (net_rx::radio_enabled()) {
    const net_rx::Stats s = net_rx::stats();
    if (s.stream_up) {
      net_on = true;  // solid
    } else if (s.wifi_up && s.rx_packets > 0) {
      net_on = (now / 100) % 2;  // 5 Hz: stream lost (fallback active)
    } else {
      net_on = (now / 500) % 2;  // 1 Hz: connecting / no stream yet
    }
  }
  digitalWrite(PIN_LED_NET, net_on);

  // MODE (amber)
  bool mode_on = false;
  switch (mode_manager::mode()) {
    case mode_manager::Mode::LOCAL:
      mode_on = true;
      break;
    case mode_manager::Mode::HYBRID:
      mode_on = (now / 500) % 2;
      break;
    default:
      mode_on = false;
      break;
  }
  digitalWrite(PIN_LED_MODE, mode_on);

  // Onboard blue: heartbeat, or triple-blink burst when battery is low.
  bool blue_on;
  if (mic_in::vbat_mv() < VBAT_WARN_MV && mic_in::vbat_mv() > 1000) {
    const uint32_t t = now % 2000;
    blue_on = (t < 600) && ((t / 100) % 2 == 0);  // 3 fast blinks / 2 s
  } else {
    blue_on = (now % HEARTBEAT_PERIOD_MS) < 50;
  }
  digitalWrite(PIN_LED_ONBOARD, blue_on ? LOW : HIGH);  // active low
}

// --- Battery policy ----------------------------------------------------------
void battery_policy() {
  static uint32_t last_check_ms = 0;
  static uint32_t below_sleep_since_ms = 0;
  const uint32_t now = millis();
  if (now - last_check_ms < 1000) return;
  last_check_ms = now;

  const uint16_t mv = mic_in::vbat_mv();
  // Divider not connected (bench/USB bring-up): a floating GPIO1 can drift
  // past 1 V and fake a dying cell (radio off / deep sleep). Any real LiPo
  // reads >= ~3000 mV through the 2:1 divider, so 2000 is still unambiguous.
  if (mv < 2000) return;

  if (mv < VBAT_WIFI_OFF_MV && net_rx::radio_enabled()) {
    Serial.printf("[batt] %u mV — radio off, forcing LOCAL\n", mv);
    net_rx::set_radio(false);
    mode_manager::set_mode(mode_manager::Mode::LOCAL);
  }

  if (mv < VBAT_SLEEP_MV) {
    if (below_sleep_since_ms == 0) below_sleep_since_ms = now;
    if (now - below_sleep_since_ms > 10000) {  // 10 s sustained
      Serial.printf("[batt] %u mV — deep sleep to protect the cell\n", mv);
      digitalWrite(PIN_LED_NET, LOW);
      digitalWrite(PIN_LED_MODE, LOW);
      digitalWrite(PIN_LED_ONBOARD, HIGH);
      // No GPIO wake configured (MODE btn is GPIO7; C3 deep-sleep wake needs
      // GPIO0-5). Recovery = power cycle, which is what a dead battery needs
      // anyway.
      esp_deep_sleep_start();
    }
  } else if (mv > VBAT_SLEEP_MV + VBAT_HYSTERESIS_MV) {
    below_sleep_since_ms = 0;
  }
}

// --- Serial console ----------------------------------------------------------
void print_help() {
  Serial.println(
      "commands: help | stat | log <on|off> | mode <local|net|hybrid> | pat | "
      "gain <0-100> | wifi <on|off> | id <0-255> | reboot");
}

void print_stat() {
  net_rx::Stats s = net_rx::stats();
  Serial.printf("mode=%s src=%s pattern=%s lpf=%.0fHz\n",
                mode_manager::mode_name(),
                mode_manager::active_source() ? "network" : "local",
                renderer_local::pattern_name(),
                (double)renderer_local::current_lpf_hz());
  Serial.printf("wifi=%s ps=%s stream=%s rssi=%d ip=%s\n",
                s.wifi_up ? "up" : "down", net_rx::ps_mode(),
                s.stream_up ? "up" : "down", s.rssi_dbm,
                WiFi.localIP().toString().c_str());
  Serial.printf("rx=%lu drop=%lu gaps=%lu lost=%lu underrun=%lu depth=%u frames\n",
                (unsigned long)s.rx_packets, (unsigned long)s.rx_dropped,
                (unsigned long)s.seq_gaps, (unsigned long)s.lost_packets,
                (unsigned long)s.underruns, s.buffer_depth_frames);
  Serial.printf(
      "sync=%s offset=%lldus vbat=%umV pot=%.2f id=%u heap=%u astk=%u\n",
      timesync::valid() ? "ok" : "stale", (long long)timesync::offset_us(),
      mic_in::vbat_mv(), (double)mic_in::pot_norm(), mode_manager::slave_id(),
      (unsigned)ESP.getFreeHeap(), (unsigned)app::audio_stack_hwm());
  Serial.printf("adc: overflow=%lu errors=%lu clamped=%lu restarts=%lu\n",
                (unsigned long)mic_in::overflow_count(),
                (unsigned long)mic_in::adc_error_count(),
                (unsigned long)mic_in::latency_clamp_count(),
                (unsigned long)mic_in::restart_count());
  Serial.printf("mic: peak=%u/32767 bias_raw=%u/4095 (expect bias ~2100-2300)\n",
                mic_in::mic_peak(), mic_in::mic_bias_raw());
  Serial.printf(
      "audio: i2s_init=%s(step=%s err=0x%x) adc_init=%s write_calls=%lu "
      "done=%lu\n",
      audio_out::init_ok() ? "ok" : "FAILED", audio_out::init_err_step(),
      audio_out::init_err_code(), mic_in::init_ok() ? "ok" : "FAILED",
      (unsigned long)audio_out::write_calls(),
      (unsigned long)audio_out::writes_done());
}

void handle_line(char* line) {
  char* cmd = strtok(line, " \r\n");
  if (cmd == nullptr) return;
  char* arg = strtok(nullptr, " \r\n");

  if (strcmp(cmd, "help") == 0) {
    print_help();
  } else if (strcmp(cmd, "stat") == 0) {
    print_stat();
  } else if (strcmp(cmd, "intr") == 0) {
    // CPU interrupt allocation table — the C3 runs out of lines for the
    // second GDMA client (I2S vs adc_continuous); this shows who holds what.
    esp_intr_dump(stdout);
    fflush(stdout);
  } else if (strcmp(cmd, "log") == 0) {
    g_log_periodic = (arg != nullptr) ? (strcmp(arg, "on") == 0)
                                      : !g_log_periodic;
    Serial.printf("log=%s\n", g_log_periodic ? "on" : "off");
  } else if (strcmp(cmd, "mode") == 0 && arg != nullptr) {
    if (strcmp(arg, "local") == 0)
      mode_manager::set_mode(mode_manager::Mode::LOCAL);
    else if (strcmp(arg, "net") == 0)
      mode_manager::set_mode(mode_manager::Mode::NETWORK);
    else if (strcmp(arg, "hybrid") == 0)
      mode_manager::set_mode(mode_manager::Mode::HYBRID);
    Serial.printf("mode=%s\n", mode_manager::mode_name());
  } else if (strcmp(cmd, "pat") == 0) {
    renderer_local::next_pattern();
    Serial.printf("pattern=%s\n", renderer_local::pattern_name());
  } else if (strcmp(cmd, "gain") == 0 && arg != nullptr) {
    audio_out::set_gain((float)atoi(arg) / 100.0f);
    Serial.printf("gain=%.2f\n", (double)audio_out::gain());
  } else if (strcmp(cmd, "wifi") == 0 && arg != nullptr) {
    net_rx::set_radio(strcmp(arg, "on") == 0);
    Serial.printf("wifi=%s\n", net_rx::radio_enabled() ? "on" : "off");
  } else if (strcmp(cmd, "id") == 0 && arg != nullptr) {
    mode_manager::set_slave_id((uint8_t)atoi(arg));
    Serial.printf("id=%u\n", mode_manager::slave_id());
  } else if (strcmp(cmd, "reboot") == 0) {
    ESP.restart();
  } else {
    print_help();
  }
}

void console_poll() {
  static char line[96];
  static size_t pos = 0;
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (pos > 0) {
        line[pos] = '\0';
        handle_line(line);
        pos = 0;
      }
    } else if (pos < sizeof(line) - 1) {
      line[pos++] = c;
    }
  }
}

}  // namespace

namespace ui {

void init() {
  pinMode(PIN_BTN_MODE, INPUT_PULLUP);
  pinMode(PIN_BTN_BOOT, INPUT_PULLUP);
  pinMode(PIN_LED_NET, OUTPUT);
  pinMode(PIN_LED_MODE, OUTPUT);
  pinMode(PIN_LED_ONBOARD, OUTPUT);
  digitalWrite(PIN_LED_NET, LOW);
  digitalWrite(PIN_LED_MODE, LOW);
  digitalWrite(PIN_LED_ONBOARD, HIGH);  // active low, off

  delay(20);  // let the pull-up settle before sampling
  g_boot_local = (digitalRead(PIN_BTN_MODE) == LOW);
  if (g_boot_local) {
    // Pre-arm the button as already-pressed-and-consumed so the boot hold
    // doesn't later fire as a short/long press (which would cycle the mode or
    // re-enable WiFi — defeating the gesture).
    btn_mode.stable_state = false;
    btn_mode.last_read = false;
    btn_mode.long_fired = true;
    btn_mode.press_started_ms = millis();
    btn_mode.last_change_ms = millis();
  }
}

bool boot_local_requested() { return g_boot_local; }

void poll() {
  switch (button_poll(btn_mode, true)) {
    case 1:
      mode_manager::cycle_mode();
      Serial.printf("[btn] mode=%s\n", mode_manager::mode_name());
      break;
    case 2:
      net_rx::set_radio(!net_rx::radio_enabled());
      Serial.printf("[btn] wifi=%s\n",
                    net_rx::radio_enabled() ? "on" : "off");
      break;
  }
  if (button_poll(btn_boot, false) == 1) {
    renderer_local::next_pattern();
    Serial.printf("[btn] pattern=%s\n", renderer_local::pattern_name());
  }

  update_leds();
  battery_policy();
  console_poll();

  if (g_log_periodic && millis() - last_log_ms >= 1000) {
    last_log_ms = millis();
    print_stat();
  }
}

void identify() {
  identify_started_ms = millis();
  identify_active = true;
}

}  // namespace ui
