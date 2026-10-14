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

// Stream join/loss timeline for the serial console (docs/
// stream-concealment-plan.md §3.5). Recorded from any task, drained and
// printed by the loop task, so nothing prints from the audio path.
enum class EventKind : uint8_t {
  MODE,         // mode changed; arg = new Mode
  STREAM_UP,    // stream audio started arriving; arg = ms since mode entry
  FIRST_PLAY,   // first block played this stint; arg = ms since mode entry
  UNDERRUN,     // buffer ran dry mid-play; arg = ms since mode entry
  STREAM_LOST,  // no audio for STREAM_TIMEOUT_MS; arg = ms since mode entry
};
struct Event {
  uint32_t ms;  // millis() when recorded
  uint32_t arg;
  EventKind kind;
  Mode mode;  // mode at the time
};
bool pop_event(Event& out);    // false when empty
uint32_t events_dropped();     // ring overflowed (console not draining)
uint32_t last_preroll_ms();    // last FIRST_PLAY arg, 0 = none yet

// JSON command entry point (wired as net_rx cmd handler in main.cpp).
void handle_command(const char* json, size_t len);

// Persisted identity/settings
uint8_t slave_id();
void set_slave_id(uint8_t id);  // persists to NVS

}  // namespace mode_manager
