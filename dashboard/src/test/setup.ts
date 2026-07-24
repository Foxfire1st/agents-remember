// Vitest setup (jsdom environment). Shared test bootstrap: stub the browser APIs jsdom omits so
// component-render tests (React Aria widgets, the honest-motion gate) don't crash on missing
// globals. Per-test code may still override these (e.g. useShouldAnimate.test stubs matchMedia).

if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof globalThis.ResizeObserver;
}

// jsdom omits scrollIntoView; cmdk (the sessions command palette) calls it on the
// selected item. Inert stub — tests assert selection state, never scroll geometry.
if (typeof Element !== "undefined" && typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = () => {};
}

// SVG geometry APIs jsdom omits or stubs-to-throw — GSAP's DrawSVG / MotionPath (engine-room timeline)
// call getBBox / getTotalLength / getPointAtLength when the effects-on path builds the GSAP context (the
// EnclosureProcessMap GSAP-gate test). Assign inert stubs across the SVG prototype chain so the plugins
// construct without throwing; the values are never asserted (the tests check the gate, not tween geometry).
for (const ctor of [
  globalThis.SVGElement,
  (globalThis as { SVGGraphicsElement?: typeof SVGElement }).SVGGraphicsElement,
  (globalThis as { SVGGeometryElement?: typeof SVGElement }).SVGGeometryElement,
]) {
  const proto = ctor?.prototype as unknown as Record<string, unknown> | undefined;
  if (!proto) continue;
  proto.getBBox = () => ({ x: 0, y: 0, width: 100, height: 100 });
  proto.getTotalLength = () => 100;
  proto.getPointAtLength = () => ({ x: 0, y: 0 });
}

// jsdom's media elements don't implement playback (play()/pause() log "not implemented" and play()
// returns undefined); the hidden-layer visibility gates (to save CPU) drive both on backdrop
// videos. Inert stubs — tests assert the CALLS, never real playback state. play() resolves like a
// real browser so `play()?.catch(...)` chains behave.
if (typeof HTMLMediaElement !== "undefined") {
  HTMLMediaElement.prototype.play = function play() {
    return Promise.resolve();
  };
  HTMLMediaElement.prototype.pause = function pause() {};
}

export {};
