// XIAO RA4M1 analog capture for the slaves' scope outputs (docs/hardware/bench-capture.md §4.3).
//
// Sampling is hardware-timed: a GPT timer's compare-match A event starts an ADC0 scan through the
// ELC at mid-period, and the same timer's overflow interrupt (half a period later) reads the
// finished scan. No CPU latency enters the sample instant; the read only has to happen before the
// next scan starts, half a period later.
//
// The onboard RGB LED (WS2812 on P112) shows one X/Y pair live: red = X swing, blue = Y swing,
// green = beam speed. A still dot, a hold, silence or a powered-off slave all go dark. The meter
// runs only while nothing streams (mon and the boot monitor): during cap the ISR does no meter
// work and the LED is never redrawn, so cap is free of the LED's interrupts-off chunks. v2 drew
// it during cap and a 4 ch / 48 kSa/s run dropped 13 % of blocks with it on.
//
// Serial protocol (USB CDC, newline-terminated text commands):
//   info                       -> one text line
//   cap <rate_hz> <pins>       -> binary blocks until "stop"; pins = comma list of XIAO pins, e.g. 2,3
//   mon <rate_hz> <pins>       -> sample for the LED only, nothing streamed
//   stop                       -> ends cap/mon, prints "stopped", returns to the boot monitor
//   led <pinX>,<pinY> | led off-> which pair drives the LED meter (off at boot; 2,3 = slave B)
//   ledtest <r> <g> <b>        -> meter off, LED set to a fixed colour (0-255, no brightness cap)
//   wsprobe on|off             -> mirror the WS2812 waveform on D8 (P111), for the logic analyzer
//   blast <seconds> [load]     -> raw USB throughput test: dummy bytes as fast as possible;
//                                 "load" keeps the boot monitor's 4 ch / 48 kSa/s ISR running
// "stopped" reports the sampling callback's cost since the last cap/mon: isr_avg/isr_max cycles
// (callback body only; FSP's ISR wrapper and exception entry/exit come on top).
//
// Block = 16-byte header + FRAMES_PER_BLOCK frames x n_ch little-endian uint16 (14-bit codes):
//   char magic[4] = "XADC"; uint32 seq; uint16 n_frames; uint8 n_ch; uint8 flags;
//   uint32 dropped_blocks (cumulative)
//   flags bit0: a scan was still running when it was read (ADC too slow for this rate/channel count)
//   flags bit1: a read happened after the next scan had started (sample may be from the wrong scan)

#include <Arduino.h>
#include "FspTimer.h"
#include "hal_data.h"
#include "r_adc.h"
#include "r_elc.h"

namespace {

constexpr uint16_t FRAMES_PER_BLOCK = 240;
constexpr uint8_t HEADER_BYTES = 16;
constexpr uint8_t MAX_CH = 5;
constexpr size_t POOL_BYTES = 8 * (16 + 240 * 4 * 2);  // 8 blocks at 4 ch; RAM is tight (sample_isr runs from it)
constexpr uint8_t FLAG_SCAN_OVERRUN = 0x01;
constexpr uint8_t FLAG_LATE_READ = 0x02;

constexpr uint32_t BOOT_RATE = 48000;
const char BOOT_PINS[] = "0,1,2,3";

// XIAO pin -> ADC0 channel, from the Seeed core's variants/XIAORA4M1/pinmux.inc.
struct PinAn { uint8_t pin; uint8_t an; };
constexpr PinAn PIN_AN[] = {{0, 9}, {1, 0}, {2, 1}, {3, 2}, {5, 22}};

uint8_t pool[POOL_BYTES] __attribute__((aligned(4)));

FspTimer timer;
uint8_t gpt_ch;
elc_instance_ctrl_t elc_ctrl;
const elc_cfg_t elc_cfg = {};  // no links; ADC0's link is set in start_sampling
adc_instance_ctrl_t adc_ctrl;
adc_extended_cfg_t adc_ext;
adc_cfg_t adc_cfg;
adc_channel_cfg_t adc_ch_cfg;
bool adc_open = false;

uint8_t n_ch = 0;
uint8_t pin_list[MAX_CH];
uint8_t an_list[MAX_CH];
uint16_t block_bytes = 0;
uint8_t n_blocks = 0;
uint16_t half_period = 0;

volatile bool sampling = false;
volatile bool streaming = false;
volatile uint8_t wr_block = 0;   // ISR fills this block
volatile uint8_t rd_block = 0;   // loop sends this block when rd != wr
uint16_t *wr_ptr, *wr_end;       // ISR-only: next frame slot / end of frames in block wr_block
volatile uint32_t seq = 0;
volatile uint32_t dropped = 0;
volatile uint8_t flags = 0;

// --- LED meter: accumulated in the ISR over 20 ms windows, drawn by loop() ---
uint8_t led_pin_x = 0xFF, led_pin_y = 0xFF;  // XIAO pins; 0xFF = LED off (enable with "led 2,3")
volatile int8_t led_ix = -1, led_iy = -1;  // indices into the active channel list
uint16_t m_win = 960;
uint16_t m_n = 0;
uint16_t m_xmin, m_xmax, m_ymin, m_ymax, m_px, m_py;
uint32_t m_speed = 0;
volatile uint16_t m_xpp, m_ypp, m_spd;
volatile bool m_ready = false;

uint8_t *block_ptr(uint8_t b) { return pool + size_t(b) * block_bytes; }

R_GPT0_Type *gpt_regs() {
  return reinterpret_cast<R_GPT0_Type *>(
      uintptr_t(R_GPT0) + gpt_ch * (uintptr_t(R_GPT1) - uintptr_t(R_GPT0)));
}

void start_block(uint8_t b) {
  uint8_t *h = block_ptr(b);
  memcpy(h, "XADC", 4);
  uint32_t s = seq;
  memcpy(h + 4, &s, 4);
  uint16_t nf = FRAMES_PER_BLOCK;
  memcpy(h + 8, &nf, 2);
  h[10] = n_ch;
  h[11] = 0;
  wr_ptr = reinterpret_cast<uint16_t *>(h + HEADER_BYTES);
  wr_end = wr_ptr + FRAMES_PER_BLOCK * n_ch;
  flags = 0;
}

void meter_reset() {
  m_n = 0;
  m_speed = 0;
  m_xmin = m_ymin = 0xFFFF;
  m_xmax = m_ymax = 0;
}

void meter_add(uint16_t x, uint16_t y) {
  if (x < m_xmin) m_xmin = x;
  if (x > m_xmax) m_xmax = x;
  if (y < m_ymin) m_ymin = y;
  if (y > m_ymax) m_ymax = y;
  if (m_n) m_speed += uint32_t(abs(int(x) - int(m_px)) + abs(int(y) - int(m_py)));
  m_px = x;
  m_py = y;
  if (++m_n < m_win) return;
  m_xpp = m_xmax - m_xmin;
  m_ypp = m_ymax - m_ymin;
  m_spd = uint16_t(m_speed / (m_n - 1));
  m_ready = true;
  meter_reset();
}

// The sampling ISR is the CPU's biggest load next to the USB stack, and USB CDC throughput on this
// part is CPU-bound (blast: 467 KB/s idle, 369 KB/s with the 48 kSa/s ISR running). So it runs from
// RAM (flash has a wait state at 48 MHz), is installed straight in the vector table instead of
// going through FSP's GPT ISR + callback, and writes each frame straight into the block.
#define RAMFUNC __attribute__((section(".code_in_ram"), noinline))

IRQn_Type samp_irq;
const volatile uint32_t *gtcnt;
const volatile uint16_t *adc_src[MAX_CH];  // &R_ADC0->ADDR[an] per active channel
uint64_t isr_cyc = 0;
uint32_t isr_calls = 0, isr_max = 0;

void finish_block() {
  uint8_t *blk = block_ptr(wr_block);
  blk[11] = flags;
  uint32_t d = dropped;
  memcpy(blk + 12, &d, 4);
  uint8_t next = uint8_t((wr_block + 1) % n_blocks);
  if (next == rd_block) {
    dropped = dropped + 1;  // ring full: overwrite this block, the seq gap shows the loss
  } else {
    wr_block = next;
  }
  seq = seq + 1;
  start_block(wr_block);
}

extern "C" RAMFUNC void sample_isr() {
  R_BSP_IrqStatusClear(samp_irq);
  if (!sampling) return;
  uint32_t t0 = DWT->CYCCNT;
  uint8_t f = R_ADC0->ADCSR_b.ADST ? FLAG_SCAN_OVERRUN : 0;
  if (streaming) {
    uint16_t *w = wr_ptr;
    for (uint8_t i = 0; i < n_ch; i++) *w++ = *adc_src[i];
    if (*gtcnt >= half_period) f |= FLAG_LATE_READ;
    flags |= f;
    if (w == wr_end) finish_block();  // once per block; may run from flash
    else wr_ptr = w;
  } else if (led_ix >= 0) {
    meter_add(*adc_src[led_ix], *adc_src[led_iy]);
  }
  uint32_t dt = DWT->CYCCNT - t0;
  isr_cyc += dt;
  isr_calls++;
  if (dt > isr_max) isr_max = dt;
}

void timer_cb(timer_callback_args_t *) {}  // unused: sample_isr replaces FSP's GPT ISR

// --- WS2812 on P112, bit-banged against the DWT cycle counter (48 MHz: 20.8 ns/cycle) ---
// Cycle counts are Adafruit_NeoPixel 1.13's RA4M1 values, same loop shape; the loop and port
// write add several cycles to each high time. v2 used T0H = 17: the LED went full white, i.e.
// every 0 bit read as a 1. Check the real widths with "wsprobe on" + the logic analyzer.
constexpr uint32_t WS_T0H = 12, WS_T1H = 38, WS_TBIT = 60;  // 0.25 / 0.79 / 1.25 us + overhead
constexpr uint32_t WS_BIT = 1u << 12, PROBE_BIT = 1u << 11;  // P112 = LED, P111 = D8 probe
uint32_t ws_set = WS_BIT, ws_clr = WS_BIT << 16;            // PCNTR3: low half sets, high clears
constexpr uint8_t LED_MAX = 40;  // brightness cap: keeps LED current (and rail ripple) small
bool ws_ok = false;             // set in setup() once the DWT cycle counter is seen running

void ws_chunk(uint32_t bits, int n) {
  uint32_t primask = __get_PRIMASK();
  __disable_irq();
  uint32_t t = DWT->CYCCNT;
  for (int i = n - 1; i >= 0; i--) {
    uint32_t hi = ((bits >> i) & 1) ? WS_T1H : WS_T0H;
    while (DWT->CYCCNT - t < WS_TBIT) {}
    t = DWT->CYCCNT;
    R_PORT1->PCNTR3 = ws_set;
    while (DWT->CYCCNT - t < hi) {}
    R_PORT1->PCNTR3 = ws_clr;
  }
  __set_PRIMASK(primask);
}

void ws_show(uint8_t r, uint8_t g, uint8_t b) {
  if (!ws_ok) return;  // a stopped CYCCNT would spin forever with interrupts off
  // A frame latches only after the line has been low >= 280 us (WS2812B). Without this gap two
  // back-to-back frames read as one 48-bit stream: the LED keeps the first 24 bits and passes the
  // rest on. ledtest (unbind -> black, then the colour) showed black that way.
  static uint32_t last_us = micros() - 1000;
  while (micros() - last_us < 300) {}
  uint32_t grb = (uint32_t(g) << 16) | (uint32_t(r) << 8) | b;
  for (int shift = 20; shift >= 0; shift -= 4) ws_chunk((grb >> shift) & 0xF, 4);
  last_us = micros();
}

uint8_t level(uint16_t v, uint16_t floor, uint16_t full) {
  if (v <= floor) return 0;
  float u = float(v - floor) / float(full - floor);
  if (u > 1.0f) u = 1.0f;
  return uint8_t(sqrtf(u) * LED_MAX + 0.5f);
}

void led_update() {
  static uint8_t r, g, b;
  if (!m_ready) return;
  m_ready = false;
  // Floors sit above the ~36 LSB rms noise; full scale ±3 V is ~10000 codes peak-to-peak.
  uint8_t nr = level(m_xpp, 400, 10000);
  uint8_t nb = level(m_ypp, 400, 10000);
  uint8_t ng = level(m_spd, 150, 1000);
  // Instant attack, ~150 ms release, so short events stay visible.
  r = nr > r ? nr : uint8_t(r * 7 / 8);
  g = ng > g ? ng : uint8_t(g * 7 / 8);
  b = nb > b ? nb : uint8_t(b * 7 / 8);
  ws_show(r, g, b);
}

void bind_led() {
  int8_t ix = -1, iy = -1;
  for (uint8_t i = 0; i < n_ch; i++) {
    if (pin_list[i] == led_pin_x) ix = int8_t(i);
    if (pin_list[i] == led_pin_y) iy = int8_t(i);
  }
  noInterrupts();
  meter_reset();
  if (streaming || ix < 0 || iy < 0) ix = iy = -1;  // meter is mon-only
  led_ix = ix;
  led_iy = iy;
  interrupts();
  if (ix < 0) ws_show(0, 0, 0);
}

const PinAn *find_pin(int pin) {
  for (const auto &p : PIN_AN)
    if (p.pin == pin) return &p;
  return nullptr;
}

void stop_sampling() {
  sampling = false;
  streaming = false;
  timer.stop();
  if (adc_open) {
    R_ADC_ScanStop(&adc_ctrl);
    R_ADC_Close(&adc_ctrl);
    adc_open = false;
  }
  timer.end();
  digitalWrite(LED_BUILTIN, HIGH);
}

bool open_adc(uint32_t scan_mask) {
  adc_ext = {};
  adc_ext.add_average_count = ADC_ADD_OFF;
  adc_ext.clearing = ADC_CLEAR_AFTER_READ_OFF;
  adc_ext.trigger_group_b = ADC_TRIGGER_SYNC_ELC;
  adc_ext.double_trigger_mode = ADC_DOUBLE_TRIGGER_DISABLED;
  adc_ext.adc_vref_control = ADC_VREF_CONTROL_AVCC0_AVSS0;
  adc_ext.enable_adbuf = 0;
  adc_ext.window_a_irq = FSP_INVALID_VECTOR;
  adc_ext.window_b_irq = FSP_INVALID_VECTOR;

  adc_cfg = {};
  adc_cfg.unit = 0;
  adc_cfg.mode = ADC_MODE_SINGLE_SCAN;
  adc_cfg.resolution = ADC_RESOLUTION_14_BIT;
  adc_cfg.alignment = ADC_ALIGNMENT_RIGHT;
  adc_cfg.trigger = ADC_TRIGGER_SYNC_ELC;
  adc_cfg.scan_end_irq = FSP_INVALID_VECTOR;
  adc_cfg.scan_end_b_irq = FSP_INVALID_VECTOR;
  adc_cfg.p_callback = nullptr;
  adc_cfg.p_extend = &adc_ext;

  adc_ch_cfg = {};
  adc_ch_cfg.scan_mask = scan_mask;
  adc_ch_cfg.priority_group_a = ADC_GROUP_A_PRIORITY_OFF;

  if (R_ADC_Open(&adc_ctrl, &adc_cfg) != FSP_SUCCESS) return false;
  adc_open = true;
  if (R_ADC_ScanCfg(&adc_ctrl, &adc_ch_cfg) != FSP_SUCCESS) return false;
  return R_ADC_ScanStart(&adc_ctrl) == FSP_SUCCESS;  // arms the ELC trigger
}

// Returns an error string, or nullptr on success.
const char *start_sampling(uint32_t rate, const char *pins, bool stream) {
  n_ch = 0;
  uint32_t mask = 0;
  for (const char *p = pins; *p;) {
    while (*p == ',' || *p == ' ' || *p == 'D' || *p == 'd') p++;
    if (!*p) break;
    char *end;
    long pin = strtol(p, &end, 10);
    if (end == p) return "bad pin list";
    p = end;
    const PinAn *pa = find_pin(int(pin));
    if (!pa) return "pin has no ADC channel in this firmware";
    if (n_ch == MAX_CH) return "too many pins";
    pin_list[n_ch] = pa->pin;
    adc_src[n_ch] = &R_ADC0->ADDR[pa->an];
    an_list[n_ch++] = pa->an;
    mask |= 1UL << pa->an;
    R_IOPORT_PinCfg(&g_ioport_ctrl, g_pin_cfg[pa->pin].pin, IOPORT_CFG_ANALOG_ENABLE);
  }
  if (n_ch == 0) return "no pins";
  if (rate < 1000 || rate > 200000) return "rate out of range";

  block_bytes = HEADER_BYTES + FRAMES_PER_BLOCK * n_ch * 2;
  n_blocks = uint8_t(POOL_BYTES / block_bytes);
  if (n_blocks < 3) return "not enough buffer";

  uint8_t type = GPT_TIMER;
  int8_t ch = FspTimer::get_available_timer(type);
  if (ch < 0 || type != GPT_TIMER) {
    ch = FspTimer::get_available_timer(type, true);
    if (ch < 0 || type != GPT_TIMER) return "no free GPT channel";
  }
  gpt_ch = uint8_t(ch);
  if (!timer.begin(TIMER_MODE_PERIODIC, GPT_TIMER, gpt_ch, float(rate), 50.0f, timer_cb))
    return "timer begin failed";
  if (!timer.setup_overflow_irq(2)) return "timer irq failed";
  if (!timer.open()) return "timer open failed";

  // Compare match A at mid-period starts the scan; the overflow IRQ reads it half a period later.
  R_GPT0_Type *gpt = gpt_regs();
  half_period = uint16_t((gpt->GTPR + 1) / 2);
  gpt->GTCCR[0] = half_period;
  gtcnt = &gpt->GTCNT;
  // IRQManager keeps this vector slot for the FspTimer object's lifetime; point it at sample_isr.
  samp_irq = timer.get_cfg()->cycle_end_irq;
  reinterpret_cast<volatile uint32_t *>(SCB->VTOR)[16 + samp_irq] = uint32_t(&sample_isr);

  fsp_err_t e = R_ELC_Open(&elc_ctrl, &elc_cfg);
  if (e != FSP_SUCCESS && e != FSP_ERR_ALREADY_OPEN) return "elc open failed";
  elc_event_t ev = elc_event_t(ELC_EVENT_GPT0_CAPTURE_COMPARE_A + 8 * gpt_ch);
  if (R_ELC_LinkSet(&elc_ctrl, ELC_PERIPHERAL_ADC0, ev) != FSP_SUCCESS) return "elc link failed";
  R_ELC_Enable(&elc_ctrl);

  if (!open_adc(mask)) return "adc open failed";
  m_win = uint16_t(rate / 50);  // 20 ms LED windows
  streaming = stream;           // before bind_led: cap leaves the meter off
  bind_led();
  wr_block = rd_block = 0;
  seq = dropped = 0;
  isr_cyc = isr_calls = isr_max = 0;
  start_block(0);
  sampling = true;
  if (stream) digitalWrite(LED_BUILTIN, LOW);
  if (!timer.start()) {
    stop_sampling();
    return "timer start failed";
  }
  return nullptr;
}

void boot_monitor() {
  if (start_sampling(BOOT_RATE, BOOT_PINS, false)) stop_sampling();
}

char line[64];
uint8_t line_len = 0;

bool read_line() {
  while (Serial.available()) {
    char c = char(Serial.read());
    if (c == '\r') continue;
    if (c == '\n') {
      line[line_len] = 0;
      line_len = 0;
      return true;
    }
    if (line_len < sizeof(line) - 1) line[line_len++] = c;
  }
  return false;
}

void blast(uint32_t seconds) {
  static uint8_t buf[1024];
  for (size_t i = 0; i < sizeof(buf); i++) buf[i] = uint8_t(i);
  uint32_t t0 = millis(), sent = 0;
  while (millis() - t0 < seconds * 1000UL) {
    sent += Serial.write(buf, sizeof(buf));
    if (Serial.available() && read_line() && strcmp(line, "stop") == 0) break;
  }
  Serial.flush();
  delay(50);
  Serial.print("\nblast_done bytes=");
  Serial.print(sent);
  Serial.print(" ms=");
  Serial.println(millis() - t0);
}

void handle(char *cmd) {
  char *verb = strtok(cmd, " ");
  if (!verb) return;
  bool is_cap = strcmp(verb, "cap") == 0;
  if (strcmp(verb, "stop") == 0) {
    stop_sampling();
    Serial.print("stopped isr_avg=");
    Serial.print(isr_calls ? uint32_t(isr_cyc / isr_calls) : 0);
    Serial.print(" isr_max=");
    Serial.print(isr_max);
    Serial.print(" calls=");
    Serial.println(isr_calls);
    boot_monitor();
  } else if (strcmp(verb, "info") == 0) {
    Serial.print("xiao_adc v3 pins=D0:AN9,D1:AN0,D2:AN1,D3:AN2,D5:AN22 res=14 frames_per_block=240 ws=");
    Serial.println(ws_ok ? "ok" : "no_cyccnt");
  } else if (is_cap || strcmp(verb, "mon") == 0) {
    char *rate = strtok(nullptr, " ");
    char *pins = strtok(nullptr, "");
    if (!rate || !pins) {
      Serial.println("err usage: cap|mon <rate_hz> <pins>");
      return;
    }
    stop_sampling();
    const char *err = start_sampling(uint32_t(strtoul(rate, nullptr, 10)), pins, is_cap);
    if (err) {
      stop_sampling();
      Serial.print("err ");
      Serial.println(err);
      boot_monitor();
      return;
    }
    // Text ack with the real timer period; for cap, binary blocks follow.
    Serial.print("ok gpt=");
    Serial.print(gpt_ch);
    Serial.print(" period_counts=");
    Serial.print(gpt_regs()->GTPR + 1);
    Serial.print(" pclkd_hz=");
    Serial.print(R_FSP_SystemClockHzGet(FSP_PRIV_CLOCK_PCLKD));
    Serial.print(" n_ch=");
    Serial.print(n_ch);
    Serial.print(" n_blocks=");
    Serial.print(n_blocks);
    Serial.print(" led=");
    Serial.println(led_ix >= 0 ? "on" : "off");
  } else if (strcmp(verb, "led") == 0) {
    char *arg = strtok(nullptr, "");
    if (arg && strcmp(arg, "off") == 0) {
      led_pin_x = led_pin_y = 0xFF;
    } else if (arg) {
      char *end;
      led_pin_x = uint8_t(strtoul(arg, &end, 10));
      led_pin_y = (*end == ',') ? uint8_t(strtoul(end + 1, nullptr, 10)) : 0xFF;
    }
    bind_led();
    if (led_ix >= 0) Serial.println("led on");
    else if (led_pin_x != 0xFF) Serial.println("led off: pair not sampled, or cap running (meter is mon-only)");
    else Serial.println("led off");
  } else if (strcmp(verb, "ledtest") == 0) {
    uint8_t c[3] = {0, 0, 0};
    for (uint8_t &v : c) {
      char *a = strtok(nullptr, " ");
      if (a) v = uint8_t(min(255UL, strtoul(a, nullptr, 10)));
    }
    if (!ws_ok) {
      Serial.println("err no_cyccnt");
      return;
    }
    led_pin_x = led_pin_y = 0xFF;
    bind_led();
    ws_show(c[0], c[1], c[2]);
    Serial.print("ledtest ");
    Serial.print(c[0]);
    Serial.print(' ');
    Serial.print(c[1]);
    Serial.print(' ');
    Serial.print(c[2]);
    Serial.println(" (meter off)");
  } else if (strcmp(verb, "wsprobe") == 0) {
    char *a = strtok(nullptr, " ");
    bool on = a && strcmp(a, "on") == 0;
    if (on) pinMode(PIN_D8, OUTPUT);
    ws_set = WS_BIT | (on ? PROBE_BIT : 0);
    ws_clr = ws_set << 16;
    if (!on) pinMode(PIN_D8, INPUT);
    Serial.println(on ? "wsprobe on D8" : "wsprobe off");
  } else if (strcmp(verb, "blast") == 0) {
    char *s = strtok(nullptr, " ");
    char *load = strtok(nullptr, " ");
    bool keep = load && strcmp(load, "load") == 0;
    if (!keep) stop_sampling();
    blast(s ? strtoul(s, nullptr, 10) : 5);
    if (!keep) boot_monitor();
  } else {
    Serial.println("err unknown command");
  }
}

}  // namespace

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);  // active low: off
  pinMode(PIN_RGB_EN, OUTPUT);
  digitalWrite(PIN_RGB_EN, HIGH);
  pinMode(RGB_BUILTIN, OUTPUT);
  digitalWrite(RGB_BUILTIN, LOW);
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  uint32_t c0 = DWT->CYCCNT;
  delayMicroseconds(20);
  ws_ok = DWT->CYCCNT - c0 > 500;  // ~960 cycles expected at 48 MHz
  Serial.begin(115200);
  boot_monitor();
}

void loop() {
  if (read_line()) handle(line);
  led_update();
  // One block per pass, so "stop" is read between blocks. A while here never got back to
  // read_line() at 4 ch / 48 kSa/s (80 % of USB): the ISR refilled the ring as fast as it drained.
  if (streaming && rd_block != wr_block) {
    Serial.write(block_ptr(rd_block), block_bytes);
    rd_block = uint8_t((rd_block + 1) % n_blocks);
  }
}
