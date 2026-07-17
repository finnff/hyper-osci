#include "renderer_local.h"

#include <Arduino.h>
#include <math.h>

#include "config.h"
#include "mic_in.h"

namespace {

renderer_local::Pattern g_pattern = renderer_local::Pattern::MIC;

// --- 2-pole Butterworth LPF (Q = 0.7071), carried over from the proven
// esp32c3SIGMADELTA unit, recomputed from the pot each block. Runs at
// SAMPLE_RATE on the upsampled mic signal.
float b0, b1, b2, a1, a2;
float fx1 = 0, fx2 = 0, fy1 = 0, fy2 = 0;
float g_fc = 50.0f;

void update_lpf_coeffs() {
  float fc = LOCAL_LPF_FC_MIN_HZ +
             (LOCAL_LPF_FC_MAX_HZ - LOCAL_LPF_FC_MIN_HZ) * mic_in::pot_norm();
  g_fc = fc;
  float w0 = 2.0f * (float)M_PI * fc / (float)SAMPLE_RATE;
  float alpha = sinf(w0) / (2.0f * 0.7071f);
  float a0 = 1.0f + alpha;
  b0 = ((1.0f - cosf(w0)) / 2.0f) / a0;
  b1 = (1.0f - cosf(w0)) / a0;
  b2 = ((1.0f - cosf(w0)) / 2.0f) / a0;
  a1 = (-2.0f * cosf(w0)) / a0;
  a2 = (1.0f - alpha) / a0;
}

inline float lpf_step(float x) {
  float y = b0 * x + b1 * fx1 + b2 * fx2 - a1 * fy1 - a2 * fy2;
  fx2 = fx1;
  fx1 = x;
  fy2 = fy1;
  fy1 = y;
  return y;
}

// Mic pattern state: previous 24 kHz sample for the ×2 linear interpolation.
int16_t mic_prev = 0;
int16_t mic_buf[AUDIO_BLOCK_FRAMES / MIC_UPSAMPLE];

// Test-pattern oscillator state
float phase_a = 0.0f;
float phase_b = 0.0f;

inline int16_t sat16(float v) {
  if (v > 32767.0f) return 32767;
  if (v < -32768.0f) return -32768;
  return (int16_t)v;
}

void render_mic(int16_t* frames, size_t frame_count, float gain) {
  const size_t n24 = frame_count / MIC_UPSAMPLE;
  mic_in::read(mic_buf, n24);
  update_lpf_coeffs();
  for (size_t i = 0; i < n24; i++) {
    int16_t cur = mic_buf[i];
    // ×2 upsample: midpoint, then the sample itself.
    float xa = 0.5f * (float)(mic_prev + cur);
    float xb = (float)cur;
    mic_prev = cur;
    float ya = lpf_step(xa);
    float yb = lpf_step(xb);
    frames[(i * 2) * 2] = sat16(xa * gain);          // X
    frames[(i * 2) * 2 + 1] = sat16(ya * gain);      // Y
    frames[(i * 2 + 1) * 2] = sat16(xb * gain);      // X
    frames[(i * 2 + 1) * 2 + 1] = sat16(yb * gain);  // Y
  }
}

void render_circle(int16_t* frames, size_t frame_count, float gain) {
  const float step = 2.0f * (float)M_PI * 100.0f / (float)SAMPLE_RATE;
  for (size_t i = 0; i < frame_count; i++) {
    frames[i * 2] = sat16(cosf(phase_a) * 26000.0f * gain);
    frames[i * 2 + 1] = sat16(sinf(phase_a) * 26000.0f * gain);
    phase_a += step;
    if (phase_a > 2.0f * (float)M_PI) phase_a -= 2.0f * (float)M_PI;
  }
}

// DESIGN §12 DC test: 0.2 / 0.13 Hz full-scale triangles. On a DC-coupled
// path the dot crawls slowly across the whole screen; any AC coupling shows
// as the dot sliding back toward center between steps.
void render_ramp(int16_t* frames, size_t frame_count, float gain) {
  const float step_x = 2.0f * 0.2f / (float)SAMPLE_RATE;   // 0.2 Hz triangle
  const float step_y = 2.0f * 0.13f / (float)SAMPLE_RATE;  // 0.13 Hz triangle
  for (size_t i = 0; i < frame_count; i++) {
    // phase_a/b run -1..+1..-1 as triangles.
    phase_a += step_x;
    if (phase_a > 1.0f) phase_a = -1.0f;
    phase_b += step_y;
    if (phase_b > 1.0f) phase_b = -1.0f;
    const float tri_x = 2.0f * fabsf(phase_a) - 1.0f;
    const float tri_y = 2.0f * fabsf(phase_b) - 1.0f;
    frames[i * 2] = sat16(tri_x * 30000.0f * gain);
    frames[i * 2 + 1] = sat16(tri_y * 30000.0f * gain);
  }
}

// DESIGN §12 ringing test: X/Y square waves (3:2) jump the beam between four
// corners; overshoot/ringing on the edges shows the DAC filter behavior.
void render_square(int16_t* frames, size_t frame_count, float gain) {
  const float step_a = 2.0f * (float)M_PI * 150.0f / (float)SAMPLE_RATE;
  const float step_b = 2.0f * (float)M_PI * 100.0f / (float)SAMPLE_RATE;
  for (size_t i = 0; i < frame_count; i++) {
    frames[i * 2] = sat16((sinf(phase_a) >= 0 ? 1.0f : -1.0f) * 24000.0f * gain);
    frames[i * 2 + 1] =
        sat16((sinf(phase_b) >= 0 ? 1.0f : -1.0f) * 24000.0f * gain);
    phase_a += step_a;
    phase_b += step_b;
    if (phase_a > 2.0f * (float)M_PI) phase_a -= 2.0f * (float)M_PI;
    if (phase_b > 2.0f * (float)M_PI) phase_b -= 2.0f * (float)M_PI;
  }
}

void render_lissajous(int16_t* frames, size_t frame_count, float gain) {
  // 3:2 Lissajous, 150/100 Hz.
  const float step_a = 2.0f * (float)M_PI * 150.0f / (float)SAMPLE_RATE;
  const float step_b = 2.0f * (float)M_PI * 100.0f / (float)SAMPLE_RATE;
  for (size_t i = 0; i < frame_count; i++) {
    frames[i * 2] = sat16(sinf(phase_a) * 26000.0f * gain);
    frames[i * 2 + 1] = sat16(sinf(phase_b) * 26000.0f * gain);
    phase_a += step_a;
    phase_b += step_b;
    if (phase_a > 2.0f * (float)M_PI) phase_a -= 2.0f * (float)M_PI;
    if (phase_b > 2.0f * (float)M_PI) phase_b -= 2.0f * (float)M_PI;
  }
}

}  // namespace

namespace renderer_local {

void init() { update_lpf_coeffs(); }

void render(int16_t* frames, size_t frame_count, float gain) {
  switch (g_pattern) {
    case Pattern::CIRCLE:
      render_circle(frames, frame_count, gain);
      break;
    case Pattern::LISSAJOUS:
      render_lissajous(frames, frame_count, gain);
      break;
    case Pattern::RAMP:
      render_ramp(frames, frame_count, gain);
      break;
    case Pattern::SQUARE:
      render_square(frames, frame_count, gain);
      break;
    case Pattern::MIC:
    default:
      render_mic(frames, frame_count, gain);
      break;
  }
}

void next_pattern() {
  g_pattern =
      (Pattern)(((uint8_t)g_pattern + 1) % (uint8_t)Pattern::COUNT);
}

void set_pattern(Pattern p) { g_pattern = p; }

Pattern pattern() { return g_pattern; }

const char* pattern_name() {
  switch (g_pattern) {
    case Pattern::CIRCLE:
      return "circle";
    case Pattern::LISSAJOUS:
      return "lissajous";
    case Pattern::RAMP:
      return "ramp";
    case Pattern::SQUARE:
      return "square";
    default:
      return "mic";
  }
}

float current_lpf_hz() { return g_fc; }

}  // namespace renderer_local
