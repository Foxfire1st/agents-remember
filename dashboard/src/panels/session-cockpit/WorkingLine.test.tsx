// The WorkingLine (260715-FEUI-L6 R6, spec §1.2-2): single home of turn theater — rendered only
// while working, plain "working" (never whimsy), ~-labeled tabular elapsed, the UA-7-gated stop
// control welded in, slow-pulse glyph only.
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { fromTerminalSessionInfo } from "../../data/sessions";
import type { PerSessionCockpit } from "../../data/sessionCockpitStore";
import { PULSE_ANIMATION } from "../../data/stateGrammar";
import { L6_CONTROLLED_WORKING } from "../../test/fixtures/catalogRows";
import { STOP_TURN_DISABLED_REASON } from "./lifecycleCopy";
import { formatApproxElapsed, WorkingLine } from "./WorkingLine";

afterEach(cleanup);

const NOW = 1_800_000_000_000;

function cockpitWorkingSince(at: number | null): PerSessionCockpit {
  return {
    snapshotLoading: false,
    echoEvidence: {},
    pendingSets: {},
    setLedger: [],
    launchEvidence: { tier: "pending" },
    composer: { draft: "", draftRevision: 0 },
    surfaceTab: "terminal",
    turnClock: { workingSince: at, lastObservedTurnState: "working" },
    freshness: { ptyWs: "none", lastOutputAt: null },
    queue: [],
  };
}

const workingSession = () => fromTerminalSessionInfo(L6_CONTROLLED_WORKING);

describe("formatApproxElapsed", () => {
  it("is ~-labeled at every magnitude", () => {
    expect(formatApproxElapsed(9_000)).toBe("~9s");
    expect(formatApproxElapsed(119_000)).toBe("~119s");
    expect(formatApproxElapsed(134_000)).toBe("~2m14s");
    expect(formatApproxElapsed(3_720_000)).toBe("~1h02m");
    expect(formatApproxElapsed(-5)).toBe("~0s");
  });
});

describe("WorkingLine", () => {
  it("renders ONLY while the seat state is working", () => {
    const ended = fromTerminalSessionInfo({ ...L6_CONTROLLED_WORKING, turnState: "turn-ended" });
    const { queryByTestId, rerender } = render(
      <WorkingLine session={ended} cockpit={cockpitWorkingSince(null)} now={NOW} />,
    );
    expect(queryByTestId("working-line")).toBeNull();
    rerender(
      <WorkingLine session={workingSession()} cockpit={cockpitWorkingSince(NOW - 134_000)} now={NOW} />,
    );
    expect(queryByTestId("working-line")).not.toBeNull();
  });

  it("says plain 'working' when no real activity form is known — never a whimsy verb", () => {
    const { getByTestId } = render(
      <WorkingLine session={workingSession()} cockpit={cockpitWorkingSince(NOW)} now={NOW} />,
    );
    expect(getByTestId("working-line-verb").textContent).toBe("working");
  });

  it("shows the ~elapsed from the client turnClock, omitting it when unobserved", () => {
    const { getByTestId, queryByTestId, rerender } = render(
      <WorkingLine session={workingSession()} cockpit={cockpitWorkingSince(NOW - 134_000)} now={NOW} />,
    );
    expect(getByTestId("working-line-elapsed").textContent).toBe("~2m14s");
    expect(getByTestId("working-line-elapsed").getAttribute("title")).toContain("sweep-bounded");
    rerender(
      <WorkingLine session={workingSession()} cockpit={cockpitWorkingSince(null)} now={NOW} />,
    );
    expect(queryByTestId("working-line-elapsed")).toBeNull();
  });

  it("welds in the Stop-turn control, disabled with the UA-7 reason", () => {
    const { getByTestId } = render(
      <WorkingLine session={workingSession()} cockpit={cockpitWorkingSince(NOW)} now={NOW} />,
    );
    const stop = getByTestId("working-line-stop") as HTMLButtonElement;
    expect(stop.disabled).toBe(true);
    expect(stop.getAttribute("data-disabled-reason")).toBe(STOP_TURN_DISABLED_REASON);
    expect(stop.getAttribute("title")).toContain("UA-7");
  });

  it("spinner is the slow-pulse glyph only (aria-hidden, the ruled 2.4 s ease-in-out)", () => {
    const { getByTestId } = render(
      <WorkingLine session={workingSession()} cockpit={cockpitWorkingSince(NOW)} now={NOW} />,
    );
    const spinner = getByTestId("working-line-spinner");
    expect(spinner.getAttribute("aria-hidden")).toBe("true");
    expect(spinner.textContent).toBe("◐");
    // The Panda literal must stay pinned to the grammar's ruled pulse string.
    expect(PULSE_ANIMATION).toBe("pulseSlow 2.4s ease-in-out infinite");
  });
});
