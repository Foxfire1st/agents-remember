# Explainer Canvas Starters

Minimal, self-contained HTML starters demonstrating each of the four pinned offline kit libraries in `package_data/canvas/kit/`.

## How kit references work

Every explainer page addresses kit files by **revision-root relative paths** under `kit/<library>/<version>/`:

| Library | Entry path | Companion asset |
| --- | --- | --- |
| Shiki + Magic Move | `kit/shiki/4.5.0/shiki-kit.js` | `kit/shiki/4.5.0/magic-move.css` |
| Motion Canvas | `kit/motion-canvas/3.17.2/motion-canvas-kit.js` | — |
| OpenGenerativeUI | `kit/open-generative-ui/0.1.0-8391a94/open-generative-ui-kit.js` | — |
| `ar-video` vocabulary | `kit/ar-video/23fd71bf/ar-video-vocabulary.js` | `kit/ar-video/23fd71bf/VOCABULARY.md` |

- In HTML attributes (`<link href="...">`, `<script src="...">`) and CSS (`@import`, `url(...)`), use `kit/<library>/<version>/<file>` from root-level files (or contained relative `../kit/<library>/<version>/<file>` from a nested file such as `sub/page.html` or `styles/page.css`; never use a leading `/` root-relative path).
- In JavaScript ES module imports, use `./kit/<library>/<version>/<file>` from a root-level script (or contained relative `../kit/<library>/<version>/<file>` from a nested script such as `sub/app.js`) so the browser resolves inside the revision root without an import map.
- References resolve relative to the referring file using standard POSIX path normalization (`canvas/kit_references.py` `resolve_reference`, called by `canvas/readiness.py`). A nested file such as `sub/app.js` may use contained parent segments (`../kit/<library>/<version>/<file>`) as long as the normalized path lands inside the revision root (`kit/<library>/<version>/<file>`). Any path that escapes above the revision root (for example `../../outside`), external `http://`, `https://`, or `//host` URLs, or `fetch()` is refused. The canvas frame runs with `connect-src 'none'` and an opaque sandbox origin.
- When `render()` publishes a draft, it carries every referenced kit file and its bundled licence notices automatically from the shipped kit.
- Modified copies placed inside `kit/` are refused by readiness (`canvas.kit_references.verify_kit_assets`), which verifies draft `kit/` payloads against the shipped bytes, manifest byte sizes, and SHA-256 digests in `kit/manifest.json`. If you need a customised copy, place it outside `kit/` (for example `styles/custom-morph.css`) and keep its attribution intact.

## Starters in this directory

| Folder | Technique (`data-technique`) | Kit library | What it demonstrates |
| --- | --- | --- | --- |
| `shiki-magic-move/index.html` | `code-morph` | Shiki 4.5.0 | Segmented Before / After switch (`←`/`→`), `<shiki-magic-move>` token transition, and still form showing both snippets, before above after |
| `motion-canvas-step-through/index.html` | `step-through` | Motion Canvas 3.17.2 | 4-step keyframed 2D canvas scene via `createKit` + `renderFrame` + `paint`, `kit.player.deactivate()` after every render, Back / Play / Pause / Next / scrubber / count, keyboard shortcuts |
| `open-generative-ui-trace/index.html` | `trace-a-path` (with `linked-highlight`) | OpenGenerativeUI 0.1.0-8391a94 | Inline SVG call chain using `THEME_CSS` and `SVG_CLASSES_CSS` (`.box`, `.node`, `.arr`, `.leader`, `text.th`/`.ts`/`.t`), `data-link` linked highlight across claims, diagram, and code, click-to-pin, `Escape` clears |
| `ar-video-measured/index.html` | `before-and-after` | `ar-video` 23fd71bf | Pure `Frame = f(t)` Canvas2D bar comparison using `col()`, `ease.outCubic`, `clamp`, `lerp`, `tween`, Linear / Log scale switch, Replay button, tabular still form |

To adapt a starter for an explainer page:

1. Copy the `<figure>` markup, styles, and interactive kit script into your draft under `drafts/assets/`.
2. Replace the synthetic code, steps, call chain, or measurements with material from the bound comparison and evidence packet.
3. Give every content section a unique `data-part="<slug>"` and `data-part-title="..."` (never `"page"`), keep a `data-technique="<slug>"` attribute and `id` on every `<figure>`, and attach `data-evidence-claim` only to claims from your revision's `claim-map.json`.

## Explainer pages and the dashboard's quiet switch

An explainer page is viewed apart from the dashboard: no reduced-motion or quiet-mode check applies to it, and the dashboard's reduced-motion setting and quiet (`effects`) switch do not apply to it. The host sends `ar-canvas-binding/v1` without an `effects` flag.

Follow these kit patterns for responsive, keyboard-accessible figures with complete markup still forms:

- **Shiki Magic Move:** place both Before and After snippets in the HTML still form before scripts run, and drive `<shiki-magic-move>` from a segmented switch (`←`/`→`). Never set `morph.renderer.animated = false`, which skips DOM updates.
- **Motion Canvas:** await `renderFrame(kit, frame)` then `kit.stage.render(kit.player.playback.currentScene, kit.player.playback.previousScene)` before calling `paint(kit, canvas)`, and call `kit.player.deactivate()` right after `createKit(...)` and after every render so `Player`'s idle loop stops when paused. Drive playback with a bounded timer that stops on the last step.
- **`ar-video` Canvas2D:** compose `Frame = f(t)` with Replay / scrubber controls and pair the canvas with a tabular still form in markup.

## Technique slugs (`data-technique`)

Use lowercase kebab-case slugs from real rows of `TECHNIQUES.md` on every `<figure>` (never invent new row names or use retired rows):

- Starter rows:
  - `code-morph` (`shiki-magic-move`; still form: both snippets, before above after)
  - `step-through` (`motion-canvas-step-through`)
  - `trace-a-path` (`open-generative-ui-trace`, paired with `linked-highlight`)
  - `before-and-after` (`ar-video-measured`)
- Other default rows in `TECHNIQUES.md`:
  - `linked-highlight`
  - `propagation`
  - `branch-taken`
  - `ladder-of-abstraction`
  - `table`
  - `code-excerpt`
  - `claim-and-basis-mark`
  - `moved-block`
  - `sandbox`
  - `stale-part`
  - `limits`
- Optional rows in `TECHNIQUES.md`: `scrollytelling`, `scrubbable-number`, `term-popover`, `repository-tree`, `reveal-order`.

## Accessibility and responsive baseline

- Provide a complete **still form** in HTML before scripts run (full prose, ordered step list, both code states or worked cases).
- Place an `aria-live="polite"` state line and a keyboard hint beneath every driven figure (`←`, `→`, `Space`, `Home`, `End` on step figures; `←`/`→` on segmented switches; `Escape` clears a pin).
- Use `data-link` for page-internal linked highlights — never reuse `data-part` or `data-evidence-claim`.
- Keep zero horizontal overflow down to `390px`, controls at least `32px` tall, and a visible `2px` amber (`current`) focus ring on `:focus-visible`.
