// The conversation-driven working line: SSE-sourced turn theater — live-stream
// gating on the projected turn state, daemon-observed ~elapsed from stateSince, canonical wire
// state words (never whimsy), and the visible stale-freshness marker. A PURE status cue —
// the stop control lives in the composer beside send.
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  emptyProjection,
  type ActiveConversationProjection,
} from "../../../data/conversation/reducer";
import { activeConversationStore } from "../../../data/conversation/store";
import type {
  ActiveConversationRef,
  ConversationStatus,
} from "../../../data/conversation/types";
import { ConversationWorkingLine } from "./ConversationWorkingLine";

afterEach(() => {
  cleanup();
  activeConversationStore.getState().reset();
});

const NOW = 1_800_000_000_000;

const IDENTITY: ActiveConversationRef = {
  harnessId: "codex",
  vendorConversationId: "v",
  projectScope: "/r",
  identityDigest: "d",
  arSessionId: "s1",
  bridgeEpoch: "e1",
};

function status(
  turnState: ConversationStatus["turn"]["state"],
  stateSince: string | null,
  freshnessState: ConversationStatus["freshness"]["state"] = "fresh",
): ConversationStatus {
  return {
    identity: IDENTITY,
    revision: 1,
    observedAt: "2026-07-20T00:00:00Z",
    freshness: {
      state: freshnessState,
      lastEvidenceAt: null,
      ageMs: null,
      staleAfterMs: 1,
      observationBound: "poll",
    },
    process: { state: "connected", generation: "g" },
    turn: { state: turnState, turnId: "turn-1", stateSince },
    evidence: { strength: "exact", origin: "codex" },
  };
}

function seed(overrides: Partial<ActiveConversationProjection>): void {
  activeConversationStore.setState({
    bySession: { s1: { ...emptyProjection(IDENTITY), ...overrides } },
  });
}

describe("ConversationWorkingLine", () => {
  it("renders the fixed anatomy on a live working turn, ~elapsed derived from stateSince", () => {
    seed({ stream: "live", status: status("working", new Date(NOW - 134_000).toISOString()) });
    const { getByTestId } = render(<ConversationWorkingLine sessionId="s1" now={NOW} />);
    expect(getByTestId("working-line-verb").textContent).toBe("working");
    const elapsed = getByTestId("working-line-elapsed");
    expect(elapsed.textContent).toBe("~2m14s");
    // The honesty copy names the daemon-observed source — never the catalog's bounded staleness.
    expect(elapsed.getAttribute("title")).toContain("daemon-observed");
    expect(getByTestId("working-line-spinner").textContent).toBe("◐");
  });

  it("shows the canonical wire word for the other working-set states — never an invented verb", () => {
    seed({ stream: "live", status: status("settling", null) });
    const { getByTestId, rerender } = render(<ConversationWorkingLine sessionId="s1" now={NOW} />);
    expect(getByTestId("working-line-verb").textContent).toBe("settling");
    act(() => seed({ stream: "live", status: status("retrying", null) }));
    rerender(<ConversationWorkingLine sessionId="s1" now={NOW} />);
    expect(getByTestId("working-line-verb").textContent).toBe("retrying");
    act(() => seed({ stream: "live", status: status("compacting", null) }));
    rerender(<ConversationWorkingLine sessionId="s1" now={NOW} />);
    expect(getByTestId("working-line-verb").textContent).toBe("compacting");
  });

  it("renders ONLY while the stream is live AND the turn state is in the working set", () => {
    seed({ stream: "reconnecting", status: status("working", null) });
    const { queryByTestId, rerender } = render(<ConversationWorkingLine sessionId="s1" now={NOW} />);
    expect(queryByTestId("working-line")).toBeNull();
    act(() => seed({ stream: "live", status: status("ready", null) }));
    rerender(<ConversationWorkingLine sessionId="s1" now={NOW} />);
    expect(queryByTestId("working-line")).toBeNull();
    act(() => seed({ stream: "live", status: status("working", null) }));
    rerender(<ConversationWorkingLine sessionId="s1" now={NOW} />);
    expect(queryByTestId("working-line")).not.toBeNull();
  });

  it("omits elapsed when stateSince is null", () => {
    seed({ stream: "live", status: status("working", null) });
    const { queryByTestId } = render(<ConversationWorkingLine sessionId="s1" now={NOW} />);
    expect(queryByTestId("working-line-elapsed")).toBeNull();
  });

  it("appends the visible stale marker ONLY on a stale freshness block", () => {
    seed({ stream: "live", status: status("working", null, "stale") });
    const { getByTestId, queryByTestId, rerender } = render(
      <ConversationWorkingLine sessionId="s1" now={NOW} />,
    );
    expect(getByTestId("working-line-stale").textContent).toBe("stale");
    act(() => seed({ stream: "live", status: status("working", null, "fresh") }));
    rerender(<ConversationWorkingLine sessionId="s1" now={NOW} />);
    expect(queryByTestId("working-line-stale")).toBeNull();
  });

  it("renders NO stop control (F-at: the ⏹ stop lives in the composer beside send)", () => {
    seed({ stream: "live", status: status("working", null) });
    const { queryByTestId, getByTestId } = render(
      <ConversationWorkingLine sessionId="s1" now={NOW} />,
    );
    // The line is a pure status cue; the stop action moved to
    // SessionComposer's footer (session-composer-stop), reading the same interrupt evidence.
    expect(getByTestId("working-line")).not.toBeNull();
    expect(queryByTestId("working-line-stop")).toBeNull();
  });
});
