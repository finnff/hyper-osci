// Network receiver: WiFi station management, UDP audio/control RX, jitter
// buffer with deadline policy, 1 Hz status TX. Runs its own FreeRTOS task.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace net_rx {

struct Stats {
  uint32_t rx_packets;
  uint32_t rx_dropped;  // stale / overflow / bad packets
  uint32_t underruns;
  uint32_t seq_gaps;
  int8_t rssi_dbm;
  bool wifi_up;
  bool stream_up;
  uint16_t buffer_depth_frames;
};

// Handler for received JSON commands (wired to mode_manager in main.cpp).
typedef void (*CmdHandler)(const char* json, size_t len);

void init(bool radio_on);  // creates the network task
void set_cmd_handler(CmdHandler h);

void set_radio(bool on);
bool radio_enabled();
bool wifi_connected();
const char* ps_mode();  // live WiFi power-save state: "none"|"min"|"max"|"?"
bool stream_active();  // audio packets within STREAM_TIMEOUT_MS

// Audio-task side: fill one block from the jitter buffer, honoring the
// ±DEADLINE_SLACK_US policy. Returns false if the network source can't
// provide the block (caller renders locally instead).
bool pull_block(int16_t* frames, size_t frame_count);

// Discard all buffered audio (race-safe). Called when the slave leaves the
// stream (LOCAL mode) so re-joining doesn't start with a stale-frame drop
// burst; the controller also stops sending to LOCAL slaves.
void flush();

Stats stats();

}  // namespace net_rx
