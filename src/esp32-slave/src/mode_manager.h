// Mode state machine (docs/DESIGN.md §7): LOCAL / NETWORK / HYBRID with
// automatic fallback to local rendering when the stream is absent. Also owns
// persisted identity/settings (NVS).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mode_manager {

enum class Mode : uint8_t { LOCAL = 0, NETWORK = 1, HYBRID = 2 };

// Boot-time setup. `force_local` = MODE held at power-on (DESIGN §7). Returns
// true if the radio should be started — false only for that hold, which is
// the one gesture that can start a boot mic-only. Nothing is persisted.
bool init(bool force_local);

void set_mode(Mode m);
Mode mode();
void cycle_mode();

// DESIGN §7 MODE long press: radio and mode move together — off means the
// radio is stopped (not just idle) and rendering is mic-only. Session-only by
// design: every boot starts with WiFi ON, whatever a power cut interrupted.
void set_wifi_enabled(bool on, const char* reason = "button");
bool wifi_enabled();
const char* radio_off_reason();  // nullptr while the radio is on
const char* mode_name();

// Fill one block from the active source. Returns the source actually used:
// 0 = local render, 1 = network stream. Called from the audio task only.
uint8_t fill_block(int16_t* frames, size_t frame_count);
uint8_t active_source();

// JSON command entry point (wired as net_rx cmd handler in main.cpp).
void handle_command(const char* json, size_t len);

// Persisted identity/settings
uint8_t slave_id();
void set_slave_id(uint8_t id);  // persists to NVS

}  // namespace mode_manager
