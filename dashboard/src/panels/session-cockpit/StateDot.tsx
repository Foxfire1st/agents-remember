import { cva } from "../../../styled-system/css";
import type { SeatVisualState } from "../../data/stateGrammar";

// The cockpit state dot — the ONLY renderer of data/stateGrammar visuals (R14). Every surface
// (rail rows, HeaderStrip, StatusLine in L7) renders THIS component from the same mapping, so a
// seat's dot can never disagree across surfaces. Pulse = the slow 2.4 s ease-in-out ruling;
// steady under prefers-reduced-motion (never hidden), frozen by html[data-effects="off"].
const dot = cva({
  base: {
    display: "inline-block",
    width: "0.6em",
    height: "0.6em",
    borderRadius: "full",
    flex: "none",
    background: "muted",
  },
  variants: {
    color: {
      cyan: { background: "cyan" },
      amber: { background: "amber" },
      mutedAmber: { background: "color-mix(in oklch, token(colors.amber) 60%, token(colors.muted))" },
      alarm: { background: "alarm" },
      mint: { background: "mint" },
      dormant: { background: "dormant" },
      muted: { background: "muted" },
    },
    pulse: {
      // Literal for Panda's static extraction — stateGrammar.PULSE_ANIMATION is the same string
      // (the cross-surface consistency test pins the two together).
      true: {
        animation: "pulseSlow 2.4s ease-in-out infinite",
        _motionReduce: { animation: "none" },
      },
    },
  },
});

export function StateDot({
  state,
  testId,
  ariaLabel,
}: {
  state: SeatVisualState;
  testId?: string;
  /** When given, the dot becomes an accessible image carrying the state word (L4 R8 — rail
   *  dots). Absent ⇒ aria-hidden (surfaces that render the word beside the dot, HeaderStrip). */
  ariaLabel?: string;
}) {
  return (
    <span
      className={dot({ color: state.color, pulse: state.pulse })}
      data-state={state.key}
      data-state-color={state.color}
      data-state-pulse={state.pulse ? "true" : "false"}
      data-testid={testId}
      {...(ariaLabel !== undefined
        ? { role: "img", "aria-label": ariaLabel }
        : { "aria-hidden": "true" as const })}
    />
  );
}
