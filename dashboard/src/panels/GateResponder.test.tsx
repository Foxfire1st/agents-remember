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
  it("records Yes on the current gate and notifies the hosted chat", async () => {
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
      expect(deliverToSession).toHaveBeenCalledWith(
        "s1",
        expect.stringContaining("Approved by developer in dashboard."),
      ),
    );
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

  it("keeps Chat message-only and does not record a gate decision", async () => {
    const { getByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    fireEvent.click(getByTestId("gate-respond-chat"));
    fireEvent.change(getByTestId("gate-respond-text"), { target: { value: "Use a clearer commit message first." } });
    fireEvent.click(getByTestId("gate-respond-send"));

    await waitFor(() =>
      expect(postOperatorInbox).toHaveBeenCalledWith({
        lifecycleId: "LC1",
        gateId: "G1",
        ask: expect.stringContaining("Ship it?"),
        response: "Use a clearer commit message first.",
      }),
    );
    expect(postGateDecision).not.toHaveBeenCalled();
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
    await waitFor(() => expect(deliverToSession).toHaveBeenCalledWith("s1", expect.any(String)));
  });
});
