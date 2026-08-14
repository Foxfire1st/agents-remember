import { describe, expect, it } from "vitest";

import { LIFECYCLE_STATES } from "../types/projection";
import type { EnclosureNode, LifecycleProjection, ProviderNode, State } from "../types/projection";
import {
  CONSTEL_STATUSES,
  CONSTEL_STATUS_BY_STATE,
  UNCLASSIFIED_STATUS,
  activeTopologyInputs,
  buildTopology,
  lifecycleStatus,
} from "./model";

const enclosure = (overrides: Partial<EnclosureNode> = {}): EnclosureNode => ({
  enclosure: "/tasks/demo/enclosures/demo/series-contract.md",
  enclosureId: "/tasks/demo/enclosures/demo/series-contract.md",
  leafId: "demo",
  taskRoot: "/tasks/demo",
  taskId: "DEMO",
  taskName: "demo",
  repoName: "agents-remember",
  lifecycleId: "LC1",
  worktreeGroup: "/worktrees/agents-remember/demo-ar",
  humanReviewStatus: "pending-review",
  closeoutStatus: "not-started",
  integrationStatus: "not-started",
  cleanup: "pending",
  codeWorktreeExists: true,
  memoryWorktreeExists: true,
  actions: [],
  ...overrides,
});

const provider = (overrides: Partial<ProviderNode> = {}): ProviderNode => ({
  id: "codegraphcontext-code@demo",
  state: "configured",
  ok: true,
  watcherUp: true,
  indexingState: "indexed",
  scope: "worktree",
  role: "code",
  ...overrides,
});

/** A vocabulary member a newer server could send that this mirror's closed union does not declare.
 *  `State` is a bare `str` server-side, so the mirror is NARROWER than the wire by construction —
 *  this is the one widening a test may make, and it is deliberately not a shape, just a token.
 *
 *  It lives here, named and once, so the two tests below that need an unclassifiable state read as
 *  what they are (a forward-compatibility check) rather than as two loose assertions that a reader
 *  could mistake for the pattern this suite otherwise bans. */
function fromANewerServer(state: string): State {
  return state as State;
}

const lifecycle = (overrides: Partial<LifecycleProjection> = {}): LifecycleProjection => ({
  id: "LC1",
  state: "running",
  phase: "build",
  fleeting: false,
  tokens: 0,
  startedAt: "2026-06-28T00:00",
  lastEventTs: "2026-06-28T00:00",
  stateEnteredAt: "2026-06-28T00:00",
  inferred: false,
  actions: [],
  tokenSeries: [],
  ...overrides,
});

describe("activeTopologyInputs", () => {
  it("keeps only active-group enclosures and drops orphan/terminal lifecycles", () => {
    const live = enclosure({
      enclosure: "/tasks/demo/enclosures/live/series-contract.md",
      worktreeGroup: "/worktrees/agents-remember/live-ar",
    });
    const dead = enclosure({
      enclosure: "/tasks/demo/enclosures/dead/series-contract.md",
      worktreeGroup: "/worktrees/agents-remember/dead-ar",
    });
    const liveLc = lifecycle({ id: "LIVE", enclosure: live.enclosure });
    const deadLc = lifecycle({ id: "DEAD", enclosure: dead.enclosure });
    const orphanLc = lifecycle({ id: "ORPHAN", enclosure: undefined });

    // activeWorktreeGroups is a basename set; EnclosureNode.worktreeGroup is a full path.
    const out = activeTopologyInputs([liveLc, deadLc, orphanLc], [live, dead], ["live-ar"]);

    expect(out.enclosures).toEqual([live]);
    expect(out.lifecycles).toEqual([liveLc]);
  });
});

describe("lifecycleStatus", () => {
  it("classifies every state the vocabulary declares", () => {
    // The grammar must be TOTAL over `LIFECYCLE_STATES`, and this is the assertion that says so
    // without holding a second copy of the state list. `Record<State, ConstelStatus>` already
    // fails `tsc -b` on a seventh state; this makes it fail under vitest too, so the gap is
    // visible from either gate rather than only from the one someone remembered to run.
    for (const state of LIFECYCLE_STATES) {
      expect(Object.keys(CONSTEL_STATUS_BY_STATE)).toContain(state);
    }
  });

  it("classifies a state it has never heard of as the declared unclassified status", () => {
    // A state from a newer server, or from a projection persisted by one. `ok` is the specific
    // wrong answer — it is the claim "nothing here needs you", made about the one case where this
    // build knows least. It must read as something to look at.
    //
    // PINNED TO THE VALUE, not to its negation. `.not.toBe("ok")` was the whole assertion here,
    // and `undefined` satisfies it: delete `?? UNCLASSIFIED_STATUS` from `lifecycleStatus` and the
    // miss returns `undefined`, this test still passes, and the `undefined` reaches the renderer's
    // palette — which used to answer `?? COLORS.ok` and paint the healthy fill. Both gates green,
    // original defect restored. An assertion that only rules a value OUT cannot notice the absence
    // of a value at all.
    const status = lifecycleStatus({ state: fromANewerServer("awaiting-review"), inferred: false });
    expect(CONSTEL_STATUSES).toContain(status); // it is a status, not a hole
    expect(status).toBe(UNCLASSIFIED_STATUS);
    expect(status).toBe("warn"); // and the declared unclassified status is the "look at this" one
  });

  it("still classifies an unknown state when the reducer INFERRED it", () => {
    // The `inferred` degrade reads `declared === "ok"`, which is false for `undefined` as well as
    // for `warn` — so this path cannot distinguish "unclassified" from "classified, not healthy"
    // on its own. Pin it here so the unclassified answer survives the second branch too.
    expect(lifecycleStatus({ state: fromANewerServer("awaiting-review"), inferred: true })).toBe(
      UNCLASSIFIED_STATUS,
    );
  });

  it("degrades an inferred healthy state and leaves every other reading alone", () => {
    // `inferred` means the reducer DERIVED the state rather than reading a written transition,
    // so a healthy reading loses its confidence. It must not upgrade anything: an inferred
    // blocked lifecycle is still a fault, an inferred abandoned one is still over.
    expect(lifecycleStatus({ state: "running", inferred: true })).toBe("warn");
    expect(lifecycleStatus({ state: "running", inferred: false })).toBe("ok");
    expect(lifecycleStatus({ state: "blocked", inferred: true })).toBe("crit");
    expect(lifecycleStatus({ state: "abandoned", inferred: true })).toBe("idle");
  });
});

describe("buildTopology", () => {
  it("folds the bound lifecycle into the enclosure node and emits no task ring", () => {
    const owner = enclosure();
    const lc = lifecycle({ id: "LCX", enclosure: owner.enclosure, phase: "build", state: "blocked" });
    const nodes = buildTopology([lc], [owner], []);

    const wt = nodes.find((node) => node.kind === "wt" && node.label === owner.taskName);
    expect(wt).toBeDefined();
    expect(wt?.id).toBe("LCX"); // click-through now lives on the enclosure node
    expect(wt?.status).toBe("crit"); // lifecycleStatus(blocked)
    expect(wt?.sub).toBe("build · blocked");
    expect(nodes.some((node) => (node.kind as string) === "task")).toBe(false);
  });

  it("draws every state in the vocabulary with the status that state declares", () => {
    // ITERATE, never enumerate. The defect was a classification that named five of the six
    // states and answered "ok" for the sixth, and this file's own test could not see it because
    // it hand-picked the one state it already knew about. Driving the whole vocabulary through
    // `buildTopology` also pins that there is exactly ONE classification path — a second
    // if-chain grown in here later disagrees with the declared grammar and fails.
    for (const state of LIFECYCLE_STATES) {
      const owner = enclosure();
      const lc = lifecycle({ id: `LC-${state}`, enclosure: owner.enclosure, state });
      const nodes = buildTopology([lc], [owner], []);
      const wt = nodes.find((node) => node.kind === "wt" && node.label === owner.taskName);
      expect(wt?.status).toBe(CONSTEL_STATUS_BY_STATE[state]);
    }
  });

  it("does not render an awaiting-developer lifecycle as a healthy node", () => {
    // The reported defect, pinned by name: the turn has been handed back to the developer and
    // `lifecycleStatus` fell through to its `return "ok"`, so the constellation drew this
    // lifecycle as a healthy node — the one surface the developer scans to find work waiting on
    // them. `warn` because it is actionable and not a fault; `crit` is for blocked.
    const owner = enclosure();
    const lc = lifecycle({
      id: "LCX",
      enclosure: owner.enclosure,
      phase: "build",
      state: "awaiting-developer",
    });
    const nodes = buildTopology([lc], [owner], []);

    const wt = nodes.find((node) => node.kind === "wt" && node.label === owner.taskName);
    expect(wt?.status).not.toBe("ok"); // the defect: it read as healthy
    expect(wt?.status).toBe("warn");
    expect(wt?.sub).toBe("build · awaiting-developer");
  });

  it("joins a worktree provider to its enclosure when worktreeGroup formats differ (path vs basename)", () => {
    const owner = enclosure({ worktreeGroup: "/worktrees/agents-remember/demo-ar" });
    // Real served data: the worktree ProviderNode.worktreeGroup is a basename, the enclosure's a full path.
    const nodes = buildTopology([], [owner], [provider({ worktreeGroup: "demo-ar" })]);

    const worktreeIndex = nodes.findIndex((node) => node.kind === "wt" && node.label === owner.taskName);
    const providerNode = nodes.find((node) => node.kind === "prov");

    expect(worktreeIndex).toBeGreaterThan(-1);
    expect(providerNode?.parent).toBe(worktreeIndex);
  });

  it("parents worktree-scoped providers to their owning worktree node", () => {
    const owner = enclosure();
    const nodes = buildTopology([], [owner], [provider({ worktreeGroup: owner.worktreeGroup })]);

    const worktreeIndex = nodes.findIndex((node) => node.kind === "wt" && node.label === owner.taskName);
    const providerNode = nodes.find((node) => node.kind === "prov" && node.label === "codegraphcontext-code@demo");

    expect(worktreeIndex).toBeGreaterThan(-1);
    expect(providerNode?.parent).toBe(worktreeIndex);
  });

  it("falls back to the workspace core when a worktree provider has no matching group", () => {
    const nodes = buildTopology([], [enclosure()], [provider({ worktreeGroup: "/missing/group" })]);
    const workspaceIndex = nodes.findIndex((node) => node.kind === "ws");
    const providerNode = nodes.find((node) => node.kind === "prov");

    expect(providerNode?.parent).toBe(workspaceIndex);
  });

  it("keeps workspace-scoped providers parented to the workspace core", () => {
    const nodes = buildTopology(
      [],
      [enclosure()],
      [provider({ id: "grepai-memory", scope: "workspace", worktreeGroup: undefined })],
    );
    const workspaceIndex = nodes.findIndex((node) => node.kind === "ws");
    const providerNode = nodes.find((node) => node.kind === "prov");

    expect(providerNode?.parent).toBe(workspaceIndex);
  });

  it("parents repo-scoped workspace providers to their covered repo node", () => {
    const nodes = buildTopology(
      [],
      [],
      [
        provider({
          id: "codegraphcontext-code:repo-b",
          scope: "workspace",
          repoId: "repo-b",
          worktreeGroup: undefined,
        }),
      ],
    );
    const repoIndex = nodes.findIndex((node) => node.kind === "repo" && node.label === "repo-b");
    const providerNode = nodes.find((node) => node.kind === "prov");

    expect(repoIndex).toBeGreaterThan(-1);
    expect(providerNode?.parent).toBe(repoIndex);
  });

  it("keeps worktreeGroup precedence over repoId for provider parenting", () => {
    const owner = enclosure();
    const nodes = buildTopology(
      [],
      [owner],
      [
        provider({
          worktreeGroup: owner.worktreeGroup,
          repoId: "repo-b",
        }),
      ],
    );
    const worktreeIndex = nodes.findIndex((node) => node.kind === "wt" && node.label === owner.taskName);
    const providerNode = nodes.find((node) => node.kind === "prov");

    expect(providerNode?.parent).toBe(worktreeIndex);
  });
});
