# ESP32-C3 vs ESP32-S3 Comparison for HYPEROSCI

## Power Consumption

| Mode | ESP32-C3 | ESP32-S3 |
|------|----------|----------|
| Active (WiFi TX) | ~130-150 mA | ~310-355 mA |
| Active (WiFi RX) | ~95-100 mA | ~100-120 mA |
| Active (CPU only) | ~35-40 mA | ~40-50 mA |
| Light Sleep | ~0.13 mA | ~0.24 mA |
| Deep Sleep | ~5 µA | ~7 µA |
| **Modem Sleep (WiFi connected, idle)** | ~20-25 mA | ~30-40 mA |

**Battery Life Estimate (1000mAh LiPo, continuous audio streaming):**
- ESP32-C3: ~8-10 hours
- ESP32-S3: ~5-7 hours

**Winner: ESP32-C3** 🏆 (roughly 40-50% better battery life)

---

## I2S Capabilities

| Feature | ESP32-C3 | ESP32-S3 |
|---------|----------|----------|
| I2S Peripherals | 1 | 2 |
| Max bit depth | 32-bit | 32-bit |
| Max sample rate | 160 kHz | 480 kHz |
| DMA support | Yes | Yes |
| TDM mode | No | Yes |
| PDM mode | RX only | TX + RX |
| LCD mode | No | Yes |

**For oscilloscope audio (48kHz stereo):**
- Both are MORE than capable
- C3's single I2S is sufficient
- No practical difference for this use case

**Winner: Tie** 🤝 (both work fine for 48kHz stereo I2S to PCM5102A)

---

## Other Specs

| Feature | ESP32-C3 | ESP32-S3 |
|---------|----------|----------|
| CPU | Single-core RISC-V 160MHz | Dual-core Xtensa 240MHz |
| RAM | 400KB | 512KB |
| Flash (typical) | 4MB | 8MB |
| WiFi | 2.4GHz only | 2.4GHz only |
| Bluetooth | BLE 5.0 | BLE 5.0 |
| USB | No native | USB OTG |
| ADC | 2× 12-bit, 6 channels | 2× 12-bit, 20 channels |
| Price | ~€2-3 | ~€4-5 |

---

## Conclusion for HYPEROSCI

### ESP32-C3 CAN work ✅

The C3's I2S peripheral is fully capable of:
- 48kHz stereo output to PCM5102A
- DMA-based streaming (low CPU overhead)
- Receiving WiFi while outputting I2S

**Potential issues with C3:**
- Single core = WiFi + audio processing on same core
- Might need careful interrupt prioritization
- Should still work with proper DMA buffering

### Recommendation

**Use ESP32-C3** if:
- Battery life is critical ⭐
- You already have them
- Budget is tight

**Use ESP32-S3** if:
- You need more processing headroom
- Want USB for easier debugging
- Battery life is less critical

---

## My Recommendation: Stick with ESP32-C3 🔋

For your use case:
1. Battery life matters for portable performance ✅
2. Audio requirements (48kHz stereo) are modest ✅
3. You already have the C3 boards ✅
4. Single I2S peripheral is sufficient ✅

**Just add PCM5102A module to your existing C3 setup!**

```
ESP32-C3 SuperMini
       │
       ├── GPIO2 ──▶ BCK (I2S bit clock)
       ├── GPIO3 ──▶ LCK (I2S word select)  
       ├── GPIO4 ──▶ DIN (I2S data)
       │
       └── GPIO1 ──▶ ADC (MAX4466 mic input)
```

The firmware just needs to be rewritten to use I2S instead of Sigma-Delta DAC.
