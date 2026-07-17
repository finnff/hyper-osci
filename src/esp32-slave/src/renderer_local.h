// Local (standalone) X/Y renderer — the fallback that reproduces the proven
// esp32c3SIGMADELTA behavior: X = mic, Y = pot-controlled Butterworth LPF of
// the mic. Plus bring-up test patterns.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace renderer_local {

// RAMP: slow full-scale triangles on X and Y — the DESIGN §12 "does the DAC
// pass DC" go/no-go test. SQUARE: sharp-edged 4-corner jumps — the §12
// interpolation-filter ringing test (compare FLT=L vs FLT=H).
enum class Pattern : uint8_t { MIC = 0, CIRCLE, LISSAJOUS, RAMP, SQUARE, COUNT };

void init();

// Fill interleaved stereo frames (L=X, R=Y) at SAMPLE_RATE, full block.
// `gain` scales the result (1.0 for LOCAL mode, HYBRID_MIC_GAIN for mixing).
void render(int16_t* frames, size_t frame_count, float gain);

void next_pattern();
void set_pattern(Pattern p);
Pattern pattern();
const char* pattern_name();
float current_lpf_hz();  // for status/console

}  // namespace renderer_local
