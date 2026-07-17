#include "audio_out.h"

#include <Arduino.h>
#include <string.h>

#include "config.h"
#include "driver/i2s_std.h"

namespace {

i2s_chan_handle_t tx_chan = nullptr;
volatile float g_gain = 1.0f;
int16_t scaled[AUDIO_BLOCK_FRAMES * 2];

}  // namespace

namespace audio_out {

bool init() {
  i2s_chan_config_t chan_cfg =
      I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  chan_cfg.dma_desc_num = 6;
  chan_cfg.dma_frame_num = AUDIO_BLOCK_FRAMES;
  chan_cfg.auto_clear = true;  // output silence on underrun, not stale DMA
  if (i2s_new_channel(&chan_cfg, &tx_chan, nullptr) != ESP_OK) return false;

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
  if (i2s_channel_init_std_mode(tx_chan, &std_cfg) != ESP_OK) return false;
  return i2s_channel_enable(tx_chan) == ESP_OK;
}

bool write(const int16_t* frames, size_t frame_count) {
  if (tx_chan == nullptr || frame_count > AUDIO_BLOCK_FRAMES) {
    // Failed init must not turn the prio-10 audio task into a WDT-starving
    // spin loop — burn one block period instead.
    vTaskDelay(pdMS_TO_TICKS(5));
    return false;
  }
  const float g = g_gain;
  const size_t n = frame_count * 2;
  if (g >= 0.999f) {
    memcpy(scaled, frames, n * sizeof(int16_t));
  } else {
    for (size_t i = 0; i < n; i++) {
      scaled[i] = (int16_t)((float)frames[i] * g);
    }
  }
  size_t written = 0;
  return i2s_channel_write(tx_chan, scaled, n * sizeof(int16_t), &written,
                           portMAX_DELAY) == ESP_OK;
}

void set_gain(float g) {
  if (g < 0.0f) g = 0.0f;
  if (g > 1.0f) g = 1.0f;
  g_gain = g;
}

float gain() { return g_gain; }

}  // namespace audio_out
