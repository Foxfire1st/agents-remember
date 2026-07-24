import { useEffect, useState } from "react";

import { css } from "../../../../styled-system/css";
import { useActiveConversation } from "../../../data/conversation/store";
import { formatApproxElapsed } from "../WorkingLine";

// The conversation-driven working line: the SAME fixed turn-theater anatomy as
// WorkingLine (`◐ <state word> · ~elapsed · ⏹ stop`), sourced from the focused session's LIVE
// conversation projection instead of the sweep-bounded catalog. The status mutation rides the SSE
// stream (the projector polls the bridge at 1 s), so the line reacts near-instantly after a send
// instead of up to ~12.5 s late. The SessionsView slot mounts THIS line only while the stream is
// live; anything else falls back to the catalog-driven WorkingLine.
//   - Visible ONLY while projection.stream === "live" AND the projected turn state is one of
//     working/settling/retrying/compacting — the exact set seat_turn_state_for maps onto the seat
//     "working" dot, so line and rail can never disagree. The state word is wire vocabulary only:
//     plain "working", else the canonical state word — never a whimsy verb (spec §1.1-10).
//   - ~elapsed comes from status.turn.stateSince — the DAEMON-OBSERVED transition timestamp
//     (projector-poll bounded ≤1 s), so the `~` stays the honesty marker, tabular-nums; omitted
//     when stateSince is null.
//   - A stale freshness block appends a visible `stale` marker — derived staleness is labeled,
//     never presented as observed.
//   - The line is a PURE status cue — the ⏹ stop control moved to the
//     composer footer beside send (SessionComposer), reading the same ConversationInterrupt.

const line = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.5rem",
  fontSize: "0.72rem",
  color: "muted",
  paddingBlock: "0.1rem",
});
const glyph = css({
  color: "cyan",
  flex: "none",
  // The same literal StateDot pins (stateGrammar.PULSE_ANIMATION) — Panda needs the string.
  animation: "pulseSlow 2.4s ease-in-out infinite",
  _motionReduce: { animation: "none" },
});
const verb = css({ color: "ink" });
const elapsed = css({ fontVariantNumeric: "tabular-nums" });
const staleMarker = css({ color: "amber", flex: "none" });

/** The turn states the canonical projection maps onto seat "working" (status.py seat_turn_state_for). */
const WORKING_TURN_STATES = new Set(["working", "settling", "retrying", "compacting"]);

export function ConversationWorkingLine({
  sessionId,
  now,
}: {
  sessionId: string;
  /** Test seam: freezes the clock; production ticks itself. */
  now?: number;
}) {
  const projection = useActiveConversation((state) => state.bySession[sessionId]);
  const status = projection?.status;
  const turnState = status?.turn.state;
  const working =
    projection?.stream === "live" &&
    turnState !== undefined &&
    WORKING_TURN_STATES.has(turnState);
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    if (!working || now !== undefined) return undefined;
    const interval = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [working, now]);
  if (!working || status === undefined) return null;

  const at = now ?? tick;
  const stateSince = status.turn.stateSince;
  const since = stateSince === null ? null : Date.parse(stateSince);

  return (
    <div className={line} data-testid="working-line">
      <span className={glyph} aria-hidden="true" data-testid="working-line-spinner">
        ◐
      </span>
      <span className={verb} data-testid="working-line-verb">
        {turnState === "working" ? "working" : turnState}
      </span>
      {since !== null ? (
        <span
          className={elapsed}
          data-testid="working-line-elapsed"
          title="daemon-observed at the turn-state transition — projector-poll bounded (≤1 s), hence the ~"
        >
          {formatApproxElapsed(at - since)}
        </span>
      ) : null}
      {status.freshness.state === "stale" ? (
        <span className={staleMarker} data-testid="working-line-stale">
          stale
        </span>
      ) : null}
    </div>
  );
}
