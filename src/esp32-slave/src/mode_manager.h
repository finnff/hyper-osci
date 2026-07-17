// Mode state machine (docs/DESIGN.md §7): LOCAL / NETWORK / HYBRID with
// automatic fallback to local rendering when the stream is absent. Also owns
// persisted identity/settings (NVS).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mode_manager {

enum class Mode : uint8_t { LOCAL = 0, NETWORK = 1, HYBRID = 2 };

void init(Mode boot_mode);

void set_mode(Mode m);
Mode mode();
void cycle_mode();
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
