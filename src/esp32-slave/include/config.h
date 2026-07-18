// HYPEROSCI slave — canonical hardware configuration.
// This file mirrors docs/DESIGN.md §4/§6/§7. Change both together or not at all.
#pragma once

#include <stdint.h>

// ---------------------------------------------------------------------------
// Pin map — ESP32-C3 SuperMini. No signals on strapping pins (2, 8, 9).
// ---------------------------------------------------------------------------
#define PIN_MIC_ADC 0     // ADC1_CH0 — MAX4466 OUT (biased ~VCC/2)
#define PIN_VBAT_ADC 1    // ADC1_CH1 — battery via 100k/100k divider + 100nF
// GPIO2: strapping pin, unused, 10k pull-up on carrier board
#define PIN_POT_ADC 3     // ADC1_CH3 — 10k pot wiper (filter cutoff control)
#define PIN_I2S_BCK 4     // PCM5102A BCK
#define PIN_I2S_LRCK 5    // PCM5102A LCK
#define PIN_I2S_DOUT 6    // PCM5102A DIN
#define PIN_BTN_MODE 7    // momentary to GND, internal pull-up
#define PIN_LED_ONBOARD 8 // SuperMini blue LED, ACTIVE LOW, heartbeat only
#define PIN_BTN_BOOT 9    // onboard BOOT button (secondary user button)
#define PIN_LED_NET 10    // green status LED (active high, ~2.2k series)
#define PIN_LED_MODE 20   // amber mode LED (active high, ~2.2k series)
// GPIO21: UART0 TX, reserved for debug header (primary console = USB-C CDC)

// ---------------------------------------------------------------------------
// Audio
// ---------------------------------------------------------------------------
#define SAMPLE_RATE 48000           // I2S output rate, fixed for v1
#define AUDIO_BLOCK_FRAMES 240      // 5 ms blocks, matches network packet size
#define ADC_SAMPLE_RATE_TOTAL 72000 // adc_continuous total (3 ch => 24k each)
#define ADC_NUM_CHANNELS 3          // mic, vbat, pot
#define MIC_SAMPLE_RATE 24000       // per-channel rate
#define MIC_UPSAMPLE 2              // 24 kHz mic -> 48 kHz out (linear interp)

// Local-mode Y filter: 2-pole Butterworth LPF, cutoff set by pot.
// Values carried over from the proven esp32c3SIGMADELTA unit.
#define LOCAL_LPF_FC_MIN_HZ 20.0f
#define LOCAL_LPF_FC_MAX_HZ 300.0f
#define HYBRID_MIC_GAIN 0.5f // mic contribution in HYBRID mode

// ---------------------------------------------------------------------------
// Jitter buffer / sync
// ---------------------------------------------------------------------------
// Sized to ride through WiFi radio stalls at the controller's 300 ms deadline
// lead. The UNO-Q's ath10k AP goes deaf ~100-300 ms every ~1.44 s (firmware
// quirk, W1 bring-up: not BT-coex, not power save, not P2P — unfixable from
// userspace); the buffer must absorb lead + post-stall delivery burst.
#define JB_CAPACITY_FRAMES 24576 // ~512 ms at 48 kHz (96 KiB)
#define JB_TARGET_DEPTH_MS 60   // startup buffering target
#define STREAM_TIMEOUT_MS 1000  // no audio packets -> fallback (v3.1 req)
#define SYNC_STALE_MS 5000      // no SYNC beacon -> clock considered stale
#define DEADLINE_SLACK_US 5000  // +-5 ms playback tolerance (v3.1 req)

// ---------------------------------------------------------------------------
// Network (docs/protocol.md is normative)
// ---------------------------------------------------------------------------
#define WIFI_SSID_DEFAULT "HYPEROSCI_AP"
#define WIFI_PASS_DEFAULT "hyperosci2026"
#define WIFI_CONNECT_TIMEOUT_MS 5000
#define MCAST_GROUP 239, 0, 0, 1
#define PORT_AUDIO 5000
#define PORT_CTRL 5001
#define PORT_STATUS 5002
#define STATUS_INTERVAL_MS 1000

// ---------------------------------------------------------------------------
// Power management (see docs/hardware/power-budget.md)
// ---------------------------------------------------------------------------
#define VBAT_DIVIDER 2.0f     // 100k/100k
#define VBAT_WARN_MV 3450     // low-battery LED pattern
#define VBAT_WIFI_OFF_MV 3300 // drop to LOCAL, radio off
#define VBAT_SLEEP_MV 3050    // deep sleep to protect the cell
#define VBAT_HYSTERESIS_MV 50

// ---------------------------------------------------------------------------
// UI timing
// ---------------------------------------------------------------------------
#define BTN_DEBOUNCE_MS 30
#define BTN_LONGPRESS_MS 2000
#define HEARTBEAT_PERIOD_MS 1000
