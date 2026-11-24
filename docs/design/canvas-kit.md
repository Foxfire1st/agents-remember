# The pinned offline presentation kit

A page that the [explainer](../concepts.md#explainer) publishes on the review page can use the
[presentation kit](../concepts.md#presentation-kit): a named minimum set of presentation libraries,
shipped with the product, pinned, licensed and loadable **with no network**. The kit has four
entries under `mcp/src/agents_remember/package_data/canvas/kit/`, their licenses read from the
files themselves, their dependencies' licenses beside them, and every byte measured.
[The canvas host](canvas-host.md) describes how a page is served.

The kit is a set of files, not a framework. It adds no schema, no template and no required page
shape: a page names the files it wants with paths relative to its own revision, and a page that
names none is still a valid page. The machine-readable form of everything below is
`kit/manifest.json` (`ar-canvas-kit/v1`), which the tests in `mcp/tests/test_canvas_kit.py`
([Verification](#verification)) hold to the bytes on disk.

## Entries

| Entry | Pinned version | License | License quotation (read from the shipped file) | Bytes | What it is for |
| --- | --- | --- | --- | ---: | --- |
| Shiki with `@shikijs/magic-move` | `4.5.0` | MIT | “Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction” | 725,730 | Offline syntax highlighting (javascript, typescript, json, bash, python; github-dark and github-light) and code-to-code morphing through the `<shiki-magic-move>` web component, plus its stylesheet |
| Motion Canvas | `3.17.2` | MIT | “Copyright (c) 2022 motion-canvas” | 189,896 | Keyframed vector scenes: `Player`, `PlaybackManager`, `Stage`, the 2D node tree (`Rect`, `Txt`, `Layout`, `Node`, `View2D`), the easing set, and a thin adapter (`scene`, `createKit`, `renderFrame`, `paint`) that renders a frame without a project file or the editor |
| OpenGenerativeUI (its repository's name: OpenIntelligentUI) | `0.1.0-8391a94` | MIT | “Copyright (c) Atai Barkai” | 18,604 | The design system for generated panels: theme tokens (light and dark), SVG diagram classes, form-control styles, first-render stagger, and the generation skill text (the export `OPEN_GEN_UI_DESIGN_SKILL`: design instructions for a model that writes a panel) |
| ar-video vocabulary | `23fd71bf` | own material (no license file; not third-party) | — (no third-party license to quote) | 12,416 | The project's own `ar-video` repository, a private repository whose code draws a film of the project frame by frame, reduced to a dependency-free ESM module (`col()`, the easing/arithmetic set, the Canvas2D perspective camera `Proj` with fog, `orbit`, `orbitKeys`, `strokeSegs`, `curveSegs`, `spark`) and `VOCABULARY.md` |

Total, entries and dependency license texts: **946,646 bytes**. With `manifest.json` (18,978 bytes),
the shipped directory is **965,624 bytes**.

Per-entry files:

| Path | Bytes |
| --- | ---: |
| `shiki/4.5.0/shiki-kit.js` | 684,647 |
| `shiki/4.5.0/magic-move.css` | 1,149 |
| `shiki/4.5.0/LICENSE` | 1,121 |
| `shiki/4.5.0/LICENSES/` (26 files, one per bundled package) | 38,813 |
| `motion-canvas/3.17.2/motion-canvas-kit.js` | 184,268 |
| `motion-canvas/3.17.2/LICENSE` | 1,070 |
| `motion-canvas/3.17.2/LICENSES/` (3 files) | 4,558 |
| `open-generative-ui/0.1.0-8391a94/open-generative-ui-kit.js` | 16,118 |
| `open-generative-ui/0.1.0-8391a94/LICENSE` | 1,067 |
| `open-generative-ui/0.1.0-8391a94/PROVENANCE.md` | 1,419 |
| `ar-video/23fd71bf/ar-video-vocabulary.js` | 4,354 |
| `ar-video/23fd71bf/VOCABULARY.md` | 6,747 |
| `ar-video/23fd71bf/PROVENANCE.md` | 1,315 |

## License readings

Each entry's license was read from the file the package ships, not from its `package.json`
`license` field; the quotation in the table is a contiguous excerpt of that file, and a test fails
if it stops occurring in it. The files show two things that a field alone hides:

- **`diff-match-patch-es@2.0.1` is Apache-2.0**, not MIT. Its `LICENSE` begins “Apache License /
  Version 2.0, January 2004”. It is inside the Shiki bundle (Magic Move's token diff), so its
  license is shipped as `shiki/4.5.0/LICENSES/diff-match-patch-es.txt`. Apache-2.0 is permissive and
  passes on with attribution; the entry as a whole is therefore *MIT with one Apache-2.0
  dependency*, not “MIT only”.
- **`chroma-js@2.4.2` declares `(BSD-3-Clause AND Apache-2.0)`**. Its complete shipped
  `LICENSE` contains the three-clause BSD grant followed by the ColorBrewer attribution and
  Apache-2.0 notice for those colours. Both sections are retained in the carried dependency notice;
  reading only the opening BSD section would miss the second license.

Every package whose code ends up inside a bundle has its license beside it, derived from esbuild's
metafile rather than from the dependency list: 26 packages for Shiki, 3 for Motion Canvas
(`@motion-canvas/core`, `@motion-canvas/2d`, `chroma-js`), none for OpenGenerativeUI (it ships only
its own design-system module) and none for ar-video (no dependencies). OpenGenerativeUI and ar-video
carry their own `LICENSE` / provenance note at the entry root.

## How a page loads the kit

The kit lives in `package_data/canvas/kit/` (copied into every explainer workspace as `kit/` for
inspection) and is used **by reference** from a page under relative paths whose POSIX normalization
from the referring file lands under `kit/<library>/<version>/...` at the revision root (for example
`./kit/...` from a root-level script or `../kit/...` from a nested file such as `sub/app.js`):

```html
<link rel="stylesheet" href="kit/shiki/4.5.0/magic-move.css">
<script type="module" src="page.js"></script>
```

```js
// page.js — every import resolves inside the same revision
import { createHighlighter } from './kit/shiki/4.5.0/shiki-kit.js'
import { col, Proj, orbit, strokeSegs, curveSegs, v3 } from './kit/ar-video/23fd71bf/ar-video-vocabulary.js'

const highlighter = await createHighlighter()
const morph = document.createElement('shiki-magic-move')
morph.highlighter = highlighter
morph.lang = 'javascript'
morph.theme = 'github-dark'
morph.code = 'function f(a) { return a + 1 }'
document.body.append(morph)
```

Reference discovery (`canvas/kit_references.py`) scans non-kit draft files relative to each
referrer:

- **HTML and SVG** (`.html`, `.htm`, `.svg`): `href`, `xlink:href`, `src`, `poster`, `srcset`,
  inline `style` attributes, inline `<style>` blocks and inline JavaScript `<script>` blocks.
- **CSS** (`.css`): `url(...)` and `@import`, ignoring comments.
- **JavaScript and ESM** (`.js`, `.mjs`): static `import`, `export ... from`, and string-literal
  dynamic `import(...)`, parsed with `tree-sitter-javascript` so imports inside comments or ordinary
  string literals are never matched.

When `render()` loads and publishes a draft, it resolves every reference that lands under
`kit/<library>/<version>/...` and **carries the referenced kit files together with their entry
attribution, dependency licences and companion files automatically** from the shipped kit. The
explainer does not copy kit files into `drafts/assets/kit/`: carried files are staged outside
`drafts/assets/` for publication and listed in `kitAssets` in `renders/run-NNN/report.json` and
in `render()`'s answer when it fits within `render()`'s 1,000-token reply bound (otherwise
`kitAssets` stays in `report.json` and the reply records a `moved` pointer with `part`, `to`,
and `total`; see [Render reply bound and overflow](canvas-host.md#render-reply-bound-and-overflow)).

If a draft already places files under `drafts/assets/kit/`, `canvas.kit_references.verify_kit_assets`
checks every one against the shipped kit's manifest byte length and SHA-256 digest. Unknown paths or
modified bytes under `kit/` fail readiness with guidance to put changed or custom copies outside
`kit/` rather than modifying a kit file in place.

The consequences of the shipped bundles, all of them properties of the bytes on disk rather than
promises:

- **No network.** The Shiki bundle contains no call of `fetch`, `XMLHttpRequest`, `WebSocket` or a
  beacon, and no URL of any scheme: the text `https?://` stands only inside the regular expressions
  of a grammar. The OpenGenerativeUI bundle contains no `https://` and no `esm.sh`. The
  Motion Canvas bundle's only load is `fetch(this.source)` for a media node the page supplies, so a
  relative path stays inside the revision; its remaining absolute URLs are two documentation links
  in error prose and three comment/namespace URLs.
- **No wasm.** Shiki is bundled with the JavaScript regex engine, so there is no `onig.wasm` to
  fetch and no `WebAssembly` instantiation.
- **No build step at runtime.** The files are finished ES modules. The TypeScript source of
  OpenGenerativeUI and ar-video, as their own repositories hold it at the pinned commit, is compiled
  and tree-shaken before a bundle enters the kit; the build
  recipe for each entry is recorded in its manifest `build` field.
- **No import map.** The bundles are self-contained, so a page needs no `<script type="importmap">`.
  The import map that the OpenGenerativeUI source exports (`IMPORTMAP`: three, gsap, d3 and chart.js
  from `esm.sh`) is deliberately not part of the kit — see below.
- **Contained relative paths only.** References resolve relative to the referring file using
  standard POSIX path normalization (`resolve_reference` in `canvas/kit_references.py`, called by
  `canvas/readiness.py`). A nested file such as `sub/app.js` may reference
  `../kit/<library>/<version>/...` as long as the normalized path lands inside the revision root
  (`kit/<library>/<version>/...`); any path that escapes above the revision root (for example
  `../../outside`), absolute URL, or `//host` reference fails readiness.

## Starters and exemplar

The product ships runnable kit examples alongside the kit itself and copies both folders into every
explainer workspace when the workspace is built or updated:

- **`package_data/canvas/starters/`** (copied into the workspace as `starters/`): `README.md` (kit
  reference rules, kit patterns, technique slugs, and accessibility baseline) plus four
  standalone passing starters, one for each kit library:
  - `shiki-magic-move/` (`code-morph`, Shiki `4.5.0`): Before/After segmented switch,
    `<shiki-magic-move>` token transition, and still form showing both snippets, before above after.
  - `motion-canvas-step-through/` (`step-through`, Motion Canvas `3.17.2`): four-step keyframed 2D
    canvas scene via `createKit`, `renderFrame`, and `paint`, deactivating `Player`'s idle loop
    after each draw, with Back/Play/Next, scrubber, keyboard shortcuts, and ordered step list.
  - `open-generative-ui-trace/` (`trace-a-path` with `linked-highlight`, OpenGenerativeUI
    `0.1.0-8391a94`): inline SVG call chain styled with `THEME_CSS` and `SVG_CLASSES_CSS`,
    `data-link` linked highlight across diagram, claims, and code, click-to-pin, and `Escape` to
    clear.
  - `ar-video-measured/` (`before-and-after`, `ar-video` `23fd71bf`): pure `Frame = f(t)` Canvas2D
    bar comparison using `col()`, `ease.outCubic`, `clamp`, `lerp`, and `tween`, with a Linear/Log
    scale switch, Replay button, and tabular still form.
- **`package_data/canvas/exemplar/`** (copied into the workspace as `exemplar/`): a complete
  synthetic reference page ("Retry jitter cap in a job queue", `index.html`, `exemplar.css`,
  `exemplar.js`) combining all four kit libraries with `code-excerpt` and `sandbox` (six techniques
  in total: `code-excerpt`, `code-morph`, `step-through`, `trace-a-path`, `sandbox`, and
  `before-and-after`). The page explicitly marks itself as a made-up change demonstrating
  techniques, layout, still forms, keyboard controls, and narrow-viewport stacking — never recorded
  evidence.

## What is not shipped, and why

| Not shipped | Why |
| --- | --- |
| Remotion | Its license, as read on 2026-10-07, is free for individuals, for organizations of up to three people and for non-profit organizations, and asks every other organization for a paid company license. Shipping Remotion as a product asset would pass that condition on to every user. It is refused rather than vendored. |
| Shiki's Oniguruma engine (the regular-expression library Oniguruma, compiled to WebAssembly as `onig.wasm`) | The JavaScript regex engine covers the shipped languages without a second binary asset and without a runtime instantiation the policy would have to allow. |
| `@shikijs/langs` and `@shikijs/themes` in full | 200+ grammars and 50+ themes are not a minimum kit. Five languages and two themes ship; adding one is a re-build of the same entry, recorded in the manifest. |
| Motion Canvas's editor, its Vite plugin and the `<motion-canvas-player>` project pipeline | A page cannot run a build. The engine and 2D node tree ship, with an adapter that builds the scene description and drives `Player`/`Stage` directly. |
| OpenGenerativeUI's `IMPORTMAP` / `IMPORTMAP_SCRIPT_TAG` | They map `three`, `gsap`, `d3` and `chart.js` to `esm.sh`, i.e. to the network. The vendored `index.ts` is the file of the OpenGenerativeUI repository with those final 758 bytes removed; the manifest records the removal, and the entry's `PROVENANCE.md` records the digest of the original file. |
| ar-video's scenes, fonts, audio and rendered output | The kit takes the engine, techniques and visual vocabulary, not the film's media. The film's fonts are under the SIL Open Font License (OFL) and its song is a third-party recording; neither belongs in a code kit. |

## Verification

The commands run from the root of a development checkout;
[Development environment](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#development-environment)
creates its `mcp/.venv`:

```sh
mcp/.venv/bin/python -m pytest mcp/tests/test_canvas_kit.py mcp/tests/test_canvas_kit_references.py mcp/tests/test_canvas_starters_exemplar.py -q
```

- **Static kit and reference tests** (`mcp/tests/test_canvas_kit.py`,
  `mcp/tests/test_canvas_kit_references.py`): cover the four named entries and the refusal of
  Remotion; byte and SHA-256 accounting for every file with nothing unaccounted on disk; each
  entry's license text containing the recorded quotation; each bundled package having its license;
  no network primitive or absolute load in any bundle; each bundle carrying the symbols it claims;
  HTML/SVG, CSS, and tree-sitter JS/MJS reference extraction and resolution; automatic carrying of
  referenced kit files and their notices; refusal of unknown or modified files under `kit/`; a plain
  page with no kit still passing; and the exact reference set inside the Motion Canvas bundle.
- **Browser runtime tests** (`mcp/tests/test_canvas_starters_exemplar.py`): run each of the four
  starters and the exemplar through automatic kit carrying, static readiness, the real Chromium
  prospective load
  ([Readiness before a revision becomes ready](canvas-host.md#readiness-before-a-revision-becomes-ready)),
  and revision inventory reporting.

## Known limits

- **Static readiness is not execution.** It parses markup, styles, and JavaScript import statements
  rather than executing scripts. The prospective browser load runs the exact selected assets in
  Chromium before publication, checking runtime errors, foreign requests, rendered evidence
  attributes, and visible rendered paint. The Motion Canvas documentation strings are not
  subresource requests; its reference inventory remains pinned by `test_canvas_kit.py`.
- **Kit files under `kit/` are immutable.** `render()` carries referenced kit files automatically
  with `canvas.kit.kit_assets`, including each selected entry's attribution, dependency licenses,
  and companion files, and `canvas.kit_references.verify_kit_assets` refuses any modified copy
  inside `kit/`. The existing `text/plain` type admits the `LICENSE` file names of the libraries
  unchanged, and the canvas host serves those files only through that revision's session prefix with
  the same integrity checks as other assets.
- **Rebuilding is manual.** The manifest records the exact esbuild command and entry for each
  bundle, but the repository holds no npm dependency for them. Shiki and Motion Canvas are built
  from the npm packages of their pinned versions, OpenGenerativeUI and ar-video from their own
  repositories at the pinned commit (the manifest's `build` field names the sources). A re-pin is a
  re-run of the same commands on the new version or commit, and the manifest digests make a silent
  substitution visible.
