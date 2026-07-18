#include "mic_in.h"

#include <Arduino.h>

#include "config.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_continuous.h"
#include "esp_private/gdma.h"

namespace {

adc_continuous_handle_t adc_handle = nullptr;
adc_cali_handle_t cali_handle = nullptr;
bool cali_ok = false;

// Mic sample ring. Produced and consumed from the audio task (via read()),
// so no locking is needed; it only absorbs ADC-vs-I2S rate wobble.
constexpr size_t RING_SIZE = 4096;  // ~170 ms at 24 kHz
int16_t ring[RING_SIZE];
size_t ring_head = 0;  // consumer
size_t ring_tail = 0;  // producer

// DC blocker state (1-pole HPF, fc ~2 Hz at 24 kHz)
float dc_prev_x = 0.0f;
float dc_prev_y = 0.0f;
constexpr float DC_R = 0.9995f;

// Slow-channel smoothers (IIR on raw ADC counts)
float vbat_raw_avg = 0.0f;
float pot_raw_avg = 0.0f;
constexpr float SLOW_ALPHA = 0.01f;

// Diagnostics (DESIGN §12 "no drops over 10 min" pass criterion: both stay 0
// while the mic is being consumed; clamp events are expected in NETWORK mode).
uint32_t st_ring_overflow = 0;  // ring full, sample dropped
uint32_t st_adc_errors = 0;     // adc_continuous_read hard errors
uint32_t st_latency_clamps = 0; // oldest samples discarded to bound latency

// Bring-up meters (see `stat`): peak |mic sample| since last query, plus a
// slow IIR of the raw mic counts to verify the MAX4466's ~VCC/2 bias.
volatile uint16_t st_mic_peak = 0;
float mic_raw_avg = 2048.0f;
constexpr float MIC_BIAS_ALPHA = 0.001f;  // ~40 ms at 24 kHz
bool g_init_ok = false;

// NOTE: an ADC-liveness watchdog that stop/recreated the driver from the
// audio task boot-looped the chip (xTaskPriorityDisinherit assert — driver
// lifecycle must stay in the task that created it). The C3's real conflict is
// interrupt-line allocation (intr_alloc "No free interrupt inputs for
// DMA_CH0"), fixed at init time, not by runtime restarts.
uint32_t st_adc_restarts = 0;  // reserved for a future safe recovery path

// The nominal 24 kHz/channel is actually ~24.51 kHz: the ADC divider is
// (80 MHz>>5)/ADC_SAMPLE_RATE_TOTAL truncated to an integer (2.5e6/72000 →
// 34 → 73.53 kHz total). Harmless for scope visuals (+2.1% pitch), but the
// ring must be depth-clamped or the surplus pins latency at full-ring depth.
constexpr size_t RING_MAX_DEPTH = 480;  // ~20 ms at 24 kHz

size_t ring_depth() {
  return (ring_tail + RING_SIZE - ring_head) % RING_SIZE;
}

void ring_push(int16_t s) {
  size_t next = (ring_tail + 1) % RING_SIZE;
  if (next == ring_head) {
    st_ring_overflow++;
    return;  // full: drop newest
  }
  ring[ring_tail] = s;
  ring_tail = next;
}

// Decimation counters for light pumps (one per channel; see pump()).
uint16_t vbat_skip = 0, pot_skip = 0, bias_skip = 0;
constexpr uint16_t LIGHT_DECIM = 256;  // IIR tau ~4 ms -> ~1 s wall time

// Drain whatever the DMA has ready into the ring / smoothers.
//
// mic_math=false is the "light" pump for paths that discard the mic anyway
// (NETWORK streaming, non-mic local patterns): the DMA pool is still fully
// drained (the documented anti-wedge — never let it overflow), but the
// per-sample soft-float DC-blocker/ring work is skipped and the slow-channel
// IIRs update 1-in-256 (they only need ~1 Hz freshness; this also matches
// power-budget.md's ">=1 s decimation"). Saves ~8-10% of the FPU-less core.
void pump(bool mic_math) {
  if (adc_handle == nullptr) return;
  static uint8_t buf[2048];
  uint32_t got = 0;
  esp_err_t err;
  while ((err = adc_continuous_read(adc_handle, buf, sizeof(buf), &got, 0)) ==
             ESP_OK &&
         got > 0) {
    for (uint32_t i = 0; i + SOC_ADC_DIGI_RESULT_BYTES <= got;
         i += SOC_ADC_DIGI_RESULT_BYTES) {
      adc_digi_output_data_t* p = (adc_digi_output_data_t*)&buf[i];
      uint32_t ch = p->type2.channel;
      uint32_t raw = p->type2.data;
      if (ch == ADC_CHANNEL_0) {  // mic
        if (!mic_math) {  // output discarded: keep only the bias meter, slow
          if (++bias_skip >= LIGHT_DECIM) {
            bias_skip = 0;
            mic_raw_avg += MIC_BIAS_ALPHA * ((float)raw - mic_raw_avg);
          }
          continue;
        }
        mic_raw_avg += MIC_BIAS_ALPHA * ((float)raw - mic_raw_avg);
        // Center 12-bit and scale to Q15, then DC-block.
        float x = (float)((int32_t)raw - 2048) * 16.0f;
        float y = x - dc_prev_x + DC_R * dc_prev_y;
        dc_prev_x = x;
        dc_prev_y = y;
        if (y > 32767.0f) y = 32767.0f;
        if (y < -32768.0f) y = -32768.0f;
        const int16_t s = (int16_t)y;
        const uint16_t mag = (uint16_t)(s < 0 ? -(int32_t)s : s);
        if (mag > st_mic_peak) st_mic_peak = mag;
        ring_push(s);
      } else if (ch == ADC_CHANNEL_1) {  // vbat
        if (mic_math || ++vbat_skip >= LIGHT_DECIM) {
          vbat_skip = 0;
          vbat_raw_avg += SLOW_ALPHA * ((float)raw - vbat_raw_avg);
        }
      } else if (ch == ADC_CHANNEL_3) {  // pot
        if (mic_math || ++pot_skip >= LIGHT_DECIM) {
          pot_skip = 0;
          pot_raw_avg += SLOW_ALPHA * ((float)raw - pot_raw_avg);
        }
      }
    }
    if (got < sizeof(buf)) break;  // pool drained
  }
  if (err != ESP_OK && err != ESP_ERR_TIMEOUT) st_adc_errors++;
}

// Everything that allocates driver/GDMA resources.
bool create_and_start() {
  // GDMA pair decoy (must run AFTER audio_out::init()). The I2S TX channel
  // sits on GDMA pair 0 and exclusively owns that pair's one interrupt source
  // (DMA_CH0). Without this, the ADC's RX channel lands on pair 0's free RX
  // slot and then can't install its interrupt ("intr_alloc: No free interrupt
  // inputs for DMA_CH0" — even with 16 CPU lines free, a source can only be
  // allocated once). A decoy RX channel (never started, installs no ISR)
  // occupies pair 0's RX slot so the ADC allocates on pair 1 and gets the
  // free DMA_CH1 interrupt.
  static gdma_channel_handle_t gdma_decoy_rx = nullptr;
  if (gdma_decoy_rx == nullptr) {
    gdma_channel_alloc_config_t decoy = {};
    decoy.direction = GDMA_CHANNEL_DIRECTION_RX;
    gdma_new_ahb_channel(&decoy, &gdma_decoy_rx);  // best-effort
  }

  adc_continuous_handle_cfg_t hcfg = {
      .max_store_buf_size = 8192,
      .conv_frame_size = 1024,
  };
  if (adc_continuous_new_handle(&hcfg, &adc_handle) != ESP_OK) return false;

  adc_digi_pattern_config_t patterns[ADC_NUM_CHANNELS] = {};
  const adc_channel_t chans[ADC_NUM_CHANNELS] = {
      ADC_CHANNEL_0,  // GPIO0 mic
      ADC_CHANNEL_1,  // GPIO1 vbat
      ADC_CHANNEL_3,  // GPIO3 pot
  };
  for (int i = 0; i < ADC_NUM_CHANNELS; i++) {
    patterns[i].atten = ADC_ATTEN_DB_12;
    patterns[i].channel = chans[i];
    patterns[i].unit = ADC_UNIT_1;
    patterns[i].bit_width = 12;
  }
  adc_continuous_config_t ccfg = {
      .pattern_num = ADC_NUM_CHANNELS,
      .adc_pattern = patterns,
      .sample_freq_hz = ADC_SAMPLE_RATE_TOTAL,
      .conv_mode = ADC_CONV_SINGLE_UNIT_1,
      .format = ADC_DIGI_OUTPUT_FORMAT_TYPE2,
  };
  if (adc_continuous_config(adc_handle, &ccfg) != ESP_OK) return false;

  return adc_continuous_start(adc_handle) == ESP_OK;
}

}  // namespace

namespace mic_in {

bool init() {
  g_init_ok = create_and_start();

  adc_cali_curve_fitting_config_t cali_cfg = {
      .unit_id = ADC_UNIT_1,
      .chan = ADC_CHANNEL_1,
      .atten = ADC_ATTEN_DB_12,
      .bitwidth = ADC_BITWIDTH_12,
  };
  cali_ok =
      adc_cali_create_scheme_curve_fitting(&cali_cfg, &cali_handle) == ESP_OK;

  return g_init_ok;
}

bool init_ok() { return g_init_ok; }
uint32_t restart_count() { return st_adc_restarts; }

void drain() {
  pump(false);  // light: DMA drained, per-sample mic math skipped (unused)
  ring_head = ring_tail;  // discard buffered mic audio; fallback starts fresh
}

size_t read(int16_t* dst, size_t n) {
  pump(true);
  // Latency clamp: the true ADC rate is ~2.1% above nominal (see
  // RING_MAX_DEPTH note), so surplus accumulates. Discard the oldest overage
  // to keep mic-to-scope latency bounded at ~20 ms.
  size_t depth = ring_depth();
  if (depth > RING_MAX_DEPTH) {
    const size_t surplus = depth - RING_MAX_DEPTH;
    ring_head = (ring_head + surplus) % RING_SIZE;
    st_latency_clamps += surplus;
  }
  size_t avail = ring_depth();
  size_t take = (avail < n) ? avail : n;
  for (size_t i = 0; i < take; i++) {
    dst[i] = ring[ring_head];
    ring_head = (ring_head + 1) % RING_SIZE;
  }
  for (size_t i = take; i < n; i++) dst[i] = 0;  // underrun: silence
  return n;
}

uint16_t vbat_mv() {
  int mv = 0;
  if (cali_ok &&
      adc_cali_raw_to_voltage(cali_handle, (int)vbat_raw_avg, &mv) == ESP_OK) {
    return (uint16_t)((float)mv * VBAT_DIVIDER);
  }
  // Uncalibrated fallback: assume ~3100 mV full scale at 12 dB attenuation.
  return (uint16_t)(vbat_raw_avg / 4095.0f * 3100.0f * VBAT_DIVIDER);
}

float pot_norm() {
  float v = pot_raw_avg / 4095.0f;
  if (v < 0.0f) v = 0.0f;
  if (v > 1.0f) v = 1.0f;
  return v;
}

uint16_t mic_peak() {
  const uint16_t p = st_mic_peak;
  st_mic_peak = 0;
  return p;
}

uint16_t mic_bias_raw() { return (uint16_t)mic_raw_avg; }

uint32_t overflow_count() { return st_ring_overflow; }
uint32_t adc_error_count() { return st_adc_errors; }
uint32_t latency_clamp_count() { return st_latency_clamps; }

}  // namespace mic_in
