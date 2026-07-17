// SeatInspector carries the L6 archetype/residual/raw-interaction evidence into L7's tab host.
// F22 is explicit: rendering or changing seats never acknowledges; only `mark seen` does.
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { fromTerminalSessionInfo } from "../../data/sessions";
import {
  L7_DECISION_PICKUP,
  L7_ESCALATED_PICKUP,
  L7_PICKUPS,
  L7_SUPERVISOR_HEARTBEAT,
} from "../../test/fixtures/busScenarios";
import {
  L6_CONTROLLED_WORKING,
  L6_INTERACTION_UNREPRESENTABLE,
  L6_LEGACY_RAW,
  L6_RETIRED_WITH_STOP_ERROR,
} from "../../test/fixtures/catalogRows";
import { SeatInspector, setLedgerEntryLine } from "./SeatInspector";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
beforeEach(() => sessionCockpitStore.setState({ focusedSessionId: null, perSession: {} }));

describe("SeatInspector (L6)", () => {
  it("exposes keyboard-navigable Evidence, Capabilities, and Bus tabs", () => {
    const { getByTestId } = render(
      <SeatInspector session={fromTerminalSessionInfo(L6_CONTROLLED_WORKING)} cockpit={undefined} />,
    );
    const evidence = getByTestId("inspector-tab-evidence");
    const capabilities = getByTestId("inspector-tab-capabilities");
    evidence.focus();
    fireEvent.keyDown(evidence, { key: "ArrowRight" });
    expect(capabilities.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(capabilities);
    expect((getByTestId("inspector-panel-evidence") as HTMLElement).hidden).toBe(true);
    expect((getByTestId("inspector-panel-capabilities") as HTMLElement).hidden).toBe(false);

    fireEvent.keyDown(capabilities, { key: "End" });
    expect(getByTestId("inspector-tab-bus").getAttribute("aria-selected")).toBe("true");
    expect((getByTestId("inspector-panel-capabilities") as HTMLElement).hidden).toBe(true);
    expect((getByTestId("inspector-panel-bus") as HTMLElement).hidden).toBe(false);
  });

  it("retains an open Bus draft across click and keyboard tabs while hiding inactive controls", () => {
    const view = render(
      <SeatInspector
        session={fromTerminalSessionInfo(L6_CONTROLLED_WORKING)}
        cockpit={undefined}
        pickups={L7_PICKUPS}
        heartbeat={L7_SUPERVISOR_HEARTBEAT}
      />,
    );
    const evidenceTab = view.getByTestId("inspector-tab-evidence");
    const capabilitiesTab = view.getByTestId("inspector-tab-capabilities");
    const busPanel = view.getByTestId("inspector-panel-bus") as HTMLElement;

    fireEvent.click(view.getByTestId("inspector-tab-bus"));
    fireEvent.click(view.getByTestId("bus-reply-toggle-inbox-decision-1"));
    fireEvent.change(view.getByTestId("bus-reply-input-inbox-decision-1"), {
      target: { value: "retain this decision across inspector tabs" },
    });

    fireEvent.click(evidenceTab);
    expect(busPanel.hidden).toBe(true);
    expect(view.queryByRole("textbox", { name: /Developer decision/ })).toBeNull();
    expect(view.queryByRole("button", { name: "post to operator inbox" })).toBeNull();
    expect(
      (view.getByTestId("bus-reply-input-inbox-decision-1") as HTMLTextAreaElement).value,
    ).toBe("retain this decision across inspector tabs");

    evidenceTab.focus();
    fireEvent.keyDown(evidenceTab, { key: "ArrowRight" });
    expect(document.activeElement).toBe(capabilitiesTab);
    expect(busPanel.hidden).toBe(true);
    expect(view.queryByRole("textbox", { name: /Developer decision/ })).toBeNull();

    fireEvent.keyDown(capabilitiesTab, { key: "ArrowRight" });
    expect(document.activeElement).toBe(view.getByTestId("inspector-tab-bus"));
    expect(busPanel.hidden).toBe(false);
    expect(view.getByRole("tabpanel", { name: "Bus" })).toBe(busPanel);
    expect(
      (view.getByRole("textbox", { name: /Developer decision/ }) as HTMLTextAreaElement).value,
    ).toBe("retain this decision across inspector tabs");
    expect(view.getByTestId("bus-reply-toggle-inbox-decision-1").getAttribute("aria-expanded")).toBe(
      "true",
    );
  });

  it("settles posted and error replies on their exact entries while the Bus tab is inactive", async () => {
    let resolvePosted: (response: Response) => void = () => {};
    let resolveError: (response: Response) => void = () => {};
    const postedRequest = new Promise<Response>((resolve) => {
      resolvePosted = resolve;
    });
    const errorRequest = new Promise<Response>((resolve) => {
      resolveError = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(postedRequest)
      .mockReturnValueOnce(errorRequest);
    vi.stubGlobal("fetch", fetchMock);
    const view = render(
      <SeatInspector
        session={fromTerminalSessionInfo(L6_CONTROLLED_WORKING)}
        cockpit={undefined}
        pickups={[L7_DECISION_PICKUP, L7_ESCALATED_PICKUP]}
        heartbeat={L7_SUPERVISOR_HEARTBEAT}
      />,
    );

    fireEvent.click(view.getByTestId("inspector-tab-bus"));
    fireEvent.click(view.getByTestId("bus-reply-toggle-inbox-decision-1"));
    fireEvent.change(view.getByTestId("bus-reply-input-inbox-decision-1"), {
      target: { value: "post this exact decision" },
    });
    fireEvent.click(view.getByTestId("bus-reply-submit-inbox-decision-1"));
    fireEvent.click(view.getByTestId("bus-reply-toggle-inbox-escalation-1"));
    fireEvent.change(view.getByTestId("bus-reply-input-inbox-escalation-1"), {
      target: { value: "retain this exact escalation reply" },
    });
    fireEvent.click(view.getByTestId("bus-reply-submit-inbox-escalation-1"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(document.getElementById("bus-reply-form-inbox-decision-1")?.getAttribute("aria-busy")).toBe(
      "true",
    );
    expect(
      document.getElementById("bus-reply-form-inbox-escalation-1")?.getAttribute("aria-busy"),
    ).toBe("true");

    const evidenceTab = view.getByTestId("inspector-tab-evidence");
    fireEvent.click(evidenceTab);
    expect((view.getByTestId("inspector-panel-bus") as HTMLElement).hidden).toBe(true);
    expect(view.queryByRole("textbox", { name: /Developer (decision|reply)/ })).toBeNull();

    await act(async () => {
      resolvePosted(new Response("{}", { status: 200 }));
      resolveError(new Response("no", { status: 500 }));
      await Promise.all([postedRequest, errorRequest]);
      await Promise.resolve();
    });

    evidenceTab.focus();
    fireEvent.keyDown(evidenceTab, { key: "End" });
    expect(document.activeElement).toBe(view.getByTestId("inspector-tab-bus"));
    expect(view.getByTestId("bus-reply-status-inbox-decision-1").textContent).toContain("posted");
    expect(
      (view.getByTestId("bus-reply-input-inbox-decision-1") as HTMLTextAreaElement).value,
    ).toBe("");
    expect(view.getByTestId("bus-reply-status-inbox-escalation-1").textContent).toContain(
      "draft is retained",
    );
    expect(
      (view.getByTestId("bus-reply-input-inbox-escalation-1") as HTMLTextAreaElement).value,
    ).toBe("retain this exact escalation reply");
  });

  it("keeps the fleet Bus reachable with no focus while seat-bound panes stay honest", () => {
    const view = render(
      <SeatInspector
        session={undefined}
        cockpit={undefined}
        pickups={L7_PICKUPS}
        heartbeat={L7_SUPERVISOR_HEARTBEAT}
      />,
    );
    expect(view.getByTestId("inspector-evidence-no-focus").textContent).toContain(
      "No focused seat",
    );

    fireEvent.click(view.getByTestId("inspector-tab-capabilities"));
    expect(view.getByTestId("inspector-capabilities-no-focus").textContent).toContain(
      "require an exact session",
    );

    fireEvent.click(view.getByTestId("inspector-tab-bus"));
    expect(view.getByTestId("bus-pickup-inbox-decision-1")).not.toBeNull();
    expect(view.getByTestId("bus-pickup-inbox-escalation-1")).not.toBeNull();
    expect(view.getByTestId("bus-focused-filter").hasAttribute("disabled")).toBe(true);
    expect(view.queryByTestId("bus-focused-empty")).toBeNull();
  });

  it("names the pane archetype for controlled vs legacy raw seats (R1)", () => {
    const { getByTestId, rerender } = render(
      <SeatInspector session={fromTerminalSessionInfo(L6_CONTROLLED_WORKING)} cockpit={undefined} />,
    );
    expect(getByTestId("inspector-archetype").textContent).toContain("runner line-log");
    rerender(
      <SeatInspector session={fromTerminalSessionInfo(L6_LEGACY_RAW)} cockpit={undefined} />,
    );
    expect(getByTestId("inspector-archetype").textContent).toContain("vendor TUI");
  });

  it("renders retireControlStopError as an informational stop note on a retired row (R5)", () => {
    const { getByTestId } = render(
      <SeatInspector
        session={fromTerminalSessionInfo(L6_RETIRED_WITH_STOP_ERROR)}
        cockpit={undefined}
      />,
    );
    expect(getByTestId("inspector-state").textContent).toBe("retired"); // retired, not failed
    const note = getByTestId("inspector-retire-stop-note");
    expect(note.textContent).toContain("informational");
    expect(note.textContent).toContain("control command queue is stopped");
    expect(note.textContent?.toLowerCase()).not.toContain("fail");
  });

  it("shows the verbatim pending-interaction payload (the unrepresentable fallback's target)", () => {
    const { getByTestId } = render(
      <SeatInspector
        session={fromTerminalSessionInfo(L6_INTERACTION_UNREPRESENTABLE)}
        cockpit={undefined}
      />,
    );
    const raw = getByTestId("inspector-pending-interaction-raw");
    expect(raw.textContent).toContain('"kind": "vendor-custom"');
    expect(raw.textContent).toContain('"opaque": true');
  });
});

describe("SeatInspector set ledger (L7 F22)", () => {
  const session = () => fromTerminalSessionInfo(L6_CONTROLLED_WORKING);

  function seedLedger(sessionId: string) {
    const store = sessionCockpitStore.getState();
    store.appendSetLedger(sessionId, {
      at: 1,
      kind: "effort",
      requestedValue: "max",
      result: {
        acceptance: "echo-verified",
        requestedValue: "max",
        effectiveValue: "high",
        detail: "thinking level clamped by the model",
      },
      acknowledged: false,
    });
    store.appendSetLedger(sessionId, {
      at: 2,
      kind: "model",
      requestedValue: "gpt-5.6-terra",
      result: { acceptance: "queued", requestedValue: "gpt-5.6-terra" },
      acknowledged: true,
    });
  }

  it("viewing the ledger does not acknowledge; the explicit mark-seen action does (F22)", async () => {
    const seat = session();
    seedLedger(seat.id);
    const cockpit = () => sessionCockpitStore.getState().perSession[seat.id];
    const { getByTestId, getAllByTestId, rerender } = render(
      <SeatInspector session={seat} cockpit={cockpit()} />,
    );
    expect(getByTestId("inspector-set-ledger-section").textContent).toContain("2 set changes");
    expect(getByTestId("inspector-set-ledger-section").textContent).toContain("1 unacknowledged");
    // Rendering the full Evidence pane is a view, never an acknowledgment side effect.
    expect(cockpit().setLedger[0].acknowledged).toBe(false);
    // Latest first; every line carries the acceptance WORD and keeps requested ≠ effective.
    const lines = getAllByTestId("inspector-set-ledger-item").map((node) => node.textContent);
    expect(lines[0]).toContain("queued: model requested gpt-5.6-terra");
    expect(lines[1]).toContain("echo-verified: effort requested max → effective high");
    expect(lines[1]).toContain("thinking level clamped by the model");
    expect(cockpit().setLedger[0].acknowledged).toBe(false);

    fireEvent.click(getByTestId("inspector-set-ledger-mark-seen"));
    await waitFor(() => expect(cockpit().setLedger[0].acknowledged).toBe(true));
    rerender(<SeatInspector session={seat} cockpit={cockpit()} />);
    expect(getByTestId("inspector-set-ledger-section").textContent).toContain("0 unacknowledged");
  });

  it("switching seats never acknowledges the newly focused seat", async () => {
    const firstSeat = session();
    const secondSeat = fromTerminalSessionInfo(L6_LEGACY_RAW);
    seedLedger(firstSeat.id);
    seedLedger(secondSeat.id);
    const cockpit = (sessionId: string) =>
      sessionCockpitStore.getState().perSession[sessionId];
    const { getByTestId, rerender } = render(
      <SeatInspector session={firstSeat} cockpit={cockpit(firstSeat.id)} />,
    );

    fireEvent.click(getByTestId("inspector-set-ledger-mark-seen"));
    await waitFor(() => expect(cockpit(firstSeat.id).setLedger[0].acknowledged).toBe(true));

    rerender(<SeatInspector session={secondSeat} cockpit={cockpit(secondSeat.id)} />);
    expect(cockpit(secondSeat.id).setLedger[0].acknowledged).toBe(false);
    expect(getByTestId("inspector-set-ledger-mark-seen")).not.toBeNull();
  });

  it("setLedgerEntryLine marks unacknowledged entries in words", () => {
    expect(
      setLedgerEntryLine({
        at: 1,
        kind: "model",
        requestedValue: "ghost",
        result: { acceptance: "unsupported", requestedValue: "ghost", detail: "absent from catalog" },
        acknowledged: false,
      }),
    ).toBe("unsupported: model requested ghost — absent from catalog · unacknowledged");
  });
});
