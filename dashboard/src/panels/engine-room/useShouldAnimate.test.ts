import { afterEach, describe, expect, it, vi } from "vitest";

import { shouldAnimate } from "./useShouldAnimate";

function setReduce(matches: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

describe("shouldAnimate", () => {
  afterEach(() => {
    document.documentElement.removeAttribute("data-effects");
  });

  it("is false when data-effects=off, even if the OS allows motion", () => {
    setReduce(false);
    document.documentElement.dataset.effects = "off";
    expect(shouldAnimate()).toBe(false);
  });

  it("is false when prefers-reduced-motion is set, even if effects are on", () => {
    setReduce(true);
    document.documentElement.dataset.effects = "on";
    expect(shouldAnimate()).toBe(false);
  });

  it("is true only when effects are on and reduced-motion is off", () => {
    setReduce(false);
    document.documentElement.dataset.effects = "on";
    expect(shouldAnimate()).toBe(true);
  });
});
