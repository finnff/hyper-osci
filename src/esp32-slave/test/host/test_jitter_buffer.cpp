// Host test for JitterBuffer::push_loop (Phase 2 loop-fill concealment).
//
// Build & run (from src/esp32-slave/):
//   g++ -std=c++17 -Wall -Wextra -I test/host/shim -I include -I src
//       test/host/test_jitter_buffer.cpp -o /tmp/test_jb && /tmp/test_jb
//
// Cases (docs/stream-concealment-plan.md §4.4):
//   1. hole longer than a loop (LZ77 overlap: the copy reads its own writes)
//   2. ring wrap with tail < loop (the (tail + CAP - loop + i) % CAP index)
//   3. pushed_since_reset_ < loop -> push_loop returns 0 (the caller holds)
//   4. reset injected between snapshot and commit -> no commit, depth 0,
//      pushed count back to 0
//   5. the loop + count <= CAP - 1 bound
//   6. last_l_/last_r_ continuity: a following push_hold repeats the last
//      filled frame
// Plain asserts; exit code 0 = pass.

#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "jitter_buffer.h"

namespace {

// The streamed pattern: period P frames; frame i has X = (i % P) * 1000,
// Y = 500 + (i % P) * 100. P = 8 divides JB_CAPACITY_FRAMES (24576), so the
// pattern formula stays valid across ring wraps.
const uint16_t P = 8;
int16_t px(uint32_t i) { return (int16_t)((i % P) * 1000); }
int16_t py(uint32_t i) { return (int16_t)(500 + (i % P) * 100); }

// Append `n` frames of the pattern, continuing at global index `base`.
void push_periodic(JitterBuffer& jb, uint16_t n, uint32_t base) {
  static int16_t scratch[JB_CAPACITY_FRAMES * 2];
  for (uint16_t i = 0; i < n; i++) {
    scratch[i * 2] = px(base + i);
    scratch[i * 2 + 1] = py(base + i);
  }
  assert(jb.push(scratch, n, 0));
}

// Pop `n` frames and check they equal the pattern at [base, base + n).
void expect_pattern(JitterBuffer& jb, uint16_t n, uint32_t base) {
  static int16_t scratch[JB_CAPACITY_FRAMES * 2];
  assert(jb.pop(scratch, n));
  for (uint16_t i = 0; i < n; i++) {
    assert(scratch[i * 2] == px(base + i));
    assert(scratch[i * 2 + 1] == py(base + i));
  }
}

// 1. Hole (16 frames) longer than the loop (8): the copy must read frames it
//    wrote earlier in the same call, and the whole buffer must read as the
//    uninterrupted periodic continuation.
void test_lz77_overlap() {
  static JitterBuffer jb;
  jb.reset();
  push_periodic(jb, 200, 0);
  assert(jb.push_loop(16, P) == 16);
  assert(jb.depth_frames() == 216);
  expect_pattern(jb, 216, 0);
}

// 2. Ring wrap with tail < loop: fill the ring, pop most of it, push 5 more
//    so the tail index wraps to 4 (< loop 8) with 10 live frames.
void test_ring_wrap() {
  static JitterBuffer jb;
  jb.reset();
  const uint32_t CAP = JB_CAPACITY_FRAMES;
  push_periodic(jb, (uint16_t)(CAP - 1), 0);  // 24575 frames: tail = CAP - 1
  {  // pop all but the last 5 frames (values don't matter here)
    static int16_t scratch[JB_CAPACITY_FRAMES * 2];
    assert(jb.pop(scratch, (uint16_t)(CAP - 6)));
  }
  push_periodic(jb, 5, CAP - 1);  // slots CAP-1 .. 3: tail = 4, depth = 10
  assert(jb.depth_frames() == 10);
  assert(jb.push_loop(16, P) == 16);
  // Pre-existing 10 frames + 16 filled must all equal the uninterrupted
  // pattern from global index CAP - 6.
  expect_pattern(jb, 26, CAP - 6);
}

// 3. pushed_since_reset_ < loop -> 0; the caller's hold fallback still works.
void test_pushed_below_loop() {
  static JitterBuffer jb;
  jb.reset();
  push_periodic(jb, 4, 0);  // only 4 frames pushed since the reset
  assert(jb.push_loop(4, P) == 0);  // 4 < loop 8: no full loop in the ring
  assert(jb.depth_frames() == 4);
  assert(jb.push_hold(4) == 4);
  static int16_t scratch[JB_CAPACITY_FRAMES * 2];
  assert(jb.pop(scratch, 8));
  for (uint16_t i = 0; i < 8; i++) {
    assert(scratch[i * 2] == (i < 4 ? px(i) : px(3)));
    assert(scratch[i * 2 + 1] == (i < 4 ? py(i) : py(3)));
  }
}

// 4. reset() injected between push_loop's snapshot and commit (via the shim
//    hook, which fires on the 2nd portENTER_CRITICAL of the call): the commit
//    must abort and the counter restart from 0.
JitterBuffer* hook_jb = nullptr;
int hook_calls = 0;
void critical_hook(portMUX_TYPE* mux) {
  (void)mux;
  hook_calls++;
  if (hook_calls == 2 && hook_jb != nullptr) hook_jb->reset();
}

void test_reset_race() {
  static JitterBuffer jb;
  jb.reset();
  push_periodic(jb, 24, 0);
  hook_jb = &jb;
  hook_calls = 0;
  g_critical_hook = critical_hook;
  const uint16_t r = jb.push_loop(16, P);
  g_critical_hook = nullptr;
  hook_jb = nullptr;
  assert(r == 0);               // commit aborted: the epoch changed
  assert(jb.depth_frames() == 0);  // the reset won; nothing committed
  // pushed_since_reset_ must be back to 0: 4 frames must NOT yet satisfy the
  // pushed >= loop guard (a stale count would let this fill through).
  push_periodic(jb, 4, 0);
  assert(jb.push_loop(4, P) == 0);
  push_periodic(jb, 4, 4);
  assert(jb.push_loop(4, P) == 4);  // 8 frames pushed: the guard passes
  expect_pattern(jb, 12, 0);  // fill continued the period-8 pattern (depth 12)
}

// 5. loop + count bound: with depth (10) < loop (100) the room clamp cannot
//    save an over-long count — only the write-window/source-window bound can.
void test_loop_count_bound() {
  static JitterBuffer jb;
  jb.reset();
  const uint32_t CAP = JB_CAPACITY_FRAMES;
  push_periodic(jb, 100, 0);
  {  // leave 10 frames live (head moves on, tail stays at 100)
    static int16_t scratch[JB_CAPACITY_FRAMES * 2];
    assert(jb.pop(scratch, 90));
  }
  assert(jb.push_loop(24500, 100) == 0);  // 100 + 24500 > CAP - 1
  assert(jb.depth_frames() == 10);
  assert(jb.push_loop((uint16_t)(CAP - 1 - 100), 100) ==
         (uint16_t)(CAP - 1 - 100));  // 100 + count == CAP - 1: just valid
  assert(jb.depth_frames() == (uint16_t)(10 + CAP - 1 - 100));
}

// 6. last-value continuity: after a loop fill, a hold must repeat the last
//    FILLED frame, not the last real one.
void test_last_value_continuity() {
  static JitterBuffer jb;
  jb.reset();
  push_periodic(jb, 16, 0);
  assert(jb.push_loop(8, P) == 8);  // hole of exactly one loop
  assert(jb.push_hold(3) == 3);
  static int16_t scratch[JB_CAPACITY_FRAMES * 2];
  assert(jb.pop(scratch, 27));
  for (uint16_t i = 0; i < 27; i++) {
    const int16_t xl = (i < 24) ? px(i) : px(23);
    const int16_t yl = (i < 24) ? py(i) : py(23);
    assert(scratch[i * 2] == xl);
    assert(scratch[i * 2 + 1] == yl);
  }
}

}  // namespace

int main() {
  test_lz77_overlap();
  test_ring_wrap();
  test_pushed_below_loop();
  test_reset_race();
  test_loop_count_bound();
  test_last_value_continuity();
  printf("push_loop: 6/6 cases passed\n");
  return 0;
}
