# Findings: text + effects rendering — port/wrap osci-render vs. reimplement

*Investigated 2026-07-18. Question: we want text in a chosen font plus basic effects
(pulsing etc.) on the scopes, keeping the UNO-Q a headless box driven by the web
dashboard. osci-render (jameshball) is already built natively for aarch64 on the board
(`~/osci-render`, free build, v2.8.10.8). Is it realistic to port/wrap it, or better to
base our own implementation on it?*

**TL;DR recommendation: reimplement the small subset we need (option B) as a new
`kind="text"` source inside `hype_controller.py`, using its algorithms as the reference.
The part osci-render actually solves for text is surprisingly small (~a few hundred
lines of math); everything around it is JUCE/GUI machinery with no headless entry
point. Estimated effort for text + pulse/rotate/wobble: 1–2 sessions.**

---

## 1. How osci-render does text (what we'd be porting)

The pipeline is short and fully understood:

1. **Text → outline path.** `Source/parser/txt/TextParser.cpp:71-73`: JUCE
   `GlyphArrangement::addFittedText(font, text, …)` + `createPath()` — i.e. it asks the
   system font engine for the **glyph outlines** (bezier contours) of the string. No
   bundled stroke font, no freetype of its own; whatever named system typeface JUCE
   resolves. The premium build only adds bold/italic/markdown formatting — plain text
   works in the free build.
2. **Path → shape list.** `SvgParser::pathToShapes` (`Source/parser/svg/SvgParser.cpp:32`,
   shared with SVG import): walks the path iterator into a flat
   `vector<osci::Shape>` of Lines / Quadratic / Cubic beziers, then normalises to
   [-1,1] around the bbox centre.
3. **Shapes → samples.** `ShapeVoice::renderNextBlock`
   (`Source/audio/synth/ShapeVoice.cpp:238-458`): **constant arc-length traversal** —
   per sample, advance along the concatenated shapes by
   `totalLength × frequency / sampleRate`, so the whole figure is drawn once per
   waveform period at uniform beam speed (uniform brightness). Each shape's
   `nextVector(progress)` is a trivial lerp/bezier eval → `osci::Point(x, y, …)` → XY
   channels. That's our PCM.
4. **Effects = per-sample point transforms.** ~30 effects, each an
   `apply(index, point, …) -> point` function walked over the buffer
   (`osci_render_core/effect/osci_SimpleEffect.h`). Example, the entire Scale effect:
   `return input * Point(values[0], values[1], values[2])`.
5. **Pulsing is a first-class freebie there:** every effect parameter has a built-in
   per-parameter **LFO** (Sine/Square/Triangle/Saw/Noise, rate + range,
   `osci_render_core/effect/osci_EffectParameter.h:518`). "Pulsing logo" in osci-render
   is literally Scale with a sine LFO on scaleX/scaleY — no code.

Note: the real engine lives in a git submodule, **`modules/osci_render_core`**
(shapes, point math, all effects, LFO engine — clean, GUI-free, deps only
`juce_core/audio_processors/dsp`). The `Source/` tree is the JUCE plugin/GUI shell.

## 2. Option A — wrap/port osci-render headlessly

Three sub-options, all assessed against "headless box, web-controlled, feed the
existing HYPE PCM stream":

| Route | Verdict |
|---|---|
| **A1. Run the built GUI standalone under xvfb, capture audio** | ✗ Dead end. There is **no CLI/headless entry point** anywhere — the only standalone entry is a windowed `JUCEApplication` (`Source/standalone/CustomStandalone.cpp:71`). Text is set through the GUI file-open machinery. No programmatic control surface for our dashboard, plus a JACK/ALSA loopback capture chain and xvfb running at showtime. |
| **A2. Host the aarch64 VST3 headlessly** (e.g. a small JUCE host, or Python `pedalboard`) | ~ Possible but fiddly. Effect params are real automatable `AudioProcessorParameter`s and the processor runs without its editor — but **text/file input is NOT a parameter**; it lives in `.osci` project state XML driven by the app's file machinery. We'd be scripting project-state XML injection into a plugin host, then capturing its output into the stream loop. Two new moving parts (host + state hack) for one feature. |
| **A3. Link `osci_render_core` + a trimmed copy of the `ShapeVoice` loop into our own small C++ binary** | ~ The most honest "port". The core module is genuinely clean (~2-3k LOC, no GUI). But: the sample loop we need is in `Source/`, welded to the 2000-line `OscirenderAudioProcessor`; text→path still needs JUCE's font engine (so we link JUCE anyway); and we'd be running a C++ side-service next to the Python controller with an IPC seam into the 5 ms pacing loop. |

Licensing is **not** a blocker for any of these: osci-render is GPLv3 and HYPEROSCI is
already GPLv3, so lifting code is legally clean (TRADEMARKS.md only forbids using the
osci-render name/logos — fine).

**Why A still loses:** every route drags in JUCE + a second process for what our
controller-side investigation shows is a ~200-line addition to `hype_controller.py`.
None gives the dashboard a natural "type text here" control without extra glue.

## 3. Option B — reimplement the subset in the controller (recommended)

The controller side is *ideally* shaped for this:

- The controller has exactly **one audio source**, `PatternGen`
  (`src/unoq-controller/tools/hype_controller.py:93-131`), and the entire source
  contract is `block(n, params) -> bytes` — 240 frames of interleaved int16 XY every
  5 ms, pulled at `hype_controller.py:646`. Patterns are phase-continuous across blocks
  via one `self.phase` accumulator. A text source is just another `kind` branch.
- The dashboard already has the exact seam: `POST /api/pattern`
  (`hype_controller.py:541-555`) with a whitelisted `kind` + clamped params, and the
  `#kindseg` buttons in the embedded page. "Text" = new kind + a text input + a couple
  of sliders (pulse rate/depth, rotate speed).
- This was the plan all along: PLAN.md:65/94 and README.md:11 name "SVG path playback /
  lightweight custom renderer" as the accepted route, with full osci-render integration
  explicitly not required for the show. A text renderer **is** SVG-path playback
  specialised to font glyphs.
- STATUS.md already concluded the built osci-render is "GUI/OpenGL only … no headless
  'just stream the audio' mode" — this investigation confirms that and closes the open
  question.

### Design sketch (mirrors osci-render's algorithm, ~150-250 lines)

**On text change (once, off the hot path, outside `state.lock`):**
1. Text → vector path. Two font routes, both `apt`-installable on the board:
   - **Hershey stroke fonts** (`hershey-fonts-data` pkg): classic single-stroke
     engraving/oscilloscope fonts. Native polylines — no outline tracing, brightest
     cleanest trace, retro lab look. Trivial parser (the .jhf format is ASCII).
   - **Any TTF/OTF via `python3-freetype`**: extract glyph outlines exactly like
     osci-render does (freetype `get_glyph`/decompose → lines + beziers). The beam then
     traces letter *outlines* (hollow letters) — that's what osci-render text looks
     like too. This is the "certain font" route: drop any .ttf in a fonts dir on the
     board; DejaVu etc. are already present.
2. Flatten beziers → polyline, then **resample to constant arc-length** at N points
   where `N = SAMPLE_RATE / draw_freq` (e.g. 48000/60 Hz = 800 points), normalised to
   [-1,1] like osci-render's `pathToShapes`. Store as a pre-scaled int16-ready array
   (`array('h')` or numpy).
3. Pen-up jumps between glyphs/contours: order contours nearest-neighbour and jump
   instantly (one sample) — at scope speeds the connecting line is invisible;
   osci-render does the same (beam just moves).

**Per block (hot path, must stay ≲ the current trig loop's cost):** copy the next 240
points from the precomputed table (wrapping), applying cheap per-block/per-sample
modulation:
- **Pulse** = amplitude LFO: `amp_eff = amp * (1 - depth/2 + depth/2 * sin(2π·rate·t))`,
  evaluated per block (5 ms resolution is plenty for a 0.5–4 Hz pulse) — multiply
  during the copy loop, exactly where `amp` is applied today.
- **Rotate** = per-block 2×2 rotation of the copied points (or per-sample if we want
  smooth fast spins; still just 4 mul + 2 add per sample).
- **Wobble/breathe** etc. are the same shape: tiny point transforms, add as taste demands.
Pure-Python cost is comparable to the existing per-sample trig loop (table lookup +
2-3 mults vs. 2 `sin()` calls), so the 5 ms budget holds; `python3-numpy` is available
in apt as a vectorisation escape hatch if a fancier per-sample effect ever gets heavy.
(Board measured: Python 3.13.5, 4 cores, ~3 GB free RAM, DejaVu/URW fonts installed;
numpy/freetype not yet installed but both in the Debian 13 archive.)

**No protocol/firmware changes at all** — slaves just play whatever XY PCM arrives.
All 4 scopes show the same text (same fan-out as patterns today); per-scope different
text would need per-slave streams, a controller-only change, out of scope for now.

### What we consciously give up vs. osci-render
- The 30-effect catalogue, Lua scripting, 3D/perspective, image/GIF/OBJ parsers,
  MIDI — none requested.
- Its LFO engine generality — we hard-wire 2-3 effect params with sine LFOs, which is
  what "pulsing" needs.
- If appetite grows later, A3 (link `osci_render_core` into a small C++ renderer
  service) remains the upgrade path, and our GPLv3 licensing keeps it open.

## 4. Effort & phasing

| Phase | Work | Estimate |
|---|---|---|
| B1 | Hershey route: `.jhf` parser + arc-length resampler + `kind="text"` in `PatternGen` + `/api/pattern` fields + dashboard text input | one session |
| B2 | Pulse (amp LFO) + rotate params + dashboard sliders | small, same session |
| B3 | TTF outline route via freetype-py (font picker listing `/usr/share/fonts` + a project fonts dir) | one session |
| B4 (optional) | More effects (wobble, trace-in/out reveal), per-slave text | as desired |

Suggested order: B1+B2 first — Hershey is the fastest to working *and* honestly the
best-looking on a scope; add B3 when a specific brand font matters.

## 5. Verdict

Porting/wrapping osci-render is **not realistic as a headless, web-driven component**
— not because of the build (we've proven it builds on aarch64) but because it has no
headless control surface: text input is GUI/project-state only, and every workaround
(xvfb, VST host + state injection, core extraction) costs more than reimplementing the
~200 lines of actual math it uses for text. **Base our implementation on it instead**:
copy its proven algorithm (glyph path → normalised shapes → constant arc-length
traversal → per-sample point-transform effects with LFO'd parameters) into
`PatternGen`, where a source is a 30-line `block()` branch and the dashboard seam
already exists. GPLv3-compatible either way.
