#include "timesync.h"

#include <Arduino.h>
#include <string.h>

#include "config.h"
#include "esp_timer.h"

namespace {

constexpr int WINDOW = 8;
int64_t samples[WINDOW];
int count = 0;
int next_slot = 0;
int64_t current_offset = 0;
uint64_t last_beacon_local_us = 0;
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

// v1 applies the median directly (no slew) — protocol.md §4.2 marks slewing
// as RECOMMENDED; jumps are bounded by beacon jitter and absorbed by the
// ±DEADLINE_SLACK_US policy.
int64_t median_of_window() {
  int64_t sorted[WINDOW];
  memcpy(sorted, samples, sizeof(int64_t) * count);
  // Insertion sort — window is tiny.
  for (int i = 1; i < count; i++) {
    int64_t v = sorted[i];
    int j = i - 1;
    while (j >= 0 && sorted[j] > v) {
      sorted[j + 1] = sorted[j];
      j--;
    }
    sorted[j + 1] = v;
  }
  if (count % 2 == 0) {
    return (sorted[count / 2 - 1] + sorted[count / 2]) / 2;
  }
  return sorted[count / 2];
}

}  // namespace

namespace timesync {

void on_beacon(uint64_t master_ts_us) {
  uint64_t local = (uint64_t)esp_timer_get_time();
  int64_t offset = (int64_t)master_ts_us - (int64_t)local;
  portENTER_CRITICAL(&mux);
  samples[next_slot] = offset;
  next_slot = (next_slot + 1) % WINDOW;
  if (count < WINDOW) count++;
  current_offset = median_of_window();
  last_beacon_local_us = local;
  portEXIT_CRITICAL(&mux);
}

bool valid() {
  portENTER_CRITICAL(&mux);
  uint64_t last = last_beacon_local_us;
  int n = count;
  portEXIT_CRITICAL(&mux);
  if (n == 0) return false;
  return ((uint64_t)esp_timer_get_time() - last) < (uint64_t)SYNC_STALE_MS * 1000ULL;
}

uint64_t master_now_us() {
  portENTER_CRITICAL(&mux);
  int64_t off = current_offset;
  portEXIT_CRITICAL(&mux);
  return (uint64_t)((int64_t)esp_timer_get_time() + off);
}

int64_t offset_us() {
  portENTER_CRITICAL(&mux);
  int64_t off = current_offset;
  portEXIT_CRITICAL(&mux);
  return off;
}

void reset() {
  portENTER_CRITICAL(&mux);
  count = 0;
  next_slot = 0;
  current_offset = 0;
  last_beacon_local_us = 0;
  portEXIT_CRITICAL(&mux);
}

}  // namespace timesync
