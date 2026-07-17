// Continuous-DMA ADC front end: MAX4466 mic (24 kHz), battery divider and
// filter pot (decimated). Single-task use only — call read() from the audio
// task; it drains the DMA pool as a side effect.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mic_in {

bool init();

// Pull n mono mic samples at 24 kHz (MIC_SAMPLE_RATE), DC-blocked, Q15.
// Zero-fills on underrun. Always returns n.
size_t read(int16_t* dst, size_t n);

// Call once per block when the mic is NOT being rendered (network source
// active): drains the ADC DMA pool so the vbat/pot smoothers keep updating
// and the mic ring holds fresh audio for an instant fallback.
void drain();

uint16_t vbat_mv();  // smoothed, divider-corrected battery voltage
float pot_norm();    // 0.0 .. 1.0, smoothed

// Diagnostics for the DESIGN §12 ADC-stability check (see `stat` console cmd).
uint32_t overflow_count();       // mic ring full, sample lost
uint32_t adc_error_count();      // adc_continuous_read hard errors
uint32_t latency_clamp_count();  // oldest samples discarded (rate surplus)

}  // namespace mic_in
