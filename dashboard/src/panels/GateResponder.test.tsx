import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { postGateDecision } from "../data/actions";
import { postOperatorInbox } from "../data/operatorInbox";
import { deliverToSession, findSessionForLifecycle, sessionStore } from "../data/sessions";
import type { GateNode } from "../types/projection";
import { GateResponder } from "./GateResponder";

vi.mock("../data/actions", () => ({ postGateDecision: vi.fn() }));
vi.mock("../data/operatorInbox", () => ({ postOperatorInbox: vi.fn() }));

vi.mock("../data/sessions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/sessions")>();
  return { ...actual, deliverToSession: vi.fn() };
});

const GATE: GateNode = {
  id: "G1",
  kind: "closeout-approval",
  state: "open",
  evidenceRefs: [],
  decisions: ["approve", "reject"],
  packet: { question: "Ship it?", changedPaths: 3 },
  ts: "2026-06-23T10:00:00+00:00",
};

const PREVIEW_GATES: Array<{ name: string; gate: GateNode; expected: string[] }> = [
  {
    name: "plan approval",
    gate: {
      id: "P1",
      kind: "plan-approval",
      state: "open",
      evidenceRefs: [],
      decisions: ["approve", "reject"],
      packet: { prompt: "Approve the implementation plan?", objective: "Polish dashboard gates" },
      ts: GATE.ts,
    },
    expected: ["Gate: Plan approval", "Approve the implementation plan?", "Objective: Polish dashboard gates"],
  },
  {
    name: "cleanup approval",
    gate: {
      id: "C1",
      kind: "cleanup-approval",
      state: "open",
      evidenceRefs: [],
      decisions: ["approve", "reject"],
      packet: { summary: "Finalize task cleanup.", commands: ["worktree_cleanup"], paths: ["enclosures/19"] },
      ts: GATE.ts,
    },
    expected: ["Gate: Cleanup approval", "Finalize task cleanup.", "Commands: worktree_cleanup", "Paths: enclosures/19"],
  },
  {
    name: "agent question",
    gate: {
      id: "Q1",
      kind: "agent-question",
      state: "open",
      evidenceRefs: [],
      decisions: ["approve", "reject"],
      packet: { question: "Use the shorter commit message?", options: ["yes", "revise"] },
      ts: GATE.ts,
    },
    expected: ["Gate: Agent question", "Use the shorter commit message?", "Options: yes, revise"],
  },
];

beforeEach(() => {
  vi.mocked(postGateDecision).mockResolvedValue("recorded");
  vi.mocked(deliverToSession).mockResolvedValue("delivered");
  vi.mocked(postOperatorInbox).mockResolvedValue("posted");
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
});

describe("GateResponder", () => {
  it("records Yes and routes the notice through the durable inbox even with a hosted chat", async () => {
    sessionStore.getState().add("Claude Code", "s1", "LC1");
    const { getByTestId, queryByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    expect(getByTestId("gate-request").textContent).toContain("Changed paths: 3");
    expect(getByTestId("gate-request").textContent).not.toContain("{");
    expect(getByTestId("gate-request-diagnostics").textContent).toContain("changedPaths");
    expect(getByTestId("gate-route").textContent).toContain("Claude Code 1");
    fireEvent.click(getByTestId("gate-respond-yes"));

    await waitFor(() =>
      expect(postGateDecision).toHaveBeenCalledWith("LC1", "approve", { gateId: "G1", note: undefined }),
    );
    await waitFor(() =>
      expect(postOperatorInbox).toHaveBeenCalledWith({
        lifecycleId: "LC1",
        gateId: "G1",
        ask: expect.stringContaining("Ship it?"),
        response: "Approved by developer in dashboard.",
      }),
    );
    expect(deliverToSession).not.toHaveBeenCalled();
    await waitFor(() => expect(queryByTestId("gate-respond-dialog")).toBeNull());
  });

  it("records Yes and queues an agent notice when no hosted session is attached", async () => {
    const { getByTestId, queryByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    expect(getByTestId("gate-route").textContent).toContain("External inbox for LC1");
    fireEvent.click(getByTestId("gate-respond-yes"));

    await waitFor(() =>
      expect(postOperatorInbox).toHaveBeenCalledWith({
        lifecycleId: "LC1",
        gateId: "G1",
        ask: expect.stringContaining("Ship it?"),
        response: "Approved by developer in dashboard.",
      }),
    );
    await waitFor(() => expect(queryByTestId("gate-respond-dialog")).toBeNull());
    expect(deliverToSession).not.toHaveBeenCalled();
  });

  it("requires a rejection reason before recording No", async () => {
    const { getByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    fireEvent.click(getByTestId("gate-respond-no"));
    fireEvent.click(getByTestId("gate-respond-send"));
    expect(postGateDecision).not.toHaveBeenCalled();

    fireEvent.change(getByTestId("gate-respond-text"), { target: { value: "Needs another pass." } });
    fireEvent.click(getByTestId("gate-respond-send"));

    await waitFor(() =>
      expect(postGateDecision).toHaveBeenCalledWith("LC1", "reject", {
        gateId: "G1",
        note: "Needs another pass.",
      }),
    );
    expect(postOperatorInbox).toHaveBeenCalledWith({
      lifecycleId: "LC1",
      gateId: "G1",
      ask: expect.stringContaining("Ship it?"),
      response: "Rejected by developer in dashboard.\n\nReason:\nNeeds another pass.",
    });
  });

  it("does not render the obsolete message-only Chat response path", async () => {
    const { getByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    expect(() => getByTestId("gate-respond-chat")).toThrow();
    expect(postGateDecision).not.toHaveBeenCalled();
    expect(postOperatorInbox).not.toHaveBeenCalled();
  });

  it("dismisses the current gate without notifying the agent", async () => {
    const { getByTestId, queryByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    fireEvent.click(getByTestId("gate-respond-dismiss"));

    await waitFor(() =>
      expect(postGateDecision).toHaveBeenCalledWith("LC1", "cancel", {
        gateId: "G1",
        note: "Dismissed by developer in dashboard.",
      }),
    );
    await waitFor(() => expect(queryByTestId("gate-respond-dialog")).toBeNull());
    expect(postOperatorInbox).not.toHaveBeenCalled();
    expect(deliverToSession).not.toHaveBeenCalled();
  });

  it.each(PREVIEW_GATES)("renders a readable $name request preview", ({ gate, expected }) => {
    const { getByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={gate} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    const previewText = getByTestId("gate-request").textContent ?? "";

    for (const text of expected) expect(previewText).toContain(text);
    expect(previewText).not.toContain("{");
    expect(getByTestId("gate-request-diagnostics").textContent).toContain("Gate packet");
  });

  it("does not notify the agent when the targeted gate is stale", async () => {
    vi.mocked(postGateDecision).mockResolvedValue("stale-gate");
    const { getByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    fireEvent.click(getByTestId("gate-respond-yes"));

    await waitFor(() =>
      expect(getByTestId("gate-respond-status").textContent).toContain("replaced by a newer request"),
    );
    expect(postOperatorInbox).not.toHaveBeenCalled();
    expect(deliverToSession).not.toHaveBeenCalled();
  });

  it("can attach the active untagged hosted chat before responding", async () => {
    sessionStore.getState().add("Terminal", "s1");
    const { getByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    fireEvent.click(getByTestId("gate-respond-attach-active"));
    expect(findSessionForLifecycle("LC1")?.id).toBe("s1");

    fireEvent.click(getByTestId("gate-respond-yes"));
    await waitFor(() => expect(postOperatorInbox).toHaveBeenCalled());
    expect(deliverToSession).not.toHaveBeenCalled();
  });

  it("surfaces a reopened gate's adapterDecisionFailure: prior answer, proven-undelivered honesty, and reason (M6)", () => {
    const reopened: GateNode = {
      ...GATE,
      id: "REOPENED-1",
      kind: "agent-question",
      state: "open",
      packet: {
        adapterInteraction: { sessionId: "s1", interactionId: "approval-1" },
        adapterDecisionFailure: {
          delivery: "not-sent",
          decision: "rejected",
          decisionNote: "Use the shorter commit message.",
          reason: "control bridge is not connected",
          attempts: 3,
        },
      },
    };
    const { getByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={reopened} />);
    const notice = getByTestId("gate-decision-failure");
    expect(getByTestId("gate-decision-failure-lead").textContent).toContain(
      "previous rejection could not be applied",
    );
    expect(getByTestId("gate-decision-failure-answer").textContent).toContain(
      "Use the shorter commit message.",
    );
    expect(getByTestId("gate-decision-failure-delivery").textContent).toContain("never received it");
    // Honesty: a proven-undelivered answer must NOT read as maybe-delivered.
    expect(notice.textContent).not.toContain("may already hold it");
    expect(getByTestId("gate-decision-failure-reason").textContent).toContain(
      "control bridge is not connected",
    );
  });

  it("does NOT surface a failure notice on a normal open gate (no adapterDecisionFailure) (M6)", () => {
    const { queryByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);
    expect(queryByTestId("gate-decision-failure")).toBeNull();
  });

  it("states may-already-hold honesty for an unknown-delivery reopen, and invents no prior answer (M6)", () => {
    const reopened: GateNode = {
      ...GATE,
      id: "REOPENED-2",
      kind: "agent-question",
      state: "open",
      packet: {
        adapterDecisionFailure: {
          delivery: "unknown",
          decision: "approved",
          reason: "control write timed out after dispatch",
        },
      },
    };
    const { getByTestId, queryByTestId } = render(
      <GateResponder lifecycleId="LC1" gateNode={reopened} />,
    );
    expect(getByTestId("gate-decision-failure-lead").textContent).toContain(
      "previous approval could not be applied",
    );
    expect(getByTestId("gate-decision-failure-delivery").textContent).toContain("may already hold it");
    // A bare approval carried no note — the answer block is absent, never fabricated.
    expect(queryByTestId("gate-decision-failure-answer")).toBeNull();
  });

  it("uses the durable adapter-interaction gate as the sole response source", async () => {
    const adapterGate: GateNode = {
      ...GATE,
      id: "ADAPTER-1",
      kind: "agent-question",
      packet: { adapterInteraction: { sessionId: "s1", interactionId: "approval-1" } },
    };
    const { getByTestId } = render(
      <GateResponder lifecycleId="LC1" gateNode={adapterGate} />,
    );
    fireEvent.click(getByTestId("gate-respond-open"));
    fireEvent.click(getByTestId("gate-respond-yes"));
    await waitFor(() => expect(postGateDecision).toHaveBeenCalled());
    expect(postOperatorInbox).not.toHaveBeenCalled();
    expect(deliverToSession).not.toHaveBeenCalled();
  });
});
