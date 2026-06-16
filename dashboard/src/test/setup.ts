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

export {};
