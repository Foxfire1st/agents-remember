import type { TerminalCatalogRow } from "../../types/terminalCatalog";

// Catalog-row fixtures (260715-FEUI-L2 S6), FULL wire shape (`TerminalCatalogEntry.to_json()`),
// placed under src/test/fixtures to be shared with the L3 fixture pack. `catalogRow` builds one
// row with sane defaults; `FLEET` is the mockup-mirroring scenario (flat command spine, two leaf
// clusters, a completed folder, an awaiting-input scout, a failed launch, a landed probe).

let counter = 0;

export function catalogRow(overrides: Partial<TerminalCatalogRow> = {}): TerminalCatalogRow {
  counter += 1;
  const id = overrides.id ?? `session-${counter}`;
  return {
    id,
    label: id,
    kind: "harness",
    harness: "claude",
    cwd: "/workspace",
    tmuxName: `ar-${id}`,
    command: ["claude"],
    createdAt: "2026-07-16T08:00:00Z",
    lastAttachedAt: "2026-07-16T08:00:00Z",
    status: "running",
    seatRole: "chat",
    ...overrides,
  };
}

const MASTER = "agents-remember/260714_own-adapter-capability";
const MASTER_2 = "agents-remember/260715_react-tui-cockpit-frontend";

export const FLEET: TerminalCatalogRow[] = [
  catalogRow({
    id: "architect",
    label: "architect",
    spawnRole: "architect",
    seatRole: "architect",
    turnState: "turn-ended",
    turnStateChangedAt: "2026-07-16T09:00:00Z",
    controlState: "ready",
  }),
  catalogRow({
    id: "orchestrator",
    label: "orchestrator",
    spawnRole: "orchestrator",
    seatRole: "orchestrator",
    spawnedBySession: "architect",
    turnState: "working",
    turnStateChangedAt: "2026-07-16T09:05:00Z",
    controlState: "ready",
  }),
  catalogRow({
    id: "manager-l4",
    label: "manager-L4",
    spawnRole: "manager",
    seatRole: "manager",
    spawnedBySession: "orchestrator",
    leafKey: `${MASTER}/00_series-management`,
    spawnLevel: "master",
    spawnLevelSource: "explicit",
    turnState: "working",
    turnStateChangedAt: "2026-07-16T09:10:00Z",
    controlState: "ready",
  }),
  catalogRow({
    id: "worker-l4",
    label: "worker-L4-serving",
    spawnRole: "worker",
    seatRole: "worker",
    spawnedBySession: "manager-l4",
    leafKey: `${MASTER}/04_serving`,
    spawnLevel: "leaf",
    spawnLevelSource: "default",
    resolvedModel: "gpt-5.6-sol",
    resolvedEffort: "xhigh",
    turnState: "working",
    turnStateChangedAt: "2026-07-16T09:20:00Z",
    controlState: "ready",
    controlActivity: "running",
    controlLastEventSequence: 42,
    controlVendorSessionId: "vendor-abc",
  }),
  catalogRow({
    id: "reviewer-l4",
    label: "reviewer-L4",
    spawnRole: "reviewer",
    seatRole: "reviewer",
    spawnedBySession: "manager-l4",
    leafKey: `${MASTER}/04_serving`,
    turnState: "turn-ended",
    turnStateChangedAt: "2026-07-16T09:15:00Z",
    controlState: "ready",
  }),
  catalogRow({
    id: "curator-l4",
    label: "curator-L4",
    spawnRole: "curator",
    seatRole: "curator",
    spawnedBySession: "manager-l4",
    leafKey: `${MASTER}/04_serving`,
    turnState: "turn-ended",
    turnStateChangedAt: "2026-07-16T09:12:00Z",
    controlState: "ready",
  }),
  catalogRow({
    id: "worker-caps",
    label: "worker-caps",
    spawnRole: "worker",
    seatRole: "worker",
    spawnedBySession: "manager-l4",
    leafKey: `${MASTER}/05_capabilities`,
    turnState: "working",
    turnStateChangedAt: "2026-07-16T09:25:00Z",
    controlState: "ready",
  }),
  catalogRow({
    id: "landed-w1",
    label: "worker-done-1",
    spawnRole: "worker",
    seatRole: "worker",
    leafKey: `${MASTER}/01_protocol`,
    status: "landed",
    landedAt: "2026-07-16T07:00:00Z",
    landedReason: "leaf integrated",
    landedEdge: "leaf-integration",
  }),
  catalogRow({
    id: "landed-r1",
    label: "reviewer-done-1",
    spawnRole: "reviewer",
    seatRole: "reviewer",
    leafKey: `${MASTER}/01_protocol`,
    status: "landed",
    landedAt: "2026-07-16T07:05:00Z",
    landedReason: "review approved",
  }),
  catalogRow({
    id: "worker-tui",
    label: "worker-tui-shell",
    spawnRole: "worker",
    seatRole: "worker",
    leafKey: `${MASTER_2}/01_view-shell`,
    turnState: "awaiting-input",
    turnStateChangedAt: "2026-07-16T09:18:00Z",
    controlState: "ready",
    controlPendingInteraction: {
      interactionId: "ix_42",
      kind: "approval",
      prompt: "Apply the migration to harness_control_api.py as proposed?",
      choices: ["apply + tests", "show diff", "cancel"],
    },
  }),
  catalogRow({
    id: "scout",
    label: "scout-claude",
    controlState: "failed",
    controlRaw: {
      bridgeError: 'requested model "ar-unknown-model" is absent from the dynamic catalog',
    },
    livenessFailures: 2,
    livenessFirstFailedAt: "2026-07-16T09:26:00Z",
    livenessEvidence: "pane-gone",
  }),
  catalogRow({
    id: "pi-probe",
    label: "pi-probe",
    harness: "pi",
    status: "landed",
    landedAt: "2026-07-16T06:00:00Z",
    landedReason: "probe finished",
  }),
];

// ── 260715-FEUI-L6 fixture pack (R9) — APPENDED, never reshaping FLEET ──────────────────────
// The two archetypes + interaction kinds + stop residuals, as separate exports so FLEET-order-
// dependent tests stay byte-identical.

/** Controlled session (archetype 1): the PTY shows the runner LINE-LOG — no vendor TUI exists. */
export const L6_CONTROLLED_WORKING: TerminalCatalogRow = catalogRow({
  id: "l6-controlled",
  label: "worker-l6-controlled",
  spawnRole: "worker",
  seatRole: "worker",
  leafKey: `${MASTER_2}/06_pty-stage-interactions-lifecycle`,
  lifecycleId: "lc-l6-controlled",
  controlState: "ready",
  controlActivity: "running",
  controlAcceptance: "queued",
  turnState: "working",
  turnStateChangedAt: "2026-07-17T09:00:00Z",
});

/** Legacy raw session (archetype 2): `controlState: "unsupported"` — the vendor TUI runs in
 *  tmux; chrome controls are 409-disabled; bell/OSC harvesting applies to THIS archetype only. */
export const L6_LEGACY_RAW: TerminalCatalogRow = catalogRow({
  id: "l6-raw-vendor",
  label: "codex-raw-legacy",
  harness: "codex",
  controlState: "unsupported",
  turnState: "working",
  turnStateChangedAt: "2026-07-17T09:01:00Z",
});

/** A pending interaction WITH choices (buttons path). */
export const L6_INTERACTION_CHOICES: TerminalCatalogRow = catalogRow({
  id: "l6-ix-choices",
  label: "worker-l6-choices",
  spawnRole: "worker",
  seatRole: "worker",
  leafKey: `${MASTER_2}/06_pty-stage-interactions-lifecycle`,
  lifecycleId: "lc-l6-choices",
  controlState: "ready",
  turnState: "awaiting-input",
  turnStateChangedAt: "2026-07-17T09:02:00Z",
  controlPendingInteraction: {
    interactionId: "ix_l6_choice",
    kind: "approval",
    prompt: "Allow the agent to run `npm install`?",
    choices: ["allow", "allow for this session", "deny"],
  },
});

/** A NON-choice pending interaction (free-text kind → composer answer-mode via the gate). */
export const L6_INTERACTION_FREETEXT: TerminalCatalogRow = catalogRow({
  id: "l6-ix-freetext",
  label: "worker-l6-freetext",
  spawnRole: "worker",
  seatRole: "worker",
  leafKey: `${MASTER_2}/06_pty-stage-interactions-lifecycle`,
  lifecycleId: "lc-l6-freetext",
  controlState: "ready",
  turnState: "awaiting-input",
  turnStateChangedAt: "2026-07-17T09:03:00Z",
  controlPendingInteraction: {
    interactionId: "ix_l6_text",
    kind: "input",
    prompt: "Which base branch should the worktree start from?",
    choices: [],
  },
});

/** A pending interaction the bar cannot represent (no interactionId to answer against). */
export const L6_INTERACTION_UNREPRESENTABLE: TerminalCatalogRow = catalogRow({
  id: "l6-ix-opaque",
  label: "worker-l6-opaque",
  spawnRole: "worker",
  seatRole: "worker",
  controlState: "ready",
  turnState: "awaiting-input",
  turnStateChangedAt: "2026-07-17T09:04:00Z",
  controlPendingInteraction: {
    kind: "vendor-custom",
    payload: { opaque: true },
  },
});

/** A retired row carrying the failed-graceful-stop residual — informational, never a failure. */
export const L6_RETIRED_WITH_STOP_ERROR: TerminalCatalogRow = catalogRow({
  id: "l6-retired-residual",
  label: "worker-l6-retired",
  spawnRole: "worker",
  seatRole: "worker",
  leafKey: `${MASTER_2}/06_pty-stage-interactions-lifecycle`,
  status: "terminated",
  terminatedAt: "2026-07-17T09:05:00Z",
  retiredAt: "2026-07-17T09:05:00Z",
  retiredBySession: "manager-l6",
  retiredReason: "seat superseded",
  retiredEdge: "manual",
  controlRaw: {
    retireControlStopError: "control command queue is stopped",
  },
});

/** The terminate route's response when the graceful control stop failed (stop residual). */
export const L6_TERMINATE_RESPONSE_WITH_RESIDUAL = {
  session: "l6-controlled",
  status: "terminated",
  terminatedAt: "2026-07-17T09:06:00Z",
  tmuxName: "ar-l6-controlled",
  controlStopDetail: "control command queue is stopped",
} as const;
