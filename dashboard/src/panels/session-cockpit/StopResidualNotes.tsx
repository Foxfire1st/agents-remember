import { css } from "../../../styled-system/css";
import { useLifecycleNotices } from "../../data/sessionLifecycle";
import { retireResidualCopy, terminateResidualCopy } from "./lifecycleCopy";

// Stop-residual rendering (260715-FEUI-L6 R5): `controlStopDetail` (terminate response) and
// `retireControlStopError` (retired row) are INFORMATIONAL facts about sessions that terminated
// or retired SUCCESSFULLY — e.g. "control command queue is stopped" from a startup-failed
// bridge whose graceful stop had nothing to talk to. They render as dismissable informational
// lines here (the terminated row itself is a tombstone the rail no longer shows), never as a
// "termination failed" state, and never silently discarded. Copy: lifecycleCopy.ts.

const stack = css({ display: "grid", gap: "0.25rem", flexShrink: 0 });
const note = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.4rem",
  fontSize: "0.68rem",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.15rem",
  minWidth: "0",
  "& > span": { minWidth: "0", overflowWrap: "anywhere" },
});
const dismiss = css({
  font: "inherit",
  fontSize: "0.64rem",
  marginLeft: "auto",
  flex: "none",
  color: "muted",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  _hover: { color: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

export function StopResidualNotes() {
  const residuals = useLifecycleNotices((state) => state.residuals);
  const dismissResidual = useLifecycleNotices((state) => state.dismissResidual);
  if (residuals.length === 0) return null;
  return (
    <div className={stack} data-testid="stop-residual-notes">
      {residuals.map((residual) => (
        <div
          key={`${residual.sessionId}-${residual.at}`}
          className={note}
          role="status"
          data-testid={`stop-residual-${residual.sessionId}`}
        >
          <span>
            {residual.kind === "terminate"
              ? terminateResidualCopy(residual.label, residual.detail)
              : retireResidualCopy(residual.label, residual.detail)}
          </span>
          <button
            type="button"
            className={dismiss}
            aria-label={`Dismiss note for ${residual.label}`}
            onClick={() => dismissResidual(residual.sessionId, residual.at)}
            data-testid="stop-residual-dismiss"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
