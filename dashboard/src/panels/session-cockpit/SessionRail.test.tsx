// The rail's jsdom state matrix (260715-FEUI-L2 S6): ruled row anatomy + hierarchy, the dot
// grammar on real DOM, the attention strip, gate/brief markers, bulk end with honest previews,
// the bus footer, and the cross-surface consistency contract (rail dot ≡ HeaderStrip dot).
import { act, cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { attentionRollup, buildRailModel } from "../../data/railModel";
import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { fromTerminalSessionInfo, sessionStore } from "../../data/sessions";
import { seatVisualState } from "../../data/stateGrammar";
import { dashboardStore } from "../../data/store";
import { catalogRow, FLEET } from "../../test/fixtures/catalogRows";
import type { Analytics, SupervisorHeartbeat } from "../../types/projection";
import { HeaderStrip } from "./HeaderStrip";
import { SessionRail } from "./SessionRail";

const fleet = () => sessionStore.getState().sessions;

function renderRail(overrides: { focusedSessionId?: string | null } = {}) {
  const sessions = fleet();
  const model = buildRailModel(sessions);
  const rollup = attentionRollup(sessions);
  const onFocusSession = vi.fn();
  const view = render(
    <SessionRail
      onFocusSession={onFocusSession}
      focusedSessionId={overrides.focusedSessionId ?? null}
      model={model}
      rollup={rollup}
    />,
  );
  return { ...view, onFocusSession, model, rollup, sessions };
}

const HEARTBEAT: SupervisorHeartbeat = {
  lastTickAt: "2026-07-16T09:30:00Z",
  ageSeconds: 2,
  staleCutoffSeconds: 60,
  stale: false,
  pendingInboxCount: 2,
  redeliverableInboxCount: 0,
  lastSweepDurationSeconds: 0.1,
};

beforeEach(() => {
  sessionStore.getState().hydrate(FLEET.map(fromTerminalSessionInfo));
  sessionCockpitStore.setState({
    pollHealth: { lastBeatAt: null, missedBeats: 0, healthy: true },
    orchestrationTreeView: false,
    perSession: {},
  });
  dashboardStore.setState({ supervisorHeartbeat: HEARTBEAT, analytics: null, lifecycles: {} });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("rail-state matrix (R14 — one grammar on real DOM)", () => {
  it("every row's dot carries exactly the stateGrammar visual for that seat", () => {
    const { getByTestId, sessions } = renderRail();
    for (const session of sessions) {
      if (session.status === "landed") continue; // folded until the done-folder opens
      const visual = seatVisualState(session);
      const dot = getByTestId(`rail-dot-${session.id}`);
      expect(dot.getAttribute("data-state"), session.id).toBe(visual.key);
      expect(dot.getAttribute("data-state-pulse"), session.id).toBe(String(visual.pulse));
      expect(dot.getAttribute("data-state-color"), session.id).toBe(visual.color);
    }
  });

  it("status chips carry the status vocabulary ONLY — the model never renders in the rail (R6)", () => {
    const { container, getByTestId } = renderRail();
    expect(getByTestId("rail-status-worker-l4").textContent).toBe("working");
    expect(getByTestId("rail-status-worker-tui").textContent).toBe("input?");
    expect(getByTestId("rail-status-scout").textContent).toBe("failed");
    // The fixture's resolvedModel/resolvedEffort must appear NOWHERE in the rail DOM.
    expect(container.textContent).not.toContain("gpt-5.6-sol");
    expect(container.textContent).not.toContain("xhigh");
  });

  it("rows follow the ruled anatomy order: dot | role | title | status | End", () => {
    const { getByTestId } = renderRail();
    const row = getByTestId("rail-row-worker-l4");
    const sequence = [
      getByTestId("rail-dot-worker-l4"),
      getByTestId("rail-role-worker-l4"),
      within(row).getByText("worker-L4-serving"),
      getByTestId("rail-status-worker-l4"),
      getByTestId("rail-end-worker-l4"),
    ];
    for (let i = 1; i < sequence.length; i += 1) {
      // DOCUMENT_POSITION_FOLLOWING = 4: each segment renders after the previous one.
      expect(sequence[i - 1].compareDocumentPosition(sequence[i]) & 4).toBe(4);
    }
    expect(getByTestId("rail-role-worker-l4").textContent).toBe("WKR");
    expect(getByTestId("rail-role-manager-l4").textContent).toBe("MGR");
  });

  it("the input? chip tooltip carries the prompt preview (R16)", () => {
    const { getByTestId } = renderRail();
    expect(getByTestId("rail-status-worker-tui").getAttribute("title")).toContain(
      "Apply the migration to harness_control_api.py",
    );
  });
});

describe("ruled hierarchy (R5)", () => {
  it("architect/orchestrator render flat on the spine, managers flat inside their master box", () => {
    const { getByTestId } = renderRail();
    const rail = getByTestId("session-rail");
    const architect = getByTestId("rail-row-architect");
    expect(architect.closest("[data-testid^='rail-master-']")).toBeNull(); // never boxed
    expect(getByTestId("rail-row-orchestrator").closest("[data-testid^='rail-master-']")).toBeNull();
    const masterBox = getByTestId("rail-master-agents-remember/260714_own-adapter-capability");
    expect(within(masterBox).getByTestId("rail-row-manager-l4")).toBeDefined();
    expect(rail.contains(architect)).toBe(true);
  });

  it("leaf clusters indent under the manager with the active seat on top", () => {
    const { getByTestId } = renderRail();
    const cluster = getByTestId("rail-cluster-agents-remember/260714_own-adapter-capability/04_serving");
    const rows = within(cluster).getAllByTestId(/^rail-row-/);
    expect(rows.map((row) => row.getAttribute("data-testid"))).toEqual([
      "rail-row-worker-l4", // ACTIVE (working) sorts to the top
      "rail-row-reviewer-l4",
      "rail-row-curator-l4",
    ]);
  });

  it("the tree toggle swaps to the spawn-edge provenance view (R5 palette/button toggle)", () => {
    const { getByTestId, queryByTestId } = renderRail();
    expect(queryByTestId("rail-spawn-tree")).toBeNull();
    fireEvent.click(getByTestId("rail-tree-toggle"));
    expect(sessionCockpitStore.getState().orchestrationTreeView).toBe(true);
    expect(getByTestId("rail-spawn-tree")).toBeDefined();
    expect(window.localStorage.getItem("cockpit.sessions.orchestration-tree")).toBe("true");
  });
});

describe("fleet attention strip (R12)", () => {
  it("renders live rollup counts as filter buttons and focuses the first matching seat", () => {
    const { getByTestId, onFocusSession } = renderRail();
    expect(getByTestId("rail-attention-input").textContent).toContain("1 need input");
    expect(getByTestId("rail-attention-failed").textContent).toContain("1 failed");
    fireEvent.click(getByTestId("rail-attention-input"));
    expect(onFocusSession).toHaveBeenCalledWith("worker-tui");
    // The set is highlighted after the click.
    expect(getByTestId("rail-row-worker-tui").getAttribute("data-attention-highlight")).toBe("true");
  });

  it("the filter highlight expires once the underlying state resolves (review finding 3)", () => {
    const onFocusSession = vi.fn();
    const props = () => {
      const current = fleet();
      return { model: buildRailModel(current), rollup: attentionRollup(current) };
    };
    const view = render(
      <SessionRail onFocusSession={onFocusSession} focusedSessionId={null} {...props()} />,
    );
    fireEvent.click(view.getByTestId("rail-attention-input"));
    expect(view.getByTestId("rail-row-worker-tui").getAttribute("data-attention-highlight")).toBe(
      "true",
    );
    // The pending question is answered (the poll delivers the resolved state).
    act(() => {
      sessionStore
        .getState()
        .patch("worker-tui", { turnState: "turn-ended", controlPendingInteraction: undefined });
    });
    view.rerender(
      <SessionRail onFocusSession={onFocusSession} focusedSessionId={null} {...props()} />,
    );
    expect(
      view.getByTestId("rail-row-worker-tui").getAttribute("data-attention-highlight"),
    ).toBeNull();
  });

  it("ZERO-STATE renders nothing — even with seats working", () => {
    sessionStore
      .getState()
      .hydrate(
        FLEET.filter((row) => !["worker-tui", "scout"].includes(row.id)).map(fromTerminalSessionInfo),
      );
    const { queryByTestId } = renderRail();
    expect(queryByTestId("rail-attention-strip")).toBeNull();
  });

  it("master headers carry the dominant attention rollup badge", () => {
    const { getByTestId } = renderRail();
    expect(
      getByTestId("rail-master-attention-agents-remember/260715_react-tui-cockpit-frontend").textContent,
    ).toBe("❗1");
  });
});

describe("gate + brief joins (R13, R8)", () => {
  it("shows a gate badge on rows whose leaf holds an undecided gate", () => {
    dashboardStore.setState({
      lifecycles: {
        LC1: {
          id: "LC1",
          gate: { id: "g1", kind: "plan-approval", state: "open", decisions: [], packet: {}, ts: "t" },
        } as never,
      },
      analytics: {
        taskDocuments: [
          {
            id: "04_serving",
            lifecycleId: "LC1",
            repository: "agents-remember",
            title: "serving",
            kind: "subTask",
            docPath: "tasks/agents-remember/260714_own-adapter-capability/04_serving.md",
          },
        ],
        agentPickups: [],
      } as unknown as Analytics,
    });
    const { getByTestId, queryByTestId } = renderRail();
    const badge = getByTestId("rail-gate-worker-l4");
    expect(badge.getAttribute("title")).toBe("gate plan-approval — decision pending");
    expect(getByTestId("rail-row-worker-l4").getAttribute("data-attention-gate")).toBe("true");
    expect(queryByTestId("rail-gate-worker-caps")).toBeNull(); // no gate on that leaf
  });

  it("brief column is two-state: marker while pending, nothing otherwise", () => {
    dashboardStore.setState({
      analytics: {
        taskDocuments: [],
        agentPickups: [
          {
            id: "p1",
            entryId: "e1",
            messageKind: "dispatch-brief",
            deliveryState: "delivered",
            state: "waiting-for-agent",
            ttlSeconds: 900,
            deliveredToSession: "worker-l4",
          },
        ],
      } as unknown as Analytics,
    });
    const { getByTestId, queryByTestId } = renderRail();
    expect(getByTestId("rail-brief-worker-l4")).toBeDefined();
    expect(queryByTestId("rail-brief-worker-caps")).toBeNull();
  });
});

describe("completed folder + bulk end (R17)", () => {
  it("folds landed seats per master, collapsed by default, expandable", () => {
    const { getByTestId, queryByTestId } = renderRail();
    expect(queryByTestId("rail-row-landed-w1")).toBeNull();
    const toggle = getByTestId("rail-done-toggle-agents-remember/260714_own-adapter-capability");
    expect(toggle.textContent).toContain("completed · 2");
    fireEvent.click(toggle);
    expect(getByTestId("rail-row-landed-w1")).toBeDefined();
    // Completed rows carry landedReason in the tooltip (R17) and keep the ruled End segment as
    // the mockup's compact ✕ (review finding 5).
    expect(getByTestId("rail-row-landed-w1").getAttribute("title")).toContain("landed: leaf integrated");
    expect(getByTestId("rail-end-landed-w1").textContent).toBe("✕");
  });

  it("bulk end shows honest counts, arms an inline preview NAMING what is removed, then executes", async () => {
    const cleanupCalls: string[][] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (String(url).includes("landed-cleanup")) {
          const body = JSON.parse(String(init?.body)) as { sessionIds: string[] };
          cleanupCalls.push(body.sessionIds);
          return {
            ok: true,
            json: async () => ({
              closed: body.sessionIds.length,
              skipped: 0,
              closedSessions: body.sessionIds,
              skippedSessions: [],
            }),
          } as Response;
        }
        return { ok: true, json: async () => ({ sessions: [] }) } as Response;
      }),
    );
    const { getByTestId, findByTestId } = renderRail();
    expect(getByTestId("rail-bulk-sprint").textContent).toBe("✕ end 3 completed");
    const masterBulk = getByTestId("rail-bulk-master-agents-remember/260714_own-adapter-capability");
    expect(masterBulk.textContent).toBe("✕ end 2 done");
    fireEvent.click(masterBulk);
    const confirm = await findByTestId(
      "rail-bulk-confirm-agents-remember/260714_own-adapter-capability",
    );
    expect(confirm.textContent).toContain("reviewer-done-1");
    expect(confirm.textContent).toContain("worker-done-1");
    fireEvent.click(getByTestId("rail-bulk-execute"));
    await Promise.resolve();
    expect(cleanupCalls).toEqual([["landed-r1", "landed-w1"]]);
  });
});

describe("freshness + bus footer (R15, R8)", () => {
  it("shows the poll-stale banner once beats are missed past the cutoff", () => {
    sessionCockpitStore.setState({ pollHealth: { lastBeatAt: null, missedBeats: 3, healthy: false } });
    const { getByTestId } = renderRail();
    expect(getByTestId("rail-poll-stale").textContent).toContain("3 beats missed");
  });

  it("renders anchored bus numbers: pending vs redeliverable, heartbeat vs stale cutoff", () => {
    const { getByTestId } = renderRail();
    const footer = getByTestId("rail-bus-footer");
    expect(footer.textContent).toContain("2 pending");
    expect(footer.textContent).toContain("0 redeliverable");
    expect(footer.textContent).toContain("heartbeat 2s / stale 60s");
  });

  it("states the truth when the supervisor never ticked — never fake numbers", () => {
    dashboardStore.setState({ supervisorHeartbeat: null });
    const { getByTestId } = renderRail();
    expect(getByTestId("rail-bus-footer").textContent).toContain("supervisor has not ticked");
  });
});

describe("cross-surface consistency (R14)", () => {
  it("the rail dot and the HeaderStrip dot render the SAME grammar state for the same seat", () => {
    const worker = fleet().find((session) => session.id === "worker-l4");
    if (!worker) throw new Error("fixture missing");
    const rail = renderRail();
    const header = render(<HeaderStrip session={worker} cockpit={undefined} />);
    const railDot = rail.getByTestId("rail-dot-worker-l4");
    const headerDot = header.getByTestId("header-dot");
    for (const attr of ["data-state", "data-state-color", "data-state-pulse"]) {
      expect(headerDot.getAttribute(attr)).toBe(railDot.getAttribute(attr));
    }
  });
});

describe("zero-state (R9 — never an unexplained empty rail)", () => {
  it("explains itself when no session exists", () => {
    sessionStore.getState().hydrate([]);
    const { getByTestId } = renderRail();
    expect(getByTestId("rail-zero-state").textContent).toContain("no sessions");
  });

  it("waiting(reason) renders steady muted-amber when a reason is supplied (reserved AEO word)", () => {
    sessionStore
      .getState()
      .hydrate([fromTerminalSessionInfo(catalogRow({ id: "w", turnState: "working" }))]);
    // No wire source sets waitingReason yet — the grammar is rendered-ready; assert via grammar.
    expect(seatVisualState({ turnState: "working", waitingReason: "ci run 8323" })).toMatchObject({
      key: "waiting",
      color: "mutedAmber",
      pulse: false,
    });
  });
});
