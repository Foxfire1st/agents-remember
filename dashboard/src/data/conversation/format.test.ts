import { describe, expect, it } from "vitest";

import { ABSENT, freshnessTone, humanizeAge, humanizeDuration, joinChips, truncateMiddle } from "./format";

describe("conversation format conventions (developer findings A1/A4/A5)", () => {
  it("humanizes durations with fixed sensible precision (A4)", () => {
    expect(humanizeDuration(800)).toBe("800 ms");
    expect(humanizeDuration(45_000)).toBe("45 s");
    expect(humanizeDuration(192_000)).toBe("3 m 12 s");
    expect(humanizeDuration(3_600_000 * 2 + 60_000 * 5)).toBe("2 h 5 m");
    expect(humanizeDuration(86_400_000 * 6)).toBe("6 d 0 h");
  });

  it("never renders raw minutes or six-decimal seconds", () => {
    // The exact developer-cited eyesores: 8638.1m and 518288.173569s must humanize.
    expect(humanizeDuration(8638.1 * 60_000)).toMatch(/^\d+ d \d+ h$/);
    expect(humanizeAge(null)).toBe(ABSENT);
  });

  it("uses the em-dash for a genuinely absent value, not a chain (A1)", () => {
    expect(humanizeAge(undefined)).toBe(ABSENT);
    expect(humanizeAge("not-a-date")).toBe(ABSENT);
  });

  it("joins only present chips with one interpunct separator (A1/A2)", () => {
    expect(joinChips(["codex", null, "working", undefined, ""])).toBe("codex · working");
    expect(joinChips([])).toBe("");
  });

  it("degrades long-stale to a QUIET distinct tone, not an alarm (A4)", () => {
    expect(freshnessTone("fresh", 0)).toBe("fresh");
    expect(freshnessTone("stale", 5_000)).toBe("aging");
    expect(freshnessTone("stale", 86_400_000 * 6)).toBe("stale");
    expect(freshnessTone("unknown", null)).toBe("unknown");
  });

  it("truncates at a boundary keeping the distinguishing tail (A5)", () => {
    const truncated = truncateMiddle("a-very-long-master-title-model-effort-advertisement-suffix", 24);
    expect(truncated.length).toBeLessThanOrEqual(24);
    expect(truncated).toContain("…");
    expect(truncated.endsWith("suffix")).toBe(true);
    expect(truncateMiddle("short", 24)).toBe("short");
  });
});
