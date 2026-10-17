// Host-test shim for the FreeRTOS critical-section primitives used by
// jitter_buffer.h. Single-threaded: there is no real locking.
// portENTER_CRITICAL calls an optional test hook so a test can inject
// reset() between an in-flight push's snapshot and commit — the one race a
// single thread cannot hit any other way (docs/stream-concealment-plan.md
// §4.4). The test arms the hook (fires only on the Nth call) and clears it
// from the hook.
#pragma once

#include <stdint.h>

typedef struct {
  uint32_t dummy;
} portMUX_TYPE;

#define portMUX_INITIALIZER_UNLOCKED {0}

// Set by the test (nullptr by default); called on every portENTER_CRITICAL.
inline void (*g_critical_hook)(portMUX_TYPE* mux) = nullptr;

#define portENTER_CRITICAL(mux)            \
  do {                                     \
    if (g_critical_hook != nullptr) g_critical_hook(mux); \
  } while (0)

#define portEXIT_CRITICAL(mux) \
  do {                         \
  } while (0)
