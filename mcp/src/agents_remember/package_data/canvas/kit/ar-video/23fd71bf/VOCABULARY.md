# The ar-video vocabulary

The engine, techniques and visual vocabulary of the developer's video repo
`ar-video` (commit `23fd71bf320b016c7d9baf99d55cb339f4efdd87`), reduced to what a
canvas page can copy. The repo has no dependencies: everything here is Canvas2D,
plain arithmetic and a palette. `ar-video-vocabulary.js` beside this file is the
compiled, dependency-free subset; this document is the rest of the vocabulary.

The repo itself is not a library. It is one film: a scene per beat window, each
frame a pure function of song time. What is worth copying is that discipline and
the handful of drawing techniques that make a diagram read as machinery rather
than as a slide.

## The rule the whole film is built on

**Frame = f(t).** Every frame is computed from a single time value, so the live
preview and the offline render are identical and any frame can be drawn alone.
There is no accumulated state between frames, no physics integration, no
"advance by dt": particles and bursts are closed-form functions of their age.
A page that adopts this rule can render one frame, seek, or export without a
player loop.

## Palette (from the AR dashboard tokens, as oklch)

`col(tone, alpha, dl)` returns a CSS colour; `tone` is one of the tokens below.
`dl` shifts lightness, so a hover/lit state needs no second token.

| Tone | oklch | Role |
| --- | --- | --- |
| `bg` | `0.16 0.02 250` | the map, deep blue-black |
| `panel` | `0.20 0.02 250` | panels, console slabs |
| `well` | `0.115 0.012 245` | terminal insets, fog base |
| `grid` | `0.30 0.02 250` | blueprint grid, map lines |
| `ink` | `0.92 0.03 90` | type, warm bone |
| `muted` | `0.70 0.02 250` | secondary labels |
| `amber` | `0.82 0.16 75` | nominal wireframe: code, structures |
| `cyan` | `0.85 0.13 200` | progress / running |
| `alarm` | `0.67 0.22 25` | bugs, violated invariants |
| `mint` | `0.88 0.16 165` | live / fresh, accepted memory |
| `gold` | `0.87 0.15 95` | families and guarantees (sparingly) |
| `purple` | `0.76 0.14 305` | memory repo / ledger (sparingly) |
| `dormant` | `0.67 0.06 25` | ruled out, inactive |

Two colour rules from the treatment: **only one of `alarm` / `mint` / `cyan`
dominates a frame**, and **only signal colours glow** — `ink` type stays crisp.

## Timing and easing

`ease` carries the curves the film uses: `linear`, `inCubic`, `outCubic`,
`inOutCubic`, `outExpo`, `inExpo`, `inOutExpo`, `outBack`, `spring`. Two rules
behind them:

- **Strong eases, no floaty motion.** Snaps use `outExpo` / `outBack`; a hold
  then a snap reads better than a constant drift.
- **Windows, not fades.** `window01(t, a, b, fade)` gives 1 inside `[a, b]` with
  eased ramps on both sides — the film's way of saying "this technique is live
  between these two times" without a state machine.

`prog`, `remap`, `lerp`, `clamp` and `smooth` are the arithmetic the rest uses.

## Canvas2D in 3D: the projection layer

`Proj` is a perspective camera over plain 2D canvas; there is no WebGL and no
depth buffer. This is what lets a page draw a repo tree on a laptop without a GPU
context, and keep it deterministic.

- `orbit(target, radius, azimuth, elevation, fov, roll)` places a camera on a
  sphere; `lerpCam3` and `orbitKeys(t, keys, ease)` interpolate **orbit
  parameters, not positions**, so a 180° swing arcs around the subject instead of
  cutting through it.
- `proj.p(point)` returns `{x, y, z, s}` or `null` behind the camera; `s` is the
  scale factor (`focal / z`) that sizes anything drawn at that point.
- `proj.fog(z)` is a linear alpha falloff clamped at 0.08 — depth reads as fog,
  not as darker paint.
- `proj.near` (default 260) drops points closer than that, so a ring segment
  passing the lens does not become a full-screen bar.
- `strokeSegs(ctx, proj, segs, color, width)` strokes many segments in one pass,
  **bucketed by fogged alpha** so thousands of hairlines stay fast.
- `curveSegs(a, c, b, n)` flattens a quadratic bezier into segments for the same
  pass; `spark(ctxs, proj, p, trail, color, size)` draws a head with a fading
  trail.

The logical frame is **1920 × 1080** (`W`, `H`); a page that renders at another
size scales the whole drawing by `width / 1920` rather than re-deriving the
camera maths.

## What the film draws with it

- **Repo as a 3D tree.** Radial wedge layout: each subtree gets an azimuth wedge
  proportional to its leaf count; height and radius grow with depth; files sit
  just beyond their directory; branches are quadratic curves that rise then reach
  out.
- **Memory as mirrored roots.** The memory tree is the code tree mirrored below
  the ground plane, same shape, same paths; a path thread is the vertical line
  from a leaf to its mirror. This is the film's central image and it is a
  layout rule, not an asset.
- **Two layers + bloom.** A main layer and a half-resolution emissive layer; the
  glow layer is blurred twice (6 px, 22 px) and added with `lighter`. `emit()`
  draws into both; `glowOnly()` only into the glow layer.
- **Finish.** Vignette, 4 px scanlines at 5 %, and overlay grain from six
  pre-baked tiles offset per frame. Grain is what destroys small encodes — a web
  variant drops grain and scanlines.
- **Dust and data walls.** A wrapped box of ~700 motes whose size and alpha come
  from depth makes every camera move visible; vertical planes carry pre-rendered
  textures of real commit hashes as affine billboards (three projected corners
  define `setTransform`, then one `drawImage` per plane).
- **Closed-form particles.** A burst's position is a function of its age
  (direction from a hash, drag `v·a·(1 − a/2L)`, optional gravity); shapes are
  sphere, ring, up. No state means any frame renders alone.
- **Type and HUD.** Title slam: scale 1.35 → 1 with `outExpo`, a ghost offset in
  the accent colour, a dark full-width band with hairlines. Console voice: mono
  readouts typed at 42 cps with two scrambled leading glyphs, last four lines,
  older lines dimmer. Chips are chamfered panels anchored to projected 3D points
  with leader lines; when labels would collide, assign fixed screen slots by
  screen-x order.
- **Cuts on the music.** Transitions are named moments (drop, whip, glitch,
  flash, portal, mosaic, slice) that ramp in 90 ms before and decay after; zoom
  blur is a progressive mean of six scaled copies; chroma split multiplies the
  frame into red and cyan copies recombined with `lighter`.

## Reduced motion

Every technique above is time-driven, so honouring `prefers-reduced-motion:
reduce` is a matter of choosing a static `t` (or the scene's establishing frame)
and skipping the transition ramps. This is also what the canvas readiness check
requires of any admitted page that declares animation.
