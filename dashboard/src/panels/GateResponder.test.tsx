import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { postOperatorInbox } from "../data/operatorInbox";
import { deliverToSession, findSessionForLifecycle, sessionStore } from "../data/sessions";
import type { GateNode } from "../types/projection";
import { GateResponder } from "./GateResponder";

vi.mock("../data/operatorInbox", () => ({ postOperatorInbox: vi.fn() }));

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
  vi.mocked(postOperatorInbox).mockResolvedValue("posted");
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

  it("queues a response in the external inbox when no hosted session is attached", async () => {
    const { getByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    expect(getByTestId("gate-route").textContent).toContain("External inbox for LC1");
    fireEvent.click(getByTestId("gate-respond-yes"));

    await waitFor(() =>
      expect(postOperatorInbox).toHaveBeenCalledWith({
        lifecycleId: "LC1",
        gateId: "G1",
        ask: expect.stringContaining("Ship it?"),
        response: "Proceed.",
      }),
    );
    expect(getByTestId("gate-respond-status").textContent).toContain("Queued in external inbox");
    expect(deliverToSession).not.toHaveBeenCalled();
  });

  it("surfaces external inbox post failures", async () => {
    vi.mocked(postOperatorInbox).mockResolvedValue("error");
    const { getByTestId } = render(<GateResponder lifecycleId="LC1" gateNode={GATE} />);

    fireEvent.click(getByTestId("gate-respond-open"));
    fireEvent.click(getByTestId("gate-respond-yes"));

    await waitFor(() =>
      expect(getByTestId("gate-respond-status").textContent).toContain(
        "Couldn't queue external inbox response",
      ),
    );
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
