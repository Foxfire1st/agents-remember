import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import type {
  CapabilityCacheStatus,
  CapabilityEnvelope,
} from "../types/harnessCapabilities";
import { CAPABILITY_SCHEMA } from "../types/harnessCapabilities";

// The pre-session capability catalog store (260715-FEUI-L3 R1, design §4.2): one envelope per
// harness, mirrored from `GET /api/harnesses/{h}/capabilities` and NOTHING else. HONESTY
// INVARIANTS live in this shape:
//   - DYNAMIC-ONLY: there is no fallback catalog, no seeded rows, no persistence — a picker is
//     empty until the daemon answers, and a reload starts from scratch (memory-only store).
//   - Errors keep the server's verbatim status+detail (404/409 capability-unavailable, 503
//     control-unavailable incl. the failed-refresh quarantine) — rendered as-is, never softened,
//     never caught-and-retried with a fallback.
//   - On ANY error the harness's envelope is DROPPED, mirroring the daemon's own quarantine
//     posture (a failed refresh pops the exact cache entry it evaluated): a stale success must
//     never masquerade as current after the daemon refused to vouch for it.

export type CapabilityFetchState = "idle" | "loading" | "refreshing" | "error";

/** A capability-route failure kept verbatim; `httpStatus` null = transport (fetch threw). */
export interface CapabilityCatalogError {
  httpStatus: number | null;
  /** The server's `status` word (`capability-unavailable` / `control-unavailable`) or `transport`. */
  status: string;
  detail: string;
}

export interface PerHarnessCapabilities {
  fetchState: CapabilityFetchState;
  /** Replaced whole on success, dropped on error — never merged, never invented. */
  envelope?: CapabilityEnvelope;
  error?: CapabilityCatalogError;
  fetchedAt?: number;
}

export interface CapabilityCatalogState {
  perHarness: Record<string, PerHarnessCapabilities>;
}

export const capabilityCatalogStore = createStore<CapabilityCatalogState>(() => ({
  perHarness: {},
}));

export const useCapabilityCatalog = <T>(selector: (state: CapabilityCatalogState) => T): T =>
  useStore(capabilityCatalogStore, selector);

const IDLE: PerHarnessCapabilities = { fetchState: "idle" };

export function harnessCapabilities(harness: string): PerHarnessCapabilities {
  return capabilityCatalogStore.getState().perHarness[harness] ?? IDLE;
}

function patchHarness(harness: string, entry: PerHarnessCapabilities): void {
  capabilityCatalogStore.setState((state) => ({
    perHarness: { ...state.perHarness, [harness]: entry },
  }));
}

/**
 * COST HONESTY (R2): the daemon runs the same short-lived native discovery process on a cache
 * MISS as on refresh=true — the flag only forces invalidation. So the miss/loading treatment and
 * the refresh treatment carry the SAME cost naming. GENERIC copy only: observed per-harness
 * seconds are L5 evidence and never become UI constants.
 */
export function capabilityCostNote(harness: string): string {
  return `starts a short-lived native ${harness} process`;
}

/** Loading copy — both modes carry the SAME `capabilityCostNote` cost naming (R2). */
export function capabilityLoadingCopy(harness: string, mode: "initial" | "refresh"): string {
  return mode === "refresh"
    ? `refreshing ${harness} capabilities — ${capabilityCostNote(harness)}`
    : `loading ${harness} capabilities — a cache miss ${capabilityCostNote(harness)} (same cost as an explicit refresh)`;
}

/** Post-load cache-status honesty: name whether native discovery actually ran for THIS read. */
export function cacheStatusNote(cacheStatus: CapabilityCacheStatus): string {
  switch (cacheStatus) {
    case "hit":
      return "cache hit — served from the daemon cache, no native process ran";
    case "miss":
      return "cache miss — the daemon ran the same short-lived native discovery as a refresh";
    case "refreshed":
      return "refreshed — a short-lived native discovery process ran";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

// Validates every field a picker actually reads (review finding 4): a malformed 200 must land
// in the honest schema-mismatch error path, never crash rendering after being adopted.
function isEffortOption(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.key === "string" &&
    typeof value.displayName === "string" &&
    typeof value.launchSettable === "boolean" &&
    typeof value.sessionSettable === "boolean"
  );
}

function isModelRow(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.key === "string" &&
    typeof value.displayName === "string" &&
    typeof value.hidden === "boolean" &&
    typeof value.selectable === "boolean" &&
    (value.defaultEffort === null || typeof value.defaultEffort === "string") &&
    Array.isArray(value.effortOptions) &&
    value.effortOptions.every(isEffortOption)
  );
}

function isEnvelope(body: unknown): body is CapabilityEnvelope {
  if (!isRecord(body)) return false;
  const candidate = body as Partial<CapabilityEnvelope>;
  return (
    candidate.schema === CAPABILITY_SCHEMA &&
    typeof candidate.harness === "string" &&
    (candidate.cacheStatus === "hit" ||
      candidate.cacheStatus === "miss" ||
      candidate.cacheStatus === "refreshed") &&
    typeof candidate.installFingerprint === "string" &&
    isRecord(candidate.capabilities) &&
    Array.isArray(candidate.capabilities.models) &&
    candidate.capabilities.models.every(isModelRow)
  );
}

async function readError(response: Response): Promise<CapabilityCatalogError> {
  // A non-JSON/shapeless error body is a TRANSPORT-level fact (review finding 2): the server's
  // own vocabulary (`capability-unavailable`/`control-unavailable`) is used only when the
  // server actually said it — a 502 gateway page must never wear a server status word.
  let status = "transport";
  let detail = `HTTP ${response.status}`;
  try {
    const body = (await response.json()) as { status?: unknown; detail?: unknown };
    if (typeof body.status === "string") status = body.status;
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // Keep the honest HTTP status line — never invented detail.
  }
  return { httpStatus: response.status, status, detail };
}

// Per-harness single-flight (the daemon has its own per-key lock; the client mirrors it so a
// re-rendering picker never stacks concurrent GETs). Refresh semantics are EXPLICIT (review
// finding 3): a plain read joins any in-flight read, but `refresh: true` is a promise of
// daemon-side invalidation — it joins only an in-flight REFRESH, and chains behind an in-flight
// plain read so the invalidation actually happens.
interface InflightRead {
  promise: Promise<PerHarnessCapabilities>;
  refresh: boolean;
}
const inflight = new Map<string, InflightRead>();
let catalogGeneration = 0;

/**
 * Dev-bench scenario boundary: discard both rendered catalog state and single-flight ownership.
 * The generation guard is required because a fetch started by the scenario being unmounted may
 * settle after the selector has installed the next authority; that stale result must not repopulate
 * the new scenario's otherwise-empty dynamic catalog.
 */
export function resetCapabilityCatalogForDev(): void {
  catalogGeneration += 1;
  inflight.clear();
  capabilityCatalogStore.setState({ perHarness: {} });
}

/**
 * Fetch one harness's pre-session catalog. `refresh=true` forces daemon-side invalidation (the
 * auth/install-change path) — never silently satisfied by a joined plain read. Resolves with
 * the resulting per-harness entry (also written to the store); NEVER throws — failures land as
 * `fetchState: "error"` with the verbatim detail.
 */
export function fetchHarnessCapabilities(
  harness: string,
  options: { refresh?: boolean; base?: string } = {},
): Promise<PerHarnessCapabilities> {
  const generation = catalogGeneration;
  const refresh = options.refresh ?? false;
  const base = options.base ?? "";
  const pending = inflight.get(harness);
  if (pending) {
    if (!refresh || pending.refresh) return pending.promise;
    // Chain the demanded refresh behind the in-flight plain read (no concurrent double-GET,
    // no silently-stale result). The recursive call finds the map slot released and runs.
    const chained: InflightRead = { refresh: true, promise: pending.promise };
    chained.promise = pending.promise.then(() => {
      if (inflight.get(harness) === chained) inflight.delete(harness);
      if (generation !== catalogGeneration) return harnessCapabilities(harness);
      return fetchHarnessCapabilities(harness, options);
    });
    inflight.set(harness, chained);
    return chained.promise;
  }

  const current = harnessCapabilities(harness);
  patchHarness(harness, {
    ...current,
    // A refresh — or any re-read over an existing snapshot — is 'refreshing'; only the very
    // first read of a harness is 'loading'. Both render the same cost naming (R2).
    fetchState: refresh || current.envelope ? "refreshing" : "loading",
    error: undefined,
  });

  const run = (async (): Promise<PerHarnessCapabilities> => {
    let entry: PerHarnessCapabilities;
    try {
      const query = refresh ? "?refresh=true" : "";
      const response = await fetch(
        `${base}/api/harnesses/${encodeURIComponent(harness)}/capabilities${query}`,
      );
      if (!response.ok) {
        entry = { fetchState: "error", error: await readError(response) };
      } else {
        const body: unknown = await response.json();
        entry = isEnvelope(body)
          ? { fetchState: "idle", envelope: body, fetchedAt: Date.now() }
          : {
              fetchState: "error",
              error: {
                httpStatus: response.status,
                status: "transport",
                detail: "capability response did not match ar-harness-capabilities/v1",
              },
            };
      }
    } catch (error) {
      entry = {
        fetchState: "error",
        error: {
          httpStatus: null,
          status: "transport",
          detail: error instanceof Error ? error.message : String(error),
        },
      };
    }
    if (generation === catalogGeneration) patchHarness(harness, entry);
    return entry;
  })();

  // Identity-guarded release: a chained refresh may have replaced this slot already, and its
  // entry must not be deleted by the older read's completion.
  const started: InflightRead = { refresh, promise: run };
  started.promise = run.finally(() => {
    if (inflight.get(harness) === started) inflight.delete(harness);
  });
  inflight.set(harness, started);
  return started.promise;
}
