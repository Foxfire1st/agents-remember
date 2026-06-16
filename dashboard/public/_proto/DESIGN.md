# Engine Room · Pod Stage — DESIGN LANGUAGE

> Working design reference for the 5f Engine Room "pod stage". The **structural** prototype
> (`podstage-structural.html`) nails topology + sequences. This doc captures the **visual
> language** to push the design from "took the colors" → "looks like the podracer instrument".
> Source frames: `notes/raw/Inspirations/podracer-clip/core-frames/`.

The clip = Anakin's pod-racer cockpit/engine HUD: an **amber-phosphor instrument cluster** inside an
oval canopy. Two engines power up green; energy redistributes between them; a fault flares red.

---

## 1. What's already transferred (keep)

- **OKLCH palette as state** — amber=nominal, cyan=running/charge, mint=went-green/complete,
  alarm=fault, dormant=skeletal, muted/grid/ink. (`panda.config.ts`.)
- **CRT scanlines + vignette** background.
- **Idioms as behavior** — boot-fill rise, flow packet, fault flicker (cadence ≤3/s), went-green flash.
- **Two-world canvas topology** (Image #8) + the build-up / tear-down sequences. **Do not regress these.**

## 2. The gap — forms, not just colors (the work)

Everything below is **shape / form / instrumentation** that the frames have and the current prototype
does not. Each item: *what the frame shows* → *what it becomes in the Engine Room*.

### A. Instrument housing (the canopy)
Frames: every shot sits inside a dark **oval/rounded canopy** with a metallic rim, concentric inner
framing, and an inner amber wire-border. → The **pod stage** gets an outer canopy frame: a large
rounded/beveled vignette housing the two-world canvas, with a faint concentric inner rim. Not a flat
rectangle. Corner brackets + tick marks on the frame edges (HUD framing).

### B. Engine = wireframe schematic column  ← biggest miss
Frames (`Engines go green 1/3/5`, `2 distributing energy`): each engine is a **tall segmented column
drawn as a technical wireframe** — stacked cells, internal cross-linework, nested rectangles, a spine —
that **glows by state** (amber outline idle → cyan charge climbing the cells → mint flash → green-lock;
red flicker on fault). → Replace the plain `CGC`/`GrepAI` rounded boxes with a **reusable
`<EngineColumn>` schematic**: outline + 5–7 segmented cells + inner spine/ticks + a charge fill that
rises cell-by-cell. The *box* was a placeholder; the *column* is the identity.

### C. Radial gauges (diagnostics)
Frames (`left display`, `Error signal 1`): circular dials — **concentric rings, perimeter tick marks,
trapezoidal "petal" sectors, a glowing central core/readout, a top row of small rectangular indicator
lights**, intense bloom. → The **diagnostics panel** (right zone) should read as an instrument cluster:
- heartbeat / setup% / token-fuel as **radial gauges** (segmented arc + sweep), not plain text rows;
- a row of **indicator segments** (little lit rectangles) for phase/health flags;
- keep the text mirror for a11y (honest-motion §11) beneath/within each gauge.

### D. Segmented "petal" charge sectors
Frames: **trapezoidal amber segments arranged radially / in banks** around the engines (capacity bars).
→ Use for provider **boot-fill** (segments light in sequence = charge climbing) and as bezel accents
around each engine column, instead of a single solid fill rect.

### E. Coupler = instrument bar with readout
Frames: the central **horizontal bar binding the two engines carries a glowing digital readout**
(digits/glyphs) + directional chevrons. → The `Code===Mem` coupler becomes a short **instrument bar**:
thick bar + a small inset readout chip (e.g. contract id / commit short-hash) + faint chevrons. Today
it's just a thick line.

### F. Directional chevrons on conduits
Frames: energy channels show **stacked chevrons** marking flow direction (not just a moving dot).
→ Flow conduits (clone/integrate/push/pull/carryover) get **chevron tick-marks along the path** plus
the travelling packet, so direction reads even when paused/snapshotted.

### G. Glow / bloom
Frames: lit elements have strong **bloom/halo**. → Increase glow on active state (cyan/mint/alarm):
layered `drop-shadow` / soft outer stroke. Idle amber stays subtle; active elements bloom. Keep
compositor-only (opacity/filter), respect reduced-motion.

### H. Type / glyphs
Frames: angular, techy, **semi-stencil** glyphs; glowing **tabular digits** on readouts. → Keep the
mono house font, but: tabular-nums + slight letter-spacing on readouts/hashes; treat hashes & counts as
glowing digit readouts; uppercase + tracked labels (already partly done) for the HUD feel.

## 2.5 Animation keyframe series (numbered frames = sparse keyframes of motion)

Two frame sets are **ordered, representative keyframes** of an animation — **NOT** frame-rate captures.
The real motion is continuous and smoother between them; the numbered frames just pin start / mid / end.
They are the choreography reference for our two core idioms. **For finer flow** (easing, intermediate
states), use the fuller reference: `podracer-clip/` holds the source **video** + a **dense JPG extraction**
(`anakin-…NNNNN.jpg`). Read the on-screen timestamp on a sequence's first & last keyframe, then inspect the
dense JPGs in that range — do this when actually *tuning* an animation, not for the static design.

**Boot-fill / power-up** — `Engines go green from inactive 1→5`:
- **1** inactive — amber dim outline, dark cells.
- **2** ignition — a cyan-green band sparks across the **lower** cells.
- **3–4** climb — green fills the segmented column **bottom-up, cell by cell**; the amber **petal sectors**
  light progressively as power rises.
- **5** locked — fully green, brightest, all petals lit.
→ Drives §B `EngineColumn`: the energy gauge is subdivided into **slim cells/boxes**; the charge climbs
  **bottom-up through them, continuously** (slim cells read as a segmented-but-smooth fill, not 5 jumps);
  petals light progressively; then went-green flash → settle amber-nominal (AR mapping). ~2s.

**Energy redistribution** — `distributing energy from right to left 1→3`:
- **1** start — source engine **cyan** (charged), target engine **red** (depleted); central channel shows a
  **directional arrow + a glowing numeric readout** (the amount).
- **2** mid — channel active, energy band travelling, readout counting.
- **3** done — both engines amber/balanced with a cyan reserve at the base; a large directional arrow remains.
→ Drives §F conduit grammar: a flow = **channel + big directional arrow/chevrons + a numeric readout**
  (commits / bytes / %), with the **source/target node colour shifting** as it completes. Also the literal
  template for **fault → recovery** (red depleted → amber balanced).

**Implication:** the boot-fill gauge = **slim subdivided cells**, with the charge climbing **continuously**
through them (the keyframes are sparse samples, not literal steps); conduits carry a *readout + arrow*, not
just a dot. When tuning either, sample the dense frame range for the in-between motion.

## 3. Constraints (non-negotiable)

- **Stay in the AR palette + house style.** This is *deepening* the existing amber-phosphor language,
  not a re-theme (5f §13). Use the existing OKLCH tokens; no new hues.
- **Honest-motion law.** Every animation reads `data-effects` + `prefers-reduced-motion`; under freeze,
  render the end-state instantly. Compositor-only props. Flash ≤3/s.
- **Don't regress structure/sequences.** The two-world topology, beats, and PR-gated teardown logic from
  `podstage-structural.html` are correct — the redesign re-skins the *forms*, not the choreography.
- **SVG-first** so it scales + animates (draw-on, charge, chevrons). Text mirrors for a11y.

## 4. Proposed first implementation steps (on the new html)

1. **`EngineColumn` schematic** (B) — swap the four provider boxes for segmented wireframe columns with
   state-driven charge. Highest-impact single change.
2. **Canopy frame + corner brackets** (A) — wrap the stage.
3. **Coupler instrument bar + readout** (E) and **chevrons on conduits** (F).
4. **Radial diagnostics gauges + indicator lights** (C/D) — re-skin the right zone.
5. **Bloom pass** (G) + **type/readout polish** (H).

Verify each step against the frames with `playwright-cli` screenshots; auto-publish to Open Design.
