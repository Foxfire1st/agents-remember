import { describe, expect, it } from "vitest";

import { SCENARIOS } from "./scenarios";

describe("scenario player model (5i)", () => {
  // The build-up and the tear-down are TWO separate animations (as in the mockup), not one combined run.
  const buildUp = SCENARIOS.find((scenario) => scenario.name === "build-up");
  const tearDown = SCENARIOS.find((scenario) => scenario.name === "tear-down");

  it("authors the build-up timeline (B0 worktree_start → B5 idle constellation)", () => {
    expect(buildUp).toBeTruthy();
    expect(buildUp!.frames.length).toBe(6);
    expect(buildUp!.frames[0].caption).toMatch(/worktree_start/i);
    expect(buildUp!.frames.at(-1)!.caption).toMatch(/idle constellation/i);
  });

  it("authors the tear-down timeline (D0 idle → D6 stack removed), driving the H4 de-materialise", () => {
    expect(tearDown).toBeTruthy();
    expect(tearDown!.frames.length).toBeGreaterThanOrEqual(6);
    expect(tearDown!.frames[0].caption).toMatch(/idle/i);
    expect(tearDown!.frames.at(-1)!.caption).toMatch(/removed|stack/i);
    const phases = tearDown!.frames.flatMap((frame) =>
      frame.projection.analytics.engineProcesses.map((node) => node.phase),
    );
    expect(phases).toContain("cleanup-pending"); // the de-materialise beat
  });

  it("every frame carries a full, valid WorkspaceProjection + a caption", () => {
    for (const scenario of SCENARIOS) {
      expect(scenario.frames.length).toBeGreaterThan(0);
      for (const frame of scenario.frames) {
        expect(frame.projection.version).toBe(2);
        expect(Array.isArray(frame.projection.analytics.engineProcesses)).toBe(true);
        expect(typeof frame.caption).toBe("string");
      }
    }
  });

  it("authors the T3B memory-block arc (verify → block → reconcile → clone), one enclosure throughout", () => {
    const memoryBlock = SCENARIOS.find((scenario) => scenario.name === "memory-block");
    expect(memoryBlock).toBeTruthy();
    const captions = memoryBlock!.frames.map((frame) => frame.caption).join(" | ");
    expect(captions).toMatch(/verif/i); // verify / verifies / verifying
    expect(captions).toMatch(/block/i);
    expect(captions).toMatch(/reconcile/i);
    // a blocked beat must actually drive a blocked engine process (not just a caption)
    const healths = memoryBlock!.frames.flatMap((frame) =>
      frame.projection.analytics.engineProcesses.map((node) => node.health),
    );
    expect(healths).toContain("blocked");
    // the recover must run the provider seed/clone beats (the cross-stage copy arrows) — not teleport to
    // nominal. At least one frame drives a running cgc-seed / grepai-clone edge (matches the mockup M5/M6).
    const hasCloneBeat = memoryBlock!.frames.some((frame) =>
      frame.projection.analytics.engineProcesses.some((node) =>
        node.edges.some((e) => (e.kind === "cgc-seed" || e.kind === "grepai-clone") && e.state === "running"),
      ),
    );
    expect(hasCloneBeat).toBe(true);
    // one enclosure throughout — every frame drives the SAME worktree group (so the recover animates,
    // not remounts)
    const groups = new Set(
      memoryBlock!.frames.flatMap((frame) =>
        frame.projection.analytics.engineProcesses.map((node) => node.worktreeGroup),
      ),
    );
    expect(groups.size).toBe(1);
  });

  it("folds the old gallery states in as single-frame resting scenarios (no coverage lost)", () => {
    const resting = SCENARIOS.find((scenario) => scenario.name === "engine-cleanup-pending");
    expect(resting?.frames).toHaveLength(1);
  });
});
