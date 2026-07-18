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

// Crash forensics (2026-07-18 LOCAL-pattern wedge): progress markers the
// debugger can read over JTAG when the console is dead. TEMPORARY.
extern "C" {
volatile uint32_t g_ckpt = 0;   // last checkpoint passed (see call sites)
volatile uint32_t g_iter = 0;   // audio loop iterations
}

namespace {

TaskHandle_t g_audio_task = nullptr;

// The audio task is the heartbeat of the unit: one 5 ms block per iteration,
// paced by the blocking I2S DMA write. It must never block on anything else.
void audio_task(void*) {
  static int16_t block[AUDIO_BLOCK_FRAMES * 2];
  for (;;) {
    g_iter++;
    g_ckpt = 1;
    mode_manager::fill_block(block, AUDIO_BLOCK_FRAMES);
    g_ckpt = 2;
    audio_out::write(block, AUDIO_BLOCK_FRAMES);
    g_ckpt = 3;
  }
}

}  // namespace

namespace app {
// Audio-task stack headroom (words), for `stat` — crash forensics 2026-07-18.
uint32_t audio_stack_hwm() {
  return g_audio_task ? uxTaskGetStackHighWaterMark(g_audio_task) : 0;
}
}  // namespace app

void setup() {
  Serial.begin(115200);

  ui::init();  // must be first: samples "MODE held at boot"

  const bool boot_local = ui::boot_local_requested();
  mode_manager::init(boot_local ? mode_manager::Mode::LOCAL
                                : mode_manager::Mode::NETWORK);

  // I2S before ADC: both allocate GDMA/interrupt resources; probing which one
  // loses the race when they contend (bring-up).
  if (!audio_out::init()) Serial.println("[init] i2s FAILED");
  if (!mic_in::init()) Serial.println("[init] adc_continuous FAILED");
  renderer_local::init();

  net_rx::set_cmd_handler(mode_manager::handle_command);
  net_rx::init(!boot_local);  // radio off when booted into forced-local

  // Audio gets the highest priority in the system after WiFi internals.
  // (4096 is plenty: stack HWM measured ~90% free during the 2026-07-18
  // wedge forensics — the 8192 experiment ruled out stack overflow.)
  xTaskCreate(audio_task, "audio", 4096, nullptr, 10, &g_audio_task);

  Serial.printf("HYPEROSCI slave id=%u mode=%s — 'help' for console\n",
                mode_manager::slave_id(), mode_manager::mode_name());
}

void loop() {
  ui::poll();
  delay(10);  // ~100 Hz UI/console; audio + network run in their own tasks
}
