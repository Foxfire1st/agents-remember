import type { HTMLAttributes } from "react";

import { cx } from "../../../styled-system/css";
import {
  cacheStatusNote,
  capabilityCostNote,
  capabilityLoadingCopy,
  type PerHarnessCapabilities,
} from "../../data/capabilityCatalog";
import {
  launchableEfforts,
  modelByKey,
  type LaunchSelectionState,
  type OpenOutcome,
} from "../../data/launchFlow";
import type { HarnessCatalogState } from "./useHarnessCatalogRead";
import {
  errorLine,
  footerRow,
  launchButton,
  noteLine,
  optionButton,
  optionRow,
  outcomeBox,
  box,
  heading,
  overlay,
  quietButton,
  smallInput,
} from "./launchFlowStyles";

// The dialog surface owns the modal ARIA contract; the call site supplies the visual box and the
// dismissal handling (Escape + stop-propagation) as ordinary DOM props.
function LaunchDialog({ children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div role="dialog" aria-label="Launch session" {...props}>
      {children}
    </div>
  );
}

function HarnessErrorBox({
  catalog,
  onRetry,
}: {
  catalog: HarnessCatalogState;
  onRetry: () => void;
}) {
  if (catalog.status === "error") {
    return (
      <div className={outcomeBox}>
        <p className={errorLine} role="alert" data-testid="launch-harness-error">
          {catalog.kind} error reading /api/harnesses — {catalog.detail}
        </p>
        <div className={optionRow}>
          <button
            type="button"
            className={quietButton}
            data-testid="launch-harness-retry"
            onClick={onRetry}
          >
            Retry harness list
          </button>
        </div>
      </div>
    );
  }
  if (catalog.status !== "empty" && catalog.status !== "timeout") {
    return null;
  }
  const empty = catalog.status === "empty";
  return (
    <div className={outcomeBox}>
      <p
        className={empty ? noteLine : errorLine}
        role={empty ? "status" : "alert"}
        data-testid={
          empty
            ? "launch-harness-empty"
            : catalog.status === "timeout"
              ? "launch-harness-timeout"
              : "launch-harness-error"
        }
      >
        {empty
          ? "no harnesses advertised by this daemon"
          : "harness list timed out — the request belonged to an unavailable serving process"}
      </p>
      <div className={optionRow}>
        <button
          type="button"
          className={quietButton}
          data-testid="launch-harness-retry"
          onClick={onRetry}
        >
          Retry harness list
        </button>
      </div>
    </div>
  );
}

export function HarnessSection({
  catalog,
  harnessId,
  onPick,
  onRetry,
}: {
  catalog: HarnessCatalogState;
  harnessId: string | null;
  onPick: (harnessId: string) => void;
  onRetry: () => void;
}) {
  if (catalog.status === "idle" || catalog.status === "loading") {
    return (
      <p className={noteLine} role="status" data-testid="launch-harness-loading">
        loading harnesses…
      </p>
    );
  }
  if (catalog.status === "ready") {
    return (
      <div className={optionRow} data-testid="launch-harness-list">
        {catalog.harnesses.map((harness) => (
          <button
            key={harness.id}
            type="button"
            className={optionButton}
            aria-pressed={harnessId === harness.id}
            disabled={!harness.detected}
            data-testid={`launch-harness-${harness.id}`}
            onClick={() => onPick(harness.id)}
          >
            {harness.name}
            {harness.detected ? "" : " — not installed"}
          </button>
        ))}
      </div>
    );
  }
  return <HarnessErrorBox catalog={catalog} onRetry={onRetry} />;
}

function ModelPicker({
  harnessId,
  envelope,
  selection,
  onSelectModel,
  onVendorDefaults,
  onRefresh,
}: {
  harnessId: string;
  envelope: NonNullable<PerHarnessCapabilities["envelope"]>;
  selection: LaunchSelectionState;
  onSelectModel: (key: string) => void;
  onVendorDefaults: () => void;
  onRefresh: () => void;
}) {
  return (
    <>
      <p className={noteLine} data-testid="launch-cache-status">
        {cacheStatusNote(envelope.cacheStatus)}
        {" · "}
        <button
          type="button"
          className={quietButton}
          data-testid="launch-cap-refresh"
          title={capabilityCostNote(harnessId)}
          onClick={onRefresh}
        >
          refresh catalog
        </button>
      </p>
      <div className={optionRow} data-testid="launch-model-list">
        {envelope.capabilities.models
          .filter((model) => !model.hidden)
          .map((model) => (
            <button
              key={model.key}
              type="button"
              className={optionButton}
              aria-pressed={selection.modelKey === model.key}
              disabled={!model.selectable}
              data-testid={`launch-model-${model.key}`}
              title={model.description ?? undefined}
              onClick={() => onSelectModel(model.key)}
            >
              {/* keys render VERBATIM — Pi stays provider-qualified */}
              {model.key}
              {model.selectable ? "" : " — not selectable"}
            </button>
          ))}
        <button
          type="button"
          className={optionButton}
          aria-pressed={selection.vendorDefaults}
          data-testid="launch-vendor-defaults"
          onClick={onVendorDefaults}
        >
          vendor defaults — send no selection
        </button>
      </div>
    </>
  );
}

function EffortPicker({
  envelope,
  selection,
  onSelectEffort,
}: {
  envelope: NonNullable<PerHarnessCapabilities["envelope"]>;
  selection: LaunchSelectionState;
  onSelectEffort: (key: string) => void;
}) {
  const selectedModel = modelByKey(envelope.capabilities, selection.modelKey);
  const efforts = selectedModel ? launchableEfforts(selectedModel) : [];
  return selectedModel ? (
    <>
      {efforts.length === 0 ? (
        <p className={noteLine} data-testid="launch-effort-none">
          {selectedModel.key} advertises no launch-settable efforts — a complete
          model·effort pair cannot be formed; launch with vendor defaults instead
        </p>
      ) : (
        <>
          <div className={optionRow} data-testid="launch-effort-list">
            {/* advertised NATIVE order, no reordering, no emphasis (R4) */}
            {efforts.map((option) => (
              <button
                key={option.key}
                type="button"
                className={optionButton}
                aria-pressed={selection.effort === option.key}
                data-testid={`launch-effort-${option.key}`}
                title={option.description ?? undefined}
                onClick={() => onSelectEffort(option.key)}
              >
                {option.key}
              </button>
            ))}
          </div>
          {selection.effort === null ? (
            <p className={noteLine} data-testid="launch-effort-choose">
              no advertised launch default for this model — choose an effort explicitly
            </p>
          ) : null}
        </>
      )}
    </>
  ) : null;
}

function CapabilityBody({
  harnessId,
  entry,
  selection,
  onSelectModel,
  onSelectEffort,
  onVendorDefaults,
  onRefresh,
}: {
  harnessId: string;
  entry: PerHarnessCapabilities;
  selection: LaunchSelectionState;
  onSelectModel: (key: string) => void;
  onSelectEffort: (key: string) => void;
  onVendorDefaults: () => void;
  onRefresh: () => void;
}) {
  const envelope = entry.envelope;
  if (!envelope) return null;
  return (
    <>
      <ModelPicker
        harnessId={harnessId}
        envelope={envelope}
        selection={selection}
        onSelectModel={onSelectModel}
        onVendorDefaults={onVendorDefaults}
        onRefresh={onRefresh}
      />
      <EffortPicker
        envelope={envelope}
        selection={selection}
        onSelectEffort={onSelectEffort}
      />
    </>
  );
}

export function CapabilitySection({
  harnessId,
  entry,
  selection,
  onSelectModel,
  onSelectEffort,
  onVendorDefaults,
  onRetry,
  onRefresh,
}: {
  harnessId: string;
  entry: PerHarnessCapabilities | undefined;
  selection: LaunchSelectionState;
  onSelectModel: (key: string) => void;
  onSelectEffort: (key: string) => void;
  onVendorDefaults: () => void;
  onRetry: () => void;
  onRefresh: () => void;
}) {
  if (!entry || entry.fetchState === "loading" || entry.fetchState === "refreshing") {
    return (
      <p className={noteLine} data-testid="launch-cap-loading">
        {capabilityLoadingCopy(
          harnessId,
          entry?.fetchState === "refreshing" ? "refresh" : "initial",
        )}
      </p>
    );
  }
  if (entry.fetchState === "error" && entry.error) {
    return (
      <div className={outcomeBox}>
        <p className={errorLine} role="alert" data-testid="launch-cap-error">
          {entry.error.status}: {entry.error.detail}
        </p>
        <div className={optionRow}>
          <button
            type="button"
            className={quietButton}
            data-testid="launch-cap-retry"
            onClick={onRetry}
          >
            retry
          </button>
        </div>
      </div>
    );
  }
  if (!entry.envelope) return null;
  return (
    <CapabilityBody
      harnessId={harnessId}
      entry={entry}
      selection={selection}
      onSelectModel={onSelectModel}
      onSelectEffort={onSelectEffort}
      onVendorDefaults={onVendorDefaults}
      onRefresh={onRefresh}
    />
  );
}

export function OptionalFields({
  label,
  leafKey,
  setLabel,
  setLeafKey,
}: {
  label: string;
  leafKey: string;
  setLabel: (value: string) => void;
  setLeafKey: (value: string) => void;
}) {
  return (
    <div className={optionRow}>
      <input
        className={smallInput}
        value={label}
        onChange={(event) => setLabel(event.target.value)}
        placeholder="label (optional)"
        aria-label="Session label"
        data-testid="launch-label"
      />
      <input
        className={smallInput}
        value={leafKey}
        onChange={(event) => setLeafKey(event.target.value)}
        // V7 — the placeholder no longer truncates its own sentence; the arbitration note moves to
        // the field tooltip (progressive disclosure) so the input reads at any width.
        placeholder="leaf key (optional)"
        title="If set, the server arbitrates leaf ownership — a leaf already owned is refused, never silently reassigned."
        aria-label="Leaf key"
        data-testid="launch-leaf-key"
      />
    </div>
  );
}

export function LaunchFooter({
  posting,
  readyToLaunch,
  attempted,
  launchBlockReason,
  harnessId,
  unknownId,
  onLaunch,
  onDismiss,
}: {
  posting: boolean;
  readyToLaunch: boolean;
  attempted: string;
  launchBlockReason: string | null;
  harnessId: string | null;
  unknownId: string | null;
  onLaunch: () => void;
  onDismiss: () => void;
}) {
  const summary = posting
    ? "launching…"
    : readyToLaunch
      ? `${harnessId} · ${attempted}`
      : (launchBlockReason ?? `${harnessId ?? "pick a harness"} · ${attempted}`);
  return (
    <div className={footerRow}>
      <button
        type="button"
        className={launchButton}
        disabled={!readyToLaunch}
        data-testid="launch-submit"
        onClick={onLaunch}
      >
        {posting ? "launching…" : "launch"}
      </button>
      <span className={noteLine} data-testid="launch-summary">
        {summary}
      </span>
      <button
        type="button"
        className={quietButton}
        style={{ marginLeft: "auto" }}
        data-testid="launch-cancel"
        onClick={onDismiss}
      >
        {unknownId ? "dismiss (resolves via the catalog)" : "cancel"}
      </button>
    </div>
  );
}

export function LeafTakenOutcome({
  outcome,
  onFocusSession,
  onClose,
}: {
  outcome: Extract<OpenOutcome, { path: "leaf-taken" }>;
  onFocusSession: (id: string) => void;
  onClose: () => void;
}) {
  const owner = outcome.ownerSession;
  return (
    <div className={outcomeBox} role="alert" data-testid="launch-outcome-leaf-taken">
      <span className={errorLine}>
        leaf-taken: {outcome.leafKey ?? "the requested leaf"} is already owned by session{" "}
        {owner ?? "(unnamed)"}
      </span>
      {owner ? (
        <div className={optionRow}>
          <button
            type="button"
            className={quietButton}
            data-testid="launch-focus-owner"
            onClick={() => {
              onFocusSession(owner);
              onClose();
            }}
          >
            focus owning session
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function ConflictOutcome({
  outcome,
  attempted,
  onFocusSession,
  onClose,
}: {
  outcome: Extract<OpenOutcome, { path: "launch-selection-conflict" }>;
  attempted: string;
  onFocusSession: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <div className={outcomeBox} role="alert" data-testid="launch-outcome-conflict">
      <span className={errorLine}>
        launch-selection-conflict{outcome.detail ? `: ${outcome.detail}` : ""}
      </span>
      <span className={noteLine} data-testid="launch-conflict-pairs">
        live retained pair: {outcome.liveModel ?? "vendor defaults"}
        {outcome.liveEffort ? ` · ${outcome.liveEffort}` : ""} — attempted: {attempted}. The
        live process keeps its provenance; nothing was rewritten.
      </span>
      <div className={optionRow}>
        <button
          type="button"
          className={quietButton}
          data-testid="launch-focus-existing"
          onClick={() => {
            onFocusSession(outcome.session);
            onClose();
          }}
        >
          focus existing session
        </button>
      </div>
    </div>
  );
}

function LaunchOutcome({
  outcome,
  attempted,
  onFocusSession,
  onClose,
}: {
  outcome: OpenOutcome;
  attempted: string;
  onFocusSession: (id: string) => void;
  onClose: () => void;
}) {
  if (outcome.path === "opened") return null; // the flow closes on success — the rail row is the rendering surface
  if (outcome.path === "launch-selection-invalid") {
    return (
      <div className={outcomeBox} role="alert" data-testid="launch-outcome-invalid">
        <span className={errorLine}>launch-selection-invalid: {outcome.detail}</span>
      </div>
    );
  }
  if (outcome.path === "open-refused") {
    return (
      <div className={outcomeBox} role="alert" data-testid="launch-outcome-refused">
        <span className={errorLine}>
          {outcome.status}: {outcome.detail}
        </span>
      </div>
    );
  }
  if (outcome.path === "leaf-taken") {
    return (
      <LeafTakenOutcome
        outcome={outcome}
        onFocusSession={onFocusSession}
        onClose={onClose}
      />
    );
  }
  if (outcome.path === "launch-selection-conflict") {
    return (
      <ConflictOutcome
        outcome={outcome}
        attempted={attempted}
        onFocusSession={onFocusSession}
        onClose={onClose}
      />
    );
  }
  return (
    <div className={outcomeBox} role="status" data-testid="launch-outcome-unknown">
      <span className={noteLine}>
        open outcome unknown — checking the catalog ({outcome.detail}). The selection is kept;
        the caller-minted id reconciles on the next poll. No re-POST is sent.
      </span>
    </div>
  );
}

export interface LaunchFlowDialogProps {
  catalog: HarnessCatalogState;
  harnessId: string | null;
  entry: PerHarnessCapabilities | undefined;
  selection: LaunchSelectionState;
  label: string;
  leafKey: string;
  outcome: OpenOutcome | null;
  posting: boolean;
  unknownId: string | null;
  readyToLaunch: boolean;
  attempted: string;
  launchBlockReason: string | null;
  onRetryHarness: () => void;
  onPickHarness: (id: string) => void;
  onSelectModel: (key: string) => void;
  onSelectEffort: (key: string) => void;
  onVendorDefaults: () => void;
  onRetryCapabilities: () => void;
  onRefreshCapabilities: () => void;
  setLabel: (value: string) => void;
  setLeafKey: (value: string) => void;
  onLaunch: () => void;
  onDismiss: () => void;
  onOutcomeFocus: (id: string) => void;
  onOutcomeClose: () => void;
}

export function LaunchFlowDialog(props: LaunchFlowDialogProps) {
  return (
    <div
      className={overlay}
      role="presentation"
      data-testid="launch-flow-overlay"
      onClick={props.onDismiss}
    >
      <LaunchDialog
        className={cx(box, "sessions__launch")}
        data-testid="launch-flow"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            props.onDismiss();
          }
        }}
      >
        <span className={heading}>Launch session</span>

        <span className={heading}>Harness</span>
        <HarnessSection
          catalog={props.catalog}
          harnessId={props.harnessId}
          onPick={props.onPickHarness}
          onRetry={props.onRetryHarness}
        />

        {props.harnessId ? (
          <>
            <span className={heading}>Model · effort — live catalog</span>
            <CapabilitySection
              harnessId={props.harnessId}
              entry={props.entry}
              selection={props.selection}
              onSelectModel={props.onSelectModel}
              onSelectEffort={props.onSelectEffort}
              onVendorDefaults={props.onVendorDefaults}
              onRetry={props.onRetryCapabilities}
              onRefresh={props.onRefreshCapabilities}
            />
          </>
        ) : null}

        <span className={heading}>Optional</span>
        <OptionalFields
          label={props.label}
          leafKey={props.leafKey}
          setLabel={props.setLabel}
          setLeafKey={props.setLeafKey}
        />

        {props.outcome ? (
          <LaunchOutcome
            outcome={props.outcome}
            attempted={props.attempted}
            onFocusSession={props.onOutcomeFocus}
            onClose={props.onOutcomeClose}
          />
        ) : null}

        <LaunchFooter
          posting={props.posting}
          readyToLaunch={props.readyToLaunch}
          attempted={props.attempted}
          launchBlockReason={props.launchBlockReason}
          harnessId={props.harnessId}
          unknownId={props.unknownId}
          onLaunch={props.onLaunch}
          onDismiss={props.onDismiss}
        />
      </LaunchDialog>
    </div>
  );
}
