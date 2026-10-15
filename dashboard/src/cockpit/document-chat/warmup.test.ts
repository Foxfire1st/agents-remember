import { describe, expect, it, vi, afterEach } from "vitest";
import { readWarmupStatus } from "./warmup";

afterEach(() => { vi.unstubAllGlobals(); });

describe("the host-start warmup status", () => {
  it("reads the one line the dashboard shows while the loading runs", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      running: true, total: 35, finished: 12,
      line: "loading agents after the host's start: 12 of 35",
      pending: ["a13"], stoppedForMemory: false,
    }))));
    const status = await readWarmupStatus();
    expect(status?.line).toBe("loading agents after the host's start: 12 of 35");
    expect(status?.pending).toEqual(["a13"]);
  });

  it("reads per-agent states with reasons and the in-flight list", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      running: true, total: 2, finished: 0,
      line: "loading agents after the host's start: 0 of 2",
      pending: [], inFlight: ["a1"],
      agents: {
        a1: { state: "loading", reason: null },
        a2: { state: "failed", reason: "the host flaked" },
        bogus: { state: 42 },
      },
      stoppedForMemory: false,
    }))));
    const status = await readWarmupStatus();
    expect(status?.inFlight).toEqual(["a1"]);
    expect(status?.agents).toEqual({
      a1: { state: "loading", reason: null },
      a2: { state: "failed", reason: "the host flaked" },
    });
    expect(status?.stoppedForMemory).toBe(false);
  });

  it("is null when the loading is done and the line goes", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      running: false, line: null, pending: [], stoppedForMemory: false,
    }))));
    const status = await readWarmupStatus();
    expect(status?.line).toBeNull();
    expect(status?.pending).toEqual([]);
  });

  it("is null when the backend does not answer", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("away"); }));
    await expect(readWarmupStatus()).resolves.toBeNull();
  });
});
