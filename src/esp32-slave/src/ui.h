// Buttons, LEDs, battery policy, USB-CDC serial console.
// poll() is called from Arduino loop() at ~100 Hz.
#pragma once

namespace ui {

void init();          // GPIO setup; also samples "MODE held at boot"
bool boot_local_requested();  // MODE button was held during power-on
void poll();
void identify();      // blink both LEDs for ~3 s (network "identify" cmd)

}  // namespace ui
