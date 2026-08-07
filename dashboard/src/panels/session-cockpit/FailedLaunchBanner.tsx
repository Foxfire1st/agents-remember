import { useState } from "react";

import { css } from "../../../styled-system/css";
import { hydrateTerminalSessionsFromCatalog } from "../../data/catalogPoll";
import { verbatimBridgeError } from "../../data/launchEvidence";
import type { OpenSession } from "../../data/sessions";
import { terminateTerminalSession } from "../../data/terminal";
import { leafIdFromKey } from "../../data/taskIdentity";
import { EvidenceBadge } from "../../grammar/EvidenceBadge";
import type { LaunchPrefill } from "./LaunchFlow";

// The failed-launch banner (260715-FEUI-L3 R6): the runner INTENTIONALLY keeps a refused launch
// addressable — the row is never hidden — so the banner renders the sweep-projected refusal
// VERBATIM (controlRaw.bridgeError names the advertised alternatives) beside the retained pair
// at tier 'refused' (never as validated evidence). Exactly two actions: Retire (an honest
// confirm naming the session and its leaf) and 'Launch corrected…' (the flow pre-filled from
// the refused pair). NEVER an auto-retry — this component holds no timers and sends nothing
// unprompted. Uniform across ALL THREE native harnesses: the refusal path is identical for
// Claude, Codex, and Pi (the async fail-loud invariant).

const banner = css({
  display: "grid",
  gap: "0.35rem",
  flexShrink: 0,
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "color-mix(in oklch, token(colors.alarm) 55%, transparent)",
  borderRadius: "2px",
  background: "color-mix(in oklch, token(colors.alarm) 8%, transparent)",
  padding: "0.45rem 0.55rem",
  fontSize: "0.72rem",
  color: "muted",
});
const headline = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.4rem",
  color: "alarm",
  fontSize: "0.74rem",
});
const errorText = css({ color: "ink", whiteSpace: "pre-wrap", overflowWrap: "anywhere" });
const pairLine = css({ display: "flex", alignItems: "baseline", gap: "0.35rem", flexWrap: "wrap" });
const actionRow = css({ display: "flex", gap: "0.4rem", flexWrap: "wrap", alignItems: "baseline" });
const actionButton = css({
  font: "inherit",
  fontSize: "0.7rem",
  paddingInline: "0.5rem",
  paddingBlock: "0.12rem",
  background: "transparent",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _disabled: { opacity: 0.55, cursor: "not-allowed" },
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
});
const confirmBox = css({
  display: "grid",
  gap: "0.3rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  padding: "0.35rem 0.45rem",
});

export function FailedLaunchBanner({
  session,
  onLaunchCorrected,
}: {
  session: OpenSession;
  onLaunchCorrected: (prefill: LaunchPrefill) => void;
}) {
  const [arming, setArming] = useState(false);
  const [retiring, setRetiring] = useState(false);
  const [retireError, setRetireError] = useState<string | null>(null);

  const bridgeError = verbatimBridgeError(session.controlRaw);
  const hasPair = session.resolvedModel != null || session.resolvedEffort != null;

  const retire = async () => {
    setRetiring(true);
    setRetireError(null);
    const ok = await terminateTerminalSession(session.id);
    setRetiring(false);
    if (!ok) {
      setRetireError("retire failed — the server did not confirm; the row is unchanged");
      return;
    }
    setArming(false);
    await hydrateTerminalSessionsFromCatalog(false);
  };

  const correctedPrefill = (): LaunchPrefill | undefined =>
    session.harness
      ? {
          harness: session.harness,
          modelKey: session.resolvedModel ?? undefined,
          effort: session.resolvedEffort ?? undefined,
        }
      : undefined;

  return (
    <div className={banner} role="alert" data-testid="failed-launch-banner">
      <FailedLaunchSummary session={session} bridgeError={bridgeError} hasPair={hasPair} />
      <div className={actionRow}>
        <button
          type="button"
          className={actionButton}
          data-testid="failed-launch-retire"
          disabled={retiring}
          onClick={() => setArming((current) => !current)}
        >
          retire…
        </button>
        <button
          type="button"
          className={actionButton}
          data-testid="failed-launch-correct"
          disabled={!session.harness}
          onClick={() => {
            const prefill = correctedPrefill();
            if (prefill) onLaunchCorrected(prefill);
          }}
        >
          launch corrected…
        </button>
        <span>the failed row stays visible until retired — the refusal is addressable evidence</span>
      </div>
      {arming ? (
        <RetireConfirmation
          session={session}
          retiring={retiring}
          retireError={retireError}
          onRetire={() => void retire()}
          onKeep={() => setArming(false)}
        />
      ) : null}
    </div>
  );
}

function FailedLaunchSummary({
  session,
  bridgeError,
  hasPair,
}: {
  session: OpenSession;
  bridgeError: string | null;
  hasPair: boolean;
}) {
  return (
    <>
      <span className={headline}>
        <EvidenceBadge tier="refused" showWord />
        <span>launch failed — {session.harness ?? "harness"} refused the selection</span>
      </span>
      <span className={errorText} data-testid="failed-launch-bridge-error">
        {bridgeError ?? "no bridgeError retained — see the session terminal for the runner log"}
      </span>
      <span className={pairLine} data-testid="failed-launch-refused-pair">
        {hasPair ? (
          <>
            refused pair: {session.resolvedModel ?? "—"}
            {session.resolvedEffort ? ` · ${session.resolvedEffort}` : ""} (requested provenance —
            never validated)
          </>
        ) : (
          <>no selection was sent (vendor defaults) — the failure is the runner's own refusal</>
        )}
      </span>
    </>
  );
}

function RetireConfirmation({
  session,
  retiring,
  retireError,
  onRetire,
  onKeep,
}: {
  session: OpenSession;
  retiring: boolean;
  retireError: string | null;
  onRetire: () => void;
  onKeep: () => void;
}) {
  return (
    <div className={confirmBox} data-testid="failed-launch-retire-confirm">
      <span>
        retire session “{session.label}”
        {session.leafKey ? ` (leaf ${leafIdFromKey(session.leafKey)})` : ""}? This ends the
        refused runner and removes the row from the live rail.
      </span>
      <div className={actionRow}>
        <button
          type="button"
          className={actionButton}
          data-testid="failed-launch-retire-confirm-yes"
          disabled={retiring}
          onClick={onRetire}
        >
          {retiring ? "retiring…" : "retire"}
        </button>
        <button
          type="button"
          className={actionButton}
          data-testid="failed-launch-retire-confirm-no"
          disabled={retiring}
          onClick={onKeep}
        >
          keep
        </button>
      </div>
      {retireError ? (
        <span role="alert" data-testid="failed-launch-retire-error">
          {retireError}
        </span>
      ) : null}
    </div>
  );
}
