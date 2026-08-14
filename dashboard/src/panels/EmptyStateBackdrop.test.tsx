import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmptyStateBackdrop } from "./EmptyStateBackdrop";

// The honest-motion gate is OR'd: data-effects=off OR prefers-reduced-motion suppresses the backdrop.
// Drive prefers-reduced-motion explicitly via matchMedia (the setup default is matches:false), mirroring
// useShouldAnimate.test.ts's setReduce helper, and restore the global between tests.
const originalMatchMedia = window.matchMedia;
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

afterEach(() => {
  cleanup();
  document.documentElement.removeAttribute("data-effects");
  window.matchMedia = originalMatchMedia;
});

// 07b targeted: empty-state canvases get a faint, effects-gated boomerang backdrop; the message always
// shows, the video is absent under calm-cockpit / reduced-motion.
describe("EmptyStateBackdrop (07b)", () => {
  it("always renders the message children", () => {
    const { getByText } = render(
      <EmptyStateBackdrop src="/assets/sc2-adjutant-boomerang.mp4">No session</EmptyStateBackdrop>,
    );
    expect(getByText("No session")).not.toBeNull();
  });

  it("mounts the looping, muted, autoplaying boomerang <video> directly when effects are on", () => {
    const { getByTestId } = render(
      <EmptyStateBackdrop src="/assets/sc2-battlecruiser-boomerang.mp4">x</EmptyStateBackdrop>,
    );
    const backdrop = getByTestId("empty-backdrop");
    const video = backdrop.querySelector("video") as HTMLVideoElement | null;

    expect(video?.parentElement).toBe(backdrop);
    expect(backdrop.querySelector('[data-testid="empty-backdrop-zoom"]')).toBeNull();
    expect(video?.getAttribute("src")).toBe("/assets/sc2-battlecruiser-boomerang.mp4");
    expect(video?.hasAttribute("loop")).toBe(true);
    // muted is load-bearing: browsers block autoplay on a non-muted <video>. React 19 reflects `muted`
    // as a DOM property only (never an attribute), so assert the property — hasAttribute would be false.
    expect(video?.muted).toBe(true);
    expect(video?.hasAttribute("autoplay")).toBe(true);
    expect(video?.hasAttribute("playsinline")).toBe(true);
    expect(backdrop.getAttribute("aria-hidden")).toBe("true");
  });

  it("omits the backdrop under calm-cockpit (data-effects=off; message still shown)", () => {
    document.documentElement.dataset.effects = "off";
    const { queryByTestId, getByText } = render(
      <EmptyStateBackdrop src="/assets/sc2-adjutant-boomerang.mp4">Calm</EmptyStateBackdrop>,
    );
    expect(queryByTestId("empty-backdrop")).toBeNull();
    expect(getByText("Calm")).not.toBeNull();
  });

  it("omits the backdrop under prefers-reduced-motion alone (effects on; message still shown)", () => {
    setReduce(true);
    document.documentElement.dataset.effects = "on";
    const { queryByTestId, getByText } = render(
      <EmptyStateBackdrop src="/assets/sc2-battlecruiser-boomerang.mp4">Reduced</EmptyStateBackdrop>,
    );
    expect(queryByTestId("empty-backdrop")).toBeNull();
    expect(getByText("Reduced")).not.toBeNull();
  });
});

// ── The kept-mounted DetailPanel empty state kept decoding while display:none ────
// jsdom has no IntersectionObserver — a controllable mock drives the hide/show flips.
const observed = new Map<Element, IntersectionObserverCallback>();
class MockIntersectionObserver {
  private readonly callback: IntersectionObserverCallback;
  readonly root = null;
  readonly rootMargin = "0px";
  readonly thresholds = [0];
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
  }
  observe = (element: Element) => observed.set(element, this.callback);
  unobserve = (element: Element) => void observed.delete(element);
  disconnect = () => observed.clear();
  takeRecords = (): IntersectionObserverEntry[] => [];
}

function fireIntersection(element: Element, isIntersecting: boolean) {
  act(() => {
    observed.get(element)?.(
      [{ isIntersecting, target: element } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    );
  });
}

describe("EmptyStateBackdrop — video follows layer visibility (260721 C5)", () => {
  it("pauses the boomerang video while its layer is hidden and resumes on re-show", () => {
    observed.clear();
    (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = MockIntersectionObserver;
    const playSpy = vi.spyOn(HTMLMediaElement.prototype, "play");
    const pauseSpy = vi.spyOn(HTMLMediaElement.prototype, "pause");
    try {
      const { container } = render(
        <EmptyStateBackdrop src="/assets/sc2-battlecruiser-boomerang.mp4">x</EmptyStateBackdrop>,
      );
      const canvas = container.firstChild as Element; // the observed wrapper
      expect(observed.has(canvas)).toBe(true);
      expect(pauseSpy).not.toHaveBeenCalled(); // visible on mount → playing

      fireIntersection(canvas, false); // the cockpit layer flips to display:none
      expect(pauseSpy).toHaveBeenCalledTimes(1);

      fireIntersection(canvas, true); // re-shown → resume
      expect(playSpy.mock.calls.length).toBeGreaterThanOrEqual(2);
      expect(pauseSpy).toHaveBeenCalledTimes(1);
    } finally {
      playSpy.mockRestore();
      pauseSpy.mockRestore();
      observed.clear();
      delete (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver;
    }
  });
});
