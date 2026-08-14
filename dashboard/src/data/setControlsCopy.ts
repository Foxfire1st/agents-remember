import type { SetResultWire } from "../types/harnessCapabilities";
import { isClamp, type SetKind } from "./setAcceptance";

// THE set-controls copy module (260715-FEUI-L4 R8, design §9.5): every live-region announcement
// string and every acceptance-chip phrase comes from here so tests assert the EXACT words. Rules:
//   - every chip/announcement carries the acceptance WORD as text — never color-only;
//   - requested and effective are never merged in any phrase;
//   - server detail strings render verbatim, appended after an em dash — never paraphrased.

// ── Menu-state words (R1) ──────────────────────────────────────────────────────────────────────

export const NO_EFFORT_CONTROL_COPY = "no effort control for this model";
/** Null selectedEffort with a NON-EMPTY menu (fresh Claude) — not a missing control. */
export const EFFORT_NOT_ECHOED_COPY = "effort not echoed";
export const NO_SELECTED_MODEL_COPY = "no selected model echoed";

// ── In-flight honesty (R3: the ~35 s server acceptance window) ─────────────────────────────────

export function setWaitingCopy(kind: SetKind, requestedValue: string): string {
  return `set ${kind} ${requestedValue} — waiting for the harness's acceptance evidence (the server bounds this at ~35 s)`;
}

// ── Chip phrases (R2 — the acceptance word is always the first token) ──────────────────────────

export function clampChipCopy(requestedValue: string, effectiveValue: string): string {
  return `echo-verified (clamped): requested ${requestedValue} → effective ${effectiveValue}`;
}

export function queuedChipCopy(kind: SetKind, requestedValue: string): string {
  return `queued — ${kind} ${requestedValue} applies on next turn`;
}

/** The composer-hint slot for queued sets (R2): the next turn is what promotes them — and a
 *  submit is one way to start one (PTY lines and inbox deliveries count too). */
export function composerQueuedHintCopy(kinds: SetKind[]): string {
  return `queued ${kinds.join(" + ")} change applies when the next turn starts — sending a message starts one`;
}

export function unknownChipCopy(kind: SetKind, requestedValue: string): string {
  return `unknown — ${kind} ${requestedValue} unresolved; verifying by one snapshot readback`;
}

/** An unknown resolved NOT-APPLIED by its readback: the prior value stayed (still unacked). */
export function unknownKeptPriorChipCopy(kind: SetKind, requestedValue: string): string {
  return `unknown — ${kind} ${requestedValue} did not take effect; the readback kept the prior value`;
}

export function unsupportedChipCopy(kind: SetKind, result: SetResultWire): string {
  const detail = result.detail ? ` — ${result.detail}` : "";
  return `unsupported — ${kind} ${result.requestedValue} refused, prior value kept${detail}`;
}

/** Defensive: echo-verified with NO echoed value proves nothing — the marker did not move. */
export function echoWithoutValueChipCopy(kind: SetKind, requestedValue: string): string {
  return `echo-verified — but no effective ${kind} value was echoed for ${requestedValue}; the marker did not move`;
}

// ── Route-error phrases (R3 — transport vocabulary, never worn by a 200) ───────────────────────

export function setRouteErrorCopy(error: {
  kind: SetKind;
  status: string;
  detail: string;
  httpStatus: number | null;
}): string {
  if (error.status === "unknown-session") return `set ${error.kind} failed — session gone (404)`;
  if (error.status === "unsupported") {
    return `controls disabled — ${error.detail}`;
  }
  if (error.status === "control-unavailable") {
    return `set ${error.kind} failed — control outage (503): ${error.detail}`;
  }
  return `set ${error.kind} failed — transport: ${error.detail}`;
}

export function snapshotErrorCopy(error: { status: string; detail: string; httpStatus: number | null }): string {
  const status = error.httpStatus === null ? "transport" : `HTTP ${error.httpStatus}`;
  return `capability snapshot failed (${status} ${error.status}): ${error.detail}`;
}

// ── Live-region announcements (R8 — polite: SetResult arrivals/promotions, focused session) ────

export function setResultAnnouncement(kind: SetKind, result: SetResultWire): string {
  switch (result.acceptance) {
    case "echo-verified":
      if (isClamp(result)) {
        return `${kind} set clamped: requested ${result.requestedValue}, effective ${result.effectiveValue}`;
      }
      return result.effectiveValue !== null
        ? `${kind} set echo-verified: now ${result.effectiveValue}`
        : `${kind} set echo-verified, but no effective value was echoed`;
    case "immediate":
      return `${kind} ${result.requestedValue} was already effective`;
    case "queued":
      return `${kind} ${result.requestedValue} queued — applies on next turn`;
    case "unknown":
      return `${kind} set outcome unknown — verifying by snapshot readback`;
    case "unsupported":
      return `${kind} set unsupported — prior value kept${result.detail ? `: ${result.detail}` : ""}`;
  }
}

export function promotionAnnouncement(kind: SetKind, value: string): string {
  return `queued ${kind} change applied — now ${value}`;
}

export function readbackKeptPriorAnnouncement(kind: SetKind, requestedValue: string): string {
  return `${kind} set ${requestedValue} did not take effect — the snapshot readback kept the prior value`;
}

export function cycleNoControlAnnouncement(): string {
  return NO_EFFORT_CONTROL_COPY;
}

export function cycleNoSnapshotAnnouncement(): string {
  return "effort menu unknown — fetching the exact-session snapshot; press again once it loads";
}

// ── Assertive announcements (R8 — ANY session entering failed or awaiting-input) ───────────────

export function sessionFailedAnnouncement(label: string): string {
  return `${label} failed`;
}

export function sessionAwaitingInputAnnouncement(label: string): string {
  return `${label} awaiting input`;
}
