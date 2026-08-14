import type {
  CapabilitySnapshotWire,
  EffortOptionWire,
  ModelCapabilityWire,
} from "../types/harnessCapabilities";
import type { PerSessionCockpit } from "./sessionCockpitStore";

// Exact-session capability sourcing (260715-FEUI-L4 R1, design §6.1): the options source for a
// LIVE session is `GET /api/terminal/{session}/capabilities` ONLY — never the pre-session harness
// cache (data/capabilityCatalog stays pre-session-only). HONESTY INVARIANTS:
//   - The effort menu derives from the SELECTED MODEL ROW's `effortOptions` (filtered
//     sessionSettable) plus the nullable top-level `selectedEffort` — NEVER from configOptions
//     presence: the serializer omits the thought_level option exactly when selectedEffort is null
//     (harness_capabilities.py CapabilitySnapshot.config_options), which is the fresh-Claude case.
//   - Null selectedEffort with a non-empty menu = 'effort not echoed', NOT 'no effort control'.
//   - A model row without effortOptions = 'no effort control for this model' — never inherited.
//   - Advertised native order preserved; Pi provider-qualified keys verbatim.

/** The classified outcome of one exact-session capability GET — every server path mirrored. */
export type SessionSnapshotOutcome =
  | { kind: "snapshot"; snapshot: CapabilitySnapshotWire }
  /** 404 `unknown-session` — the session is gone (or no longer running). */
  | { kind: "session-gone" }
  /** 409 `unsupported` — no native protocol control endpoint (legacy raw / plain sessions). */
  | { kind: "no-native-control"; detail: string }
  /** 503 `control-unavailable` — control outage; retryable. */
  | { kind: "outage"; detail: string }
  /** Network loss / malformed body — the transport failed, the server said nothing usable. */
  | { kind: "transport"; detail: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isEffortOption(value: unknown): value is EffortOptionWire {
  return (
    isRecord(value) &&
    typeof value.key === "string" &&
    typeof value.displayName === "string" &&
    typeof value.launchSettable === "boolean" &&
    typeof value.sessionSettable === "boolean"
  );
}

function isModelRow(value: unknown): value is ModelCapabilityWire {
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

/** The exact-session route returns the BARE snapshot (capability_snapshot_json), no envelope. */
export function isSessionSnapshot(body: unknown): body is CapabilitySnapshotWire {
  if (!isRecord(body)) return false;
  const candidate = body as Partial<CapabilitySnapshotWire>;
  return (
    Array.isArray(candidate.models) &&
    candidate.models.every(isModelRow) &&
    (candidate.selectedModelKey === null || typeof candidate.selectedModelKey === "string") &&
    (candidate.selectedEffort === null || typeof candidate.selectedEffort === "string")
  );
}

/** Pure classification of one GET /api/terminal/{s}/capabilities response (R1/R3/F16). */
export function classifySessionCapabilitiesResponse(
  httpStatus: number | null,
  body: unknown,
): SessionSnapshotOutcome {
  if (httpStatus === null) {
    return { kind: "transport", detail: "network failure — the capability GET did not answer" };
  }
  const record = isRecord(body) ? body : {};
  const detail = typeof record.detail === "string" ? record.detail : `HTTP ${httpStatus}`;
  if (httpStatus === 200) {
    return isSessionSnapshot(body)
      ? { kind: "snapshot", snapshot: body }
      : {
          kind: "transport",
          detail: "capability response did not match the exact-session snapshot shape",
        };
  }
  if (httpStatus === 404) return { kind: "session-gone" };
  if (httpStatus === 409) return { kind: "no-native-control", detail };
  if (httpStatus === 503) return { kind: "outage", detail };
  return { kind: "transport", detail: `unrecognized capability response (HTTP ${httpStatus})` };
}

/**
 * GET the exact live session's snapshot and classify every path. NEVER the pre-session route:
 * the URL is the one honest assertion tests pin.
 */
export async function fetchSessionCapabilities(
  sessionId: string,
  base = "",
): Promise<SessionSnapshotOutcome> {
  let response: Response;
  try {
    response = await fetch(`${base}/api/terminal/${encodeURIComponent(sessionId)}/capabilities`);
  } catch (error) {
    return {
      kind: "transport",
      detail: error instanceof Error ? error.message : String(error),
    };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  return classifySessionCapabilitiesResponse(response.status, body);
}

// ── The corrected effort-menu derivation (the leaf's header note — R1) ─────────────────────────

export type EffortMenu =
  /** The snapshot echoes no selected model (or the key is absent from the catalog): there is no
   *  row to gate a menu on — the control says so instead of inventing one. */
  | { kind: "no-selected-model" }
  /** The selected row advertises NO effort options — 'no effort control for this model'. */
  | { kind: "no-effort-control"; modelKey: string }
  | {
      kind: "menu";
      modelKey: string;
      /** sessionSettable options in advertised NATIVE order — never reordered client-side. */
      options: EffortOptionWire[];
      /** The top-level selectedEffort — null = 'effort not echoed' (fresh Claude), NOT absent. */
      selected: string | null;
      /** The row's advertised default when session-settable — the pre-highlight (R1). */
      defaultEffort: string | null;
    };

export function modelRowByKey(
  snapshot: CapabilitySnapshotWire,
  key: string | null,
): ModelCapabilityWire | undefined {
  if (key === null) return undefined;
  return snapshot.models.find((model) => model.key === key);
}

/** Session-settable menu for ONE row, advertised order preserved (filter never sorts). */
export function sessionSettableEfforts(row: ModelCapabilityWire): EffortOptionWire[] {
  return row.effortOptions.filter((option) => option.sessionSettable);
}

/**
 * Derive the effort menu for a model row within a snapshot. `modelKey` defaults to the
 * snapshot's selectedModelKey; passing a staged key re-gates the menu on THAT row (R1).
 * configOptions is deliberately never consulted.
 */
export function deriveEffortMenu(
  snapshot: CapabilitySnapshotWire,
  modelKey: string | null = snapshot.selectedModelKey,
): EffortMenu {
  const row = modelRowByKey(snapshot, modelKey);
  if (!row) return { kind: "no-selected-model" };
  const options = sessionSettableEfforts(row);
  if (options.length === 0) return { kind: "no-effort-control", modelKey: row.key };
  const defaultEffort =
    row.defaultEffort !== null && options.some((option) => option.key === row.defaultEffort)
      ? row.defaultEffort
      : null;
  return {
    kind: "menu",
    modelKey: row.key,
    options,
    // The menu's marked value is honest ONLY for the snapshot's own selected row; a staged
    // (different) row has no echoed selection yet.
    selected: modelKey === snapshot.selectedModelKey ? snapshot.selectedEffort : null,
    defaultEffort,
  };
}

/** The rows a session picker offers — the server's own visibility rule (config projection):
 *  the selected row always shows (even hidden/unselectable); others need selectable && !hidden. */
export function visibleModelRows(snapshot: CapabilitySnapshotWire): ModelCapabilityWire[] {
  return snapshot.models.filter(
    (model) => model.key === snapshot.selectedModelKey || (model.selectable && !model.hidden),
  );
}

/**
 * Cycle-effort target (R7): the next sessionSettable option after `current`, wrapping, in
 * advertised order. When `current` is absent from the menu (not echoed / stale), the cycle
 * starts from the first (direction 1) or last (direction -1) option — never an invented value.
 */
export function cycleEffortTarget(
  options: EffortOptionWire[],
  current: string | null,
  direction: 1 | -1,
): string | null {
  if (options.length === 0) return null;
  const index = current === null ? -1 : options.findIndex((option) => option.key === current);
  if (index === -1) return options[direction === 1 ? 0 : options.length - 1].key;
  return options[(index + direction + options.length) % options.length].key;
}

// ── Effective selection (the marker every chip reads) ──────────────────────────────────────────

export interface EffectiveSelection {
  modelKey: string | null;
  effort: string | null;
  /** Where each value came from — snapshot readback or a LATER echo-verified SetResult. */
  modelSource: "snapshot" | "echo" | "none";
  effortSource: "snapshot" | "echo" | "none";
}

/**
 * The one effective-marker derivation: the exact-session snapshot's selected values, overlaid by
 * any echo-verified SetResult evidence NEWER than the snapshot. Both are server truths — the
 * freshest wins; nothing here is ever inferred from a request.
 */
export function effectiveSelection(
  cockpit: Pick<PerSessionCockpit, "liveSnapshot" | "echoEvidence"> | undefined,
): EffectiveSelection {
  const none: EffectiveSelection = {
    modelKey: null,
    effort: null,
    modelSource: "none",
    effortSource: "none",
  };
  if (!cockpit) return none;
  const snapshot = cockpit.liveSnapshot;
  const pick = (
    fromSnapshot: string | null,
    echo: { value: string; at: number } | undefined,
  ): { value: string | null; source: "snapshot" | "echo" | "none" } => {
    if (echo && (!snapshot || echo.at > snapshot.fetchedAt)) {
      return { value: echo.value, source: "echo" };
    }
    if (snapshot) return { value: fromSnapshot, source: "snapshot" };
    return { value: null, source: "none" };
  };
  const model = pick(snapshot?.payload.selectedModelKey ?? null, cockpit.echoEvidence.model);
  const effort = pick(snapshot?.payload.selectedEffort ?? null, cockpit.echoEvidence.effort);
  return {
    modelKey: model.value,
    effort: effort.value,
    modelSource: model.source,
    effortSource: effort.source,
  };
}
