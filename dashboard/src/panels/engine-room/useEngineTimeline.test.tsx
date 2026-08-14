// Unit coverage for the GSAP fx substrate itself (the EnclosureCanvas suite covers what the canvas
// RENDERS; this covers what the hook DOES to it). Mounted on a minimal SVG harness so the assertions
// are about the tween contract, not the canvas geometry.

import { cleanup, render } from "@testing-library/react";
import gsap from "gsap";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { EngineProcessNode } from "../../types/projection";
import { ENGINE_ROOM_SCENARIOS } from "./fixtures";
import { useEngineTimeline } from "./useEngineTimeline";

function nodeFrom(name: string): EngineProcessNode {
  const node = ENGINE_ROOM_SCENARIOS.find((entry) => entry.name === name)?.processes[0];
  if (!node) throw new Error(`no fixture node for ${name}`);
  return node;
}

// The verify-sweep frame (ledger-map running, memory worktree not yet on disk) — the projection state
// that puts the scan ring on the canvas.
const VERIFY = nodeFrom("engine-boot-memory-verify");

/** The scan ring exactly as EnclosureCanvas renders it: a stroked <circle r=6> tagged data-fx='scan'. */
function ScanHarness({ node = VERIFY }: { node?: EngineProcessNode }) {
  const ref = useRef<SVGSVGElement>(null);
  useEngineTimeline(ref, node);
  return (
    <svg ref={ref} data-testid="root">
      <circle data-fx="scan" data-testid="scan-ring" cx={100} cy={100} r={6} />
    </svg>
  );
}

const scanTweens = (ring: Element) => gsap.getTweensOf(ring);

beforeEach(() => {
  document.documentElement.removeAttribute("data-effects"); // effects ON — the context builds
});
afterEach(() => {
  cleanup(); // unmount → ctx.revert(), killing the repeating fx tweens
  document.documentElement.removeAttribute("data-effects");
});

describe("useEngineTimeline — scan-ring sweep (05o verify ring)", () => {
  it("expands the ring by a transform, not by per-frame r attribute writes (the C1 win)", () => {
    const { getByTestId } = render(<ScanHarness />);
    const tweens = scanTweens(getByTestId("scan-ring"));
    expect(tweens.length).toBe(1);
    // scale 1 → 52/6 reproduces the old r 6 → 52 expansion; `attr` would be the per-frame re-raster.
    expect(tweens[0].vars.scale).toBeCloseTo(52 / 6);
    expect(tweens[0].vars.attr).toBeUndefined();
  });

  it("declares the stroke non-scaling so the scale expands the ring instead of thickening it", () => {
    // Without vector-effect the uniform scale multiplies the 2u stroke to ≈17.3u (and the glow with
    // it): the ring renders as a growing blob, which is NOT the radius tween it replaced. The
    // attribute is the whole difference between "scaled circle" and "radius tween" for a stroked path.
    const { getByTestId } = render(<ScanHarness />);
    const ring = getByTestId("scan-ring");
    expect(ring.getAttribute("vector-effect")).toBe("non-scaling-stroke");
    expect(scanTweens(ring).length).toBe(1); // …and it is the SCALED ring that carries it
  });

  it("touches nothing under data-effects=off (no tween, no attribute — the honest-motion gate)", () => {
    document.documentElement.dataset.effects = "off";
    const { getByTestId } = render(<ScanHarness />);
    const ring = getByTestId("scan-ring");
    expect(scanTweens(ring).length).toBe(0);
    // no transform is applied, so the stroke is at its rendered width already — nothing to declare.
    expect(ring.getAttribute("vector-effect")).toBeNull();
  });
});
