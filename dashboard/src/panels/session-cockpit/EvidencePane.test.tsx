import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { endSessionDetailed, lifecycleNoticeStore } from "../../data/sessionLifecycle";
import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { fromTerminalSessionInfo, sessionStore } from "../../data/sessions";
import { startSubmitRecord } from "../../data/submitMachine";
import {
  catalogRow,
  L6_CONTROLLED_WORKING,
  L6_TERMINATE_RESPONSE_WITH_RESIDUAL,
} from "../../test/fixtures/catalogRows";
import { EvidencePane, submitEvidenceLines } from "./EvidencePane";
import { StopResidualNotes } from "./StopResidualNotes";

beforeEach(() => {
  sessionCockpitStore.setState({ focusedSessionId: null, perSession: {} });
  lifecycleNoticeStore.setState({ residuals: [], cleanupOutcome: null, sweptRetire: {} });
  sessionStore.getState().hydrate([]);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("EvidencePane", () => {
  it("reveals launch, receipt, reconciliation, bridge, pane, residual, and liveness evidence", () => {
    const session = fromTerminalSessionInfo(
      catalogRow({
        id: "l7-evidence",
        label: "worker-l7-evidence",
        spawnRole: "worker",
        seatRole: "worker",
        resolvedModel: "claude-sonnet",
        resolvedEffort: "high",
        controlState: "ready",
        turnState: "turn-ended",
        controlRaw: {
          bridgeError: "native control refused a stale bridge epoch",
          paneDiagnostic: { kind: "line-log", rows: 42 },
          retireControlStopError: "control command queue is stopped",
        },
        retiredReason: "seat superseded",
        livenessFailures: 2,
        livenessFirstFailedAt: "2026-07-17T19:00:00Z",
        livenessLastFailedAt: "2026-07-17T19:00:10Z",
        livenessEvidence: "pane-gone",
        exitEvidence: "tmux-command-failed",
      }),
    );
    const store = sessionCockpitStore.getState();
    store.setLaunchEvidence(session.id, {
      retainedModel: "claude-sonnet",
      retainedEffort: "high",
      tier: "model-validated",
    });
    store.upsertSubmitRecord(session.id, {
      ...startSubmitRecord({
        requestId: "req-l7-1",
        text: "inspect the full receipt",
        expectedBridgeEpoch: "epoch-before",
        submittedRevision: 3,
        at: 100,
      }),
      phase: "accepted",
      updatedAt: 200,
      expectedBridgeEpoch: "epoch-after",
      serverLifecycleState: "delivered",
      receipt: {
        requestId: "req-l7-1",
        acceptance: "unknown",
        submittedAt: "2026-07-17T19:01:00Z",
        vendorCorrelationId: "vendor-submit-7",
        acceptedAt: null,
        detail: "receipt transport ended before acceptance was known",
        bridgeEpoch: "epoch-after",
      },
      reconciliation: {
        requestId: "req-l7-1",
        state: "accepted",
        reconciledAt: "2026-07-17T19:01:04Z",
        vendorCorrelationId: "vendor-submit-7",
        detail: "same request id was delivered",
        bridgeEpoch: "epoch-after",
        submissionState: "delivered",
      },
      reconcileAttempts: 2,
      reconcileWindowElapsedMs: 4_000,
    });
    const cockpit = sessionCockpitStore.getState().perSession[session.id];
    const view = render(<EvidencePane session={session} cockpit={cockpit} />);

    expect(view.getByTestId("inspector-launch-evidence").textContent).toContain("claude-sonnet");
    expect(view.getByTestId("inspector-launch-tier").textContent).toContain("model-validated");
    const receipt = view.getByTestId("inspector-submit-history-item").textContent ?? "";
    expect(receipt).toContain("request req-l7-1");
    expect(receipt).toContain("receipt unknown · vendor correlation vendor-submit-7");
    expect(receipt).toContain("accepted —");
    expect(receipt).toContain("reconciliation accepted · submission delivered");
    expect(receipt).toContain("reconcile attempts 2 · window 4000 ms");
    expect(view.getByTestId("inspector-bridge-error").textContent).toContain("stale bridge epoch");
    expect(view.getByTestId("inspector-pane-diagnostic").textContent).toContain('"rows": 42');
    expect(view.getByTestId("inspector-retire-stop-note").textContent).toContain("informational");
    expect(view.getByTestId("inspector-liveness").textContent).toBe("pane-gone");
    expect(view.getByTestId("inspector-state-vocabulary-note").textContent).toContain(
      "runner's raw vocabulary",
    );
  });

  it("keeps missing receipt evidence explicitly absent in the pure detail projection", () => {
    const lines = submitEvidenceLines(
      startSubmitRecord({
        requestId: "req-no-receipt",
        text: "lost response",
        expectedBridgeEpoch: "epoch-1",
        submittedRevision: 1,
        at: 1,
      }),
    );
    expect(lines).toContain("receipt —");
    expect(lines.join(" ")).not.toContain("accepted —");
  });

  it("keeps terminate and retire residuals inspectable without focus and shares exact dismissal", () => {
    lifecycleNoticeStore.getState().recordResidual({
      sessionId: "terminated-seat",
      label: "worker-terminated",
      kind: "terminate",
      detail: "control command queue is stopped",
      at: 100,
    });
    lifecycleNoticeStore.getState().recordResidual({
      sessionId: "retired-seat",
      label: "worker-retired",
      kind: "retire",
      detail: "retire control command queue is stopped",
      at: 200,
    });

    const view = render(
      <>
        <StopResidualNotes />
        <EvidencePane session={undefined} cockpit={undefined} />
      </>,
    );
    expect(view.getByTestId("inspector-evidence-no-focus").textContent).toContain(
      "No focused seat",
    );
    const terminate = view.getByTestId("inspector-stop-residual-terminate-terminated-seat");
    const retire = view.getByTestId("inspector-stop-residual-retire-retired-seat");
    for (const residual of [terminate, retire]) {
      expect(residual.textContent).toContain("informational");
      expect(residual.textContent?.toLowerCase()).not.toContain("fail");
    }
    expect(terminate.textContent).toContain("control command queue is stopped");
    expect(terminate.textContent).toContain("terminate / controlStopDetail");
    expect(retire.textContent).toContain("retire control command queue is stopped");
    expect(retire.textContent).toContain("retire / retireControlStopError");
    expect(view.getByTestId("inspector-stop-residual-retention").textContent).toContain(
      "until explicitly dismissed",
    );
    expect(lifecycleNoticeStore.getState().residuals).toHaveLength(2);

    fireEvent.click(view.getByTestId("inspector-stop-residual-dismiss-terminated-seat"));
    expect(view.queryByTestId("inspector-stop-residual-terminate-terminated-seat")).toBeNull();
    expect(view.queryByTestId("stop-residual-terminated-seat")).toBeNull();
    expect(view.getByTestId("inspector-stop-residual-retire-retired-seat")).not.toBeNull();

    fireEvent.click(within(view.getByTestId("stop-residual-retired-seat")).getByRole("button"));
    expect(view.queryByTestId("inspector-stop-residual-retire-retired-seat")).toBeNull();
    expect(view.getByTestId("inspector-stop-residuals-empty")).not.toBeNull();
  });

  it("reveals a successful terminate residual after the terminated seat is removed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(L6_TERMINATE_RESPONSE_WITH_RESIDUAL), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const session = fromTerminalSessionInfo(L6_CONTROLLED_WORKING);
    sessionStore.getState().hydrate([session]);

    const outcome = await endSessionDetailed(session);
    expect(outcome.ok).toBe(true);
    expect(sessionStore.getState().sessions).toHaveLength(0);

    const view = render(<EvidencePane session={undefined} cockpit={undefined} />);
    const residual = view.getByTestId(`inspector-stop-residual-terminate-${session.id}`);
    expect(residual.textContent).toContain(`${session.label} terminated`);
    expect(residual.textContent).toContain("informational");
    expect(residual.textContent?.toLowerCase()).not.toContain("fail");
  });
});
