// The F6 region cycle (260715-FEUI-L1 S4, design §5.3): rail → stage → inspector → statusline,
// wrapping both ways, with collapsed panels dropping out of the cycle.
import { describe, expect, it } from "vitest";

import { FOCUS_REGIONS, nextRegion, regionTargetSelector } from "./focus";

describe("nextRegion", () => {
  it("cycles forward rail → stage → inspector → statusline → rail", () => {
    expect(nextRegion("rail", 1)).toBe("stage");
    expect(nextRegion("stage", 1)).toBe("inspector");
    expect(nextRegion("inspector", 1)).toBe("statusline");
    expect(nextRegion("statusline", 1)).toBe("rail");
  });

  it("cycles backward with Shift+F6", () => {
    expect(nextRegion("rail", -1)).toBe("statusline");
    expect(nextRegion("stage", -1)).toBe("rail");
  });

  it("starts from the edge when focus is outside every region", () => {
    expect(nextRegion(null, 1)).toBe("rail");
    expect(nextRegion(null, -1)).toBe("statusline");
  });

  it("skips collapsed panels (they leave the cycle)", () => {
    const noInspector = FOCUS_REGIONS.filter((region) => region !== "inspector");
    expect(nextRegion("stage", 1, noInspector)).toBe("statusline");
    const stageOnly = ["stage", "statusline"] as const;
    expect(nextRegion("statusline", 1, stageOnly)).toBe("stage");
  });

  it("recovers when the current region itself is unavailable", () => {
    expect(nextRegion("rail", 1, ["stage", "statusline"])).toBe("stage");
  });

  it("returns null when nothing is available", () => {
    expect(nextRegion("stage", 1, [])).toBeNull();
  });
});

describe("regionTargetSelector", () => {
  it("targets the region's primary focus target", () => {
    expect(regionTargetSelector("rail")).toBe('[data-region="rail"] [data-focus-target]');
  });
});
