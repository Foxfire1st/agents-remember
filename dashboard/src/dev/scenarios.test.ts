import { describe, expect, it } from "vitest";

import { SCENARIOS } from "./scenarios";

describe("scenario player model (5i)", () => {
  // The build-up and the tear-down are TWO separate animations (as in the mockup), not one combined run.
  const buildUp = SCENARIOS.find((scenario) => scenario.name === "build-up");
  const tearDown = SCENARIOS.find((scenario) => scenario.name === "tear-down");

  it("authors the build-up timeline (B0 worktree_start → B5 idle constellation)", () => {
    expect(buildUp).toBeTruthy();
    // Four beats, not six: the two provider beats (dim engines, then the seed/clone charge) left
    // with the search providers, so the timeline is the official line → two checkouts → powered.
    expect(buildUp!.frames.length).toBe(4);
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
    // The recover runs the ledger-map beat, not a teleport to nominal — and no frame carries the
    // seed/clone conduits the providers took with them.
    const kinds = memoryBlock!.frames.flatMap((frame) =>
      frame.projection.analytics.engineProcesses.flatMap((node) => node.edges.map((edge) => edge.kind)),
    );
    expect(kinds).toContain("ledger-map");
    expect(kinds).not.toContain("cgc-seed");
    expect(kinds).not.toContain("grepai-clone");
    // one enclosure throughout — every frame drives the SAME worktree group (so the recover animates,
    // not remounts)
    const groups = new Set(
      memoryBlock!.frames.flatMap((frame) =>
        frame.projection.analytics.engineProcesses.map((node) => node.worktreeGroup),
      ),
    );
    expect(groups.size).toBe(1);
  });

  it("authors the T1B stale-base arc (preflight → fleeting block → fast-forward → clone), one enclosure", () => {
    const staleBase = SCENARIOS.find((scenario) => scenario.name === "stale-base");
    expect(staleBase).toBeTruthy();
    const captions = staleBase!.frames.map((frame) => frame.caption).join(" | ");
    expect(captions).toMatch(/preflight/i);
    expect(captions).toMatch(/block/i);
    expect(captions).toMatch(/fast-forward/i);
    // the block beat drives a blocked, pre-contract (fleeting) process whose base is behind upstream
    const blocked = staleBase!.frames
      .flatMap((frame) => frame.projection.analytics.engineProcesses)
      .find((node) => node.health === "blocked");
    expect(blocked).toBeTruthy();
    expect(blocked!.codeSource.behindSource ?? 0).toBeGreaterThan(0);
    expect(blocked!.missingFacts.some((fact) => /contract not yet written/i.test(fact))).toBe(true);
    // the recover adds the code worktree (its own beat), not a teleport to nominal, and no frame
    // carries a provider seed/clone conduit
    const kinds = staleBase!.frames.flatMap((frame) =>
      frame.projection.analytics.engineProcesses.flatMap((node) => node.edges.map((edge) => edge.kind)),
    );
    expect(kinds).toContain("worktree-add");
    expect(kinds).not.toContain("cgc-seed");
    expect(kinds).not.toContain("grepai-clone");
    // one enclosure throughout
    const groups = new Set(
      staleBase!.frames.flatMap((frame) =>
        frame.projection.analytics.engineProcesses.map((node) => node.worktreeGroup),
      ),
    );
    expect(groups.size).toBe(1);
  });

  it("authors the T12B live-sync arc (moved → memory gate → merge/ff), one enclosure, code lane keeps advancing", () => {
    const liveSync = SCENARIOS.find((scenario) => scenario.name === "live-sync");
    expect(liveSync).toBeTruthy();
    const captions = liveSync!.frames.map((frame) => frame.caption).join(" | ");
    expect(captions).toMatch(/moved/i);
    expect(captions).toMatch(/block/i);
    expect(captions).toMatch(/merge/i);
    const blocked = liveSync!.frames
      .flatMap((frame) => frame.projection.analytics.engineProcesses)
      .find((node) => node.health === "blocked");
    expect(blocked).toBeTruthy();
    expect(blocked!.edges.some((e) => e.kind === "ledger-map" && e.state === "blocked")).toBe(true);
    expect(blocked!.edges.some((e) => e.kind === "worktree-add" && e.state === "blocked")).toBe(false);
    expect(blocked!.memorySource?.behindSource ?? 0).toBeGreaterThan(0);
    expect(blocked!.memoryWorktree?.exists).toBe(true);
    // the recover is a ff/ref diff — the sync gate lifts and nothing else is added. No frame carries
    // a provider seed/clone conduit, because the surface no longer composes them.
    const kinds = liveSync!.frames.flatMap((frame) =>
      frame.projection.analytics.engineProcesses.flatMap((node) => node.edges.map((edge) => edge.kind)),
    );
    expect(kinds).not.toContain("cgc-seed");
    expect(kinds).not.toContain("grepai-clone");
    const last = liveSync!.frames.at(-1)!.projection.analytics.engineProcesses[0];
    expect(last.health).not.toBe("blocked");
    expect(last.memorySource?.behindSource ?? 0).toBe(0);
    const groups = new Set(
      liveSync!.frames.flatMap((frame) =>
        frame.projection.analytics.engineProcesses.map((node) => node.worktreeGroup),
      ),
    );
    expect(groups.size).toBe(1);
  });

  it("authors the T14C integration-conflict arc (replay → flash → terminal STOP), one enclosure, NO recover tail", () => {
    const conflict = SCENARIOS.find((scenario) => scenario.name === "integration-conflict");
    expect(conflict).toBeTruthy();
    const captions = conflict!.frames.map((frame) => frame.caption).join(" | ");
    expect(captions).toMatch(/closeout/i);
    expect(captions).toMatch(/replay/i);
    expect(captions).toMatch(/conflict/i);
    expect(captions).toMatch(/terminal/i);
    // the transient CONFLICT flash beat drives a `failed` integration return-lane (the refused-conduit flash)
    const flashFrame = conflict!.frames.find((frame) =>
      frame.projection.analytics.engineProcesses.some((node) =>
        node.edges.some((e) => (e.kind === "integration" || e.kind === "integration-mem") && e.state === "failed"),
      ),
    );
    expect(flashFrame).toBeTruthy();
    // TERMINAL — the scenario ENDS on the STOP (last frame is integration-blocked); no recover tail
    const last = conflict!.frames[conflict!.frames.length - 1];
    expect(last.projection.analytics.engineProcesses.some((node) => node.phase === "integration-blocked")).toBe(true);
    const kinds = conflict!.frames.flatMap((frame) =>
      frame.projection.analytics.engineProcesses.flatMap((node) => node.edges.map((edge) => edge.kind)),
    );
    expect(kinds).not.toContain("cgc-seed");
    expect(kinds).not.toContain("grepai-clone");
    // the CONFLICT flash RESOLVES INTO the steady STOP as a PROP DIFF (same boot-audio enclosure), not a
    // remount. The C0 idle lead-in reuses the boot-demo nominal fixture, so the prop-diff invariant is
    // asserted on the flash → STOP pair (the failure-arc frames), which share identity.
    const fIdx = conflict!.frames.indexOf(flashFrame!);
    const flashGroup = flashFrame!.projection.analytics.engineProcesses[0].worktreeGroup;
    expect(conflict!.frames[fIdx + 1].projection.analytics.engineProcesses[0].worktreeGroup).toBe(flashGroup);
  });

  it("authors the T18 abandon arc (idle → abandon → dissolve → gone), one boot-demo identity, terminal (no landing)", () => {
    const abandon = SCENARIOS.find((scenario) => scenario.name === "abandon");
    expect(abandon).toBeTruthy();
    expect(abandon!.frames).toHaveLength(4);
    const phases = abandon!.frames.map((frame) => frame.projection.analytics.engineProcesses[0].phase);
    expect(phases[0]).not.toBe("abandoned"); // X0 is at-rest nominal
    expect(phases.slice(1)).toEqual(["abandoned", "abandoned", "abandoned"]);
    // TERMINAL — never lands: no landing dock refs on any abandon frame
    for (const frame of abandon!.frames.slice(1)) {
      expect(frame.projection.analytics.engineProcesses[0].landing).toHaveLength(0);
    }
    expect(abandon!.frames[2].durMs).toBe(1300); // X2 holds the dissolve longest
    const ids = new Set(abandon!.frames.map((frame) => frame.projection.analytics.engineProcesses[0].id));
    expect(ids).toEqual(new Set(["boot-demo"])); // one continuous identity (a prop diff, not a remount)
  });

  it("folds the old gallery states in as single-frame resting scenarios (no coverage lost)", () => {
    const resting = SCENARIOS.find((scenario) => scenario.name === "engine-cleanup-pending");
    expect(resting?.frames).toHaveLength(1);
  });

  it("authors no provider arc and no provider edge kind (the retired surface)", () => {
    // The three provider arcs — the GrepAI seed fault, the CGC reindex reroute and the pre-contract
    // provider-plan block — existed only to animate engines materialising and seed/clone conduits.
    // Their names and their frames are gone, and no surviving scenario carries an edge kind the
    // server no longer composes (only worktree-add, ledger-map, sync and the integration lanes).
    for (const retired of ["seed-fault", "reindex-reroute", "provider-block"]) {
      expect(SCENARIOS.some((scenario) => scenario.name === retired), retired).toBe(false);
    }
    const SERVED_EDGE_KINDS = new Set([
      "worktree-add",
      "ledger-map",
      "sync",
      "integration",
      "integration-mem",
    ]);
    const seen = new Set(
      SCENARIOS.flatMap((scenario) =>
        scenario.frames.flatMap((frame) =>
          frame.projection.analytics.engineProcesses.flatMap((node) =>
            node.edges.map((edge) => edge.kind),
          ),
        ),
      ),
    );
    expect(seen.size).toBeGreaterThan(0);
    for (const kind of seen) {
      expect(
        SERVED_EDGE_KINDS.has(kind),
        `fixture edge kind '${kind}' is not in the served EngineProcessEdge vocabulary`,
      ).toBe(true);
    }
  });

  // These fixtures are the only thing that ever "produced" an engine-process edge on this side of
  // the wire, so an author can invent a state here that the reducer cannot emit — and then a
  // renderer branch gets written to match it and ships permanently dead. That is exactly how the
  // `refused` state and its `refusedPolarity` companion field got in. Pin the fixtures to the
  // vocabulary `observer/projection.py::EngineProcessEdge.state` documents.
  const SERVED_EDGE_STATES = new Set([
    "nominal",
    "running",
    "blocked",
    "failed",
    "stale",
    "skipped",
    "complete",
    "planned",
    "unknown",
  ]);

  it("never authors an engine edge state the reducer cannot emit", () => {
    const seen = new Set(
      SCENARIOS.flatMap((scenario) =>
        scenario.frames.flatMap((frame) =>
          frame.projection.analytics.engineProcesses.flatMap((node) =>
            node.edges.map((edge) => edge.state),
          ),
        ),
      ),
    );
    expect(seen.size).toBeGreaterThan(0);
    for (const state of seen) {
      expect(
        SERVED_EDGE_STATES.has(state),
        `fixture edge state '${state}' is not in the served EngineProcessEdge vocabulary`,
      ).toBe(true);
    }
  });
});
