import { css } from "../../../styled-system/css";
import type { OpenSession } from "../../data/sessions";
import { seatVisualState } from "../../data/stateGrammar";

// Exited and retired rows are catalog evidence, not empty terminals. This focused-stage overview
// makes that terminal boundary explicit while PtySurface keeps any other inspectable pane mounted.

const state = css({
  height: "100%",
  minHeight: "0",
  display: "grid",
  placeItems: "center",
  padding: "1rem",
  color: "muted",
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  textAlign: "center",
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "-2px",
  },
});
const facts = css({ display: "grid", gap: "0.35rem", maxWidth: "36rem" });
const heading = css({
  color: "amber",
  fontSize: "0.9rem",
  letterSpacing: "0.06em",
});
const label = css({ color: "ink", fontSize: "0.78rem" });
const evidence = css({ fontSize: "0.68rem", overflowWrap: "anywhere" });

export function EndedSessionState({ session }: { session: OpenSession }) {
  const word = seatVisualState(session).word;
  const detail = session.retiredReason ?? session.exitEvidence;
  return (
    <div
      className={state}
      role="status"
      tabIndex={-1}
      data-focus-target
      data-ended-status={session.status}
      data-testid="sessions-ended-state"
    >
      <div className={facts}>
        <strong className={heading}>Chat ended</strong>
        <span className={label}>
          {session.label} · {word}
        </span>
        {detail ? <span className={evidence}>{detail}</span> : null}
        <span className={evidence}>
          No live terminal is attached; messaging is unavailable for this ended
          chat.
        </span>
      </div>
    </div>
  );
}
