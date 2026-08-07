import { useEffect, useRef, useState, type RefObject } from "react";

import {
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
  openHostedSession,
  selectionComplete,
  type LaunchSelectionState,
  type OpenOutcome,
} from "../../data/launchFlow";
import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { notifySessionCatalogChanged, type OpenSession } from "../../data/sessions";
import { useDashboard } from "../../data/store";
import { LaunchFlowDialog, type LaunchFlowDialogProps } from "./launchFlowParts";
import { useHarnessCatalogRead, type HarnessCatalogState } from "./useHarnessCatalogRead";

// The LaunchFlow (260715-FEUI-L3 S2/S3, design §7.1): harness → model → effort → open, with
// every picker populated EXCLUSIVELY from the daemon (GET /api/harnesses + the pre-session
// capability envelope) — no hardcoded menu, no client fallback, no invented default. The pair
// rules live in data/launchFlow (pure); this component renders them plus all four open-response
// paths and the F9 transport-unknown reconciliation (the session id is caller-minted, so
// "does the row exist" resolves an unanswered POST — never a blind re-POST with a fresh id).
// The render sections live in launchFlowParts.tsx and the shared styles in launchFlowStyles.ts.

export interface LaunchPrefill {
  harness: string;
  /** The refused pair from a failed row ('Launch corrected…') — applied only where the live
   *  catalog still advertises it; an absent row simply leaves the picker unselected. */
  modelKey?: string;
  effort?: string;
}

export interface LaunchFlowProps {
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
  /** Test seam for the bounded daemon-read window. */
  harnessReadTimeoutMs?: number;
}

const defaultMint = (): string => crypto.randomUUID();

function useLaunchFlowForm(open: boolean, prefill: LaunchPrefill | undefined) {
  const [harnessId, setHarnessId] = useState<string | null>(null);
  const [selection, setSelection] = useState<LaunchSelectionState>(EMPTY_SELECTION);
  const [label, setLabel] = useState("");
  const [leafKey, setLeafKey] = useState("");
  const [posting, setPosting] = useState(false);
  const [outcome, setOutcome] = useState<OpenOutcome | null>(null);
  const [unknownId, setUnknownId] = useState<string | null>(null);
  const prefillPairRef = useRef<{ modelKey: string; effort?: string } | null>(null);

  // Reset the launch form on every open. The catalog hook owns the one live request separately.
  useEffect(() => {
    if (!open) return undefined;
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
    return undefined;
  }, [open, prefill]);

  return {
    harnessId,
    setHarnessId,
    selection,
    setSelection,
    label,
    setLabel,
    leafKey,
    setLeafKey,
    posting,
    setPosting,
    outcome,
    setOutcome,
    unknownId,
    setUnknownId,
    prefillPairRef,
  };
}

function useLaunchCapability(open: boolean, harnessId: string | null) {
  const entry = useCapabilityCatalog((state) =>
    harnessId ? state.perHarness[harnessId] : undefined,
  );
  // Selecting a harness reads its live envelope (single-flighted; a daemon cache hit is cheap).
  useEffect(() => {
    if (!open || !harnessId) return;
    void fetchHarnessCapabilities(harnessId);
  }, [open, harnessId]);
  const snapshot = entry?.envelope?.capabilities;
  return { entry, snapshot };
}

function usePrefillApply(
  open: boolean,
  snapshot: ReturnType<typeof useLaunchCapability>["snapshot"],
  prefillPairRef: RefObject<{ modelKey: string; effort?: string } | null>,
  setSelection: (next: LaunchSelectionState) => void,
) {
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
  }, [open, snapshot, setSelection, prefillPairRef]);
}

function useOutcomeWatch(
  open: boolean,
  unknownId: string | null,
  sessions: OpenSession[],
  onFocusSession: (id: string) => void,
  onClose: () => void,
) {
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
}

function openOptions(
  harnessId: string,
  selection: LaunchSelectionState,
  label: string,
  leafKey: string,
  lifecycleId: string | undefined,
) {
  return {
    harness: harnessId,
    selection,
    ...(label.trim() ? { label: label.trim() } : {}),
    ...(leafKey.trim() ? { leafKey: leafKey.trim() } : {}),
    ...(lifecycleId ? { lifecycleId } : {}),
  };
}

async function settleOpened(
  result: Extract<OpenOutcome, { path: "opened" }>,
  launchAuthority: ReturnType<typeof captureCatalogAuthority>,
  harnessId: string,
  onFocusSession: (id: string) => void,
  onClose: () => void,
): Promise<void> {
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
}

function useLaunchSubmit({
  readyToLaunch,
  harnessId,
  selection,
  label,
  leafKey,
  lifecycleId,
  mintSessionId,
  setPosting,
  setOutcome,
  setUnknownId,
  onFocusSession,
  onClose,
}: {
  readyToLaunch: boolean;
  harnessId: string | null;
  selection: LaunchSelectionState;
  label: string;
  leafKey: string;
  lifecycleId: string | undefined;
  mintSessionId: () => string;
  setPosting: (value: boolean) => void;
  setOutcome: (value: OpenOutcome | null) => void;
  setUnknownId: (value: string | null) => void;
  onFocusSession: (id: string) => void;
  onClose: () => void;
}) {
  const launch = async () => {
    if (!readyToLaunch || !harnessId) return;
    // A dev-scenario reset cannot cancel an open POST already in flight. Carry its original catalog
    // authority through every follow-on edge so settlement cannot adopt the successor fixture.
    const launchAuthority = captureCatalogAuthority();
    const sessionId = mintSessionId();
    setPosting(true);
    setOutcome(null);
    const result = await openHostedSession(
      sessionId,
      openOptions(harnessId, selection, label, leafKey, lifecycleId),
    );
    if (!catalogAuthorityIsCurrent(launchAuthority)) return;
    setPosting(false);
    if (result.path === "opened") {
      await settleOpened(result, launchAuthority, harnessId, onFocusSession, onClose);
      return;
    }
    if (result.path === "outcome-unknown") setUnknownId(sessionId);
    setOutcome(result);
  };
  return launch;
}

function resolveLaunchBlockReason(
  posting: boolean,
  unknownId: string | null,
  harnessId: string | null,
  catalog: HarnessCatalogState,
  selectedHarness: { name: string; detected: boolean } | undefined,
  selection: LaunchSelectionState,
): string | null {
  if (posting) return null;
  if (unknownId !== null) {
    return "resolving the previous launch via the catalog…";
  }
  if (!harnessId) return "pick a harness";
  if (
    catalog.status === "ready" &&
    selectedHarness !== undefined &&
    !selectedHarness.detected
  ) {
    return `${selectedHarness.name} is not installed on this daemon`;
  }
  if (!selectionComplete(selection)) return "pick a model and effort";
  return null;
}

function selectedHarnessFor(
  catalog: HarnessCatalogState,
  harnessId: string | null,
) {
  return catalog.status === "ready"
    ? catalog.harnesses.find((harness) => harness.id === harnessId)
    : undefined;
}

function readyToLaunchFor(
  selectedHarness: { detected: boolean } | undefined,
  selection: LaunchSelectionState,
  posting: boolean,
  unknownId: string | null,
): boolean {
  return (
    selectedHarness?.detected === true &&
    selectionComplete(selection) &&
    !posting &&
    unknownId === null
  );
}

function attemptedCopy(selection: LaunchSelectionState): string {
  return selection.vendorDefaults
    ? "vendor defaults (no selection sent)"
    : `${selection.modelKey ?? "—"} · ${selection.effort ?? "—"}`;
}

function buildLaunchHandlers(
  form: ReturnType<typeof useLaunchFlowForm>,
  snapshot: ReturnType<typeof useLaunchCapability>["snapshot"],
  retryHarnessCatalog: () => void,
  launch: () => Promise<void>,
  dismiss: () => void,
  onFocusSession: (id: string) => void,
  onClose: () => void,
): Omit<
  LaunchFlowDialogProps,
  | "catalog" | "harnessId" | "entry" | "selection" | "label" | "leafKey" | "outcome"
  | "posting" | "unknownId" | "readyToLaunch" | "attempted" | "launchBlockReason"
> {
  return {
    onRetryHarness: retryHarnessCatalog,
    onPickHarness: (id) => {
      form.setHarnessId(id);
      form.setSelection(EMPTY_SELECTION);
      form.setOutcome(null);
    },
    onSelectModel: (key) => {
      if (snapshot) form.setSelection(chooseModel(snapshot, key));
      form.setOutcome(null);
    },
    onSelectEffort: (key) => {
      if (snapshot) {
        form.setSelection((current) => chooseEffort(snapshot, current, key));
      }
      form.setOutcome(null);
    },
    onVendorDefaults: () => {
      form.setSelection(chooseVendorDefaults());
      form.setOutcome(null);
    },
    onRetryCapabilities: () => {
      if (form.harnessId) void fetchHarnessCapabilities(form.harnessId);
    },
    onRefreshCapabilities: () => {
      if (form.harnessId) {
        void fetchHarnessCapabilities(form.harnessId, { refresh: true });
      }
    },
    setLabel: form.setLabel,
    setLeafKey: form.setLeafKey,
    onLaunch: () => void launch(),
    onDismiss: dismiss,
    onOutcomeFocus: onFocusSession,
    onOutcomeClose: onClose,
  };
}

export function LaunchFlow({
  open,
  prefill,
  sessions,
  lifecycleId,
  onClose,
  onFocusSession,
  mintSessionId = defaultMint,
  harnessReadTimeoutMs,
}: LaunchFlowProps) {
  const form = useLaunchFlowForm(open, prefill);
  const servingBootedAt = useDashboard((state) => state.servingBuild?.bootedAt ?? null);
  const { catalog, retry: retryHarnessCatalog } = useHarnessCatalogRead({
    open,
    servingBootedAt,
    ...(harnessReadTimeoutMs === undefined ? {} : { timeoutMs: harnessReadTimeoutMs }),
  });
  const { entry, snapshot } = useLaunchCapability(open, form.harnessId);
  usePrefillApply(open, snapshot, form.prefillPairRef, form.setSelection);
  useOutcomeWatch(open, form.unknownId, sessions, onFocusSession, onClose);

  const selectedHarness = selectedHarnessFor(catalog, form.harnessId);
  const readyToLaunch = readyToLaunchFor(selectedHarness, form.selection, form.posting, form.unknownId);
  const attempted = attemptedCopy(form.selection);
  const launchBlockReason = resolveLaunchBlockReason(
    form.posting,
    form.unknownId,
    form.harnessId,
    catalog,
    selectedHarness,
    form.selection,
  );
  // An explicit dismiss also ENDS the unknown-outcome watch IMMEDIATELY (delta-verify residual):
  // a stale unknownId surviving dismissal would fire one late focus steal on the next open's
  // first effect pass, before the reset effect's state update lands.
  const dismiss = () => {
    form.setUnknownId(null);
    onClose();
  };
  const launch = useLaunchSubmit({ readyToLaunch, harnessId: form.harnessId, selection: form.selection, label: form.label, leafKey: form.leafKey, lifecycleId, mintSessionId, setPosting: form.setPosting, setOutcome: form.setOutcome, setUnknownId: form.setUnknownId, onFocusSession, onClose });
  const handlers = buildLaunchHandlers(form, snapshot, retryHarnessCatalog, launch, dismiss, onFocusSession, onClose);

  if (!open) return null;

  return (
    <LaunchFlowDialog
      catalog={catalog}
      harnessId={form.harnessId}
      entry={entry}
      selection={form.selection}
      label={form.label}
      leafKey={form.leafKey}
      outcome={form.outcome}
      posting={form.posting}
      unknownId={form.unknownId}
      readyToLaunch={readyToLaunch}
      attempted={attempted}
      launchBlockReason={launchBlockReason}
      {...handlers}
    />
  );
}
