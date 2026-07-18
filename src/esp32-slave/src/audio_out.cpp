#include "audio_out.h"

#include <Arduino.h>
#include <string.h>

#include "config.h"
#include "driver/i2s_std.h"

namespace {

i2s_chan_handle_t tx_chan = nullptr;
volatile float g_gain = 1.0f;
// Q15 mirror of g_gain: the C3 has no FPU, so the per-sample scale in write()
// must stay integer (a float multiply here is 480 soft-float calls per block
// in the prio-10 audio task). 32768 == unity.
volatile int32_t g_gain_q15 = 32768;
int16_t scaled[AUDIO_BLOCK_FRAMES * 2];
bool g_init_ok = false;
volatile uint32_t g_write_calls = 0;   // audio-task liveness (see `stat`)
volatile uint32_t g_write_done = 0;    // completed i2s writes
esp_err_t g_init_err = ESP_OK;         // first failing step's code, for `stat`
const char* g_init_step = "ok";

}  // namespace

namespace audio_out {

bool init() {
  i2s_chan_config_t chan_cfg =
      I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  chan_cfg.dma_desc_num = 6;
  chan_cfg.dma_frame_num = AUDIO_BLOCK_FRAMES;
  chan_cfg.auto_clear = true;  // output silence on underrun, not stale DMA
  esp_err_t err = i2s_new_channel(&chan_cfg, &tx_chan, nullptr);
  if (err != ESP_OK) {
    g_init_err = err;
    g_init_step = "new_channel";
    return false;
  }

  i2s_std_config_t std_cfg = {
      .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                      I2S_SLOT_MODE_STEREO),
      .gpio_cfg =
          {
              .mclk = I2S_GPIO_UNUSED,
              .bclk = (gpio_num_t)PIN_I2S_BCK,
              .ws = (gpio_num_t)PIN_I2S_LRCK,
              .dout = (gpio_num_t)PIN_I2S_DOUT,
              .din = I2S_GPIO_UNUSED,
              .invert_flags = {.mclk_inv = false,
                               .bclk_inv = false,
                               .ws_inv = false},
          },
  };
  err = i2s_channel_init_std_mode(tx_chan, &std_cfg);
  if (err != ESP_OK) {
    g_init_err = err;
    g_init_step = "init_std_mode";
    return false;
  }
  err = i2s_channel_enable(tx_chan);
  if (err != ESP_OK) {
    g_init_err = err;
    g_init_step = "enable";
    return false;
  }
  g_init_ok = true;
  return true;
}

bool write(const int16_t* frames, size_t frame_count) {
  g_write_calls++;
  // !g_init_ok guard: writing to a created-but-never-enabled channel blocks
  // forever (no DMA drain), which would also starve the mic pump — the audio
  // task must keep spinning at block rate even with the DAC path down.
  if (!g_init_ok || tx_chan == nullptr || frame_count > AUDIO_BLOCK_FRAMES) {
    // Failed init must not turn the prio-10 audio task into a WDT-starving
    // spin loop — burn one block period instead.
    vTaskDelay(pdMS_TO_TICKS(5));
    return false;
  }
  const int32_t q = g_gain_q15;
  const size_t n = frame_count * 2;
  if (q >= 32768) {
    memcpy(scaled, frames, n * sizeof(int16_t));
  } else {
    for (size_t i = 0; i < n; i++) {
      scaled[i] = (int16_t)(((int32_t)frames[i] * q) >> 15);
    }
  }
  size_t written = 0;
  const bool ok = i2s_channel_write(tx_chan, scaled, n * sizeof(int16_t),
                                    &written, portMAX_DELAY) == ESP_OK;
  if (ok) g_write_done++;
  return ok;
}

bool init_ok() { return g_init_ok; }
const char* init_err_step() { return g_init_step; }
int init_err_code() { return (int)g_init_err; }
uint32_t write_calls() { return g_write_calls; }
uint32_t writes_done() { return g_write_done; }

void set_gain(float g) {
  if (g < 0.0f) g = 0.0f;
  if (g > 1.0f) g = 1.0f;
  g_gain = g;
  g_gain_q15 = (int32_t)(g * 32768.0f + 0.5f);
}

float gain() { return g_gain; }

}  // namespace audio_out
