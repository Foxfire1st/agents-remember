import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  servedAgeSeconds,
  stableEquals,
  stampServed,
  useNowMs,
  VOLATILE_AGE_FIELDS,
} from "./servedAges";

afterEach(() => {
  vi.useRealTimers();
});

describe("VOLATILE_AGE_FIELDS", () => {
  it("mirrors serving/delta.py exactly (keep the two sets in lockstep)", () => {
    expect([...VOLATILE_AGE_FIELDS].sort()).toEqual([
      "ageSeconds",
      "heartbeatAgeSeconds",
      "snapshotStaleSeconds",
      "staleSeconds",
      "waitSeconds",
    ]);
  });
});

describe("stableEquals", () => {
  it("treats nodes differing only in volatile ages as equal", () => {
    const a = { id: "L1", tokens: 3, staleSeconds: 1.5 };
    const b = { id: "L1", tokens: 3, staleSeconds: 99.5 };
    expect(stableEquals(a, b)).toBe(true);
  });

  it("ignores volatile ages nested in arrays and objects", () => {
    const a = { queue: [{ id: "q1", waitSeconds: 10 }], doc: { ageSeconds: 5, title: "t" } };
    const b = { queue: [{ id: "q1", waitSeconds: 600 }], doc: { ageSeconds: 900, title: "t" } };
    expect(stableEquals(a, b)).toBe(true);
  });

  it("treats a volatile field appearing or disappearing as equal (exclude_none wire form)", () => {
    // The server omits None fields, so a node can gain/lose `staleSeconds` between emissions
    // without any real change.
    expect(stableEquals({ id: "L1" }, { id: "L1", staleSeconds: 4 })).toBe(true);
  });

  it("detects real changes", () => {
    expect(stableEquals({ id: "L1", tokens: 3 }, { id: "L1", tokens: 4 })).toBe(false);
    expect(stableEquals({ items: [1, 2] }, { items: [1, 2, 3] })).toBe(false);
    expect(stableEquals({ a: 1 }, { a: 1, b: 2 })).toBe(false);
    expect(stableEquals({ gate: null }, { gate: { id: "g" } })).toBe(false);
  });

  it("handles primitives and null", () => {
    expect(stableEquals(null, null)).toBe(true);
    expect(stableEquals(null, {})).toBe(false);
    expect(stableEquals("x", "x")).toBe(true);
    expect(stableEquals(1, "1")).toBe(false);
  });
});

describe("servedAgeSeconds", () => {
  it("advances a stamped node's served age by the elapsed wall-clock", () => {
    const node = { id: "L1" };
    stampServed(node, 1_000_000);
    expect(servedAgeSeconds(node, 30, 1_045_000)).toBe(75); // 30 s served + 45 s elapsed
  });

  it("never advances backwards on clock skew", () => {
    const node = { id: "L1" };
    stampServed(node, 1_000_000);
    expect(servedAgeSeconds(node, 30, 999_000)).toBe(30);
  });

  it("serves an unstamped node's value as-is (fixtures, tests)", () => {
    expect(servedAgeSeconds({ id: "L1" }, 30, Date.now())).toBe(30);
  });

  it("never fabricates a missing served value", () => {
    const node = { id: "L1" };
    stampServed(node, 1_000_000);
    expect(servedAgeSeconds(node, undefined, 2_000_000)).toBeUndefined();
    expect(servedAgeSeconds(node, null, 2_000_000)).toBeUndefined();
  });

  it("handles a missing node (optional chains at call sites)", () => {
    expect(servedAgeSeconds(undefined, 30, Date.now())).toBe(30);
    expect(servedAgeSeconds(null, undefined, Date.now())).toBeUndefined();
  });
});

describe("useNowMs", () => {
  it("freezes while its kept-mounted layer is hidden and catches up once on re-show", () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000);
    const clock = renderHook(
      ({ active }: { active: boolean }) => useNowMs(10_000, active),
      { initialProps: { active: false } },
    );
    expect(clock.result.current).toBe(1_000);

    act(() => {
      vi.advanceTimersByTime(20_000);
    });
    expect(clock.result.current).toBe(1_000);

    clock.rerender({ active: true });
    expect(clock.result.current).toBe(21_000);
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(clock.result.current).toBe(31_000);

    clock.rerender({ active: false });
    act(() => {
      vi.advanceTimersByTime(20_000);
    });
    expect(clock.result.current).toBe(31_000);
  });
});
