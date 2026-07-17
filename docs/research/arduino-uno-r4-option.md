# Arduino UNO R4 WiFi as HYPEROSCI Slave?

**Date:** 2026-02-03

---

## Board Specs

**Arduino UNO R4 WiFi:**
- **Main MCU:** Renesas RA4M1 (Arm Cortex-M4 @ 48MHz)
- **RAM:** 32KB SRAM
- **Flash:** 256KB
- **DAC:** 12-bit, 2 channels (DA0, DA1) ✅
- **WiFi:** ESP32-S3 coprocessor ✅
- **Price:** ~€25-30

Note: Je noemde STM32U585, maar de UNO R4 heeft RA4M1. STM32U585 zit in andere boards (bv. B-U585I-IOT02A discovery kit).

---

## Kan osci-render lokaal draaien op UNO R4?

### Analyse

| Requirement | osci-render (full) | UNO R4 Capability | Verdict |
|-------------|-------------------|-------------------|---------|
| RAM | >100MB typical | 32KB | ❌ 3000x te weinig |
| CPU | 1+ GHz recommended | 48 MHz | ❌ 20x te langzaam |
| OS | Windows/macOS/Linux | Bare metal | ❌ Geen OS |
| Framework | JUCE (C++ desktop) | Arduino | ❌ Niet compatibel |
| Floating point | Extensive | Cortex-M4 has FPU ✓ | ⚠️ Limited |
| Lua scripting | LuaJIT | No | ❌ |

**Conclusie: Full osci-render = ONMOGELIJK op MCU**

---

## Wat KAN wel op UNO R4?

### Simple Shape Generator ✅

```cpp
// Dit werkt wel op UNO R4:
void generate_circle() {
    static float phase = 0;
    int x = (int)(cos(phase) * 2047 + 2048);  // 12-bit: 0-4095
    int y = (int)(sin(phase) * 2047 + 2048);
    
    analogWrite(DAC0, x);
    analogWrite(DAC1, y);
    
    phase += 0.01;
}
```

### Audio Visualization (Mic → Scope) ✅

```cpp
// Mic input → FFT → scope visualization
// Dit past in 32KB als je het simpel houdt
void audio_visualize() {
    int mic = analogRead(A0);
    
    // X = raw audio
    analogWrite(DAC0, mic);
    
    // Y = simple low-pass
    static int filtered = 0;
    filtered = (filtered * 7 + mic) / 8;
    analogWrite(DAC1, filtered);
}
```

### Wat NIET op UNO R4:

- ❌ SVG/OBJ file parsing
- ❌ Complex path tracing
- ❌ Lua scripting
- ❌ Multiple effects chain
- ❌ Text rendering
- ❌ Blender integration

---

## Vergelijking: UNO R4 vs ESP32-C3 + PCM5102A

| Aspect | Arduino UNO R4 WiFi | ESP32-C3 + PCM5102A |
|--------|---------------------|---------------------|
| **DAC Resolution** | 12-bit | 16-24 bit |
| **DAC Output** | Built-in ✅ | External module |
| **CPU Speed** | 48 MHz | 160 MHz |
| **RAM** | 32KB | 400KB |
| **WiFi** | ESP32-S3 coprocessor | Built-in |
| **Price** | €25-30 | €4 + €2 = €6 |
| **Battery (active)** | ~50-80mA (estimate) | ~130mA |
| **Audio streaming** | Possible but tight | Comfortable |
| **Local osci-render** | ❌ No | ❌ No |
| **Simple shapes** | ✅ Yes | ✅ Yes |

---

## Interessante Optie: Raspberry Pi Pico 2 W

Als je écht meer lokale processing wilt:

**Raspberry Pi Pico 2 W:**
- Dual-core Cortex-M33 @ 150MHz
- 520KB SRAM
- **WiFi built-in** (RP2350 + CYW43439)
- **PIO** = programmable I/O (kan custom protocols)
- Price: ~€8

Met externe DAC (PCM5102A) zou dit meer headroom geven voor lokale visualisaties.

---

## Hybrid Architectuur Idee

Wat als je BEIDE doet?

```
┌─────────────────────────────────────────────────────────────────┐
│                        SLAVE DEVICE                              │
│                                                                  │
│  ┌─────────────┐        ┌──────────────────────────────────────┐│
│  │  ESP32-C3   │        │      Arduino UNO R4 WiFi             ││
│  │             │  SPI/  │                                      ││
│  │ WiFi RX    ─┼────────┼─▶ RA4M1 CPU                         ││
│  │ from PC     │  UART  │    │                                ││
│  │             │        │    ├──▶ DAC0 ────────────────────────┼┼─▶ Scope X
│  └─────────────┘        │    ├──▶ DAC1 ────────────────────────┼┼─▶ Scope Y
│                         │    │                                ││
│  ┌─────────────┐        │    │   Local processing:           ││
│  │   MAX4466   │        │    │   - Simple shapes             ││
│  │     Mic    ─┼────────┼────┘   - Mic visualization         ││
│  └─────────────┘        │        - Fallback patterns         ││
│                         └──────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

**Maar... dit is overengineering.** 😅

---

## Mijn Aanbeveling

### Blijf bij ESP32-C3 + PCM5102A

Waarom:
1. **Je hebt de PCM5102A's al besteld** ✅
2. **ESP32-C3 heeft meer RAM** (400KB vs 32KB)
3. **Goedkoper** (€6 vs €25)
4. **Betere batterijduur** dan UNO R4
5. **Eén chip doet alles** (WiFi + processing + I2S)

### Als je later meer lokale power wilt:

Upgrade naar **Raspberry Pi Pico 2 W + PCM5102A**:
- Meer CPU (dual-core 150MHz)
- Meer RAM (520KB)
- Zelfde prijs als ESP32-C3
- Zelfde PCM5102A DAC modules

---

## Conclusie

| Vraag | Antwoord |
|-------|----------|
| Kan UNO R4 osci-render draaien? | ❌ Nee, veel te weinig resources |
| Kan UNO R4 simpele visualisaties? | ✅ Ja, maar beperkt |
| Is UNO R4 beter dan ESP32-C3 + PCM5102A? | ❌ Nee, duurder en minder RAM |
| Heeft UNO R4 12-bit DAC? | ✅ Ja, 2 kanalen ingebouwd |

**Aanbeveling: Ga door met ESP32-C3 + PCM5102A plan!**

De UNO R4 is een leuk board, maar voor dit project is ESP32-C3 + PCM5102A:
- Goedkoper
- Meer RAM
- Betere audio kwaliteit (16-24 bit vs 12 bit)
- Flexibeler
