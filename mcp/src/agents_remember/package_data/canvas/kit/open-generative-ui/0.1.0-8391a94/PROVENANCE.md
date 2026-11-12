# Provenance

- Upstream: `https://github.com/CopilotKit/OpenGenerativeUI`, which now resolves to the repository
  named `OpenIntelligentUI` (same project, renamed). Commit
  `8391a94c1c3939c679e40ddd7bfc08a9ef690e70` (2026-10-13).
- Licence: MIT. `LICENSE` at the repository root reads “The MIT License / Copyright (c) Atai
  Barkai”. A test asserts the recorded quotation is still in the shipped file.
- Version identity: the repository publishes no npm package and no tags. The pin is therefore the
  commit, plus the `version` in `packages/design-system/package.json` (`0.1.0`, `"private": true`).
  The directory name `0.1.0-8391a94` carries both.
- Taken: `packages/design-system/src/index.ts` — the theme tokens, SVG classes, form styles,
  first-render stagger and the generation skill text. Compiled to one ES module by esbuild; the
  template strings are unchanged.
- Removed: the last 758 bytes of that file, the `IMPORTMAP` and `IMPORTMAP_SCRIPT_TAG` exports,
  which map `three`, `gsap`, `d3` and `chart.js` to `esm.sh`. A page that needs those libraries is
  not an offline page, so the kit does not ship the map. Upstream `index.ts` on disk at admission:
  18,276 bytes, sha256 `220d87b7b1a6d416b1a71e2b884205d0f05afe422b42ec4fd43e1baff97af607`.
- Dependencies: none. `packages/design-system/package.json` lists TypeScript and Vitest as dev
  dependencies only; the shipped module imports nothing.
