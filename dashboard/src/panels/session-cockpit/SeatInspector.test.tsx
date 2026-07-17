// SeatInspector L6 additions: the two-archetype pane fact, the retire stop residual
// (informational, on a successfully retired row), and the raw pending-interaction payload the
// InteractionBar's unrepresentable fallback points at. L4 adds the SET LEDGER section whose
// expansion IS the acknowledging view (R6/F22).
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { fromTerminalSessionInfo } from "../../data/sessions";
import {
  L6_CONTROLLED_WORKING,
  L6_INTERACTION_UNREPRESENTABLE,
  L6_LEGACY_RAW,
  L6_RETIRED_WITH_STOP_ERROR,
} from "../../test/fixtures/catalogRows";
import { SeatInspector, setLedgerEntryLine } from "./SeatInspector";

afterEach(cleanup);
beforeEach(() => sessionCockpitStore.setState({ focusedSessionId: null, perSession: {} }));

describe("SeatInspector (L6)", () => {
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

describe("SeatInspector set ledger (L4 R6)", () => {
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

  it("EXPANDING the ledger is the viewing act that acknowledges (R6/F22)", async () => {
    const seat = session();
    seedLedger(seat.id);
    const cockpit = () => sessionCockpitStore.getState().perSession[seat.id];
    const { getByTestId, getAllByTestId, rerender } = render(
      <SeatInspector session={seat} cockpit={cockpit()} />,
    );
    const toggle = getByTestId("inspector-set-ledger-toggle");
    expect(toggle.textContent).toContain("2 set changes");
    expect(toggle.textContent).toContain("1 unacknowledged");
    // Merely rendering the inspector did NOT acknowledge — expansion is the explicit act.
    expect(cockpit().setLedger[0].acknowledged).toBe(false);
    fireEvent.click(toggle);
    await waitFor(() => expect(cockpit().setLedger[0].acknowledged).toBe(true));
    rerender(<SeatInspector session={seat} cockpit={cockpit()} />);
    // Latest first; every line carries the acceptance WORD and keeps requested ≠ effective.
    const lines = getAllByTestId("inspector-set-ledger-entry").map((node) => node.textContent);
    expect(lines[0]).toContain("queued: model requested gpt-5.6-terra");
    expect(lines[1]).toContain("echo-verified: effort requested max → effective high");
    expect(lines[1]).toContain("thinking level clamped by the model");
  });

  it("switching seats collapses the ledger without acknowledging the new seat", async () => {
    const firstSeat = session();
    const secondSeat = fromTerminalSessionInfo(L6_LEGACY_RAW);
    seedLedger(firstSeat.id);
    seedLedger(secondSeat.id);
    const cockpit = (sessionId: string) =>
      sessionCockpitStore.getState().perSession[sessionId];
    const { getByTestId, rerender } = render(
      <SeatInspector session={firstSeat} cockpit={cockpit(firstSeat.id)} />,
    );

    fireEvent.click(getByTestId("inspector-set-ledger-toggle"));
    await waitFor(() => expect(cockpit(firstSeat.id).setLedger[0].acknowledged).toBe(true));
    rerender(<SeatInspector session={firstSeat} cockpit={cockpit(firstSeat.id)} />);
    expect(getByTestId("inspector-set-ledger-toggle").getAttribute("aria-expanded")).toBe("true");

    rerender(<SeatInspector session={secondSeat} cockpit={cockpit(secondSeat.id)} />);
    await waitFor(() =>
      expect(getByTestId("inspector-set-ledger-toggle").getAttribute("aria-expanded")).toBe(
        "false",
      ),
    );
    expect(cockpit(secondSeat.id).setLedger[0].acknowledged).toBe(false);
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
