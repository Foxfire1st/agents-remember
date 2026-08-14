import { act, cleanup, render } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { useElementVisible } from "./useElementVisible";

// Controllable IntersectionObserver: jsdom has none, and the gate defaults to visible without one.
// Each observed element's callback is captured so a test can fire hide/show flips by hand.
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

function installMockIO() {
  observed.clear();
  (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = MockIntersectionObserver;
}

function uninstallMockIO() {
  observed.clear();
  delete (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver;
}

function fireIntersection(element: Element, isIntersecting: boolean) {
  act(() => {
    observed.get(element)?.(
      [{ isIntersecting, target: element } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    );
  });
}

function Probe({ onState }: { onState: (visible: boolean) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const visible = useElementVisible(ref);
  onState(visible);
  return <div ref={ref} data-testid="probe" />;
}

afterEach(() => {
  cleanup();
  uninstallMockIO();
});

describe("useElementVisible (260721 hidden-layer gate)", () => {
  it("stays visible when IntersectionObserver is unavailable (the jsdom default — a no-op gate)", () => {
    const states: boolean[] = [];
    render(<Probe onState={(visible) => states.push(visible)} />);
    expect(states).toEqual([true]);
  });

  it("flips false on hide and true on re-show, and stops observing on unmount", () => {
    installMockIO();
    const states: boolean[] = [];
    const view = render(<Probe onState={(visible) => states.push(visible)} />);
    const element = view.getByTestId("probe");
    expect(observed.has(element)).toBe(true); // mounted → observed
    expect(states).toEqual([true]); // initial state until the observer reports

    fireIntersection(element, false);
    expect(states).toEqual([true, false]);
    fireIntersection(element, true);
    expect(states).toEqual([true, false, true]);

    view.unmount();
    expect(observed.size).toBe(0); // disconnected — no retained observers
  });
});
