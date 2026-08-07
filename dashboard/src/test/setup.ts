// Vitest setup (jsdom environment). Shared test bootstrap: stub the browser APIs jsdom omits so
// component-render tests (React Aria widgets, the honest-motion gate) don't crash on missing
// globals. Per-test code may still override these (e.g. useShouldAnimate.test stubs matchMedia).
import { afterEach } from "vitest";

// The setup-level unhandled-error trap (L8-R10): a green suite must never hide a live exception.
// Errors reported through the window, unhandled promise rejections, and console.error are all
// recorded and fail the owning test via afterEach — the canvas "Not implemented" crash that used
// to pass 1,500+ tests is caught here.
const unhandledErrors: unknown[] = [];
const recordUnhandled = (error: unknown) => {
  unhandledErrors.push(error);
};
process.on("unhandledRejection", recordUnhandled);
window.addEventListener("error", (event) =>
  recordUnhandled(event.error ?? event.message),
);
window.addEventListener("unhandledrejection", (event) =>
  recordUnhandled(event.reason),
);
const originalConsoleError = console.error;
console.error = (...args: unknown[]) => {
  // React's development-only act()/suspense warnings reach console.error but are not
  // application errors; failing on them would force test rewrites. Every other console.error
  // (jsdom's "Not implemented" canvas crash, real app errors) fails the owning test.
  const first = args[0];
  const isReactDevWarning =
    typeof first === "string" &&
    (first.startsWith("An update to %s inside a test was not wrapped in act") ||
      first.startsWith(
        "A component suspended while responding to synchronous input",
      ));
  if (!isReactDevWarning) recordUnhandled(first);
  originalConsoleError(...args);
};

afterEach(() => {
  const pending = unhandledErrors.splice(0);
  if (pending.length === 0) return;
  const detail = pending
    .map((error) =>
      error instanceof Error ? `${error.name}: ${error.message}` : String(error),
    )
    .join("\n");
  throw new Error(`Unhandled error(s) during test:\n${detail}`);
});

// jsdom implements getContext as a throwing "not implemented" stub; the Topology canvas mounts
// in passive effects, so the throw used to print and still pass. Give the test environment an
// inert 2d context (the canvas npm package is not a runtime dependency): every method is a
// no-op and properties are settable, exactly like the SVG/media stubs above.
if (typeof HTMLCanvasElement !== "undefined") {
  const inertContext = new Proxy(
    {},
    {
      get: () => () => {},
      set: () => true,
    },
  );
  const stubGetContext = (() =>
    inertContext) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = stubGetContext;
}

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

// CodeMirror's vim mode measures the cursor via Range.getClientRects(); jsdom's Range omits it,
// and the exception used to print while the test still passed. An empty rect list is the honest
// jsdom answer (no layout), and CodeMirror treats it as "no measurable position".
if (typeof Range !== "undefined" && typeof Range.prototype.getClientRects !== "function") {
  Range.prototype.getClientRects = (() => []) as unknown as typeof Range.prototype.getClientRects;
  Range.prototype.getBoundingClientRect = (() => ({
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
  })) as unknown as typeof Range.prototype.getBoundingClientRect;
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
