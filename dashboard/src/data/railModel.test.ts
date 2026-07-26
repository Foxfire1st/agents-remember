import { describe, expect, it } from "vitest";

import { FLEET, catalogRow } from "../test/fixtures/catalogRows";
import {
  attentionRollup,
  attentionZeroState,
  briefPendingSessionIds,
  buildRailModel,
  buildSpawnTree,
  compareClusterSeats,
  criticalBusSessionIds,
  heldGatesByLeafKey,
  interactionPromptPreview,
  jumpToAttentionTarget,
  masterAttentionBadge,
  railCycleOrder,
  railRowTooltip,
  roleCode,
  ROW_ELIDABLE_SEGMENTS,
  ROW_SEGMENTS,
  smartDefaultFocus,
  waitingSeats,
} from "./railModel";
import { fromTerminalSessionInfo, type OpenSession } from "./sessions";
import type { AgentPickupNode, GateNode, LifecycleProjection, TaskDocNode } from "../types/projection";

const fleet = FLEET.map(fromTerminalSessionInfo);
const byId = (id: string) => {
  const found = fleet.find((session) => session.id === id);
  if (!found) throw new Error(`fixture missing: ${id}`);
  return found;
};

describe("role codes (R6 — RULED three-letter chips)", () => {
  it("maps the six ruled roles and derives the rest; absent without a spawn role", () => {
    expect(roleCode(byId("architect"))).toBe("ARC");
    expect(roleCode(byId("orchestrator"))).toBe("ORC");
    expect(roleCode(byId("manager-l4"))).toBe("MGR");
    expect(roleCode(byId("worker-l4"))).toBe("WKR");
    expect(roleCode(byId("reviewer-l4"))).toBe("REV");
    expect(roleCode(byId("curator-l4"))).toBe("CUR");
    expect(roleCode(byId("scout"))).toBeUndefined(); // hand-opened: no spawn role, no chip
  });
});

describe("buildRailModel (R5 — flat command spine, workers-only clusters)", () => {
  const model = buildRailModel(fleet);

  it("renders architect → orchestrator flat, in role order, never nested by spawn edges", () => {
    expect(model.spine.map((session) => session.id)).toEqual(["architect", "orchestrator"]);
  });

  it("places managers flat inside their master section", () => {
    const master = model.masters.find((section) => section.key.endsWith("260714_own-adapter-capability"));
    expect(master?.managers.map((session) => session.id)).toEqual(["manager-l4"]);
  });

  it("clusters leaf agents per leaf under the manager's master", () => {
    const master = model.masters.find((section) => section.key.endsWith("260714_own-adapter-capability"));
    expect(master?.clusters.map((cluster) => cluster.label)).toEqual([
      "01_protocol",
      "04_serving",
      "05_capabilities",
    ].filter((label) => label !== "01_protocol")); // 01_protocol seats are all landed → completed folder
  });

  it("sorts the ACTIVE seat to the top of its cluster; ties keep worker → reviewer → curator", () => {
    const master = buildRailModel(fleet).masters.find((section) =>
      section.key.endsWith("260714_own-adapter-capability"),
    );
    const serving = master?.clusters.find((cluster) => cluster.key.endsWith("04_serving"));
    // worker-l4 is working → top; reviewer/curator idle keep base order.
    expect(serving?.seats.map((seat) => seat.id)).toEqual(["worker-l4", "reviewer-l4", "curator-l4"]);

    // Flip the curator to working: it bubbles up; the idle worker/reviewer keep base order.
    const flipped = fleet.map((session) =>
      session.id === "curator-l4"
        ? { ...session, turnState: "working" }
        : session.id === "worker-l4"
          ? { ...session, turnState: "turn-ended" }
          : session,
    );
    const reordered = buildRailModel(flipped)
      .masters.find((section) => section.key.endsWith("260714_own-adapter-capability"))
      ?.clusters.find((cluster) => cluster.key.endsWith("04_serving"));
    expect(reordered?.seats.map((seat) => seat.id)).toEqual(["curator-l4", "worker-l4", "reviewer-l4"]);
  });

  it("resorts only on state change: identical states give identical order (no jumpy reflows)", () => {
    const seats = [byId("curator-l4"), byId("worker-l4"), byId("reviewer-l4")];
    const once = [...seats].sort(compareClusterSeats).map((seat) => seat.id);
    const twice = [...seats].reverse().sort(compareClusterSeats).map((seat) => seat.id);
    expect(once).toEqual(twice);
  });

  it("moves landed seats into the per-master completed folder (R17) and counts the sprint total", () => {
    const master = buildRailModel(fleet).masters.find((section) =>
      section.key.endsWith("260714_own-adapter-capability"),
    );
    expect(master?.completed.map((seat) => seat.id)).toEqual(["landed-r1", "landed-w1"]);
    // 2 in the master + pi-probe unattached = 3 sprint-wide.
    expect(buildRailModel(fleet).completedTotal).toBe(3);
    expect(buildRailModel(fleet).completedUnattached.map((seat) => seat.id)).toEqual(["pi-probe"]);
  });

  it("keeps unclaimed non-command seats in unattached; terminated tombstones never render", () => {
    const model2 = buildRailModel([
      ...fleet,
      fromTerminalSessionInfo(catalogRow({ id: "tomb", status: "terminated" })),
    ]);
    expect(model2.unattached.map((seat) => seat.id)).toEqual(["scout"]);
  });

  it("applies master labels when provided", () => {
    const labeled = buildRailModel(fleet, {
      masterLabel: (key) => (key.endsWith("260714_own-adapter-capability") ? "Own-adapter capability" : undefined),
    });
    expect(labeled.masters.find((m) => m.label === "Own-adapter capability")).toBeDefined();
  });
});

describe("railCycleOrder (alt+↑/↓)", () => {
  it("cycles spine → managers → clusters → unattached, live rows only", () => {
    const order = railCycleOrder(buildRailModel(fleet));
    expect(order).toEqual([
      "architect",
      "orchestrator",
      "manager-l4",
      "worker-l4",
      "reviewer-l4",
      "curator-l4",
      "worker-caps",
      "worker-tui",
      "scout",
    ]);
  });
});

describe("buildSpawnTree (R5 — the palette-toggled provenance view)", () => {
  it("nests by spawn edges, exactly what the ruled default view must NOT do", () => {
    const rows = buildSpawnTree(fleet);
    const depthOf = (id: string) => rows.find((row) => row.session.id === id)?.depth;
    expect(depthOf("architect")).toBe(0);
    expect(depthOf("orchestrator")).toBe(1);
    expect(depthOf("manager-l4")).toBe(2);
    expect(depthOf("worker-l4")).toBe(3);
    expect(depthOf("scout")).toBe(0);
  });
});

describe("row anatomy invariants (R6)", () => {
  it("dot + role + title always survive; only the status chip elides — to the tooltip", () => {
    expect(ROW_SEGMENTS).toEqual(["dot", "role", "title", "status", "end"]);
    expect(ROW_ELIDABLE_SEGMENTS).toEqual(["status"]);
    for (const segment of ["dot", "role", "title"] as const) {
      expect(ROW_ELIDABLE_SEGMENTS).not.toContain(segment);
    }
    const tooltip = railRowTooltip(byId("worker-l4"), "04_serving");
    expect(tooltip).toContain("state: working"); // the elided chip's truth stays reachable
    expect(tooltip).toContain("role: worker");
  });

  it("carries landed/retired reasons in the tooltip (R17)", () => {
    expect(railRowTooltip(byId("landed-w1"))).toContain("landed: leaf integrated");
  });
});

describe("fleet attention (R12)", () => {
  it("rolls up needs-input / failed / working and honors the joins", () => {
    const rollup = attentionRollup(fleet, {
      unackedSessionIds: ["worker-caps"],
      criticalBusSessionIds: ["reviewer-l4", "ghost"],
    });
    expect(rollup.needsInput).toEqual(["worker-tui"]);
    expect(rollup.failed).toEqual(["scout"]);
    expect(rollup.unacked).toEqual(["worker-caps"]);
    expect(rollup.criticalBus).toEqual(["reviewer-l4"]); // unknown ids dropped
    expect(new Set(rollup.working)).toEqual(new Set(["orchestrator", "manager-l4", "worker-l4", "worker-caps"]));
  });

  it("ZERO-STATE renders nothing — working alone is not attention", () => {
    const calm = fleet.filter((session) => !["worker-tui", "scout"].includes(session.id));
    const rollup = attentionRollup(calm);
    expect(rollup.working.length).toBeGreaterThan(0);
    expect(attentionZeroState(rollup)).toBe(true);
    expect(attentionZeroState(attentionRollup(fleet))).toBe(false);
  });

  it("jump priority: awaiting-input → failed → unacked → critical bus → OLDEST working", () => {
    const rollup = attentionRollup(fleet, { unackedSessionIds: ["worker-caps"], criticalBusSessionIds: ["reviewer-l4"] });
    expect(jumpToAttentionTarget(rollup, fleet)).toBe("worker-tui");
    expect(jumpToAttentionTarget({ ...rollup, needsInput: [] }, fleet)).toBe("scout");
    expect(jumpToAttentionTarget({ ...rollup, needsInput: [], failed: [] }, fleet)).toBe("worker-caps");
    expect(
      jumpToAttentionTarget({ ...rollup, needsInput: [], failed: [], unacked: [] }, fleet),
    ).toBe("reviewer-l4");
    expect(
      jumpToAttentionTarget(
        { ...rollup, needsInput: [], failed: [], unacked: [], criticalBus: [] },
        fleet,
      ),
    ).toBe("orchestrator"); // oldest working (09:05 < 09:10 < 09:20 < 09:25)
    expect(
      jumpToAttentionTarget(
        { needsInput: [], failed: [], unacked: [], criticalBus: [], working: [] },
        fleet,
      ),
    ).toBeNull();
  });

  it("intra-class tiebreak: the longest-waiting seat wins in EVERY class (review finding 4)", () => {
    // curator (09:12) waited longer than reviewer (09:15); join order deliberately reversed.
    const base = { needsInput: [], failed: [], criticalBus: [], working: [] };
    expect(
      jumpToAttentionTarget({ ...base, unacked: ["reviewer-l4", "curator-l4"], criticalBus: [] }, fleet),
    ).toBe("curator-l4");
    expect(
      jumpToAttentionTarget({ ...base, unacked: [], criticalBus: ["reviewer-l4", "curator-l4"] }, fleet),
    ).toBe("curator-l4");
  });

  it("group headers surface the dominant attention class (❗ input beats ✖ failed)", () => {
    const model = buildRailModel(fleet);
    const rollup = attentionRollup(fleet);
    const cockpitMaster = model.masters.find((section) =>
      section.key.endsWith("260715_react-tui-cockpit-frontend"),
    );
    const adapterMaster = model.masters.find((section) =>
      section.key.endsWith("260714_own-adapter-capability"),
    );
    expect(cockpitMaster && masterAttentionBadge(cockpitMaster, rollup)).toEqual({
      glyph: "❗",
      count: 1,
      kind: "needsInput",
    });
    expect(adapterMaster && masterAttentionBadge(adapterMaster, rollup)).toBeNull();
  });
});

describe("smart-default focus (R9)", () => {
  it("awaiting-input first, oldest wins", () => {
    expect(smartDefaultFocus(fleet)).toBe("worker-tui");
  });

  it("then failed, then the most recently active running seat, then null (launcher)", () => {
    const noInput = fleet.filter((session) => session.id !== "worker-tui");
    expect(smartDefaultFocus(noInput)).toBe("scout");
    const calm = noInput.filter((session) => session.id !== "scout");
    expect(smartDefaultFocus(calm)).toBe("worker-caps"); // newest turnStateChangedAt (09:25)
    expect(smartDefaultFocus([])).toBeNull();
    expect(smartDefaultFocus(fleet.filter((session) => session.status === "landed"))).toBeNull();
  });
});

describe("projection joins", () => {
  const doc = (id: string, lifecycleId: string): TaskDocNode =>
    ({
      id,
      lifecycleId,
      repository: "agents-remember",
      title: id,
      status: "inProgress",
      kind: "subTask",
      stepsDone: 0,
      stepsTotal: 0,
      docPath: `tasks/agents-remember/260714_own-adapter-capability/${id}.md`,
      steps: [],
      objective: "",
      requirements: [],
      codeExamples: [],
      decisions: [],
      openQuestions: [],
      references: [],
      subTasks: [],
      sections: [],
    }) as TaskDocNode;
  const gate: GateNode = { id: "g1", kind: "plan-approval", state: "open", decisions: [], packet: {}, ts: "t" };
  const lifecycles: Record<string, LifecycleProjection> = {
    LC1: { id: "LC1", gate } as unknown as LifecycleProjection,
    LC2: { id: "LC2", gate: { ...gate, state: "approved" } } as unknown as LifecycleProjection,
  };

  it("joins HELD gates by leafKey only while undecided (R13)", () => {
    const held = heldGatesByLeafKey([doc("04_serving", "LC1"), doc("05_capabilities", "LC2")], lifecycles);
    expect(held.has("agents-remember/260714_own-adapter-capability/04_serving")).toBe(true);
    expect(held.has("agents-remember/260714_own-adapter-capability/05_capabilities")).toBe(false);
  });

  const pickup = (overrides: Partial<AgentPickupNode>): AgentPickupNode =>
    ({
      id: "p1",
      entryId: "e1",
      messageKind: "dispatch-brief",
      deliveryState: "delivered",
      state: "waiting-for-agent",
      ttlSeconds: 900,
      ...overrides,
    }) as AgentPickupNode;

  it("brief column is TWO-state: pending while unacknowledged, gone otherwise — never a tri-state", () => {
    const sessions: OpenSession[] = [{ id: "worker-l4", label: "w", lifecycleId: "LC9" }];
    expect(
      briefPendingSessionIds([pickup({ deliveredToSession: "worker-l4" })], sessions).has("worker-l4"),
    ).toBe(true);
    expect(briefPendingSessionIds([pickup({ lifecycleId: "LC9" })], sessions).has("worker-l4")).toBe(true);
    // A non-brief message or an unmatchable pickup never marks the column.
    expect(
      briefPendingSessionIds([pickup({ messageKind: "message", deliveredToSession: "worker-l4" })], sessions).size,
    ).toBe(0);
    expect(briefPendingSessionIds([pickup({})], sessions).size).toBe(0);
  });

  it("critical bus = age ≥ ttl·0.8 or escalated check-chat (F11)", () => {
    const sessions: OpenSession[] = [{ id: "s", label: "s", lifecycleId: "LC9" }];
    expect(
      criticalBusSessionIds([pickup({ lifecycleId: "LC9", ageSeconds: 720 })], sessions).has("s"),
    ).toBe(true);
    expect(
      criticalBusSessionIds([pickup({ lifecycleId: "LC9", ageSeconds: 100 })], sessions).size,
    ).toBe(0);
    expect(
      criticalBusSessionIds([pickup({ lifecycleId: "LC9", state: "check-chat" })], sessions).has("s"),
    ).toBe(true);
  });
});

describe("question triage (R16)", () => {
  it("previews the prompt and clamps long ones", () => {
    expect(interactionPromptPreview(byId("worker-tui").controlPendingInteraction)).toBe(
      "Apply the migration to harness_control_api.py as proposed?",
    );
    expect(interactionPromptPreview({ prompt: "x".repeat(200) }, 20)).toHaveLength(20);
    expect(interactionPromptPreview(undefined)).toBeUndefined();
    expect(interactionPromptPreview({ kind: "approval" })).toBeUndefined(); // no prompt → no fake preview
  });

  it("lists ALL waiting seats, newest first", () => {
    const second = fromTerminalSessionInfo(
      catalogRow({
        id: "waiter-2",
        turnState: "awaiting-input",
        turnStateChangedAt: "2026-07-16T09:30:00Z",
        controlPendingInteraction: { prompt: "Continue?" },
      }),
    );
    expect(waitingSeats([...fleet, second]).map((seat) => seat.id)).toEqual(["waiter-2", "worker-tui"]);
  });

  it("lists a seat blocked SOLELY on a multiplexed sub-agent approval", () => {
    const agentOnly = fromTerminalSessionInfo(
      catalogRow({
        id: "waiter-agent",
        turnStateChangedAt: "2026-07-16T10:00:00Z",
        controlPendingInteractions: [
          {
            interactionId: "ix_agent",
            kind: "permission",
            prompt: "Allow the sub-agent command?",
            raw: { threadId: "agent-thread-1", agentLabel: "agent agent-t" },
          },
        ],
      }),
    );
    expect(waitingSeats([agentOnly]).map((seat) => seat.id)).toEqual(["waiter-agent"]);
  });
});
