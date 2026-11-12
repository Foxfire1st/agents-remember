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

The kit lives in `package_data/canvas/kit/` and is addressed by a page **relative to its own
revision**, under the prefix `kit/`:

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
- **Relative paths only.** The page's own `href`/`src` values are `kit/...` and `page.*`; the
  readiness check resolves them inside the revision, and a `../`, absolute or `//host` reference
  fails it.

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

Static (no browser, no model). The command runs from the root of a development checkout;
[Development environment](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#development-environment)
creates its `mcp/.venv`:

```sh
mcp/.venv/bin/python -m pytest mcp/tests/test_canvas_kit.py -q
```

Nine tests cover: the four named entries and the refusal of Remotion; byte and SHA-256 accounting for
every file with nothing unaccounted on disk; each entry's license text containing the recorded
quotation; each bundled package having its license; no network primitive or absolute load in any
bundle; each bundle carrying the symbols it claims; a page assembled from Shiki + ar-video passing
`agents_remember.canvas.readiness.check_page` with no network references and no template; a plain
page with no kit at all still passing; and the exact reference set inside the Motion Canvas bundle.

No test or script of the repository runs the kit in a browser by itself. The render path checks a
kit page in a browser: the prospective browser load, the load of the draft that `render`, the
explainer's tool, runs before it publishes
([Readiness before a revision becomes ready](canvas-host.md#readiness-before-a-revision-becomes-ready)),
opens the exact selected assets in Chromium before anything is published (see below).

## Known limits

- **Static readiness is not execution.** It scans markup/styles rather than JavaScript error
  strings. The prospective browser load checks the exact selected assets before publication,
  including references inserted into the rendered DOM. The Motion Canvas documentation strings
  are not subresource requests; its reference inventory remains pinned by `test_canvas_kit.py`.
- **The revision must carry what it uses.** `canvas.kit.kit_assets` verifies the pinned bytes and
  includes the selected entry's attribution, dependency licenses and provenance. The existing
  `text/plain` type admits the `LICENSE` file names of the libraries unchanged. The canvas host serves those files
  only through that revision's session prefix, with the same integrity checks as other assets.
- **No standalone runtime witness.** No model-free witness exercises the four entries in a
  browser. A kit page is exercised in a browser only by the prospective load of the page that uses
  it; nothing proves every feature of the libraries.
- **Rebuilding is manual.** The manifest records the exact esbuild command and entry for each
  bundle, but the repository holds no npm dependency for them. Shiki and Motion Canvas are built
  from the npm packages of their pinned versions, OpenGenerativeUI and ar-video from their own
  repositories at the pinned commit (the manifest's `build` field names the sources). A re-pin is a
  re-run of the same commands on the new version or commit, and the manifest digests make a silent
  substitution visible.
