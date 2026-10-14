import { describe, expect, it } from "vitest";

import { humanizeDuration } from "./format";

describe("format conventions (developer findings A1/A4)", () => {
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
    expect(humanizeDuration(518288.173569 * 1000)).toMatch(/^\d+ d \d+ h$/);
  });

  it("uses the em-dash for a genuinely absent value, not a chain (A1)", () => {
    expect(humanizeDuration(Number.NaN)).toBe("—");
    expect(humanizeDuration(-1)).toBe("—");
  });
});
