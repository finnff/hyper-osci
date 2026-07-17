// HYPEROSCI slave firmware — ESP32-C3 SuperMini + PCM5102A + MAX4466.
// Architecture: docs/firmware/esp32-architecture.md. Pins: include/config.h.
#include <Arduino.h>

#include "audio_out.h"
#include "config.h"
#include "mic_in.h"
#include "mode_manager.h"
#include "net_rx.h"
#include "renderer_local.h"
#include "ui.h"

namespace {

// The audio task is the heartbeat of the unit: one 5 ms block per iteration,
// paced by the blocking I2S DMA write. It must never block on anything else.
void audio_task(void*) {
  static int16_t block[AUDIO_BLOCK_FRAMES * 2];
  for (;;) {
    mode_manager::fill_block(block, AUDIO_BLOCK_FRAMES);
    audio_out::write(block, AUDIO_BLOCK_FRAMES);
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);

  ui::init();  // must be first: samples "MODE held at boot"

  const bool boot_local = ui::boot_local_requested();
  mode_manager::init(boot_local ? mode_manager::Mode::LOCAL
                                : mode_manager::Mode::NETWORK);

  if (!mic_in::init()) Serial.println("[init] adc_continuous FAILED");
  if (!audio_out::init()) Serial.println("[init] i2s FAILED");
  renderer_local::init();

  net_rx::set_cmd_handler(mode_manager::handle_command);
  net_rx::init(!boot_local);  // radio off when booted into forced-local

  // Audio gets the highest priority in the system after WiFi internals.
  xTaskCreate(audio_task, "audio", 4096, nullptr, 10, nullptr);

  Serial.printf("HYPEROSCI slave id=%u mode=%s — 'help' for console\n",
                mode_manager::slave_id(), mode_manager::mode_name());
}

void loop() {
  ui::poll();
  delay(10);  // ~100 Hz UI/console; audio + network run in their own tasks
}
