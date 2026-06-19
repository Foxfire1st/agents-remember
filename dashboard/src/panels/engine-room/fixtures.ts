// Engine Room scenario fixtures (slice 5e, 05e §11). Each is a set of server-shaped
// `EngineProcessNode`s + the workspace (official/main) provider stack. The dev gallery wraps
// them in a WorkspaceProjection (dev/fixtures.ts), and component tests can import them directly.
// They cover the boot states (bootstrap, setup running, GrepAI failure, CGC fallback, memory
// blocked, sync needed, cleanup pending) plus a step-through of one enclosure being assembled.

import type {
  CommitRefNode,
  EngineProcessEdge,
  EngineProcessNode,
  ProviderBootNode,
  ProviderNode,
} from "../../types/projection";

export interface EngineRoomScenario {
  name: string;
  processes: EngineProcessNode[];
  workspace: ProviderNode[];
}

const SOURCE_BRANCH = "feat/observable-lifecycle-dashboard";

const wsEngine = (id: string, indexingState = "indexed"): ProviderNode => ({
  id,
  state: "ready",
  ok: true,
  watcherUp: true,
  indexingState,
  snapshotStaleSeconds: 4,
  scope: "workspace",
  role: id.includes("memory") || id.includes("grepai") ? "memory" : "code",
});

const WORKSPACE = [wsEngine("codegraphcontext-code"), wsEngine("grepai-memory")];

function ref(over: Partial<CommitRefNode> = {}): CommitRefNode {
  return { factState: "observed", ...over };
}

function boot(role: "code" | "memory", runtimeState = "nominal"): ProviderBootNode {
  return {
    id: role === "code" ? "codegraphcontext-code@grp" : "grepai-memory@grp",
    role,
    runtimeState,
    factState: "observed",
  };
}

interface EdgeStates {
  worktreeAdd?: string;
  cgc?: string;
  ledger?: string;
  grepai?: string;
  sync?: string;
  integration?: string;
}

function edges(states: EdgeStates, external = true): EngineProcessEdge[] {
  const out: EngineProcessEdge[] = [
    {
      id: "code-worktree-add",
      fromNode: "code-source",
      toNode: "code-worktree",
      kind: "worktree-add",
      state: states.worktreeAdd ?? "complete",
      label: "add code worktree",
    },
    {
      id: "cgc-seed",
      fromNode: "code-worktree",
      toNode: "cgc-engine",
      kind: "cgc-seed",
      state: states.cgc ?? "complete",
      label: "CGC seed",
    },
  ];
  if (external) {
    out.push({
      id: "memory-worktree-add",
      fromNode: "memory-source",
      toNode: "memory-worktree",
      kind: "ledger-map",
      state: states.ledger ?? "complete",
      label: "ledger-map + memory worktree",
    });
    out.push({
      id: "grepai-clone",
      fromNode: "memory-worktree",
      toNode: "grepai-engine",
      kind: "grepai-clone",
      state: states.grepai ?? "complete",
      label: "GrepAI clone",
    });
  }
  if (states.sync) {
    out.push({
      id: "sync",
      fromNode: "official-line",
      toNode: "code-worktree",
      kind: "sync",
      state: states.sync,
      label: "official line moved — sync",
    });
  }
  if (states.integration) {
    out.push({
      id: "integration",
      fromNode: "code-worktree",
      toNode: "official-line",
      kind: "integration",
      state: states.integration,
      label: "integrate ff-only → source",
    });
  }
  return out;
}

function engineProcess(
  over: Partial<EngineProcessNode> & Pick<EngineProcessNode, "id" | "taskName" | "repoName">,
): EngineProcessNode {
  const group = `/home/dev/Projects/ar-coordination/worktrees/${over.repoName}/${over.id}-ar`;
  return {
    enclosure: `${group}/contract.md`,
    worktreeGroup: group,
    taskId: over.id.toUpperCase(),
    phase: "worktree-started",
    health: "nominal",
    codeSource: ref({ branch: SOURCE_BRANCH, commit: "08e9221a", path: `/home/dev/Projects/${over.repoName}` }),
    codeWorktree: ref({
      branch: `ar/${over.id}`,
      commit: "08e9221a",
      path: `${group}/${over.id}`,
      exists: true,
      dirty: false,
    }),
    memoryMode: "external",
    memorySource: ref({ branch: SOURCE_BRANCH, commit: "d60a0511", path: `/home/dev/Projects/ar-coordination/memory-repos/ar-${over.repoName}` }),
    memoryWorktree: ref({
      branch: `ar/${over.id}`,
      commit: "d60a0511",
      path: `${group}/memory-${over.id}`,
      exists: true,
      dirty: false,
    }),
    ledgerPath: `${group}/memory-${over.id}/memory.md`,
    humanReviewStatus: "pending-review",
    closeoutStatus: "not-started",
    integrationStatus: "not-started",
    cleanup: "pending",
    setupState: "ok",
    completedPhases: [
      "codegraphcontext-code seed: ok",
      "grepai-memory clone: ok",
      "grepai-memory watcher: ok",
    ],
    failedPhases: [],
    seedFallback: false,
    providers: [boot("code"), boot("memory")],
    edges: edges({}),
    actions: [],
    nextAction: "continue_work",
    summary: "Worktree task started; continue the wrapped workflow before closeout.",
    missingFacts: [],
    sourceFiles: [`${group}/contract.md`, `${group}/${over.id}`, `${group}/provider-runtime/setup-progress.json`],
    ...over,
  };
}

// --- the boot-up step-through: one enclosure assembling after worktree_start --

const BOOT_ID = "boot-demo";
const bootBase = { id: BOOT_ID, taskName: "device-management", repoName: "agents-remember" } as const;

const bootStages: EngineRoomScenario[] = [
  {
    name: "engine-boot-1-code-worktree",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "code-worktree",
        health: "running",
        memoryWorktree: ref({ branch: `ar/${BOOT_ID}`, exists: false, factState: "planned" }),
        setupState: undefined,
        completedPhases: [],
        providers: [],
        edges: edges({ cgc: "planned", ledger: "planned", grepai: "planned" }),
        summary: "Code worktree created; resolving external memory compatibility.",
        nextAction: "continue_work",
        missingFacts: ["provider setup not observed for this worktree group"],
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-boot-2-memory-contract",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "contract-written",
        health: "running",
        setupState: undefined,
        completedPhases: [],
        providers: [],
        edges: edges({ cgc: "planned", grepai: "planned" }),
        summary: "Memory ledger maps the base commit; contract written.",
        missingFacts: ["provider setup not observed for this worktree group"],
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-boot-3-cgc-seeding",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "provider-setup",
        health: "running",
        setupState: "running",
        currentPhase: "codegraphcontext-code seed",
        heartbeatAgeSeconds: 2,
        completedPhases: [],
        providers: [boot("code", "indexing")],
        edges: edges({ cgc: "running", grepai: "planned" }),
        summary: "Provider runtime booting — CGC seeding from the source bundle.",
        nextAction: "continue_work",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-boot-4-grepai-cloning",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "provider-setup",
        health: "running",
        setupState: "running",
        currentPhase: "grepai-memory clone",
        heartbeatAgeSeconds: 3,
        completedPhases: ["codegraphcontext-code seed: ok"],
        providers: [boot("code"), boot("memory", "indexing")],
        edges: edges({ cgc: "complete", grepai: "running" }),
        summary: "CGC seeded; GrepAI cloning the memory database.",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-boot-5-watchers",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "provider-setup",
        health: "running",
        setupState: "running",
        currentPhase: "grepai-memory watcher start",
        heartbeatAgeSeconds: 1,
        completedPhases: ["codegraphcontext-code seed: ok", "grepai-memory clone: ok"],
        providers: [boot("code", "indexing"), boot("memory", "indexing")],
        edges: edges({}),
        summary: "Seeds complete; watchers igniting after stable filesystem prep.",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-boot-6-nominal",
    processes: [engineProcess({ ...bootBase })],
    workspace: WORKSPACE,
  },
];

// --- discrete state scenarios (05e §11) --------------------------------------

export const ENGINE_ROOM_SCENARIOS: EngineRoomScenario[] = [
  {
    name: "engine-fleet",
    processes: [
      engineProcess({ id: "browser-dashboard", taskName: "browser-dashboard", repoName: "agents-remember", lifecycleId: "build-001" }),
      engineProcess({
        id: "device-mgmt",
        taskName: "device-management",
        repoName: "agents-remember",
        phase: "provider-setup",
        health: "running",
        setupState: "running",
        currentPhase: "grepai-memory clone",
        heartbeatAgeSeconds: 3,
        completedPhases: ["codegraphcontext-code seed: ok"],
        providers: [boot("code"), boot("memory", "indexing")],
        edges: edges({ cgc: "complete", grepai: "running" }),
        summary: "Provider setup running — GrepAI cloning.",
      }),
      engineProcess({
        id: "gate-plane",
        taskName: "gate-control-plane",
        repoName: "agents-remember",
        health: "failed",
        setupState: "failed",
        failedPhases: ["grepai-memory clone: failed (clone stalled)"],
        completedPhases: ["codegraphcontext-code seed: ok"],
        providers: [boot("code"), boot("memory", "down")],
        edges: edges({ grepai: "failed" }),
        retryArgs: { repo_id: "agents-remember", task_name: "gate-control-plane", worktree_name: "gate-plane", retry_provider_setup: true },
        summary: "GrepAI clone failed; retry available.",
      }),
      engineProcess({
        id: "read-packet",
        taskName: "read-packet",
        repoName: "helpdesk",
        phase: "sync-needed",
        health: "blocked",
        codeSource: ref({ branch: SOURCE_BRANCH, commit: "1a2b3c4d", path: "/home/dev/Projects/helpdesk", behindSource: 3 }),
        edges: edges({ sync: "blocked" }),
        summary: "Official line moved 3 commits ahead — sync before continuing.",
        nextAction: "worktree_sync",
      }),
      engineProcess({
        id: "boot-audio",
        taskName: "boot-audio-polish",
        repoName: "agents-remember",
        phase: "integration-pending",
        health: "nominal",
        humanReviewStatus: "approved",
        closeoutStatus: "completed",
        cleanup: "pending",
        actions: [
          { action: "integrate", enabled: true },
          { action: "cleanup", enabled: false, disabledReason: "integration not complete", nextSafeAction: "integrate first" },
        ],
        summary: "Closeout complete; ready to integrate.",
        nextAction: "request_integration_decision",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-bootstrap",
    processes: [engineProcess({ id: "browser-dashboard", taskName: "browser-dashboard", repoName: "agents-remember" })],
    workspace: WORKSPACE,
  },
  {
    name: "engine-setup-running",
    processes: [
      engineProcess({
        id: "device-mgmt",
        taskName: "device-management",
        repoName: "agents-remember",
        phase: "provider-setup",
        health: "running",
        setupState: "running",
        currentPhase: "grepai-memory clone",
        heartbeatAgeSeconds: 4,
        completedPhases: ["codegraphcontext-code seed: ok"],
        providers: [boot("code"), boot("memory", "indexing")],
        edges: edges({ cgc: "complete", grepai: "running" }),
        summary: "Provider setup running — current phase grepai-memory clone (heartbeat fresh).",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-grepai-failed",
    processes: [
      engineProcess({
        id: "device-mgmt",
        taskName: "device-management",
        repoName: "agents-remember",
        phase: "provider-setup",
        health: "failed",
        setupState: "failed",
        failedPhases: ["grepai-memory clone: failed (clone stalled — no progress for 120s)"],
        completedPhases: ["codegraphcontext-code seed: ok"],
        providers: [boot("code"), boot("memory", "down")],
        edges: edges({ grepai: "failed" }),
        retryArgs: { repo_id: "agents-remember", task_name: "device-management", worktree_name: "device-mgmt", retry_provider_setup: true },
        summary: "GrepAI clone failed; the code engine is independent and healthy.",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-cgc-fallback",
    processes: [
      engineProcess({
        id: "device-mgmt",
        taskName: "device-management",
        repoName: "agents-remember",
        setupState: "ok",
        seedFallback: true,
        completedPhases: [
          "codegraphcontext-code seed: skipped (commit mismatch — reroute to reindex)",
          "codegraphcontext-code reindex: ok",
          "grepai-memory clone: ok",
        ],
        edges: edges({ cgc: "complete" }),
        summary: "CGC seed refused on commit mismatch; rerouted to a full reindex (not a failure).",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-memory-blocked",
    processes: [
      engineProcess({
        id: "v12-feature",
        taskName: "dm-v1.2-feature",
        repoName: "agents-remember",
        phase: "worktree-started",
        health: "blocked",
        memoryWorktree: ref({ branch: "ar/v12-feature", path: "(not created)", exists: false, factState: "missing" }),
        setupState: undefined,
        completedPhases: [],
        providers: [],
        edges: edges({ ledger: "blocked", cgc: "planned", grepai: "planned" }),
        summary: "External memory blocked: no ledger mapping for the selected code base commit.",
        missingFacts: [
          "memory worktree not present on disk",
          "provider setup not observed for this worktree group",
        ],
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-precontract-blocked",
    processes: [
      engineProcess({
        id: "v12-feat",
        taskName: "dm-v1.2-feature",
        repoName: "agents-remember",
        phase: "memory-compatibility",
        health: "blocked",
        codeSource: ref({ branch: SOURCE_BRANCH, commit: "abc1234", path: "/home/dev/Projects/agents-remember", factState: "derived" }),
        codeWorktree: ref({ path: "/home/dev/Projects/ar-coordination/worktrees/agents-remember/v12-feat-ar/v12-feat", exists: true, factState: "observed" }),
        memorySource: ref({ factState: "planned" }),
        memoryWorktree: ref({ exists: false, factState: "missing" }),
        ledgerPath: undefined,
        setupState: undefined,
        completedPhases: [],
        providers: [],
        edges: edges({ ledger: "blocked", cgc: "planned", grepai: "planned" }),
        summary: "no exact ledger mapping for selected code base commit",
        nextAction: "reconciliation",
        missingFacts: [
          "start gated at memory-compatibility — contract not yet written",
          "no exact ledger mapping for selected code base commit",
        ],
        sourceFiles: ["/home/dev/Projects/ar-coordination/temp/worktree-start/agents-remember/v12-feat.json"],
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-sync-needed",
    processes: [
      engineProcess({
        id: "read-packet",
        taskName: "read-packet",
        repoName: "agents-remember",
        phase: "sync-needed",
        health: "blocked",
        codeSource: ref({ branch: SOURCE_BRANCH, commit: "1a2b3c4d", path: "/home/dev/Projects/agents-remember", behindSource: 3 }),
        memorySource: ref({ branch: SOURCE_BRANCH, commit: "9f8e7d6c", path: "/home/dev/Projects/ar-coordination/memory-repos/ar-agents-remember", behindSource: 1 }),
        edges: edges({ sync: "blocked" }),
        summary: "Official line moved (code 3 / memory 1 behind) — worktree_sync before continuing.",
        nextAction: "worktree_sync",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-cleanup-pending",
    processes: [
      engineProcess({
        id: "boot-audio",
        taskName: "boot-audio-polish",
        repoName: "agents-remember",
        phase: "cleanup-pending",
        health: "nominal",
        humanReviewStatus: "approved",
        closeoutStatus: "completed",
        integrationStatus: "completed",
        cleanup: "pending",
        providers: [boot("code"), boot("memory")],
        actions: [{ action: "cleanup", enabled: true }],
        summary: "Integrated; provider runtime teardown + worktree removal pending.",
        nextAction: "request_cleanup_decision",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-integration-conflict",
    processes: [
      engineProcess({
        id: "boot-audio",
        taskName: "boot-audio-polish",
        repoName: "agents-remember",
        phase: "integration-blocked",
        health: "blocked",
        humanReviewStatus: "approved",
        closeoutStatus: "completed",
        integrationStatus: "conflict",
        // the integration return-lane is blocked; the source line did NOT move (all-or-nothing).
        edges: edges({ integration: "blocked" }),
        actions: [],
        nextAction: "resolve_integration_conflict",
        summary: "Integration conflict on the source line — resolve manually; nothing landed (all-or-nothing).",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    name: "engine-abandoned",
    processes: [
      engineProcess({
        id: "spike-ui",
        taskName: "spike-ui-experiment",
        repoName: "agents-remember",
        phase: "abandoned",
        health: "skipped",
        humanReviewStatus: "n/a",
        closeoutStatus: "not-started",
        cleanup: "done",
        providers: [],
        actions: [],
        nextAction: "",
        summary: "Worktree abandoned without integration — record kept.",
      }),
    ],
    workspace: WORKSPACE,
  },
  ...bootStages,
];
