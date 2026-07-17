// Single-producer / single-consumer jitter buffer for stereo audio frames.
// Producer: network task (push packets / hold-fill). Consumer: audio task.
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
#include "freertos/FreeRTOS.h"

class JitterBuffer {
 public:
  void reset() {
    portENTER_CRITICAL(&mux_);
    head_ = tail_ = 0;
    head_deadline_us_ = 0;
    epoch_++;
    started_ = false;
    portEXIT_CRITICAL(&mux_);
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
    if (head_ == tail_) head_deadline_us_ = deadline_us;
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
    if (ok) tail_ = (tail + count) % JB_CAPACITY_FRAMES;
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
    if (ok) tail_ = (tail + count) % JB_CAPACITY_FRAMES;
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

  int16_t buf_[JB_CAPACITY_FRAMES * 2];
  uint32_t head_ = 0;  // consumer index (frames)
  uint32_t tail_ = 0;  // producer index (frames)
  uint64_t head_deadline_us_ = 0;
  uint32_t epoch_ = 0;  // bumped by reset(); aborts in-flight push/pop commits
  int16_t last_l_ = 0, last_r_ = 0;  // producer-owned (concealment source)
  volatile bool started_ = false;
  mutable portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
};
