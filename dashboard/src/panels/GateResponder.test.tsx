import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deliverToSession, findSessionForLifecycle, sessionStore } from "../data/sessions";
import type { GateNode } from "../types/projection";
import { GateResponder } from "./GateResponder";

vi.mock("../data/sessions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/sessions")>();
  return { ...actual, deliverToSession: vi.fn() };
});

const GATE: GateNode = {
  id: "G1",
  kind: "closeout-approval",
  state: "open",
  decisions: [],
  packet: { question: "Ship it?", changedPaths: 3 },
  ts: "2026-06-23T10:00:00+00:00",
};

beforeEach(() => {
  vi.mocked(deliverToSession).mockResolvedValue("delivered");
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
});

describe("GateResponder", () => {
  it("routes Yes to the hosted chat attached to the lifecycle", async () => {
    sessionStore.getState().add("Claude Code", "s1", "LC1");
    const { getByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    expect(getByTestId("gate-request").textContent).toContain("changedPaths");
    expect(getByTestId("gate-route").textContent).toContain("Claude Code 1");
    fireEvent.click(getByTestId("gate-respond-yes"));

    await waitFor(() =>
      expect(deliverToSession).toHaveBeenCalledWith("s1", expect.stringContaining("Proceed.")),
    );
    expect(getByTestId("gate-respond-status").textContent).toContain("Sent to Claude Code 1");
  });

  it("surfaces a missing hosted session instead of silently sending", () => {
    const { getByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    expect(getByTestId("gate-route").textContent).toContain("No hosted chat attached to LC1");
    expect((getByTestId("gate-respond-yes") as HTMLButtonElement).disabled).toBe(true);
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
