import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

// Legacy-raw byte-stream harvesting (260715-FEUI-L6 R7, design §8.1) — CLIENT-SIDE ONLY. The
// vendor TUI in a legacy raw ('unsupported') pane is the only attention/turn signal that pane
// has, so xterm-observed facts are harvested into this store: onBell → a rail attention marker,
// OSC 0/2 title changes → row label hints, and — where a vendor emits them (e.g. Pi) —
// OSC 133 prompt marks / OSC 9;4 progress → turn-state HINTS. Hints never enter stateGrammar:
// the dot must never lie, so harvested signals render as clearly-labeled hints beside the
// grammar, not as states. Controlled panes get NONE of this (the runner line-log carries real
// control words; harvesting is only wired for the raw archetype in PtySurface).

export interface PtyTurnHint {
  /** The hinted phase, from the mark vocabulary the vendor emitted. */
  hint: "prompt" | "command-running" | "command-finished" | "progress" | "progress-done";
  /** OSC 9;4 percentage when present (0–100). */
  progress?: number;
  at: number;
}

export interface PtyHarvest {
  /** Bell observed and not yet acknowledged by focusing the seat (the rail attention marker). */
  bellPending: boolean;
  lastBellAt?: number;
  /** The vendor TUI's own window title (OSC 0/2) — a label hint, never the catalog label. */
  title?: string;
  turnHint?: PtyTurnHint;
}

interface PtyHarvestState {
  bySession: Record<string, PtyHarvest>;
  recordBell: (sessionId: string, at: number) => void;
  /** Focusing the seat is the acknowledgment — the marker exists to pull attention there. */
  acknowledgeBell: (sessionId: string) => void;
  recordTitle: (sessionId: string, title: string) => void;
  recordTurnHint: (sessionId: string, hint: PtyTurnHint) => void;
  clear: (sessionId: string) => void;
}

const EMPTY: PtyHarvest = { bellPending: false };

function withHarvest(
  state: PtyHarvestState,
  sessionId: string,
  update: (current: PtyHarvest) => PtyHarvest,
): Pick<PtyHarvestState, "bySession"> {
  const current = state.bySession[sessionId] ?? EMPTY;
  return { bySession: { ...state.bySession, [sessionId]: update(current) } };
}

export const ptyHarvestStore = createStore<PtyHarvestState>((set) => ({
  bySession: {},
  recordBell: (sessionId, at) =>
    set((state) =>
      withHarvest(state, sessionId, (current) => ({ ...current, bellPending: true, lastBellAt: at })),
    ),
  acknowledgeBell: (sessionId) =>
    set((state) => {
      if (!state.bySession[sessionId]?.bellPending) return state;
      return withHarvest(state, sessionId, (current) => ({ ...current, bellPending: false }));
    }),
  recordTitle: (sessionId, title) =>
    set((state) => withHarvest(state, sessionId, (current) => ({ ...current, title }))),
  recordTurnHint: (sessionId, turnHint) =>
    set((state) => withHarvest(state, sessionId, (current) => ({ ...current, turnHint }))),
  clear: (sessionId) =>
    set((state) => {
      if (!(sessionId in state.bySession)) return state;
      const bySession = { ...state.bySession };
      delete bySession[sessionId];
      return { bySession };
    }),
}));

export const usePtyHarvest = <T>(selector: (state: PtyHarvestState) => T): T =>
  useStore(ptyHarvestStore, selector);

// ── Pure OSC parsers (unit-tested; xterm stays out of jsdom) ────────────────────────────────

/**
 * OSC 133 shell-integration marks (FinalTerm vocabulary; Pi emits them): `A` prompt start,
 * `B` prompt end, `C` command pre-exec (running), `D[;exit]` command finished. Unknown/other
 * payloads → null (never a fabricated hint).
 */
export function parseOsc133(data: string, at: number): PtyTurnHint | null {
  const mark = data.split(";")[0];
  if (mark === "A" || mark === "B") return { hint: "prompt", at };
  if (mark === "C") return { hint: "command-running", at };
  if (mark === "D") return { hint: "command-finished", at };
  return null;
}

/**
 * OSC 9;4 progress (ConEmu vocabulary; Pi emits it): `4;st;pr` where st 0 = clear, 1/2/4 =
 * active (pr = percent), 3 = indeterminate. The leading ident (`9`) is stripped by xterm's
 * handler registration, so `data` starts at `4;…`. Non-progress OSC 9 payloads → null.
 */
export function parseOsc94(data: string, at: number): PtyTurnHint | null {
  const parts = data.split(";");
  if (parts[0] !== "4") return null;
  const state = Number.parseInt(parts[1] ?? "", 10);
  if (Number.isNaN(state)) return null;
  if (state === 0) return { hint: "progress-done", at };
  const percent = Number.parseInt(parts[2] ?? "", 10);
  return {
    hint: "progress",
    ...(Number.isNaN(percent) ? {} : { progress: Math.min(100, Math.max(0, percent)) }),
    at,
  };
}

/** The dim, clearly-hint-labeled words a harvested turn hint renders as. */
export function turnHintWord(hint: PtyTurnHint): string {
  switch (hint.hint) {
    case "prompt":
      return "at prompt";
    case "command-running":
      return "command running";
    case "command-finished":
      return "command finished";
    case "progress":
      return hint.progress !== undefined ? `progress ${hint.progress}%` : "progress";
    case "progress-done":
      return "progress done";
  }
}
