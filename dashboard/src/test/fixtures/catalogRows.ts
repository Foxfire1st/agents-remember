import type { TaskDocNode } from "../../types/projection";
import type { TerminalCatalogRow } from "../../types/terminalCatalog";
import { taskDoc } from "./wire";

// Catalog-row fixtures in the FULL wire shape (`TerminalCatalogEntry.to_json()`),
// placed under src/test/fixtures so multiple test suites can import them. `catalogRow` builds one
// row with sane defaults; `FLEET` is the mockup-mirroring scenario (a sprint-local command group,
// two leaf clusters, a completed folder, an awaiting-input scout, a failed launch, a landed probe).

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
const SPRINT = "agents-remember/260700_sprint";

/** Canonical document identities for the fleet scenario's sprint/master/leaf hierarchy. */
export const FLEET_TASK_DOCUMENTS: TaskDocNode[] = [
  taskDoc({
    id: "sprint",
    repository: "agents-remember",
    kind: "master",
    title: "Sprint",
    docPath: "/tasks/agents-remember/260700_sprint/task.json",
    orchestrates: [
      "260714_own-adapter-capability",
      "260715_react-tui-cockpit-frontend",
    ],
  }),
  taskDoc({
    id: "adapter-master",
    repository: "agents-remember",
    kind: "master",
    title: "Own adapter capability",
    docPath: "/tasks/agents-remember/260714_own-adapter-capability/task.json",
  }),
  ...["01_protocol", "04_serving", "05_capabilities"].map((id) =>
    taskDoc({
      id,
      repository: "agents-remember",
      kind: "subTask",
      title: id,
      docPath: `/tasks/agents-remember/260714_own-adapter-capability/${id}.json`,
    }),
  ),
  taskDoc({
    id: "cockpit-master",
    repository: "agents-remember",
    kind: "master",
    title: "React TUI cockpit frontend",
    docPath: "/tasks/agents-remember/260715_react-tui-cockpit-frontend/task.json",
  }),
  taskDoc({
    id: "01_view-shell",
    repository: "agents-remember",
    kind: "subTask",
    title: "01_view-shell",
    docPath: "/tasks/agents-remember/260715_react-tui-cockpit-frontend/01_view-shell.json",
  }),
];

function taskRef(owner: string, file: string) {
  const [repository, folder] = owner.split("/");
  return { repository, path: `${folder}/${file}.json` };
}

export const FLEET: TerminalCatalogRow[] = [
  catalogRow({
    id: "architect",
    label: "architect",
    spawnRole: "architect",
    seatRole: "architect",
    taskDocumentRef: taskRef(SPRINT, "task"),
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
    taskDocumentRef: taskRef(SPRINT, "task"),
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
    taskDocumentRef: taskRef(MASTER, "task"),
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
    taskDocumentRef: taskRef(MASTER, "04_serving"),
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
    taskDocumentRef: taskRef(MASTER, "04_serving"),
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
    taskDocumentRef: taskRef(MASTER, "04_serving"),
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
    taskDocumentRef: taskRef(MASTER, "05_capabilities"),
    turnState: "working",
    turnStateChangedAt: "2026-07-16T09:25:00Z",
    controlState: "ready",
  }),
  catalogRow({
    id: "landed-w1",
    label: "worker-done-1",
    spawnRole: "worker",
    seatRole: "worker",
    taskDocumentRef: taskRef(MASTER, "01_protocol"),
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
    taskDocumentRef: taskRef(MASTER, "01_protocol"),
    status: "landed",
    landedAt: "2026-07-16T07:05:00Z",
    landedReason: "review approved",
  }),
  catalogRow({
    id: "worker-tui",
    label: "worker-tui-shell",
    spawnRole: "worker",
    seatRole: "worker",
    taskDocumentRef: taskRef(MASTER_2, "01_view-shell"),
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

// A legacy-raw terminal seat for the e2e PTY-focus/scrollback scenarios: no controlState (so
// isControlledSession is false), kind "terminal" so the Chats stage renders the interactive PTY
// layer instead of the structured conversation surface.
export const RAW_TERMINAL_ROW: TerminalCatalogRow = catalogRow({
  id: "raw-terminal",
  label: "raw-terminal-shell",
  kind: "terminal",
  harness: "codex",
  status: "running",
});

// ── Extra fixture pack — APPENDED, never reshaping FLEET ─────────────────────────────────────
// The two archetypes + interaction kinds + stop residuals, as separate exports so FLEET-order-
// dependent tests stay byte-identical.

/** Controlled session (archetype 1): the PTY shows the runner LINE-LOG — no vendor TUI exists. */
export const L6_CONTROLLED_WORKING: TerminalCatalogRow = catalogRow({
  id: "l6-controlled",
  label: "worker-l6-controlled",
  spawnRole: "worker",
  seatRole: "worker",
  taskDocumentRef: taskRef(MASTER_2, "06_pty-stage-interactions-lifecycle"),
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
  taskDocumentRef: taskRef(MASTER_2, "06_pty-stage-interactions-lifecycle"),
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
  taskDocumentRef: taskRef(MASTER_2, "06_pty-stage-interactions-lifecycle"),
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

/** A STRUCTURED AskUserQuestion interaction — two question pages (one
 *  single-select, one multiSelect), each with ITS OWN option group. The legacy flattened
 *  prompt/choices stay on the row (compat) but the bar renders the structured pages. */
export const L5I_INTERACTION_QUESTIONS: TerminalCatalogRow = catalogRow({
  id: "l5i-ix-questions",
  label: "worker-l5i-questions",
  spawnRole: "worker",
  seatRole: "worker",
  taskDocumentRef: taskRef(MASTER_2, "05i_structured_decisions"),
  lifecycleId: "lc-l5i-questions",
  controlState: "ready",
  turnState: "awaiting-input",
  turnStateChangedAt: "2026-07-22T09:00:00Z",
  controlPendingInteraction: {
    interactionId: "ix_l5i_questions",
    kind: "user-input",
    prompt: "Base: Which base branch?\nExtras: Which extras should run?",
    choices: ["main", "develop", "tests", "docs", "bench"],
    questions: [
      {
        text: "Which base branch?",
        header: "Base",
        options: [
          { label: "main", description: "the gated main line" },
          { label: "develop", description: "the integration branch" },
        ],
        multiSelect: false,
      },
      {
        text: "Which extras should run?",
        header: "Extras",
        options: [
          { label: "tests" },
          { label: "docs" },
          { label: "bench" },
        ],
        multiSelect: true,
      },
    ],
  },
});

/** One structured question on a seat WITHOUT a lifecycle — answerable via the direct
 *  session route (this case used to be unanswerable from the cockpit). */
export const L5I_INTERACTION_NO_LIFECYCLE: TerminalCatalogRow = catalogRow({
  id: "l5i-ix-nolifecycle",
  label: "worker-l5i-nolifecycle",
  spawnRole: "worker",
  seatRole: "worker",
  controlState: "ready",
  turnState: "awaiting-input",
  turnStateChangedAt: "2026-07-22T09:01:00Z",
  controlPendingInteraction: {
    interactionId: "ix_l5i_nolifecycle",
    kind: "user-input",
    prompt: "Pet: Which would you rather have as a pet?",
    choices: ["A tiny dragon", "A talking cat"],
    questions: [
      {
        text: "Which would you rather have as a pet?",
        header: "Pet",
        options: [
          { label: "A tiny dragon", description: "breathes warm air" },
          { label: "A talking cat", description: "judges your code" },
        ],
        multiSelect: false,
      },
    ],
  },
});

/** A permission-kind interaction (choices exactly allow/deny, no structured questions) on
 *  a seat WITHOUT a lifecycle — answered via the direct route's `response` body. */
export const L5I_INTERACTION_PERMISSION: TerminalCatalogRow = catalogRow({
  id: "l5i-ix-permission",
  label: "worker-l5i-permission",
  spawnRole: "worker",
  seatRole: "worker",
  controlState: "ready",
  turnState: "awaiting-input",
  turnStateChangedAt: "2026-07-22T09:02:00Z",
  controlPendingInteraction: {
    interactionId: "ix_l5i_permission",
    kind: "permission",
    prompt: "Allow Claude tool Bash?",
    choices: ["allow", "deny"],
    questions: [],
  },
});

/** A seat on a PRE-FIX runner (spawned before the runner-PYTHONPATH fix): NO top-level
 *  `questions`, but the full claude-native structure at `raw.input.questions` (text key
 *  `question`). Parsed as structured and answered through the direct route — no lifecycle. */
export const L5I_INTERACTION_LEGACY_RUNNER: TerminalCatalogRow = catalogRow({
  id: "l5i-ix-legacy-runner",
  label: "worker-l5i-legacy-runner",
  spawnRole: "worker",
  seatRole: "worker",
  controlState: "ready",
  turnState: "awaiting-input",
  turnStateChangedAt: "2026-07-22T09:03:00Z",
  controlPendingInteraction: {
    interactionId: "ix_l5i_legacy_runner",
    kind: "user-input",
    prompt: "Test pick: Pick one — purely a rendering test?",
    choices: ["A tiny dragon", "A talking cat"],
    questions: [],
    raw: {
      input: {
        questions: [
          {
            header: "Test pick",
            multiSelect: false,
            options: [
              { label: "A tiny dragon", description: "breathes warm air" },
              { label: "A talking cat", description: "judges your code" },
            ],
            question: "Pick one — purely a rendering test?",
          },
        ],
      },
      toolName: "AskUserQuestion",
      toolUseId: "toolu_01legacy",
    },
  },
});

/** A retired row carrying the failed-graceful-stop residual — informational, never a failure. */
export const L6_RETIRED_WITH_STOP_ERROR: TerminalCatalogRow = catalogRow({
  id: "l6-retired-residual",
  label: "worker-l6-retired",
  spawnRole: "worker",
  seatRole: "worker",
  taskDocumentRef: taskRef(MASTER_2, "06_pty-stage-interactions-lifecycle"),
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

/** A multiplexed seat: the parent's singular pending slot PLUS the
 *  additive plural list carrying the parent AND a sub-agent approval with its adapter-bound
 *  label — the InteractionBar renders and answers one bar per pending interaction. */
export const L7_MULTIPLEXED_INTERACTIONS: TerminalCatalogRow = catalogRow({
  id: "l7-ix-multiplexed",
  label: "worker-l7-multiplexed",
  spawnRole: "worker",
  seatRole: "worker",
  controlState: "ready",
  turnState: "awaiting-input",
  turnStateChangedAt: "2026-07-26T09:02:00Z",
  controlPendingInteraction: {
    interactionId: "ix_l7_parent",
    kind: "permission",
    prompt: "Allow the parent command?",
    choices: ["allow", "deny"],
    questions: [],
  },
  controlPendingInteractions: [
    {
      interactionId: "ix_l7_parent",
      kind: "permission",
      prompt: "Allow the parent command?",
      choices: ["allow", "deny"],
      questions: [],
    },
    {
      interactionId: "ix_l7_agent",
      kind: "permission",
      prompt: "Allow the sub-agent command?",
      choices: ["allow", "deny"],
      questions: [],
      raw: { threadId: "agent-thread-1", agentLabel: "agent agent-t" },
    },
  ],
});
