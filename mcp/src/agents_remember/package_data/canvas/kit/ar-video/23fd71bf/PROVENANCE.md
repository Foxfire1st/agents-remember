# Provenance

- Upstream: the developer's own `ar-video` repository (private), commit
  `23fd71bf320b016c7d9baf99d55cb339f4efdd87`.
- Licence: none. The repository ships no `LICENSE` file and is not third-party material; it is
  reproduced here by its owner as part of this product's kit. There is no licence text to read or
  pass on, and none is claimed.
- Dependencies: none. `package.json` lists only build-time tools (TypeScript, Vite, Playwright);
  the engine imports nothing outside its own modules.
- Taken, unmodified in substance:
  - `src/engine/palette.ts` — the AR dashboard colour tokens and `col()`;
  - `src/engine/math.ts` — clamp/lerp/prog/remap/smooth, the `ease` set, `tween`, `window01`,
    small vector and rectangle helpers;
  - `src/engine/three.ts` — the Canvas2D perspective camera `Proj` (projection, fog, depth, near
    clip), `orbit`/`lerpCam3`/`orbitKeys`, `bez3`, `strokeSegs`, `curveSegs`, `screenCtx`, `spark`.
- Transformed: TypeScript compiled to one ES module and tree-shaken by esbuild (types erased,
  nothing else changed). `VOCABULARY.md` is written for this kit from the repo's `TECHNIQUES.md`
  and `TREATMENT.md`.
- Not taken: scenes, fonts, audio, rendered output, `data/`, `analysis/` and the render scripts.
  The repository itself is not modified by the kit.
