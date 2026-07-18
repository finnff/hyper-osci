// Continuous-DMA ADC front end: MAX4466 mic (24 kHz), battery divider and
// filter pot (decimated). Single-task use only — call read() from the audio
// task; it drains the DMA pool as a side effect.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace mic_in {

bool init();
bool init_ok();  // last init() result, for `stat` (boot banner is easy to miss)
uint32_t restart_count();  // ADC-liveness watchdog firings (C3 GDMA conflict)

// Pull n mono mic samples at 24 kHz (MIC_SAMPLE_RATE), DC-blocked, Q15.
// Zero-fills on underrun. Always returns n.
size_t read(int16_t* dst, size_t n);

// Call once per block when the mic is NOT being rendered (network source
// active, non-mic local pattern): drains the ADC DMA pool — mandatory, an
// overflowing pool is the documented wedge risk — but skips the per-sample
// soft-float mic math (output would be discarded) and decimates the
// vbat/pot/bias IIRs to ~1 s freshness, saving ~8-10% of the core.
void drain();

uint16_t vbat_mv();  // smoothed, divider-corrected battery voltage
float pot_norm();    // 0.0 .. 1.0, smoothed

// Bring-up meters (see `stat`): music near the mic should move mic_peak()
// well above the idle noise floor; mic_bias_raw() should sit near the
// MAX4466's VCC/2 bias point (~2100-2300 counts at 12 dB attenuation).
uint16_t mic_peak();      // max |sample| (Q15) since last call, resets on read
uint16_t mic_bias_raw();  // slow-averaged raw mic ADC counts

// Diagnostics for the DESIGN §12 ADC-stability check (see `stat` console cmd).
uint32_t overflow_count();       // mic ring full, sample lost
uint32_t adc_error_count();      // adc_continuous_read hard errors
uint32_t latency_clamp_count();  // oldest samples discarded (rate surplus)

}  // namespace mic_in
