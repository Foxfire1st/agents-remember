import { css } from "../../../styled-system/css";
import { launchTier } from "../../data/launchEvidence";
import type { OpenSession } from "../../data/sessions";
import type { PerSessionCockpit } from "../../data/sessionCockpitStore";
import { seatVisualState } from "../../data/stateGrammar";
import { paneArchetypeCopy, retireResidualCopy } from "./lifecycleCopy";

// The focused seat's read-only identity/provenance card (260715-FEUI-L2 R7 + R17): the inspector
// half of moat 1 — spawn provenance, requested model/effort at its honest tier, spawned-by edge,
// landed/retired reasons — rendered from catalog truth only. The L7 inspector (Evidence /
// Capabilities / Bus tabs) replaces this pane; the card keeps the facts visible until then.

const card = css({
  display: "grid",
  gap: "0.3rem",
  alignContent: "start",
  fontSize: "0.7rem",
  color: "muted",
  minWidth: "0",
});
const heading = css({
  fontSize: "0.66rem",
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  color: "cyan",
});
const fact = css({
  display: "flex",
  gap: "0.4rem",
  minWidth: "0",
  "& > dt": { flex: "none", color: "muted" },
  "& > dd": { margin: "0", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "ink" },
});

function Fact({ label, value, testId }: { label: string; value: string | undefined; testId?: string }) {
  if (!value) return null;
  return (
    <dl className={fact}>
      <dt>{label}</dt>
      <dd data-testid={testId} title={value}>
        {value}
      </dd>
    </dl>
  );
}

export function SeatInspector({
  session,
  cockpit,
}: {
  session: OpenSession | undefined;
  cockpit: PerSessionCockpit | undefined;
}) {
  if (!session) {
    return (
      <div className={card} data-testid="seat-inspector">
        <span>no focused seat</span>
      </div>
    );
  }
  const visual = seatVisualState(session);
  void cockpit; // set-evidence rendering joins in L4; launch tier derives from row truth (L3 R7)
  const tier = launchTier(session);
  const rawRetireStop = session.controlRaw?.retireControlStopError;
  const retireStopNote = typeof rawRetireStop === "string" ? rawRetireStop : undefined;
  return (
    <div className={card} data-testid="seat-inspector">
      <span className={heading}>Seat</span>
      <Fact label="session" value={session.label} />
      <Fact label="state" value={visual.word} testId="inspector-state" />
      <Fact label="harness" value={session.harness} />
      {/* The two-archetype truth (L6 R1, design §1.4): what this seat's PTY actually shows. */}
      <Fact label="pane" value={paneArchetypeCopy(session)} testId="inspector-archetype" />
      <Fact label="leaf" value={session.leafKey} testId="inspector-leaf" />
      <span className={heading}>Provenance</span>
      <Fact label="spawn role" value={session.spawnRole} testId="inspector-spawn-role" />
      <Fact label="seat role" value={session.seatRole} />
      <Fact
        label="level"
        value={
          session.spawnLevel
            ? `${session.spawnLevel}${session.spawnLevelSource ? ` (${session.spawnLevelSource})` : ""}`
            : undefined
        }
        testId="inspector-spawn-level"
      />
      <Fact
        label="model"
        value={
          session.resolvedModel
            ? `${session.resolvedModel}${session.resolvedEffort ? ` · ${session.resolvedEffort}` : ""} (${tier === "pending" ? "requested" : tier})`
            : session.kind === "harness" && session.controlState
              ? "vendor defaults — no selection sent (defaults)"
              : undefined
        }
        testId="inspector-model"
      />
      <Fact label="spawned by" value={session.spawnedBySession} testId="inspector-spawned-by" />
      <Fact label="original label" value={session.spawnedLabel} />
      {session.landedReason || session.retiredReason ? (
        <>
          <span className={heading}>Outcome</span>
          <Fact label="landed" value={session.landedReason} testId="inspector-landed-reason" />
          <Fact label="landed at" value={session.landedAt} />
          <Fact label="retired" value={session.retiredReason} testId="inspector-retired-reason" />
          <Fact label="retired by" value={session.retiredBySession} />
          {/* Stop residual (L6 R5): informational evidence on a SUCCESSFULLY retired seat —
              the graceful control stop had nothing healthy to talk to. Never a failure state. */}
          {retireStopNote ? (
            <Fact
              label="stop note"
              value={retireResidualCopy(session.label, retireStopNote)}
              testId="inspector-retire-stop-note"
            />
          ) : null}
        </>
      ) : null}
      {session.controlPendingInteraction ? (
        <>
          <span className={heading}>Pending interaction (raw)</span>
          {/* The verbatim payload — the InteractionBar's honest fallback points here when a
              kind cannot be represented (L6 R4). */}
          <pre
            className={css({
              margin: "0",
              maxHeight: "10rem",
              overflow: "auto",
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              fontSize: "0.64rem",
              padding: "0.3rem",
              background: "bg",
              borderWidth: "1px",
              borderStyle: "solid",
              borderColor: "grid",
            })}
            data-testid="inspector-pending-interaction-raw"
          >
            {JSON.stringify(session.controlPendingInteraction, null, 2)}
          </pre>
        </>
      ) : null}
      {session.livenessEvidence || session.exitEvidence ? (
        <>
          <span className={heading}>Liveness</span>
          <Fact label="evidence" value={session.livenessEvidence} testId="inspector-liveness" />
          <Fact label="exit evidence" value={session.exitEvidence} />
          <Fact label="first failed" value={session.livenessFirstFailedAt} />
        </>
      ) : null}
    </div>
  );
}
