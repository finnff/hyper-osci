// Network receiver: WiFi station management, UDP audio/control RX, jitter
// buffer with deadline policy, 1 Hz status TX. Runs its own FreeRTOS task.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace net_rx {

// --- Phase 2: loop-fill concealment (docs/stream-concealment-plan.md §4) ---

// Small-gap fill method. kLoop: loop-copy when the packet's loop_frames is
// usable, else last-value hold; kHold: always last-value hold.
enum class FillMode : uint8_t { kLoop = 0, kHold = 1 };
void set_fill_mode(FillMode m);

// Bench drop pattern (RAM only, console `droptest`): drop `run` consecutive
// AUDIO packets at the head of every `every`-packet cycle, BEFORE seq
// accounting, so the holes count as real loss. every == 0: off.
void set_droptest(uint32_t every, uint32_t run);

// One line per small-gap fill event, enqueued only while the fill log is on
// (console `log on`); drained by the UI loop.
struct FillEvent {
  uint8_t method;     // FillMode: 0 = loop, 1 = hold
  uint16_t frames;    // frames appended to the jitter buffer
  uint16_t pkts;      // missing packets in the hole (0 = none / unknown)
  uint32_t since0_ms; // ms since the last loop_frames == 0 packet;
                      // UINT32_MAX = never seen
};
void set_fill_log(bool on);
size_t drain_fill_events(FillEvent* out, size_t cap);  // FIFO, returns count
uint32_t fill_log_dropped();  // events lost to a full ring (cumulative)

struct Stats {
  uint32_t rx_packets;
  uint32_t rx_dropped;  // stale / overflow / bad packets
  uint32_t underruns;
  uint32_t seq_gaps;      // gap events (any seq discontinuity)
  uint32_t lost_packets;  // cumulative missing packets inferred from gap sizes
  uint32_t gap_resets;    // > HYPE_LOOP_FILL_MAX_MS deadline holes that flushed
  uint32_t loop_fills;    // small-gap fill events, loop-copy (Phase 2)
  uint32_t hold_fills;    // small-gap fill events, last-value hold
  uint32_t reanchor_gaps; // deadline holes with contiguous seq (re-anchors)
  uint32_t gap_hist[5];   // seq holes by packets missing: 1, 2, 3-4, 5-30, >30
  FillMode fill_mode;     // current small-gap fill method
  uint16_t last_loop_frames;  // last loop_frames field seen (0 = no usable loop)
  uint32_t droptest_every;  // bench drop pattern (0 = off)
  uint32_t droptest_run;
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
// Lead (deadline - now, ms) of the packet that last started an empty buffer;
// mode_manager::EV_NO_LEAD if the clock wasn't synced.
int32_t start_lead_ms();

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
