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
    // the recover runs the provider clone beats (copy-arrows), not a teleport to nominal
    const hasClone = staleBase!.frames.some((frame) =>
      frame.projection.analytics.engineProcesses.some((node) =>
        node.edges.some((e) => (e.kind === "cgc-seed" || e.kind === "grepai-clone") && e.state === "running"),
      ),
    );
    expect(hasClone).toBe(true);
    // one enclosure throughout
    const groups = new Set(
      staleBase!.frames.flatMap((frame) =>
        frame.projection.analytics.engineProcesses.map((node) => node.worktreeGroup),
      ),
    );
    expect(groups.size).toBe(1);
  });

  it("authors the T9B seed-fault arc (charge → red fault → re-seed → nominal), one enclosure, never teleporting", () => {
    const seedFault = SCENARIOS.find((scenario) => scenario.name === "seed-fault");
    expect(seedFault).toBeTruthy();
    const captions = seedFault!.frames.map((frame) => frame.caption).join(" | ");
    expect(captions).toMatch(/fault/i);
    expect(captions).toMatch(/retry|re-seed/i);
    // the FAULT beat drives a failed health + a failed grepai-clone edge (the red flash) + the GrepAI memory
    // engine down (the red flicker), with CGC (code) NOT down (unaffected).
    const faultFrame = seedFault!.frames.find((frame) =>
      frame.projection.analytics.engineProcesses.some((node) => node.health === "failed"),
    );
    expect(faultFrame).toBeTruthy();
    const faultNode = faultFrame!.projection.analytics.engineProcesses.find((n) => n.health === "failed")!;
    expect(faultNode.edges.some((e) => e.kind === "grepai-clone" && e.state === "failed")).toBe(true);
    expect(faultNode.providers.find((p) => p.role === "memory")?.runtimeState).toBe("down");
    expect(faultNode.providers.find((p) => p.role === "code")?.runtimeState).not.toBe("down");
    // the recover re-seeds (grepai-clone running again) AFTER the fault — not a teleport.
    const faultIdx = seedFault!.frames.indexOf(faultFrame!);
    const reseed = seedFault!.frames.slice(faultIdx + 1).some((frame) =>
      frame.projection.analytics.engineProcesses.some((node) =>
        node.edges.some((e) => e.kind === "grepai-clone" && e.state === "running"),
      ),
    );
    expect(reseed).toBe(true);
    const groups = new Set(
      seedFault!.frames.flatMap((frame) =>
        frame.projection.analytics.engineProcesses.map((node) => node.worktreeGroup),
      ),
    );
    expect(groups.size).toBe(1);
  });

  it("authors the T9C reindex-reroute arc (refused → reindex → nominal), one enclosure, soft (never blocked)", () => {
    const reroute = SCENARIOS.find((scenario) => scenario.name === "reindex-reroute");
    expect(reroute).toBeTruthy();
    const captions = reroute!.frames.map((frame) => frame.caption).join(" | ");
    expect(captions).toMatch(/refused/i);
    expect(captions).toMatch(/reindex/i);
    // the refuse beat drives a `refused` cgc-seed edge with AMBER polarity + a seedFallback (reindexing) engine
    const refusedFrame = reroute!.frames.find((frame) =>
      frame.projection.analytics.engineProcesses.some((node) =>
        node.edges.some((e) => e.kind === "cgc-seed" && e.state === "refused"),
      ),
    );
    expect(refusedFrame).toBeTruthy();
    const refusedNode = refusedFrame!.projection.analytics.engineProcesses.find((node) =>
      node.edges.some((e) => e.kind === "cgc-seed" && e.state === "refused"),
    )!;
    expect(refusedNode.edges.find((e) => e.kind === "cgc-seed")!.refusedPolarity).toBe("amber");
    expect(refusedNode.seedFallback).toBe(true);
    // soft reroute: never a hard block on any frame
    const healths = reroute!.frames.flatMap((frame) =>
      frame.projection.analytics.engineProcesses.map((node) => node.health),
    );
    expect(healths).not.toContain("blocked");
    // the refuse → reindex-settle recover is a PROP DIFF (same enclosure), not a remount. NB: T9C reuses the
    // shared device-mgmt gallery fixtures (cgc-seed-refused → cgc-fallback) for the failure beats, with the
    // boot-demo boot frames as the lead-in, so the prop-diff invariant is asserted on the refuse→settle pair.
    const rIdx = reroute!.frames.indexOf(refusedFrame!);
    const refusedGroup = refusedFrame!.projection.analytics.engineProcesses[0].worktreeGroup;
    expect(reroute!.frames[rIdx + 1].projection.analytics.engineProcesses[0].worktreeGroup).toBe(refusedGroup);
  });

  it("authors the T7B provider-block arc (verify → pre-contract block → retry → clone), one enclosure throughout", () => {
    const providerBlock = SCENARIOS.find((scenario) => scenario.name === "provider-block");
    expect(providerBlock).toBeTruthy();
    const captions = providerBlock!.frames.map((frame) => frame.caption).join(" | ");
    expect(captions).toMatch(/provider plan/i);
    expect(captions).toMatch(/block/i);
    expect(captions).toMatch(/retry/i);
    const blocked = providerBlock!.frames
      .flatMap((frame) => frame.projection.analytics.engineProcesses)
      .find((node) => node.health === "blocked");
    expect(blocked).toBeTruthy();
    expect(blocked!.setupState).toBe("blocked");
    expect(blocked!.providers.length).toBe(0); // the engines never light at the block
    expect(blocked!.missingFacts.some((fact) => /contract not yet written/i.test(fact))).toBe(true);
    expect(blocked!.missingFacts.some((fact) => /provider (plan|setup|runtime)|setup config/i.test(fact))).toBe(true);
    // the recover passes through the provider seed/clone beats (the copy-arrows), not a teleport
    const hasClone = providerBlock!.frames.some((frame) =>
      frame.projection.analytics.engineProcesses.some((node) =>
        node.edges.some((e) => (e.kind === "cgc-seed" || e.kind === "grepai-clone") && e.state === "running"),
      ),
    );
    expect(hasClone).toBe(true);
    const groups = new Set(
      providerBlock!.frames.flatMap((frame) =>
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
    // the recover is a ff/ref diff — NOT the clone/seed beats (T12B engines never go down)
    const hasCloneBeat = liveSync!.frames.some((frame) =>
      frame.projection.analytics.engineProcesses.some((node) =>
        node.edges.some((e) => (e.kind === "cgc-seed" || e.kind === "grepai-clone") && e.state === "running"),
      ),
    );
    expect(hasCloneBeat).toBe(false);
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
    const hasCloneTail = conflict!.frames.some((frame) =>
      frame.projection.analytics.engineProcesses.some((node) =>
        node.edges.some((e) => (e.kind === "cgc-seed" || e.kind === "grepai-clone") && e.state === "running"),
      ),
    );
    expect(hasCloneTail).toBe(false);
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
      expect(frame.projection.analytics.engineProcesses[0].landing ?? []).toHaveLength(0);
    }
    expect(abandon!.frames[2].durMs).toBe(1300); // X2 holds the dissolve longest
    const ids = new Set(abandon!.frames.map((frame) => frame.projection.analytics.engineProcesses[0].id));
    expect(ids).toEqual(new Set(["boot-demo"])); // one continuous identity (a prop diff, not a remount)
  });

  it("folds the old gallery states in as single-frame resting scenarios (no coverage lost)", () => {
    const resting = SCENARIOS.find((scenario) => scenario.name === "engine-cleanup-pending");
    expect(resting?.frames).toHaveLength(1);
  });
});
