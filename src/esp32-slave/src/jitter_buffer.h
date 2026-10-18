// Single-producer / single-consumer jitter buffer for stereo audio frames.
// Producer: network task (push packets / hold-fill / loop-fill). Consumer: audio task.
// Tracks the master-clock playback deadline of the frame at the read head so
// the consumer can apply the +-DEADLINE_SLACK_US policy, and the deadline of
// the next frame a push would append (tail) so the producer can detect stream
// discontinuities (docs/protocol.md §5-§6).
//
// reset() may race pop()/push() across tasks; an epoch counter makes every
// copy-then-commit operation abort instead of corrupting indices.
#pragma once

#include <stdint.h>
#include <string.h>

#include "config.h"
#include "protocol.h"
#include "freertos/FreeRTOS.h"

class JitterBuffer {
 public:
  void reset() {
    portENTER_CRITICAL(&mux_);
    head_ = tail_ = 0;
    head_deadline_us_ = 0;
    epoch_++;
    started_ = false;
    pushed_since_reset_ = 0;
    portEXIT_CRITICAL(&mux_);
  }

  // Number of reset() calls so far (wraps). Another task compares it with
  // its last reading to notice a flush it did not cause.
  uint32_t flushes() const {
    portENTER_CRITICAL(&mux_);
    const uint32_t e = epoch_;
    portEXIT_CRITICAL(&mux_);
    return e;
  }

  // Deadline of the next frame a push would append. Only meaningful while
  // depth > 0 (caller checks).
  uint64_t tail_deadline_us() const {
    portENTER_CRITICAL(&mux_);
    uint64_t d = head_deadline_us_;
    uint32_t h = head_, t = tail_;
    portEXIT_CRITICAL(&mux_);
    uint32_t depth = (t + JB_CAPACITY_FRAMES - h) % JB_CAPACITY_FRAMES;
    return d + (uint64_t)depth * 1000000ULL / SAMPLE_RATE;
  }

  // Push `count` interleaved stereo frames whose first frame is due at
  // `deadline_us` (master clock). Returns false if there was no room or a
  // concurrent reset() intervened (packet dropped whole either way).
  bool push(const int16_t* frames, uint16_t count, uint64_t deadline_us) {
    if (count > free_frames()) return false;
    portENTER_CRITICAL(&mux_);
    const uint32_t epoch = epoch_;
    // Empty: this frame re-anchors the head. If the buffer played dry (an
    // underrun, no reset()), the ring still holds the stream from before
    // the stall — count it out as a loop source, as reset() does.
    if (head_ == tail_) {
      head_deadline_us_ = deadline_us;
      pushed_since_reset_ = 0;
    }
    const uint32_t tail = tail_;
    portEXIT_CRITICAL(&mux_);
    for (uint16_t i = 0; i < count; i++) {
      const uint32_t idx = (tail + i) % JB_CAPACITY_FRAMES;
      buf_[idx * 2] = frames[i * 2];
      buf_[idx * 2 + 1] = frames[i * 2 + 1];
    }
    last_l_ = frames[(count - 1) * 2];
    last_r_ = frames[(count - 1) * 2 + 1];
    portENTER_CRITICAL(&mux_);
    const bool ok = (epoch == epoch_);
    if (ok) {
      tail_ = (tail + count) % JB_CAPACITY_FRAMES;
      add_pushed(count);
    }
    portEXIT_CRITICAL(&mux_);
    return ok;
  }

  // Loss concealment: append `count` copies of the last pushed frame
  // (last-value hold for a gap in the stream). No-op when empty. Returns the
  // number of frames actually appended.
  uint16_t push_hold(uint16_t count) {
    portENTER_CRITICAL(&mux_);
    const uint32_t epoch = epoch_;
    const bool empty = (head_ == tail_);
    const uint32_t tail = tail_;
    portEXIT_CRITICAL(&mux_);
    if (empty) return 0;
    const uint16_t room = free_frames();
    if (count > room) count = room;
    for (uint16_t i = 0; i < count; i++) {
      const uint32_t idx = (tail + i) % JB_CAPACITY_FRAMES;
      buf_[idx * 2] = last_l_;
      buf_[idx * 2 + 1] = last_r_;
    }
    portENTER_CRITICAL(&mux_);
    const bool ok = (epoch == epoch_);
    if (ok) {
      tail_ = (tail + count) % JB_CAPACITY_FRAMES;
      add_pushed(count);
    }
    portEXIT_CRITICAL(&mux_);
    return ok ? count : 0;
  }

  // Loop-fill loss concealment (Phase 2): the streamed pattern repeats every
  // `loop` frames, so a hole of `count` frames is filled by copying from one
  // loop back in the ring instead of parking on the last value. Frame i is
  // copied from (tail + CAP - loop + i) % CAP, forward, one frame at a time:
  // when the hole is longer than a loop the copy reads frames this call
  // wrote earlier (LZ77-style) — memcpy/memmove would read stale ring
  // contents. Same commit protocol as push_hold(); returns the number of
  // frames actually appended (0 when empty, no usable loop, or a concurrent
  // reset() intervened).
  uint16_t push_loop(uint16_t count, uint16_t loop) {
    portENTER_CRITICAL(&mux_);
    const uint32_t epoch = epoch_;
    const bool empty = (head_ == tail_);
    const uint32_t tail = tail_;
    const uint32_t pushed = pushed_since_reset_;
    portEXIT_CRITICAL(&mux_);
    if (empty) return 0;
    const uint16_t room = free_frames();
    if (count > room) count = room;
    if (count == 0) return 0;
    // The ring must still hold a full loop of post-reset content, and the
    // fill's write window must not wrap under its own source window (CAP is
    // not a power of two; the + CAP form keeps the arithmetic uint32-safe).
    if (loop == 0 || loop > HYPE_LOOP_MAX_FRAMES) return 0;
    if (pushed < loop) return 0;
    if ((uint32_t)loop + count > (uint32_t)JB_CAPACITY_FRAMES - 1) return 0;
    for (uint16_t i = 0; i < count; i++) {
      const uint32_t src =
          (tail + JB_CAPACITY_FRAMES - loop + i) % JB_CAPACITY_FRAMES;
      const uint32_t dst = (tail + i) % JB_CAPACITY_FRAMES;
      buf_[dst * 2] = buf_[src * 2];
      buf_[dst * 2 + 1] = buf_[src * 2 + 1];
    }
    const uint32_t last = (tail + count - 1) % JB_CAPACITY_FRAMES;
    last_l_ = buf_[last * 2];
    last_r_ = buf_[last * 2 + 1];
    portENTER_CRITICAL(&mux_);
    const bool ok = (epoch == epoch_);
    if (ok) {
      tail_ = (tail + count) % JB_CAPACITY_FRAMES;
      add_pushed(count);
    }
    portEXIT_CRITICAL(&mux_);
    return ok ? count : 0;
  }

  // Pop `count` frames into dst. Returns false (nothing consumed) if fewer
  // than `count` frames are buffered or a concurrent reset() intervened.
  bool pop(int16_t* dst, uint16_t count) {
    if (depth_frames() < count) return false;
    portENTER_CRITICAL(&mux_);
    const uint32_t epoch = epoch_;
    const uint32_t head = head_;
    portEXIT_CRITICAL(&mux_);
    for (uint16_t i = 0; i < count; i++) {
      const uint32_t idx = (head + i) % JB_CAPACITY_FRAMES;
      dst[i * 2] = buf_[idx * 2];
      dst[i * 2 + 1] = buf_[idx * 2 + 1];
    }
    portENTER_CRITICAL(&mux_);
    const bool ok = (epoch == epoch_);
    if (ok) {
      head_ = (head + count) % JB_CAPACITY_FRAMES;
      head_deadline_us_ += (uint64_t)count * 1000000ULL / SAMPLE_RATE;
    }
    portEXIT_CRITICAL(&mux_);
    return ok;
  }

  // Drop `count` frames without copying (catch-up after falling behind).
  void skip(uint16_t count) {
    const uint16_t d = depth_frames();
    if (count > d) count = d;
    portENTER_CRITICAL(&mux_);
    head_ = (head_ + count) % JB_CAPACITY_FRAMES;
    head_deadline_us_ += (uint64_t)count * 1000000ULL / SAMPLE_RATE;
    portEXIT_CRITICAL(&mux_);
  }

  uint16_t depth_frames() const {
    portENTER_CRITICAL(&mux_);
    const uint32_t h = head_, t = tail_;
    portEXIT_CRITICAL(&mux_);
    return (uint16_t)((t + JB_CAPACITY_FRAMES - h) % JB_CAPACITY_FRAMES);
  }

  uint64_t head_deadline_us() const {
    portENTER_CRITICAL(&mux_);
    const uint64_t d = head_deadline_us_;
    portEXIT_CRITICAL(&mux_);
    return d;
  }

  // Playback gate: opens once depth reaches the startup target, closes on
  // reset()/underrun. Keeps us from draining the buffer dry right after the
  // first packet arrives.
  bool started() const { return started_; }
  void set_started(bool s) { started_ = s; }

 private:
  uint16_t free_frames() const {
    return (uint16_t)(JB_CAPACITY_FRAMES - 1 - depth_frames());
  }

  // Called with mux_ held: saturating high-water mark of frames committed
  // since the last reset() or empty re-anchor. push_loop() refuses to fill
  // while it is below the loop length (older content belongs to another
  // stream, or to the timeline before an underrun).
  void add_pushed(uint32_t count) {
    if (pushed_since_reset_ >= JB_CAPACITY_FRAMES) return;
    const uint32_t room = (uint32_t)JB_CAPACITY_FRAMES - pushed_since_reset_;
    pushed_since_reset_ += (count < room) ? count : room;
  }

  int16_t buf_[JB_CAPACITY_FRAMES * 2];
  uint32_t head_ = 0;  // consumer index (frames)
  uint32_t tail_ = 0;  // producer index (frames)
  uint64_t head_deadline_us_ = 0;
  uint32_t epoch_ = 0;  // bumped by reset(); aborts in-flight push/pop commits
  uint32_t pushed_since_reset_ = 0;  // frames committed since the last
                                     // reset() / empty re-anchor: the
                                     // push_loop() source guard
  int16_t last_l_ = 0, last_r_ = 0;  // producer-owned (concealment source)
  volatile bool started_ = false;
  mutable portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
};
