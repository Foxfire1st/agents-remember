import { css } from "../../../styled-system/css";
import { launchTier, verbatimBridgeError } from "../../data/launchEvidence";
import { useLifecycleNotices, type StopResidual } from "../../data/sessionLifecycle";
import type { OpenSession } from "../../data/sessions";
import type { PerSessionCockpit, SetLedgerEntry } from "../../data/sessionCockpitStore";
import { acknowledgeSetAttention } from "../../data/setClient";
import { seatVisualState } from "../../data/stateGrammar";
import type { SubmitRecord } from "../../data/submitMachine";
import { EvidenceBadge } from "../../grammar/EvidenceBadge";
import {
  InspectorFact,
  InspectorNote,
  InspectorRaw,
  InspectorSection,
  inspectorAction,
  inspectorPane,
} from "./InspectorPrimitives";
import {
  paneArchetypeCopy,
  retireResidualCopy,
  terminateResidualCopy,
} from "./lifecycleCopy";
import { VirtualizedInspectorList } from "./VirtualizedInspectorList";

// The Evidence tab is the cockpit's full-detail reveal surface: it never promotes requested values
// into effective facts, never treats opening/viewing as acknowledgment, and preserves the server's
// receipt/reconciliation words verbatim. Long ledgers are virtualized, not sliced.

const evidenceTier = css({ display: "inline-flex", alignItems: "baseline", gap: "0.35rem" });
const record = css({ display: "grid", gap: "0.08rem", minWidth: "0" });
const recordLead = css({ color: "ink", fontWeight: "600" });
const recordMeta = css({ color: "muted", overflowWrap: "anywhere" });
const sectionActions = css({ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.4rem" });

/** One ledger line: acceptance WORD first; requested and effective stay separate. */
export function setLedgerEntryLine(entry: SetLedgerEntry): string {
  const result = entry.result;
  const effective =
    result.effectiveValue !== undefined ? ` → effective ${result.effectiveValue}` : "";
  const detail = result.detail ? ` — ${result.detail}` : "";
  const seen = entry.acknowledged ? "" : " · unacknowledged";
  return `${result.acceptance}: ${entry.kind} requested ${entry.requestedValue}${effective}${detail}${seen}`;
}

function optional(value: string | number | null | undefined): string {
  return value === undefined || value === null || value === "" ? "—" : String(value);
}

/** Full, field-named receipt/reconciliation details; used by rendering and assertion tests. */
export function submitEvidenceLines(entry: SubmitRecord): string[] {
  const lines = [
    `phase ${entry.phase} · source ${entry.source} · expected bridge ${entry.expectedBridgeEpoch}`,
    `started ${entry.startedAt} · updated ${entry.updatedAt} · lifecycle observation ${entry.lifecycleObservationVersion}`,
  ];
  if (entry.serverLifecycleState) lines.push(`server lifecycle ${entry.serverLifecycleState}`);
  if (entry.receipt) {
    lines.push(
      `receipt ${entry.receipt.acceptance} · vendor correlation ${optional(entry.receipt.vendorCorrelationId)}`,
      `submitted ${entry.receipt.submittedAt} · accepted ${optional(entry.receipt.acceptedAt)} · bridge ${entry.receipt.bridgeEpoch}`,
    );
    if (entry.receipt.detail) lines.push(`receipt detail ${entry.receipt.detail}`);
  } else {
    lines.push("receipt —");
  }
  if (entry.reconciliation) {
    lines.push(
      `reconciliation ${entry.reconciliation.state} · submission ${optional(entry.reconciliation.submissionState)}`,
      `reconciled ${entry.reconciliation.reconciledAt} · vendor correlation ${optional(entry.reconciliation.vendorCorrelationId)} · bridge ${entry.reconciliation.bridgeEpoch}`,
    );
    if (entry.reconciliation.detail) {
      lines.push(`reconciliation detail ${entry.reconciliation.detail}`);
    }
  }
  if (entry.routeFailure) {
    lines.push(
      `route failure ${entry.routeFailure.status} · HTTP ${optional(entry.routeFailure.httpStatus)} · ${entry.routeFailure.detail}`,
    );
  }
  if (entry.reconcileAttempts > 0 || entry.reconcileWindowElapsedMs > 0) {
    lines.push(
      `reconcile attempts ${entry.reconcileAttempts} · window ${entry.reconcileWindowElapsedMs} ms`,
    );
  }
  if (entry.detail && entry.detail !== entry.receipt?.detail && entry.detail !== entry.reconciliation?.detail) {
    lines.push(`detail ${entry.detail}`);
  }
  if (entry.releasedAt !== undefined) lines.push(`draft released ${entry.releasedAt}`);
  if (entry.restoredAt !== undefined) lines.push(`draft restored ${entry.restoredAt}`);
  if (entry.recoveryDismissedAt !== undefined) {
    lines.push(`withdrawal recovery dismissed ${entry.recoveryDismissedAt}`);
  }
  return lines;
}

function SubmitEvidenceRow({ entry }: { entry: SubmitRecord }) {
  return (
    <div className={record}>
      <span className={recordLead}>request {entry.requestId}</span>
      {submitEvidenceLines(entry).map((line) => (
        <span key={line} className={recordMeta}>
          {line}
        </span>
      ))}
    </div>
  );
}

function stopResidualCopy(residual: StopResidual): string {
  return residual.kind === "terminate"
    ? terminateResidualCopy(residual.label, residual.detail)
    : retireResidualCopy(residual.label, residual.detail);
}

/**
 * Fleet-level lifecycle evidence. A successful terminate removes its OpenSession immediately, so
 * the shared lifecycle notice store — not a fabricated focused-seat tombstone — is authoritative.
 */
function RetainedStopResiduals() {
  const residuals = useLifecycleNotices((state) => state.residuals);
  const dismissResidual = useLifecycleNotices((state) => state.dismissResidual);

  return (
    <InspectorSection title="Retained stop residuals" testId="inspector-stop-residuals-section">
      <InspectorNote testId="inspector-stop-residual-retention">
        Fleet-wide stop notes remain inspectable after a session leaves the rail. They are retained
        for this dashboard page until explicitly dismissed; dismissing here or on the stage removes
        the same record from both surfaces.
      </InspectorNote>
      {residuals.length > 0 ? (
        <VirtualizedInspectorList
          rows={residuals}
          rowKey={(residual) => `${residual.sessionId}-${residual.at}`}
          renderRow={(residual) => (
            <div
              className={record}
              data-testid={`inspector-stop-residual-${residual.kind}-${residual.sessionId}`}
            >
              <span className={recordLead}>{stopResidualCopy(residual)}</span>
              <span className={recordMeta}>
                source{" "}
                {residual.kind === "terminate"
                  ? "terminate / controlStopDetail"
                  : "retire / retireControlStopError"}
              </span>
              <button
                type="button"
                className={inspectorAction}
                aria-label={`Dismiss retained stop note for ${residual.label}`}
                onClick={() => dismissResidual(residual.sessionId, residual.at)}
                data-testid={`inspector-stop-residual-dismiss-${residual.sessionId}`}
              >
                dismiss stop note
              </button>
            </div>
          )}
          label="Retained fleet stop residuals"
          testId="inspector-stop-residuals"
        />
      ) : (
        <InspectorNote testId="inspector-stop-residuals-empty">
          No stop residual is retained for this dashboard page; this is not a lifecycle-health
          verdict.
        </InspectorNote>
      )}
    </InspectorSection>
  );
}

export function EvidencePane({
  session,
  cockpit,
}: {
  session: OpenSession | undefined;
  cockpit: PerSessionCockpit | undefined;
}) {
  if (!session) {
    return (
      <div className={inspectorPane} data-testid="evidence-pane">
        <InspectorSection title="Seat">
          <InspectorNote testId="inspector-evidence-no-focus">
            No focused seat. Seat provenance, launch, ledger, receipt, diagnostics, and liveness
            evidence require an exact seat; fleet-wide retained stop residuals remain available
            below.
          </InspectorNote>
        </InspectorSection>
        <RetainedStopResiduals />
      </div>
    );
  }

  const visual = seatVisualState(session);
  const tier = launchTier(session);
  const launch = cockpit?.launchEvidence;
  const bridgeError = verbatimBridgeError(session.controlRaw);
  const paneDiagnostic = session.controlRaw?.paneDiagnostic;
  const retireStopError = session.controlRaw?.retireControlStopError;
  const stopNote =
    typeof retireStopError === "string"
      ? retireResidualCopy(session.label, retireStopError)
      : undefined;
  const ledger = [...(cockpit?.setLedger ?? [])].reverse();
  const unacknowledged = ledger.filter((entry) => !entry.acknowledged).length;
  const submissions = [...(cockpit?.submitHistory ?? [])].reverse();

  return (
    <div className={inspectorPane} data-testid="evidence-pane">
      <InspectorSection title="Seat">
        <InspectorFact label="session" value={session.label} />
        <InspectorFact label="state" value={visual.word} testId="inspector-state" />
        <InspectorFact label="harness" value={session.harness} />
        <InspectorFact
          label="pane"
          value={paneArchetypeCopy(session)}
          testId="inspector-archetype"
        />
        <InspectorFact label="leaf" value={session.leafKey} testId="inspector-leaf" />
      </InspectorSection>

      <InspectorSection title="Launch evidence" testId="inspector-launch-evidence">
        <InspectorFact label="retained model" value={launch?.retainedModel ?? session.resolvedModel} />
        <InspectorFact label="retained effort" value={launch?.retainedEffort ?? session.resolvedEffort} />
        <InspectorFact
          label="tier"
          value={
            <span className={evidenceTier}>
              <EvidenceBadge tier={tier} size="sm" />
              <span>{tier}</span>
            </span>
          }
          testId="inspector-launch-tier"
        />
        <InspectorFact label="spawn role" value={session.spawnRole} testId="inspector-spawn-role" />
        <InspectorFact label="seat role" value={session.seatRole} />
        <InspectorFact
          label="level"
          value={
            session.spawnLevel
              ? `${session.spawnLevel}${session.spawnLevelSource ? ` (${session.spawnLevelSource})` : ""}`
              : undefined
          }
          testId="inspector-spawn-level"
        />
        <InspectorFact
          label="spawned by"
          value={session.spawnedBySession}
          testId="inspector-spawned-by"
        />
        <InspectorFact label="original label" value={session.spawnedLabel} />
      </InspectorSection>

      {ledger.length > 0 ? (
        <InspectorSection title="SetResult ledger" testId="inspector-set-ledger-section">
          <div className={sectionActions}>
            <span>
              {ledger.length} set change{ledger.length === 1 ? "" : "s"} · {unacknowledged}{" "}
              unacknowledged
            </span>
            {unacknowledged > 0 ? (
              <button
                type="button"
                className={inspectorAction}
                onClick={() => acknowledgeSetAttention(session.id)}
                data-testid="inspector-set-ledger-mark-seen"
              >
                mark seen
              </button>
            ) : null}
          </div>
          <VirtualizedInspectorList
            rows={ledger}
            rowKey={(entry, index) => `${entry.at}-${entry.kind}-${index}`}
            renderRow={(entry) => setLedgerEntryLine(entry)}
            label="SetResult ledger"
            testId="inspector-set-ledger"
          />
        </InspectorSection>
      ) : (
        <InspectorSection title="SetResult ledger">
          <InspectorNote>No set outcomes recorded for this seat.</InspectorNote>
        </InspectorSection>
      )}

      <InspectorSection title="Submit receipts and reconciliation">
        {submissions.length > 0 ? (
          <VirtualizedInspectorList
            rows={submissions}
            rowKey={(entry) => entry.requestId}
            renderRow={(entry) => <SubmitEvidenceRow entry={entry} />}
            label="Submit receipt and reconciliation history"
            testId="inspector-submit-history"
          />
        ) : (
          <InspectorNote>No cockpit submit receipts recorded for this seat.</InspectorNote>
        )}
      </InspectorSection>

      {bridgeError !== null || paneDiagnostic !== undefined ? (
        <InspectorSection title="Bridge and pane diagnostics">
          {bridgeError !== null ? (
            <InspectorFact
              label="bridge error"
              value={<InspectorRaw value={bridgeError} testId="inspector-bridge-error" />}
            />
          ) : null}
          {paneDiagnostic !== undefined ? (
            <InspectorFact
              label="pane diagnostic"
              value={<InspectorRaw value={paneDiagnostic} testId="inspector-pane-diagnostic" />}
            />
          ) : null}
        </InspectorSection>
      ) : null}

      {session.landedReason || session.retiredReason || stopNote ? (
        <InspectorSection title="Outcome">
          <InspectorFact
            label="landed"
            value={session.landedReason}
            testId="inspector-landed-reason"
          />
          <InspectorFact label="landed at" value={session.landedAt} />
          <InspectorFact
            label="retired"
            value={session.retiredReason}
            testId="inspector-retired-reason"
          />
          <InspectorFact label="retired by" value={session.retiredBySession} />
          <InspectorFact
            label="stop note"
            value={stopNote}
            testId="inspector-retire-stop-note"
          />
        </InspectorSection>
      ) : null}

      {session.controlPendingInteraction ? (
        <InspectorSection title="Pending interaction (raw)">
          <InspectorRaw
            value={session.controlPendingInteraction}
            testId="inspector-pending-interaction-raw"
          />
        </InspectorSection>
      ) : null}

      {session.livenessEvidence || session.exitEvidence ? (
        <InspectorSection title="Liveness">
          <InspectorFact
            label="evidence"
            value={session.livenessEvidence}
            testId="inspector-liveness"
          />
          <InspectorFact label="exit evidence" value={session.exitEvidence} />
          <InspectorFact label="first failed" value={session.livenessFirstFailedAt} />
          <InspectorFact label="last failed" value={session.livenessLastFailedAt} />
          <InspectorFact label="failure count" value={session.livenessFailures} />
        </InspectorSection>
      ) : null}

      <RetainedStopResiduals />

      <InspectorSection title="Vocabulary mapping">
        <InspectorNote testId="inspector-state-vocabulary-note">
          Chrome state is derived from catalog controlState, turnState, pending interaction, and
          terminal status. Controlled-pane [control] lines are the runner's raw vocabulary; they
          can differ without changing the chrome state.
        </InspectorNote>
        <InspectorNote>
          Full transcript, tool-output, and reasoning archives remain UA-1-gated; this pane does
          not invent those absent records.
        </InspectorNote>
      </InspectorSection>
    </div>
  );
}
