import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import { hydrateTerminalSessionsFromCatalog } from "./catalogPoll";
import {
  notifySessionCatalogChanged,
  sessionStore,
  type OpenSession,
} from "./sessions";
import {
  cleanupLandedTerminalSessions,
  type LandedCleanupResult,
} from "./terminal";

// Session lifecycle actions for the cockpit (260715-FEUI-L6 R5). Terminate keeps the server's
// WHOLE answer: the `/terminate` response may carry `controlStopDetail` (the graceful control
// stop failed on an already-dead bridge — e.g. "control command queue is stopped"). That detail
// is a STOP RESIDUAL: an informational fact about a session that terminated SUCCESSFULLY. It is
// never a "termination failed" state and never discarded — it is recorded here and rendered as
// an informational line (stage note + inspector), because the terminated row itself becomes a
// tombstone the rail no longer shows.
//
// Retire note: `POST /api/terminal/{id}/retire` requires an authorized ACTOR SESSION (manager /
// orchestrator seat) — the dashboard has no seat identity, so the cockpit's operator action is
// terminate; retirement happens agent-side. The cockpit still renders retire residuals
// (`controlRaw.retireControlStopError` on retired rows) with the same informational posture.

export interface StopResidual {
  sessionId: string;
  label: string;
  kind: "terminate" | "retire";
  /** The server's words, verbatim (controlStopDetail / retireControlStopError). */
  detail: string;
  at: number;
}

export interface CleanupTarget {
  id: string;
  label: string;
}

export interface CleanupFailure {
  /** Snapshot at the action boundary: later catalog/label drift cannot obscure intended rows. */
  targets: CleanupTarget[];
}

interface LifecycleNoticeState {
  /** Newest-first informational residuals — kept until explicitly dismissed, never dropped. */
  residuals: StopResidual[];
  /** The last bulk-cleanup outcome (closed/skipped honesty), null when none/dismissed. */
  cleanupOutcome: LandedCleanupResult | null;
  /** The authority result was unavailable; exact attempted rows remain visible and retryable. */
  cleanupFailure: CleanupFailure | null;
  /** sessionIds whose retire residual was already swept — a dismissal must stay dismissed
   *  across poll beats (the catalog row keeps carrying the fact forever). */
  sweptRetire: Record<string, true>;
  recordResidual: (residual: StopResidual) => void;
  dismissResidual: (sessionId: string, at: number) => void;
  /** Review F1 (sev-3): capture `retireControlStopError` for EVERY row — focus-independent. */
  sweepRetireResiduals: (sessions: readonly OpenSession[]) => void;
  recordCleanupOutcome: (outcome: LandedCleanupResult) => void;
  dismissCleanupOutcome: () => void;
  recordCleanupFailure: (failure: CleanupFailure) => void;
  dismissCleanupFailure: () => void;
}

export const lifecycleNoticeStore = createStore<LifecycleNoticeState>(
  (set) => ({
    residuals: [],
    cleanupOutcome: null,
    cleanupFailure: null,
    sweptRetire: {},
    recordResidual: (residual) =>
      set((state) => ({ residuals: [residual, ...state.residuals] })),
    dismissResidual: (sessionId, at) =>
      set((state) => ({
        residuals: state.residuals.filter(
          (entry) => !(entry.sessionId === sessionId && entry.at === at),
        ),
      })),
    sweepRetireResiduals: (sessions) =>
      set((state) => {
        let residuals = state.residuals;
        let sweptRetire = state.sweptRetire;
        let changed = false;
        for (const session of sessions) {
          const detail = session.controlRaw?.retireControlStopError;
          if (
            typeof detail !== "string" ||
            !detail ||
            state.sweptRetire[session.id]
          )
            continue;
          if (!changed) {
            residuals = [...residuals];
            sweptRetire = { ...sweptRetire };
            changed = true;
          }
          residuals = [
            {
              sessionId: session.id,
              label: session.label,
              kind: "retire",
              detail,
              at: Date.now(),
            },
            ...residuals,
          ];
          sweptRetire[session.id] = true;
        }
        return changed ? { residuals, sweptRetire } : state;
      }),
    recordCleanupOutcome: (cleanupOutcome) =>
      set({ cleanupOutcome, cleanupFailure: null }),
    dismissCleanupOutcome: () => set({ cleanupOutcome: null }),
    recordCleanupFailure: (cleanupFailure) =>
      set({ cleanupFailure, cleanupOutcome: null }),
    dismissCleanupFailure: () => set({ cleanupFailure: null }),
  }),
);

export const useLifecycleNotices = <T>(
  selector: (state: LifecycleNoticeState) => T,
): T => useStore(lifecycleNoticeStore, selector);

// ── The retire-residual sweep (review F1, sev-3) ────────────────────────────────────────────
// The catalog serves retired rows forever and every hydrate lands them in the sessionStore, but
// they tombstone out of the rail — so a residual captured only on the FOCUSED seat's handoff
// would silently drop for unfocused retirements (and on reload). This refcounted subscription
// sweeps EVERY registry change (poll hydrates AND direct patches), independent of focus; the
// swept-set makes capture once-per-session so a dismissal stays dismissed across beats.
let sweepRefs = 0;
let unsubscribeSweep: (() => void) | null = null;

export function startRetireResidualSweep(): () => void {
  sweepRefs += 1;
  if (sweepRefs === 1) {
    const sweep = (sessions: readonly OpenSession[]) =>
      lifecycleNoticeStore.getState().sweepRetireResiduals(sessions);
    sweep(sessionStore.getState().sessions); // rows already present at subscribe time count too
    unsubscribeSweep = sessionStore.subscribe((state) => sweep(state.sessions));
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    sweepRefs -= 1;
    if (sweepRefs === 0) {
      unsubscribeSweep?.();
      unsubscribeSweep = null;
    }
  };
}

export interface TerminateOutcome {
  ok: boolean;
  /** Informational stop residual from the terminate response — the session IS terminated. */
  controlStopDetail?: string;
  /** The POST failure, verbatim (review finding 4): the terminate did NOT happen. */
  error?: string;
}

/**
 * Terminate one session, keeping the response's `controlStopDetail` instead of discarding the
 * body (the boolean-only helper in data/terminal.ts predates the residual requirement). A
 * failed POST keeps the server's words so the caller can render them verbatim + retry.
 */
export async function terminateSessionDetailed(
  sessionId: string,
): Promise<TerminateOutcome> {
  try {
    const response = await fetch(
      `/api/terminal/${encodeURIComponent(sessionId)}/terminate`,
      {
        method: "POST",
      },
    );
    if (!response.ok) {
      const body = await response.text().catch(() => "");
      return { ok: false, error: body || `HTTP ${response.status}` };
    }
    const body = (await response.json().catch(() => null)) as {
      controlStopDetail?: unknown;
    } | null;
    const detail =
      body && typeof body.controlStopDetail === "string"
        ? body.controlStopDetail
        : undefined;
    return { ok: true, controlStopDetail: detail };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

/**
 * The cockpit's terminate flow: POST, mirror the store (tombstone + close), and record any stop
 * residual for the informational surfaces. Returns the outcome so callers can announce it.
 */
export async function endSessionDetailed(
  session: OpenSession,
): Promise<TerminateOutcome> {
  const outcome = await terminateSessionDetailed(session.id);
  if (!outcome.ok) return outcome;
  sessionStore.getState().setStatus(session.id, "terminated");
  sessionStore.getState().close(session.id);
  notifySessionCatalogChanged("terminate", session.id);
  if (outcome.controlStopDetail) {
    lifecycleNoticeStore.getState().recordResidual({
      sessionId: session.id,
      label: session.label,
      kind: "terminate",
      detail: outcome.controlStopDetail,
      at: Date.now(),
    });
  }
  return outcome;
}

/**
 * Bulk-end landed seats via the landed-cleanup route, keeping the route's OWN outcome (closed +
 * skipped with reasons) for the honest post-execution note instead of dropping the skips.
 */
export async function endLandedDetailed(
  sessions: readonly CleanupTarget[],
): Promise<LandedCleanupResult | null> {
  const result = await cleanupLandedTerminalSessions(
    sessions.map((session) => session.id),
  );
  if (!result) {
    lifecycleNoticeStore.getState().recordCleanupFailure({
      targets: sessions.map(({ id, label }) => ({ id, label })),
    });
    return null;
  }
  for (const id of result.closedSessions) {
    sessionStore.getState().setStatus(id, "terminated");
    sessionStore.getState().close(id);
  }
  notifySessionCatalogChanged("terminate");
  lifecycleNoticeStore.getState().recordCleanupOutcome(result);
  void hydrateTerminalSessionsFromCatalog(true, new Set(result.closedSessions));
  return result;
}
