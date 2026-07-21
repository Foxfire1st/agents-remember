import type { OpenSession } from "./sessions";

// THE one seat-state grammar (260715-FEUI-L2 R14, spec §2.4 — RULED 2026-07-16): rail rows, the
// HeaderStrip, and the StatusLine all read this mapping, so the same seat can never show two
// different states on two surfaces (the cross-surface consistency test locks it). The dot is the
// truncation-surviving signal and must never lie: blocked-on-human is STEADY (never flickers),
// only working and failed pulse — and a pulse is the SLOW 2.4 s ease-in-out opacity pulse, never
// steps()/on-off blinking (the developer animation ruling; the legacy 0.6 s steps(1) blink in
// grammar/Dot.tsx is the app-wide offender tracked outside this leaf).

/** Pulse parameters — exported so tests pin the ruling, not a magic string. */
export const PULSE_DURATION_S = 2.4;
export const PULSE_TIMING = "ease-in-out";
export const PULSE_ANIMATION = `pulseSlow ${PULSE_DURATION_S}s ${PULSE_TIMING} infinite`;

export type SeatStateKey =
  | "working"
  | "awaiting-input"
  | "waiting" // declared external wait (AEO reserved word) — rendered-ready, no wire source yet
  | "failed"
  | "starting"
  | "ready"
  | "turn-ended"
  | "stale"
  | "landed"
  | "retired"
  | "exited"
  | "unclassified";

/** The grammar's color roles — podracer tokens only (spec §2.1). */
export type SeatStateColor = "cyan" | "amber" | "mutedAmber" | "alarm" | "mint" | "dormant" | "muted";

export interface SeatVisualState {
  key: SeatStateKey;
  /** The state word rendered beside the dot (HeaderStrip / StatusLine). */
  word: string;
  /** The rail status-chip label — the status vocabulary ONLY, never the model (R6). Absent ⇒ no chip. */
  chip?: string;
  color: SeatStateColor;
  /** True ⇒ the slow 2.4 s ease-in-out pulse; false ⇒ steady. */
  pulse: boolean;
}

const VISUALS: Record<SeatStateKey, SeatVisualState> = {
  working: { key: "working", word: "working", chip: "working", color: "cyan", pulse: true },
  "awaiting-input": {
    key: "awaiting-input",
    word: "awaiting-input",
    chip: "input?",
    color: "amber",
    pulse: false, // doctrine: blocked-on-human never flickers
  },
  waiting: { key: "waiting", word: "waiting", chip: "waiting", color: "mutedAmber", pulse: false },
  failed: { key: "failed", word: "failed", chip: "failed", color: "alarm", pulse: true },
  starting: { key: "starting", word: "starting", chip: "starting", color: "cyan", pulse: false },
  ready: { key: "ready", word: "ready", chip: undefined, color: "mint", pulse: false },
  "turn-ended": {
    key: "turn-ended",
    word: "turn-ended",
    chip: "turn-ended",
    color: "mint",
    pulse: false,
  },
  // "stale" mirrors the sweep's own uncertainty word — hiding it would fake freshness (R15).
  stale: { key: "stale", word: "stale", chip: "stale", color: "muted", pulse: false },
  landed: { key: "landed", word: "landed", chip: "landed", color: "dormant", pulse: false },
  retired: { key: "retired", word: "retired", chip: "retired", color: "dormant", pulse: false },
  // "exited" is a real catalog status (process died without landing) — mirrored, not remapped.
  exited: { key: "exited", word: "exited", chip: "exited", color: "dormant", pulse: false },
  unclassified: { key: "unclassified", word: "—", chip: undefined, color: "muted", pulse: false },
};

/** The seat facts the grammar reads — a subset of OpenSession so fixtures stay tiny. */
export type SeatStateInput = Pick<
  OpenSession,
  "status" | "controlState" | "controlPendingInteraction" | "turnState"
> & {
  /** Declared external wait (AEO `waiting(reason)`) — reserved input, no producer yet. */
  waitingReason?: string;
  /**
   * 260718-CHATS-L5F R9 (audit V5): the conversation projection's OWN live turn signal, resolved
   * fresher than the sweep-bounded catalog `turnState`. When true it means a turn is actively
   * streaming right now (the projection's SSE knows this sub-second; the catalog seat state lags
   * ~10s and reads `turn-ended`). It is preferred over the lagging catalog turn-state — but NEVER
   * over a terminal status, fault, blocked-on-human, or declared wait, which still win above it.
   * Undefined/false is the honest fallback to the catalog truth (no producer → old behavior).
   */
  liveTurnWorking?: boolean;
};

/**
 * controlState × turnState × status → one visual state. Precedence: terminal statuses first
 * (landed/retired/exited never look alive), then faults, then blocked-on-human, then the declared
 * wait, then live turn-state, then control lifecycle. Server truth mirrored — an unclassified
 * row renders as unclassified, never a fabricated state.
 */
export function seatVisualState(input: SeatStateInput): SeatVisualState {
  if (input.status === "landed") return VISUALS.landed;
  if (input.status === "terminated") return VISUALS.retired;
  if (input.status === "exited") return VISUALS.exited;
  if (input.controlState === "failed") return VISUALS.failed;
  if (input.controlPendingInteraction || input.turnState === "awaiting-input") {
    return VISUALS["awaiting-input"];
  }
  if (input.waitingReason !== undefined) {
    const base = VISUALS.waiting;
    return { ...base, word: `waiting(${input.waitingReason})`, chip: `waiting: ${input.waitingReason}` };
  }
  // 260718-CHATS-L5F R9: the projection's fresher live turn signal wins over the lagging catalog
  // turn-state (a streaming turn must never read settled-green `turn-ended`) — but only AFTER the
  // terminal/fault/blocked/wait guards above, so it can never fake liveness over a real end state.
  if (input.liveTurnWorking) return VISUALS.working;
  if (input.turnState === "working") return VISUALS.working;
  if (input.turnState === "turn-ended") return VISUALS["turn-ended"];
  if (input.turnState === "stale") return VISUALS.stale;
  if (input.controlState === "starting") return VISUALS.starting;
  if (input.controlState === "ready") return VISUALS.ready;
  return VISUALS.unclassified;
}
