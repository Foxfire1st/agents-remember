import type { CapabilitySnapshotWire, SetResultWire } from "../types/harnessCapabilities";
import type { PendingSet, SetResultSnapshot } from "./sessionCockpitStore";

// The acceptance honesty table as PURE functions (260715-FEUI-L4 R2/R3/R4, the leaf's
// acceptance-reduce example; ACPUI handover normative table). SetResult IS EVIDENCE:
//   - requested and effective never merge; the effective marker moves ONLY on a server-proved
//     effectiveValue (echo-verified) or a snapshot readback — never on queued/unknown.
//   - a clamp (echo-verified with effectiveValue ≠ requestedValue — Pi thinking) demands
//     acknowledgment and renders BOTH values persistently.
//   - unsupported keeps the prior effective state with the verbatim detail.
//   - HTTP 200 carrying unknown/unsupported is an HONEST ADAPTER RESULT, never a transport
//     failure — the boundary classifier below keeps the two vocabularies apart.

export type SetKind = "model" | "effort";

// ── HTTP boundary discrimination (R3) ──────────────────────────────────────────────────────────

export type SetRouteOutcome =
  /** HTTP 200 with a normalized SetResult — INCLUDING unknown/unsupported (honest evidence). */
  | { kind: "result"; result: SetResultWire }
  /** 404 `unknown-session` — the session is gone. */
  | { kind: "session-gone" }
  /** 409 `unsupported` — no native protocol control endpoint; controls disable with this reason. */
  | { kind: "no-native-control"; detail: string }
  /** 503 `control-unavailable` — control outage; the ONLY route error with a retry affordance. */
  | { kind: "outage"; detail: string }
  /** Network loss / malformed 200 — transport, with no server words to borrow. */
  | { kind: "transport"; detail: string };

const SET_ACCEPTANCES: ReadonlySet<string> = new Set([
  "echo-verified",
  "immediate",
  "queued",
  "unknown",
  "unsupported",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function isSetResultWire(body: unknown): body is SetResultWire {
  return (
    isRecord(body) &&
    typeof body.ok === "boolean" &&
    typeof body.acceptance === "string" &&
    SET_ACCEPTANCES.has(body.acceptance) &&
    typeof body.requestedValue === "string" &&
    (body.effectiveValue === null || typeof body.effectiveValue === "string") &&
    (body.detail === null || typeof body.detail === "string")
  );
}

/** Pure classification of one set-model/set-effort response — exhaustively table-tested. */
export function classifySetResponse(httpStatus: number | null, body: unknown): SetRouteOutcome {
  if (httpStatus === null) {
    return { kind: "transport", detail: "network failure — the set POST did not answer" };
  }
  const record = isRecord(body) ? body : {};
  const detail = typeof record.detail === "string" ? record.detail : `HTTP ${httpStatus}`;
  if (httpStatus === 200) {
    return isSetResultWire(body)
      ? { kind: "result", result: body }
      : { kind: "transport", detail: "set response did not match the SetResult shape" };
  }
  if (httpStatus === 404) return { kind: "session-gone" };
  if (httpStatus === 409) return { kind: "no-native-control", detail };
  if (httpStatus === 503) return { kind: "outage", detail };
  return { kind: "transport", detail: `unrecognized set response (HTTP ${httpStatus})` };
}

// ── The acceptance reducer (R2) ────────────────────────────────────────────────────────────────

/** True when native evidence proved a DIFFERENT effective value than requested (Pi clamp). */
export function isClamp(result: Pick<SetResultWire, "acceptance" | "requestedValue" | "effectiveValue">): boolean {
  return (
    result.acceptance === "echo-verified" &&
    result.effectiveValue !== null &&
    result.effectiveValue !== result.requestedValue
  );
}

/** Outcomes that DEMAND operator attention (R6): unsupported, unknown, and the clamp. */
export function demandsAttention(result: Pick<SetResultWire, "acceptance" | "requestedValue" | "effectiveValue">): boolean {
  return result.acceptance === "unsupported" || result.acceptance === "unknown" || isClamp(result);
}

/** What ONE SetResult does to the per-session state — the leaf's honesty table, whole. */
export interface SetReduction {
  /** The pending set for this kind after the result (undefined = resolved/cleared). */
  pendingPhase: undefined | "queued-awaiting-turn" | "unknown-verifying";
  /** Ledger entry (always appended — every SetResult is a ledger fact). */
  ledger: { acknowledged: boolean };
  /** Server-proved effective value — the ONLY path that moves the marker from a set. */
  echoEffective: string | null;
  /** unknown ⇒ exactly ONE automatic exact-session snapshot re-GET (no blind retry). */
  autoReGet: boolean;
  clamped: boolean;
}

export function reduceSetResult(result: SetResultWire): SetReduction {
  switch (result.acceptance) {
    case "echo-verified": {
      const clamped = isClamp(result);
      return {
        pendingPhase: undefined,
        // A clamp demands attention; so does the defensive no-echoed-value case (the marker
        // did not move and the operator should know).
        ledger: { acknowledged: !clamped && result.effectiveValue !== null },
        // Defensive: echo-verified without an echoed value proves nothing — the marker must
        // not move to the REQUESTED value (never inferred client-side).
        echoEffective: result.effectiveValue,
        autoReGet: false,
        clamped,
      };
    }
    case "immediate":
      // Already effective — no new echo invented; the live snapshot remains authoritative.
      return {
        pendingPhase: undefined,
        ledger: { acknowledged: true },
        echoEffective: null,
        autoReGet: false,
        clamped: false,
      };
    case "queued":
      // Marker NOT moved; the pinned chip lives on the pending until a readback promotes it.
      return {
        pendingPhase: "queued-awaiting-turn",
        ledger: { acknowledged: true },
        echoEffective: null,
        autoReGet: false,
        clamped: false,
      };
    case "unknown":
      return {
        pendingPhase: "unknown-verifying",
        ledger: { acknowledged: false },
        echoEffective: null,
        autoReGet: true,
        clamped: false,
      };
    case "unsupported":
      // Revert to prior effective (nothing moved), verbatim detail in the ledger.
      return {
        pendingPhase: undefined,
        ledger: { acknowledged: false },
        echoEffective: null,
        autoReGet: false,
        clamped: false,
      };
  }
}

/** Wire → store snapshot (null → absent) — one conversion, used by every ledger append. */
export function toResultSnapshot(result: SetResultWire): SetResultSnapshot {
  return {
    acceptance: result.acceptance,
    requestedValue: result.requestedValue,
    ...(result.effectiveValue !== null ? { effectiveValue: result.effectiveValue } : {}),
    ...(result.detail !== null ? { detail: result.detail } : {}),
  };
}

// ── Readback promotion (R4) ────────────────────────────────────────────────────────────────────

/** Does a snapshot prove one requested value effective? Model compares selectedModelKey; effort
 *  compares selectedEffort — exact keys, verbatim (Pi provider-qualified). */
export function readbackMatches(
  kind: SetKind,
  requestedValue: string,
  snapshot: CapabilitySnapshotWire,
): boolean {
  return kind === "model"
    ? snapshot.selectedModelKey === requestedValue
    : snapshot.selectedEffort === requestedValue;
}

export type ReadbackResolution =
  /** The readback proved the requested value — the pending resolves as applied. */
  | {
      kind: SetKind;
      resolution: "confirmed";
      requestedValue: string;
      fromPhase: "queued-awaiting-turn" | "unknown-verifying";
    }
  /** unknown-verifying is DEFINITIVELY resolved either way by its readback; not-applied keeps
   *  the prior effective state (the snapshot is the marker). */
  | {
      kind: SetKind;
      resolution: "not-applied";
      requestedValue: string;
      fromPhase: "unknown-verifying";
    };

/**
 * Resolve pending sets against one snapshot readback (R4):
 *   - queued-awaiting-turn resolves ONLY when confirmed (an unconfirming readback keeps it
 *     pinned — Codex promotes on the next ACCEPTED fresh turn, not any turn).
 *   - unknown-verifying resolves EITHER WAY (its single automatic readback is definitive).
 *   - inflight pendings are untouched (their POST response is still coming).
 * Codex's both-queued pair naturally resolves together on the one readback.
 */
export function resolvePendingsByReadback(
  pendingSets: { model?: PendingSet; effort?: PendingSet },
  snapshot: CapabilitySnapshotWire,
): ReadbackResolution[] {
  const resolutions: ReadbackResolution[] = [];
  for (const kind of ["model", "effort"] as const) {
    const pending = pendingSets[kind];
    if (!pending) continue;
    const confirmed = readbackMatches(kind, pending.requestedValue, snapshot);
    if (pending.phase === "queued-awaiting-turn") {
      if (confirmed) {
        resolutions.push({
          kind,
          resolution: "confirmed",
          requestedValue: pending.requestedValue,
          fromPhase: "queued-awaiting-turn",
        });
      }
    } else if (pending.phase === "unknown-verifying") {
      resolutions.push({
        kind,
        resolution: confirmed ? "confirmed" : "not-applied",
        requestedValue: pending.requestedValue,
        fromPhase: "unknown-verifying",
      });
    }
  }
  return resolutions;
}

/**
 * Snapshot re-GET triggers (R4 + the v3 drift delta, design §4):
 *   - EVERY turn-ended of the FOCUSED session re-GETs (vendor drift must never stale the chip);
 *   - any turn-ended of a session holding a queued/unknown pending re-GETs (promotion);
 *   - an unfocused session re-GETs on gaining focus (handled by the focus watcher).
 */
export function shouldRefetchOnTurnEnded(
  focused: boolean,
  pendingSets: { model?: PendingSet; effort?: PendingSet } | undefined,
): boolean {
  if (focused) return true;
  if (!pendingSets) return false;
  return (["model", "effort"] as const).some((kind) => {
    const phase = pendingSets[kind]?.phase;
    return phase === "queued-awaiting-turn" || phase === "unknown-verifying";
  });
}
