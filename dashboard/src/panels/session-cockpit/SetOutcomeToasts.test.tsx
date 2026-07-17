// Unfocused set-outcome toasts (260715-FEUI-L4 R6, design §9.8): persistent until dismissed,
// never for the focused seat, collapsed into ONE stack when several sessions have outcomes;
// dismissing is the explicit mark-seen act.
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { fromTerminalSessionInfo, type OpenSession } from "../../data/sessions";
import { catalogRow } from "../../test/fixtures/catalogRows";
import { SetOutcomeToasts } from "./SetOutcomeToasts";

const store = sessionCockpitStore;

const seat = (id: string): OpenSession =>
  fromTerminalSessionInfo(catalogRow({ id, label: id, controlState: "ready" }));

function seedUnsupported(sessionId: string) {
  store.getState().appendSetLedger(sessionId, {
    at: 1,
    kind: "model",
    requestedValue: "ghost",
    result: { acceptance: "unsupported", requestedValue: "ghost", detail: "absent from catalog" },
    acknowledged: false,
  });
}

beforeEach(() => store.setState({ focusedSessionId: null, perSession: {} }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SetOutcomeToasts", () => {
  it("renders nothing without unacknowledged outcomes, and NEVER for the focused seat", () => {
    const sessions = [seat("a"), seat("b")];
    seedUnsupported("a");
    const { queryByTestId, rerender } = render(
      <SetOutcomeToasts sessions={sessions} focusedSessionId="a" onFocusSession={() => {}} />,
    );
    // 'a' is focused: its outcome renders in the control/ledger, not as a toast.
    expect(queryByTestId("set-outcome-toasts")).toBeNull();
    rerender(<SetOutcomeToasts sessions={sessions} focusedSessionId="b" onFocusSession={() => {}} />);
    expect(queryByTestId(`set-toast-a`)).not.toBeNull();
  });

  it("persists until dismissed; dismissing acknowledges (mark seen), view focuses the seat", async () => {
    const sessions = [seat("a"), seat("b")];
    seedUnsupported("a");
    const onFocus = vi.fn();
    // The component subscribes to the cockpit store itself — a dismiss re-renders it.
    const { getByTestId, queryByTestId } = render(
      <SetOutcomeToasts sessions={sessions} focusedSessionId="b" onFocusSession={onFocus} />,
    );
    // The toast carries the attention chip with the acceptance word.
    expect(getByTestId("set-toast-a").textContent).toContain("unsupported");
    fireEvent.click(getByTestId("set-toast-view-a"));
    expect(onFocus).toHaveBeenCalledWith("a");
    fireEvent.click(getByTestId("set-toast-dismiss-a"));
    await waitFor(() => expect(queryByTestId("set-outcome-toasts")).toBeNull());
    expect(store.getState().perSession["a"].setLedger[0].acknowledged).toBe(true);
  });

  it("SEVERAL sessions with outcomes collapse into ONE stack (§9.8 toast discipline)", () => {
    const sessions = [seat("a"), seat("b"), seat("c")];
    seedUnsupported("a");
    seedUnsupported("b");
    const { getByTestId, queryByTestId } = render(
      <SetOutcomeToasts sessions={sessions} focusedSessionId="c" onFocusSession={() => {}} />,
    );
    const collapsed = getByTestId("set-toast-collapsed");
    expect(collapsed.textContent).toContain("2 sessions with unacknowledged set outcomes");
    expect(queryByTestId("set-toast-a")).toBeNull(); // no per-session pile
  });
});
