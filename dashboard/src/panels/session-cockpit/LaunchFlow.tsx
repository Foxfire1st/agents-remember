import { useEffect, useRef, useState } from "react";

import { css, cx } from "../../../styled-system/css";
import {
  capabilityCostNote,
  capabilityLoadingCopy,
  cacheStatusNote,
  fetchHarnessCapabilities,
  useCapabilityCatalog,
} from "../../data/capabilityCatalog";
import {
  captureCatalogAuthority,
  catalogAuthorityIsCurrent,
  hydrateTerminalSessionsFromCatalog,
} from "../../data/catalogPoll";
import { launchTier } from "../../data/launchEvidence";
import {
  chooseEffort,
  chooseModel,
  chooseVendorDefaults,
  EMPTY_SELECTION,
  launchableEfforts,
  modelByKey,
  openHostedSession,
  selectionComplete,
  type LaunchSelectionState,
  type OpenOutcome,
} from "../../data/launchFlow";
import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { notifySessionCatalogChanged, type OpenSession } from "../../data/sessions";
import { fetchHarnessesOrNull, type HarnessInfo } from "../../data/terminal";

// The LaunchFlow (260715-FEUI-L3 S2/S3, design §7.1): harness → model → effort → open, with
// every picker populated EXCLUSIVELY from the daemon (GET /api/harnesses + the pre-session
// capability envelope) — no hardcoded menu, no client fallback, no invented default. The pair
// rules live in data/launchFlow (pure); this component renders them plus all four open-response
// paths and the F9 transport-unknown reconciliation (the session id is caller-minted, so
// "does the row exist" resolves an unanswered POST — never a blind re-POST with a fresh id).

export interface LaunchPrefill {
  harness: string;
  /** The refused pair from a failed row ('Launch corrected…') — applied only where the live
   *  catalog still advertises it; an absent row simply leaves the picker unselected. */
  modelKey?: string;
  effort?: string;
}

const overlay = css({
  position: "absolute",
  inset: "0",
  zIndex: "20",
  background: "oklch(0 0 0 / 0.35)",
});
const box = css({
  position: "absolute",
  top: "6%",
  left: "50%",
  transform: "translateX(-50%)",
  width: "min(620px, 94%)",
  maxHeight: "84%",
  display: "flex",
  flexDirection: "column",
  gap: "0.55rem",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  boxShadow: "0 10px 40px oklch(0 0 0 / 0.5)",
  padding: "0.7rem 0.8rem",
  overflowY: "auto",
  fontSize: "0.76rem",
  color: "muted",
});
const heading = css({
  fontSize: "0.66rem",
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  color: "cyan",
});
const optionRow = css({ display: "flex", flexWrap: "wrap", gap: "0.35rem" });
const optionButton = css({
  font: "inherit",
  fontSize: "0.74rem",
  paddingInline: "0.5rem",
  paddingBlock: "0.15rem",
  background: "bg",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  "&[aria-pressed='true']": { color: "amber", borderColor: "amber" },
  _disabled: { opacity: 0.55, cursor: "not-allowed", _hover: { color: "muted", borderColor: "grid" } },
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
});
const noteLine = css({ fontSize: "0.7rem", color: "muted" });
// The adapter-status word rides VISIBLY on the harness button (review finding 6 — a hover-only
// title is invisible to keyboard/touch); the word is the server's own, rendered verbatim.
const adapterWord = css({ fontSize: "0.62rem", color: "muted", marginLeft: "0.3em" });
const errorLine = css({ fontSize: "0.72rem", color: "alarm", whiteSpace: "pre-wrap" });
const smallInput = css({
  font: "inherit",
  fontSize: "0.74rem",
  background: "bg",
  color: "ink",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  padding: "0.2rem 0.4rem",
  minWidth: "0",
  flex: "1",
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
});
const footerRow = css({
  display: "flex",
  alignItems: "center",
  gap: "0.6rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
  paddingTop: "0.45rem",
});
const launchButton = css({
  font: "inherit",
  fontSize: "0.76rem",
  paddingInline: "0.7rem",
  paddingBlock: "0.2rem",
  background: "transparent",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  cursor: "pointer",
  _disabled: { opacity: 0.5, cursor: "not-allowed" },
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
});
const quietButton = css({
  font: "inherit",
  fontSize: "0.7rem",
  paddingInline: "0.45rem",
  paddingBlock: "0.12rem",
  background: "transparent",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
});
const outcomeBox = css({
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  padding: "0.4rem 0.5rem",
  display: "grid",
  gap: "0.3rem",
});

const defaultMint = (): string => crypto.randomUUID();

export function LaunchFlow({
  open,
  prefill,
  sessions,
  lifecycleId,
  onClose,
  onFocusSession,
  mintSessionId = defaultMint,
}: {
  open: boolean;
  prefill?: LaunchPrefill;
  /** The live session list — the F9 unknown-outcome reconciler watches it for the minted id. */
  sessions: OpenSession[];
  /** The selected task route inherited by a newly opened hosted chat. */
  lifecycleId?: string;
  onClose: () => void;
  onFocusSession: (id: string) => void;
  /** Test seam — the caller-minted session id. */
  mintSessionId?: () => string;
}) {
  const [harnesses, setHarnesses] = useState<HarnessInfo[] | "failed" | null>(null);
  const [harnessId, setHarnessId] = useState<string | null>(null);
  const [selection, setSelection] = useState<LaunchSelectionState>(EMPTY_SELECTION);
  const [label, setLabel] = useState("");
  const [leafKey, setLeafKey] = useState("");
  const [posting, setPosting] = useState(false);
  const [outcome, setOutcome] = useState<OpenOutcome | null>(null);
  const [unknownId, setUnknownId] = useState<string | null>(null);
  const prefillPairRef = useRef<{ modelKey: string; effort?: string } | null>(null);

  const entry = useCapabilityCatalog((state) =>
    harnessId ? state.perHarness[harnessId] : undefined,
  );
  const snapshot = entry?.envelope?.capabilities;

  // Reset + load the harness list on every open (live data, never a kept snapshot).
  useEffect(() => {
    if (!open) return undefined;
    setHarnesses(null);
    setHarnessId(prefill?.harness ?? null);
    setSelection(EMPTY_SELECTION);
    setLabel("");
    setLeafKey("");
    setPosting(false);
    setOutcome(null);
    setUnknownId(null);
    prefillPairRef.current = prefill?.modelKey
      ? { modelKey: prefill.modelKey, effort: prefill.effort }
      : null;
    let cancelled = false;
    void fetchHarnessesOrNull().then((list) => {
      if (!cancelled) setHarnesses(list ?? "failed");
    });
    return () => {
      cancelled = true;
    };
  }, [open, prefill]);

  // Selecting a harness reads its live envelope (single-flighted; a daemon cache hit is cheap).
  useEffect(() => {
    if (!open || !harnessId) return;
    void fetchHarnessCapabilities(harnessId);
  }, [open, harnessId]);

  // 'Launch corrected…' prefill: applied only where the live catalog still advertises the pair.
  useEffect(() => {
    const pending = prefillPairRef.current;
    if (!open || !pending || !snapshot) return;
    prefillPairRef.current = null;
    let next = chooseModel(snapshot, pending.modelKey);
    if (next.modelKey !== null && pending.effort) {
      next = chooseEffort(snapshot, next, pending.effort);
    }
    setSelection(next);
  }, [open, snapshot]);

  // F9: an unanswered POST resolves by catalog observation of the caller-minted id — but ONLY
  // while the dialog is open (review finding 1): an explicit dismiss ends the watch, so a row
  // the daemon surfaces minutes later can never steal focus from whatever the operator is
  // working in. The dismissed row still appears on the rail through the ordinary poll.
  useEffect(() => {
    if (!open || !unknownId) return;
    if (sessions.some((session) => session.id === unknownId)) {
      notifySessionCatalogChanged("create", unknownId);
      onFocusSession(unknownId);
      onClose();
    }
  }, [open, sessions, unknownId, onFocusSession, onClose]);

  if (!open) return null;

  const selectedModel = snapshot ? modelByKey(snapshot, selection.modelKey) : undefined;
  const efforts = selectedModel ? launchableEfforts(selectedModel) : [];
  const readyToLaunch =
    harnessId !== null && selectionComplete(selection) && !posting && unknownId === null;

  // An explicit dismiss also ENDS the unknown-outcome watch IMMEDIATELY (delta-verify residual):
  // a stale unknownId surviving dismissal would fire one late focus steal on the next open's
  // first effect pass, before the reset effect's state update lands.
  const dismiss = () => {
    setUnknownId(null);
    onClose();
  };

  const launch = async () => {
    if (!readyToLaunch || !harnessId) return;
    // A dev-scenario reset cannot cancel an open POST already in flight. Carry its original catalog
    // authority through every follow-on edge so settlement cannot adopt the successor fixture.
    const launchAuthority = captureCatalogAuthority();
    const sessionId = mintSessionId();
    setPosting(true);
    setOutcome(null);
    const result = await openHostedSession(sessionId, {
      harness: harnessId,
      selection,
      ...(label.trim() ? { label: label.trim() } : {}),
      ...(leafKey.trim() ? { leafKey: leafKey.trim() } : {}),
      ...(lifecycleId ? { lifecycleId } : {}),
    });
    if (!catalogAuthorityIsCurrent(launchAuthority)) return;
    setPosting(false);
    if (result.path === "opened") {
      notifySessionCatalogChanged("create", result.session);
      // R5: the retained pair renders at tier 'pending' (launchTier gates on controlState —
      // 'starting' ⇒ pending; both-null ⇒ defaults). Never promoted by the open response itself.
      sessionCockpitStore.getState().setLaunchEvidence(result.session, {
        retainedModel: result.resolvedModel ?? undefined,
        retainedEffort: result.resolvedEffort ?? undefined,
        tier: launchTier({
          harness: result.harness ?? harnessId,
          resolvedModel: result.resolvedModel,
          resolvedEffort: result.resolvedEffort,
          controlState: result.controlState,
        }),
      });
      const catalogAuthoritySurvived = await hydrateTerminalSessionsFromCatalog(
        false,
        new Set(),
        launchAuthority,
      );
      if (!catalogAuthoritySurvived || !catalogAuthorityIsCurrent(launchAuthority)) return;
      onFocusSession(result.session);
      if (!catalogAuthorityIsCurrent(launchAuthority)) return;
      onClose();
      return;
    }
    if (result.path === "outcome-unknown") {
      setUnknownId(sessionId);
    }
    setOutcome(result);
  };

  const attempted = selection.vendorDefaults
    ? "vendor defaults (no selection sent)"
    : `${selection.modelKey ?? "—"} · ${selection.effort ?? "—"}`;

  return (
    <div className={overlay} data-testid="launch-flow-overlay" onClick={dismiss}>
      <div
        role="dialog"
        aria-label="Launch session"
        className={cx(box, "sessions__launch")}
        data-testid="launch-flow"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            dismiss();
          }
        }}
      >
        <span className={heading}>Launch session</span>

        <span className={heading}>Harness</span>
        {harnesses === null ? (
          <p className={noteLine}>loading harnesses…</p>
        ) : harnesses === "failed" ? (
          <p className={errorLine} role="alert" data-testid="launch-harness-error">
            could not read /api/harnesses — the daemon did not answer
          </p>
        ) : (
          <div className={optionRow} data-testid="launch-harness-list">
            {harnesses.map((harness) => (
              <button
                key={harness.id}
                type="button"
                className={optionButton}
                aria-pressed={harnessId === harness.id}
                disabled={!harness.detected}
                data-testid={`launch-harness-${harness.id}`}
                onClick={() => {
                  setHarnessId(harness.id);
                  setSelection(EMPTY_SELECTION);
                  setOutcome(null);
                }}
              >
                {harness.name}
                {harness.detected ? "" : " — not installed"}
                {harness.control ? (
                  <span className={adapterWord}>adapter {harness.control}</span>
                ) : null}
              </button>
            ))}
          </div>
        )}

        {harnessId ? (
          <>
            <span className={heading}>Model · effort — live catalog</span>
            {!entry || entry.fetchState === "loading" || entry.fetchState === "refreshing" ? (
              <p className={noteLine} data-testid="launch-cap-loading">
                {capabilityLoadingCopy(
                  harnessId,
                  entry?.fetchState === "refreshing" ? "refresh" : "initial",
                )}
              </p>
            ) : entry.fetchState === "error" && entry.error ? (
              <div className={outcomeBox}>
                <p className={errorLine} role="alert" data-testid="launch-cap-error">
                  {entry.error.status}: {entry.error.detail}
                </p>
                <div className={optionRow}>
                  <button
                    type="button"
                    className={quietButton}
                    data-testid="launch-cap-retry"
                    onClick={() => void fetchHarnessCapabilities(harnessId)}
                  >
                    retry
                  </button>
                </div>
              </div>
            ) : entry.envelope ? (
              <>
                <p className={noteLine} data-testid="launch-cache-status">
                  {cacheStatusNote(entry.envelope.cacheStatus)}
                  {" · "}
                  <button
                    type="button"
                    className={quietButton}
                    data-testid="launch-cap-refresh"
                    title={capabilityCostNote(harnessId)}
                    onClick={() => void fetchHarnessCapabilities(harnessId, { refresh: true })}
                  >
                    refresh catalog
                  </button>
                </p>
                <div className={optionRow} data-testid="launch-model-list">
                  {entry.envelope.capabilities.models
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
                        onClick={() => {
                          if (!snapshot) return;
                          setSelection(chooseModel(snapshot, model.key));
                          setOutcome(null);
                        }}
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
                    onClick={() => {
                      setSelection(chooseVendorDefaults());
                      setOutcome(null);
                    }}
                  >
                    vendor defaults — send no selection
                  </button>
                </div>
                {selectedModel ? (
                  efforts.length === 0 ? (
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
                            onClick={() => {
                              if (!snapshot) return;
                              setSelection((current) => chooseEffort(snapshot, current, option.key));
                              setOutcome(null);
                            }}
                          >
                            {option.key}
                          </button>
                        ))}
                      </div>
                      {selection.effort === null ? (
                        <p className={noteLine} data-testid="launch-effort-choose">
                          no advertised launch default for this model — choose an effort
                          explicitly
                        </p>
                      ) : null}
                    </>
                  )
                ) : null}
              </>
            ) : null}
          </>
        ) : null}

        <span className={heading}>Optional</span>
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
            placeholder="leaf key (optional — the server arbitrates ownership)"
            aria-label="Leaf key"
            data-testid="launch-leaf-key"
          />
        </div>

        {outcome ? <LaunchOutcome outcome={outcome} attempted={attempted} onFocusSession={onFocusSession} onClose={onClose} /> : null}

        <div className={footerRow}>
          <button
            type="button"
            className={launchButton}
            disabled={!readyToLaunch}
            data-testid="launch-submit"
            onClick={() => void launch()}
          >
            {posting ? "launching…" : "launch"}
          </button>
          <span className={noteLine} data-testid="launch-summary">
            {harnessId ? `${harnessId} · ${attempted}` : "pick a harness"}
          </span>
          <button
            type="button"
            className={quietButton}
            style={{ marginLeft: "auto" }}
            data-testid="launch-cancel"
            onClick={dismiss}
          >
            {unknownId ? "dismiss (resolves via the catalog)" : "cancel"}
          </button>
        </div>
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
  switch (outcome.path) {
    case "opened":
      return null; // the flow closes on success — the rail row is the rendering surface
    case "launch-selection-invalid":
      return (
        <div className={outcomeBox} role="alert" data-testid="launch-outcome-invalid">
          <span className={errorLine}>launch-selection-invalid: {outcome.detail}</span>
        </div>
      );
    case "open-refused":
      return (
        <div className={outcomeBox} role="alert" data-testid="launch-outcome-refused">
          <span className={errorLine}>
            {outcome.status}: {outcome.detail}
          </span>
        </div>
      );
    case "leaf-taken": {
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
    case "launch-selection-conflict":
      return (
        <div className={outcomeBox} role="alert" data-testid="launch-outcome-conflict">
          <span className={errorLine}>launch-selection-conflict{outcome.detail ? `: ${outcome.detail}` : ""}</span>
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
    case "outcome-unknown":
      return (
        <div className={outcomeBox} role="status" data-testid="launch-outcome-unknown">
          <span className={noteLine}>
            open outcome unknown — checking the catalog ({outcome.detail}). The selection is kept;
            the caller-minted id reconciles on the next poll. No re-POST is sent.
          </span>
        </div>
      );
  }
}
