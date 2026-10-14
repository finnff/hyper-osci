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
  uint32_t seq_gaps;      // gap events (any seq discontinuity)
  uint32_t lost_packets;  // cumulative missing packets inferred from gap sizes
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
// ±DEADLINE_SLACK_US policy. Anything but PLAYED means the network source
// can't provide this block and the caller renders something else.
enum class Pull : uint8_t {
  PLAYED,    // block filled from the stream
  IDLE,      // buffering toward the start target (or nothing buffered)
  EARLY,     // head block not due yet (pre-roll of a freshly joined stream)
  UNDERRUN,  // buffer ran dry mid-play; rebuffering (counted in stats)
};
Pull pull_block(int16_t* frames, size_t frame_count);

// Whether stream audio is buffered at all (false = LOCAL mode). Both edges
// discard the buffer: going off drops audio that would rot until the next
// join; coming back on also drops anything that slipped in around the switch
// and forgets the last seq, so a LOCAL stint doesn't count as packet loss.
// Rejected packets touch no counters and don't keep stream_active() alive.
void set_accept_audio(bool on);

Stats stats();

}  // namespace net_rx
