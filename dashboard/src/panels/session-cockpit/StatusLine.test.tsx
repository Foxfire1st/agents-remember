import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { fromTerminalSessionInfo } from "../../data/sessions";
import { L6_CONTROLLED_WORKING } from "../../test/fixtures/catalogRows";
import { StatusLine } from "./StatusLine";

beforeEach(() => sessionCockpitStore.setState({ focusedSessionId: null, perSession: {} }));
afterEach(cleanup);

describe("StatusLine", () => {
  it("renders the contractual segment order, honest UA-5 absence, elapsed, queue, and freshness", () => {
    const session = {
      ...fromTerminalSessionInfo(L6_CONTROLLED_WORKING),
      harness: "claude",
      resolvedModel: "claude/sonnet",
      resolvedEffort: "high",
    };
    const store = sessionCockpitStore.getState();
    store.setLiveSnapshot(session.id, {
      sessionId: session.id,
      fetchedAt: 500,
      payload: {
        selectedModelKey: "claude/sonnet",
        selectedEffort: "high",
        configOptions: [],
        models: [
          {
            key: "claude/sonnet",
            displayName: "Sonnet",
            resolvedModel: "claude-sonnet-4",
            description: null,
            supportsEffort: true,
            effortOptions: [],
            defaultEffort: null,
            isDefault: true,
            hidden: false,
            selectable: true,
            provider: "anthropic",
          },
        ],
      },
    });
    store.recordPendingSet(session.id, "model", {
      requestedValue: "claude/opus",
      sentAt: 900,
      phase: "inflight",
    });
    store.enqueueSubmit(session.id, {
      requestId: "queued-l7",
      text: "queued message",
      preview: "queued message",
      queuedAt: 1_000,
      expectedBridgeEpoch: "epoch-l7",
      state: "queued",
    });
    store.recordTurnObservation(session.id, "working", 1_000);
    store.setPtyWs(session.id, "connected");
    store.recordPtyOutput(session.id, 4_000);
    const cockpit = sessionCockpitStore.getState().perSession[session.id];
    const view = render(
      <StatusLine
        session={session}
        cockpit={cockpit}
        pollHealth={{ lastBeatAt: 5_000, missedBeats: 3, healthy: false }}
        now={7_000}
      />,
    );

    const ordered = Array.from(view.getByTestId("sessions-statusline").children)
      .map((node) => node.getAttribute("data-testid"))
      .filter(Boolean);
    expect(ordered.slice(0, 8)).toEqual([
      "status-harness",
      "status-pair",
      "status-state",
      "status-leaf-seat",
      "status-pending-sets",
      "status-queued-messages",
      "status-ua5-slot",
      "status-freshness",
    ]);
    expect(view.getByTestId("status-harness").textContent).toBe("harness claude");
    expect(view.getByTestId("status-pair").textContent).toContain(
      "model claude/sonnet · effort high",
    );
    expect(
      view.getByTestId("status-pair").querySelector("[data-evidence-tier='model-validated']"),
    ).not.toBeNull();
    expect(view.getByTestId("status-state").textContent).toContain("working");
    expect(view.getByTestId("status-dot").getAttribute("data-state")).toBe("working");
    expect(view.getByTestId("status-elapsed").textContent).toBe("~6s");
    expect(view.getByTestId("status-leaf-seat").textContent).toContain(
      "leaf 06_pty-stage-interactions-lifecycle · seat worker",
    );
    expect(view.getByTestId("status-pending-sets").textContent).toBe("pending sets 1/2");
    expect(view.getByTestId("status-queued-messages").textContent).toBe(
      "queued messages 1 yours",
    );
    expect(view.getByTestId("status-ua5-slot").textContent).toBe(
      "ctx — / cost — (UA-5 slot)",
    );
    expect(view.getByTestId("status-freshness").textContent).toContain(
      "pty ws ✓ · quiet 3s · poll stale · missed 3 · beat age 2s",
    );
    expect(view.getByTestId("sessions-statusline").textContent).toContain(
      "ctrl+k palette · ? keys · F6 regions",
    );
  });

  it("keeps the reserved usage slot and absent seat facts visible without inventing evidence", () => {
    const view = render(
      <StatusLine
        session={undefined}
        cockpit={undefined}
        pollHealth={{ lastBeatAt: null, missedBeats: 0, healthy: true }}
        now={1_000}
      />,
    );
    expect(view.getByTestId("status-harness").textContent).toBe("harness —");
    expect(view.getByTestId("status-pair").textContent).toBe("model — · effort —");
    expect(view.getByTestId("status-state").textContent).toBe("—");
    expect(view.getByTestId("status-leaf-seat").textContent).toBe("leaf — · seat —");
    expect(view.getByTestId("status-ua5-slot").textContent).toBe(
      "ctx — / cost — (UA-5 slot)",
    );
  });
});
