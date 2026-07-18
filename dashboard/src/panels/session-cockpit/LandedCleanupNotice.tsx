import { useState } from "react";

import { css } from "../../../styled-system/css";
import {
  endLandedDetailed,
  useLifecycleNotices,
} from "../../data/sessionLifecycle";
import { cleanupFailureCopy, cleanupOutcomeCopy } from "./lifecycleCopy";

// Cleanup may be launched from the rail OR the command palette. This notice deliberately sits at
// the SessionsView root rather than inside the collapsible rail, so an unavailable authority
// result can never make its exact targets and retry action disappear with the pane that launched it.

const notice = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.45rem",
  flexWrap: "wrap",
  flexShrink: 0,
  minWidth: "0",
  paddingInline: "0.5rem",
  paddingBlock: "0.2rem",
  fontSize: "0.68rem",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
});
const copy = css({ flex: "1", minWidth: "12rem", overflowWrap: "anywhere" });
const action = css({
  font: "inherit",
  color: "amber",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  cursor: "pointer",
  _disabled: { opacity: 0.55, cursor: "default" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});

export function LandedCleanupNotice() {
  const failure = useLifecycleNotices((state) => state.cleanupFailure);
  const outcome = useLifecycleNotices((state) => state.cleanupOutcome);
  const dismissFailure = useLifecycleNotices(
    (state) => state.dismissCleanupFailure,
  );
  const dismissOutcome = useLifecycleNotices(
    (state) => state.dismissCleanupOutcome,
  );
  const [retrying, setRetrying] = useState(false);

  if (failure) {
    const failureCopy = cleanupFailureCopy(failure);
    return (
      <div className={notice} role="alert" data-testid="landed-cleanup-failure">
        <span className={copy} title={failureCopy}>
          {failureCopy} · retry asks the cleanup authority for a fresh exact
          result
        </span>
        <button
          type="button"
          className={action}
          disabled={retrying}
          onClick={() => {
            if (retrying) return;
            setRetrying(true);
            void endLandedDetailed(failure.targets).finally(() =>
              setRetrying(false),
            );
          }}
          data-testid="landed-cleanup-retry"
        >
          {retrying ? "retrying…" : "retry"}
        </button>
        <button
          type="button"
          className={action}
          disabled={retrying}
          onClick={dismissFailure}
          data-testid="landed-cleanup-failure-dismiss"
        >
          dismiss
        </button>
      </div>
    );
  }

  if (!outcome) return null;
  const outcomeCopy = cleanupOutcomeCopy(outcome);
  return (
    <div className={notice} role="status" data-testid="landed-cleanup-outcome">
      <span className={copy} title={outcomeCopy}>
        {outcomeCopy}
      </span>
      <button
        type="button"
        className={action}
        onClick={dismissOutcome}
        aria-label="Dismiss cleanup outcome"
        data-testid="landed-cleanup-outcome-dismiss"
      >
        dismiss
      </button>
    </div>
  );
}
