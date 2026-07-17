import type { EvidenceTier } from "./sessionCockpitStore";

// The launch-evidence tier machine (260715-FEUI-L3 R7): assignment is gated on CONTROL STATE,
// never on the open response — `resolvedModel`/`resolvedEffort` are the REQUESTED pair persisted
// verbatim BEFORE any validation (terminal_opener.py `_resolved_pair`), so rendering them as
// validated during the starting window (or on a failed row) would be false evidence. Pure over
// row facts; the SAME function feeds the HeaderStrip, the inspector, and the launch flow so one
// seat can never show two tiers (the stateGrammar doctrine, applied to evidence).

export interface LaunchTierInput {
  harness?: string;
  resolvedModel?: string | null;
  resolvedEffort?: string | null;
  controlState?: string | null;
}

/**
 * Whether a READY harness natively echoed the launched pair (L5 evidence, per the ACPUI
 * handover): Codex echoes model+effort through the app-server thread; Pi echoes model+thinking
 * via `get_state`. Claude stream-json echoes the launch MODEL but emits NO launch-effort echo —
 * the pair as a whole never reaches readback, so Claude launch evidence stays 'model-validated'
 * forever (weakest wins, never promoted without proof).
 */
export function hasLaunchEcho(harness: string | undefined): boolean {
  return harness === "codex" || harness === "pi";
}

/** controlState × retained pair → one of the five tiers. Follows the leaf-doc machine exactly. */
export function launchTier(row: LaunchTierInput): EvidenceTier {
  if (row.resolvedModel == null && row.resolvedEffort == null) return "defaults";
  switch (row.controlState) {
    case "starting":
      return "pending"; // requested provenance, no verification glyph
    case "failed":
      return "refused"; // render beside bridgeError, never as validated
    case "ready":
      return hasLaunchEcho(row.harness) ? "readback" : "model-validated";
    default:
      return "pending"; // disconnected/unsupported/absent: no promotion
  }
}

/** Honest one-line meaning per tier — shared by the EvidenceBadge title and the launch flow. */
export const TIER_SENSE: Record<EvidenceTier, string> = {
  pending: "requested pair recorded before validation — no evidence yet",
  readback: "the running harness echoed the launched pair",
  "model-validated":
    "validated against the model's advertised catalog — no native echo for the full pair",
  defaults: "no selection sent — the vendor's own defaults",
  refused: "the runner refused this pair — see the launch error",
};

/**
 * The retained verbatim bridge error off `controlRaw` (R6): a string renders verbatim; any other
 * retained shape is serialized rather than reworded — the UI never paraphrases a refusal.
 */
export function verbatimBridgeError(controlRaw: Record<string, unknown> | undefined): string | null {
  const raw = controlRaw?.bridgeError;
  if (raw === undefined || raw === null) return null;
  if (typeof raw === "string") return raw;
  try {
    return JSON.stringify(raw);
  } catch {
    return String(raw);
  }
}
