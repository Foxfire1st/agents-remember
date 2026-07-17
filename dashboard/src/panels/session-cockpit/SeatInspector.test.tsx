// SeatInspector L6 additions: the two-archetype pane fact, the retire stop residual
// (informational, on a successfully retired row), and the raw pending-interaction payload the
// InteractionBar's unrepresentable fallback points at.
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { fromTerminalSessionInfo } from "../../data/sessions";
import {
  L6_CONTROLLED_WORKING,
  L6_INTERACTION_UNREPRESENTABLE,
  L6_LEGACY_RAW,
  L6_RETIRED_WITH_STOP_ERROR,
} from "../../test/fixtures/catalogRows";
import { SeatInspector } from "./SeatInspector";

afterEach(cleanup);

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
