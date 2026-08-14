/**
 * The browser-side bench contract, declared once for both sides that use it.
 *
 * `/dev/bench` and `/dev/pty-bench` install probes on `window` so the Playwright drivers in
 * `e2e/` and `perf/` can read what the app actually did. That makes these globals an interface
 * between two TypeScript projects: the app installs them (`cockpitScenarios.ts`,
 * `PtyRenderBench.tsx`), the drivers read them. Declaring the shapes in either project alone
 * leaves the other reading `any` or hand-copying the fields, which is how the two halves drift.
 *
 * This module has no imports on purpose: `tsconfig.driver.json` names it directly, so the driver
 * program gains the `Window` augmentation without pulling the app's module graph in behind it.
 */

/** One request the bench fetch stub intercepted, in the order the app issued it. */
export interface CockpitBenchRequest {
  method: string;
  path: string;
  body?: unknown;
}

/** The cockpit state a scenario reset must restore, captured for comparison. */
export interface CockpitResetAudit {
  sessionIds: string[];
  activeId: string | null;
  focusedSessionId: string | null;
  cockpitSessionIds: string[];
  capabilityHarnesses: string[];
  polite: string;
  assertive: string;
  lifecycleResiduals: number;
  ptyHarvestSessions: string[];
  pollHealth: {
    lastBeatAt: number | null;
    missedBeats: number;
    healthy: boolean;
  };
}

/** A step a driver can drive the scenario through mid-test. */
export type CockpitBenchTransition =
  | "launch-failures"
  | "set-turn-ended"
  | "defer-next-open"
  | "release-open";

export interface CockpitBenchProbe {
  scenario: string;
  requestCounts: Record<string, number>;
  totalRequests: number;
  requests: CockpitBenchRequest[];
  launchedSessionIds: string[];
  resetAudit?: CockpitResetAudit;
  snapshot: () => CockpitResetAudit;
  advance: (transition: CockpitBenchTransition) => void;
}

export interface PtyFrameStats {
  renderer: string;
  panes: number;
  linesPerSecondPerPane: number;
  seconds: number;
  frames: number;
  meanMs: number;
  p95Ms: number;
  maxMs: number;
  /** Frames over 33.4 ms (two 60 Hz budgets) — the visible-jank count. */
  longFrames: number;
}

export interface PtySerializeProbe {
  bufferLines: number;
  serializedBytes: number;
  serializeMs: number;
  restoreMs: number;
}

export interface PtyBenchProbe {
  done: boolean;
  stats?: PtyFrameStats;
  serialize?: PtySerializeProbe;
  error?: string;
}

declare global {
  interface Window {
    __cockpitBench?: CockpitBenchProbe;
    __cockpitBenchResetAudit?: CockpitResetAudit;
    __ptyBench?: PtyBenchProbe;
    /** Per-pane REAL column counts (the R8 ~80-col floor verification reads these). */
    __ptyBenchCols?: Record<string, number>;
  }
}
