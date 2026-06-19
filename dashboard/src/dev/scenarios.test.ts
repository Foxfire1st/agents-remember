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

  it("folds the old gallery states in as single-frame resting scenarios (no coverage lost)", () => {
    const resting = SCENARIOS.find((scenario) => scenario.name === "engine-cleanup-pending");
    expect(resting?.frames).toHaveLength(1);
  });
});
