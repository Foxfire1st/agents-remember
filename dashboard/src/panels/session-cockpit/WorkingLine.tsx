import { useEffect, useState } from "react";

import { css } from "../../../styled-system/css";
import type { OpenSession } from "../../data/sessions";
import type { PerSessionCockpit } from "../../data/sessionCockpitStore";
import { seatVisualState } from "../../data/stateGrammar";
import { STOP_TURN_DISABLED_REASON } from "./lifecycleCopy";

// The WorkingLine (260715-FEUI-L6 R6, spec §1.2-2): the SINGLE home of turn theater. Renders
// ONLY while the focused seat's state is `working`, in the reserved slot directly under the
// HeaderStrip. Anatomy, fixed: `◐ <activity form | "working"> · ~elapsed · ⏹ stop`.
//   - The activity form is shown only when a REAL one is known; otherwise the plain word
//     "working" — NEVER whimsy verbs (spec §1.1-10).
//   - ~elapsed comes from the client turnClock: the clock starts at the OBSERVED transition
//     (poll/10 s-sweep bounded), so the `~` label is the honesty marker, tabular-nums.
//   - The Stop-turn control is WELDED in at the line's fixed end position, UA-7-gated: disabled
//     with the reason until the interrupt route exists (design §9.7). Retry/compaction states
//     join this line when UA-5 exposes them.
//   - Spinner = the slow-pulse `◐` glyph only (2.4 s ease-in-out — the animation ruling): no
//     shimmer, no braille frames; frozen by html[data-effects="off"], steady under
//     prefers-reduced-motion. Turn theater NEVER renders per rail row.

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
const stopButton = css({
  font: "inherit",
  fontSize: "0.66rem",
  marginLeft: "auto",
  flex: "none",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  cursor: "not-allowed",
  opacity: 0.7,
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

/** `~`-labeled elapsed: seconds under 2 min, then `XmYYs`, then `XhYYm`. Pure, tested. */
export function formatApproxElapsed(elapsedMs: number): string {
  const seconds = Math.max(0, Math.floor(elapsedMs / 1000));
  if (seconds < 120) return `~${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `~${minutes}m${String(seconds % 60).padStart(2, "0")}s`;
  return `~${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}m`;
}

/**
 * The REAL activity form when one is known. No wire field carries one today (turn-state is a
 * bit, not a verb phrase; UA-1 reasoning headers / UA-5 states land later), so this returns
 * undefined and the line says plain "working" — never a decorative gerund.
 */
export function workingActivityForm(session: OpenSession): string | undefined {
  void session; // the seam stays typed — UA-1/UA-5 fields plug in here
  return undefined;
}

export function WorkingLine({
  session,
  cockpit,
  now,
}: {
  session: OpenSession;
  cockpit: PerSessionCockpit | undefined;
  /** Test seam: freezes the clock; production ticks itself. */
  now?: number;
}) {
  const working = seatVisualState(session).key === "working";
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    if (!working || now !== undefined) return undefined;
    const interval = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [working, now]);
  if (!working) return null;

  const at = now ?? tick;
  const since = cockpit?.turnClock.workingSince ?? null;
  const form = workingActivityForm(session);

  return (
    <div className={line} data-testid="working-line">
      <span className={glyph} aria-hidden="true" data-testid="working-line-spinner">
        ◐
      </span>
      <span className={verb} data-testid="working-line-verb">
        {form ?? "working"}
      </span>
      {since !== null ? (
        <span
          className={elapsed}
          data-testid="working-line-elapsed"
          title="client-measured from the observed turn-state transition — poll/sweep-bounded, hence the ~"
        >
          {formatApproxElapsed(at - since)}
        </span>
      ) : null}
      <button
        type="button"
        className={stopButton}
        disabled
        title={STOP_TURN_DISABLED_REASON}
        aria-label={`Stop turn — ${STOP_TURN_DISABLED_REASON}`}
        data-disabled-reason={STOP_TURN_DISABLED_REASON}
        data-testid="working-line-stop"
      >
        ⏹ stop
      </button>
    </div>
  );
}
