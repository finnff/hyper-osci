// I2S master TX to the PCM5102A (48 kHz / 16-bit / stereo, no MCLK — the DAC
// PLLs its clock from BCK with its SCK pin tied to GND).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace audio_out {

bool init();

// Blocking write of interleaved stereo frames (L=X, R=Y). The block on the
// I2S DMA queue is what paces the audio task loop.
bool write(const int16_t* frames, size_t frame_count);

void set_gain(float g);  // 0.0 .. 1.0 output scale
float gain();

// Bring-up telemetry (see `stat`): write_calls should advance ~200/s; a stuck
// counter means the audio task is blocked/dead, calls >> done means i2s errors.
bool init_ok();
const char* init_err_step();  // "ok" or the init step that failed
int init_err_code();          // esp_err_t of that step (0 if none)
uint32_t write_calls();
uint32_t writes_done();

}  // namespace audio_out
