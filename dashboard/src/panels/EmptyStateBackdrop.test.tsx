import { cleanup, render } from "@testing-library/react";
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
