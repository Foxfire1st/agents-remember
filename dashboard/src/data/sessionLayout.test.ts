// Narrow-width + 80-col-floor decisions (260715-FEUI-L1 S2 R3): downward crossings collapse,
// upward crossings expand, and a user's manual choice below the threshold is never fought.
import { describe, expect, it } from "vitest";

import {
  autoCollapseTransition,
  hasPersistedPanelLayout,
  INSPECTOR_AUTO_COLLAPSE_PX,
  ptyFloorPx,
  RAIL_AUTO_COLLAPSE_PX,
  RAIL_FALLBACK_PERCENT,
  railDefaultPercent,
  stageBelowPtyFloor,
} from "./sessionLayout";

describe("autoCollapseTransition", () => {
  it("collapses on a downward crossing and expands on the way back up", () => {
    expect(autoCollapseTransition(1200, 1050, INSPECTOR_AUTO_COLLAPSE_PX)).toBe("collapse");
    expect(autoCollapseTransition(1050, 1200, INSPECTOR_AUTO_COLLAPSE_PX)).toBe("expand");
    expect(autoCollapseTransition(950, 850, RAIL_AUTO_COLLAPSE_PX)).toBe("collapse");
  });

  it("is quiet while staying on one side — a manual reopen below the threshold is respected", () => {
    expect(autoCollapseTransition(850, 820, RAIL_AUTO_COLLAPSE_PX)).toBeNull();
    expect(autoCollapseTransition(1000, 950, RAIL_AUTO_COLLAPSE_PX)).toBeNull();
  });

  it("collapses on the first measure only when already below", () => {
    expect(autoCollapseTransition(null, 800, RAIL_AUTO_COLLAPSE_PX)).toBe("collapse");
    expect(autoCollapseTransition(null, 1400, RAIL_AUTO_COLLAPSE_PX)).toBeNull();
  });
});

describe("the ~80-col PTY floor", () => {
  it("flags a stage narrower than the floor and never a hidden (0-width) one", () => {
    expect(stageBelowPtyFloor(ptyFloorPx() - 1)).toBe(true);
    expect(stageBelowPtyFloor(ptyFloorPx())).toBe(false);
    expect(stageBelowPtyFloor(0)).toBe(false);
  });
});

describe("the ~280px rail default (review round 2, finding 4)", () => {
  it("converts the pixel target to the width-relative percentage", () => {
    expect(railDefaultPercent(1280)).toBeCloseTo(21.875, 3);
    expect(railDefaultPercent(1920)).toBeCloseTo(14.583, 2);
    expect(railDefaultPercent(900)).toBeCloseTo(31.111, 2);
  });

  it("clamps to the rail panel's min/max percentages", () => {
    expect(railDefaultPercent(2560)).toBe(12); // 10.9% raw → min-clamped
    expect(railDefaultPercent(400)).toBe(40); // 70% raw → max-clamped
  });

  it("falls back to the 1280px-reference percentage while unmeasured", () => {
    expect(railDefaultPercent(0)).toBe(RAIL_FALLBACK_PERCENT);
    expect(railDefaultPercent(-5)).toBe(RAIL_FALLBACK_PERCENT);
  });
});

describe("hasPersistedPanelLayout", () => {
  it("reads the library's own key format and survives a throwing storage", () => {
    const items = new Map<string, string>([["react-resizable-panels:saved-id", "{}"]]);
    const storage = { getItem: (key: string) => items.get(key) ?? null };
    expect(hasPersistedPanelLayout("saved-id", storage)).toBe(true);
    expect(hasPersistedPanelLayout("fresh-id", storage)).toBe(false);
    const throwing = {
      getItem: () => {
        throw new Error("denied");
      },
    };
    expect(hasPersistedPanelLayout("saved-id", throwing)).toBe(false);
  });
});
