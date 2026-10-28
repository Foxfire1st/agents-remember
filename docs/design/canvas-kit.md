# The pinned offline presentation kit

MIK-R104 rule 12 requires a generated page to have a named minimum kit of presentation libraries,
shipped with the product, pinned, licensed and loadable **with no network**. This is what shipped:
four entries under `mcp/src/agents_remember/package_data/canvas/kit/`, their licences read from the
files themselves, their dependencies' licences beside them, and every byte measured.

The kit is a set of files, not a framework. It adds no schema, no template and no required page
shape: a page names the files it wants with paths relative to its own revision, and a page that
names none is still a valid page. The machine-readable form of everything below is
`kit/manifest.json` (`ar-canvas-kit/v1`), which the tests hold to the bytes on disk.

## Entries

| Entry | Pinned version | Licence | Licence quotation (read from the shipped file) | Bytes | What it is for |
| --- | --- | --- | --- | ---: | --- |
| Shiki with `@shikijs/magic-move` | `4.5.0` | MIT | “Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction” | 725,730 | Offline syntax highlighting (javascript, typescript, json, bash, python; github-dark and github-light) and code-to-code morphing through the `<shiki-magic-move>` web component, plus its stylesheet |
| Motion Canvas | `3.17.2` | MIT | “Copyright (c) 2022 motion-canvas” | 189,896 | Keyframed vector scenes: `Player`, `PlaybackManager`, `Stage`, the 2D node tree (`Rect`, `Txt`, `Layout`, `Node`, `View2D`), the easing set, and a thin adapter (`scene`, `createKit`, `renderFrame`, `paint`) that renders a frame without a project file or the editor |
| OpenGenerativeUI (upstream name: OpenIntelligentUI) | `0.1.0-8391a94` | MIT | “Copyright (c) Atai Barkai” | 18,604 | The design system for generated panels: theme tokens (light and dark), SVG diagram classes, form-control styles, first-render stagger, and the generation skill text |
| ar-video vocabulary | `23fd71bf` | own material (no licence file; not third-party) | — (no third-party licence to quote) | 12,416 | The developer's video repo reduced to a dependency-free ESM module (`col()`, the easing/arithmetic set, the Canvas2D perspective camera `Proj` with fog, `orbit`, `orbitKeys`, `strokeSegs`, `curveSegs`, `spark`) and `VOCABULARY.md` |

Total, entries and dependency licence texts: **946,646 bytes**. With `manifest.json` (18,978 bytes),
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

## Licence readings

Each entry's licence was read from the file the package ships, not from its `package.json`
`license` field; the quotation in the table is a contiguous excerpt of that file, and a test fails
if it stops occurring in it. Reading the files also caught two things a field alone would hide:

- **`diff-match-patch-es@2.0.1` is Apache-2.0**, not MIT. Its `LICENSE` begins “Apache License /
  Version 2.0, January 2004”. It is inside the Shiki bundle (Magic Move's token diff), so its
  licence is shipped as `shiki/4.5.0/LICENSES/diff-match-patch-es.txt`. Apache-2.0 is permissive and
  passes on with attribution; the entry as a whole is therefore *MIT with one Apache-2.0
  dependency*, not “MIT only”.
- **`chroma-js@2.4.2` declares `(BSD-3-Clause AND Apache-2.0)`**. Its complete shipped
  `LICENSE` contains the three-clause BSD grant followed by the ColorBrewer attribution and
  Apache-2.0 notice for those colours. Both sections are retained in the carried dependency notice;
  reading only the opening BSD section would miss the second licence.

Every package whose code ends up inside a bundle has its licence beside it, derived from esbuild's
metafile rather than from the dependency list: 26 packages for Shiki, 3 for Motion Canvas
(`@motion-canvas/core`, `@motion-canvas/2d`, `chroma-js`), none for OpenGenerativeUI (it ships only
its own design-system module) and none for ar-video (no dependencies). OpenGenerativeUI and ar-video
carry their own `LICENSE` / provenance note at the entry root.

## How a page loads the kit

The kit lives in `package_data/canvas/kit/` and is addressed by a page **relative to its own
revision**, under the admitted prefix `kit/`:

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

A revision that uses the kit carries the kit files it names as admitted assets under the same
relative paths; nothing else changes. The consequences, all of them properties of the shipped
bytes rather than promises:

- **No network.** The Shiki bundle contains no `fetch`, `XMLHttpRequest`, `WebSocket`, beacon call
  or URL of any scheme. The OpenGenerativeUI module contains no `https://` and no `esm.sh`. The
  Motion Canvas bundle's only load is `fetch(this.source)` for a media node the page supplies, so a
  relative path stays inside the revision; its remaining absolute URLs are two documentation links
  in error prose and three comment/namespace URLs.
- **No wasm.** Shiki is bundled with the JavaScript regex engine, so there is no `onig.wasm` to
  fetch and no `WebAssembly` instantiation.
- **No build step at runtime.** The files are finished ES modules. The upstream TypeScript of
  OpenGenerativeUI and ar-video was compiled and tree-shaken at admission time; the build recipe for
  each entry is recorded in its manifest `build` field.
- **No import map.** The bundles are self-contained, so a page needs no `<script type="importmap">`.
  OpenGenerativeUI's upstream import map (three, gsap, d3, chart.js from `esm.sh`) is deliberately
  not part of the kit — see below.
- **Relative paths only.** The page's own `href`/`src` values are `kit/...` and `page.*`; the
  readiness check resolves them inside the revision, and a `../`, absolute or `//host` reference
  fails it.

## What is not shipped, and why

| Not shipped | Why |
| --- | --- |
| Remotion | Its licence restricts passing the software on to third parties, which is exactly what shipping it as a product asset would do. It is refused rather than vendored. |
| Shiki's oniguruma wasm engine | The JavaScript regex engine covers the shipped languages without a second binary asset and without a runtime instantiation the policy would have to allow. |
| `@shikijs/langs` and `@shikijs/themes` in full | 200+ grammars and 50+ themes are not a minimum kit. Five languages and two themes ship; adding one is a re-build of the same entry, recorded in the manifest. |
| Motion Canvas's editor, its Vite plugin and the `<motion-canvas-player>` project pipeline | A page cannot run a build. The engine and 2D node tree ship, with an adapter that builds the scene description and drives `Player`/`Stage` directly. |
| OpenGenerativeUI's `IMPORTMAP` / `IMPORTMAP_SCRIPT_TAG` | They map `three`, `gsap`, `d3` and `chart.js` to `esm.sh`, i.e. to the network. The vendored `index.ts` is upstream's file with those final 758 bytes removed; the manifest records the removal and the upstream digest. |
| ar-video's scenes, fonts, audio and rendered output | The rule names the engine, techniques and visual vocabulary, not the film's media. The film's fonts are OFL and its song is third-party; neither belongs in a code kit. |

## Verification

Static (no browser, no model):

```sh
cd mcp && TMPDIR=/tmp .venv/bin/python -m pytest tests/test_canvas_kit.py -q
```

Nine tests cover: the four named entries and the refusal of Remotion; byte and SHA-256 accounting for
every file with nothing unaccounted on disk; each entry's licence text containing the recorded
quotation; each bundled package having its licence; no network primitive or absolute load in any
bundle; each bundle carrying the symbols it claims; a page assembled from Shiki + ar-video passing
`agents_remember.canvas.readiness.check_page` with no network references and no template; a plain
page with no kit at all still passing; and the exact reference set inside the Motion Canvas bundle.

There is no separate opt-in browser witness any more. `scripts/canvas-kit-proof.mjs` ran on the
isolation proof's fixture server, and both went with the isolation proof (D568, removed in L201).
What checks a kit page in a browser now is the render path itself: the prospective browser load
opens the exact selected assets in Chromium before anything is published (see below).

## Known limits

- **Static readiness is not execution.** It scans markup/styles rather than JavaScript error
  strings. The prospective browser load checks the exact selected assets before publication,
  including references inserted into the rendered DOM. The Motion Canvas documentation strings
  are not subresource requests; its reference inventory remains pinned by `test_canvas_kit.py`.
- **The revision must carry what it uses.** `canvas.kit.kit_assets` verifies the pinned bytes and
  includes the selected entry's attribution, dependency licences and provenance. The existing
  `text/plain` type admits upstream `LICENSE` basenames unchanged. The host serves those files
  only through that revision's session prefix, with the same integrity checks as other assets.
- **No standalone runtime witness.** The model-free four-entry kit witness
  (`scripts/canvas-kit-proof.mjs`) was removed with the isolation proof set (D568). A kit page is
  exercised in a browser only by the prospective load of the page that uses it; nothing proves
  every feature of the libraries.
- **Rebuilding is manual.** The manifest records the exact esbuild command and entry for each
  bundle, but no npm dependency was added to the repo: the bundles were built once at admission
  from `npm pack`ed tarballs. A future re-pin is a re-run of the same commands, and the manifest
  digests make a silent substitution visible.
