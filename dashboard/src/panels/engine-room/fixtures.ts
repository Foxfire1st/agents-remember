// Engine Room scenario fixtures (slice 5e, 05e §11). Each is a set of server-shaped
// `EngineProcessNode`s + the workspace (official/main) provider stack. The dev gallery wraps
// them in a WorkspaceProjection (dev/fixtures.ts), and component tests can import them directly.
// They cover the boot states (bootstrap, setup running, GrepAI failure, CGC fallback, memory
// blocked, sync needed, cleanup pending) plus a step-through of one enclosure being assembled.

import type {
  CommitRefNode,
  EngineProcessEdge,
  EngineProcessNode,
  LandingRefNode,
  LedgerNode,
  LedgerRefNode,
  ProcessFactState,
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

// The served memory.md ledger window for the coupler popover (5h). The newest row maps the fixture's
// current worktree/source commits (08e9221a ⇄ d60a0511), so the popover highlights it. The window fills to
// the 25-row served cap (`LEDGER_WINDOW`) so the popover's collapse (8) → expand (≤25) → scroll is
// exercised; the scenario's `ledgerRowCount` is the full total (> 25) so the "+N more in memory.md" footer
// shows once expanded. Generated rows are illustrative, not live (the sidecar notes this).
// Tier 2: each row also carries the per-side commit message + committer date (the popover's 6 columns).
// The top 4 are the real 5h commits; the generated rows get stepped synthetic metadata so the expanded
// bench popover reads complete. The honest "no metadata -> hash-only" fallback lives in a unit test.
const LEDGER_ROWS: LedgerRefNode[] = [
  {
    codeCommit: "08e9221a",
    memoryCommit: "d60a0511",
    codeSubject: "dashboard(5h): ledger popover on both warp couplers",
    codeDate: "2026-06-18T18:19:48+02:00",
    memorySubject: "memory(5h): ledger-popover sidecars + engine-room overview",
    memoryDate: "2026-06-18T18:21:10+02:00",
  },
  {
    codeCommit: "dbb81260",
    memoryCommit: "d516c141",
    codeSubject: "fix(dashboard): warp coupler = the memory.md ledger link (5h)",
    codeDate: "2026-06-18T14:02:33+02:00",
    memorySubject: "memory(5h): coupler-semantics onboarding",
    memoryDate: "2026-06-18T14:05:50+02:00",
  },
  {
    codeCommit: "e8801dce",
    memoryCommit: "aea63c67",
    codeSubject: "dashboard(5h): closeout train + ff-only/replay integration conduit",
    codeDate: "2026-06-17T20:48:12+02:00",
    memorySubject: "memory(5h): H2 integration-render sidecars",
    memoryDate: "2026-06-17T20:51:02+02:00",
  },
  {
    codeCommit: "600f7fa3",
    memoryCommit: "1e667c6d",
    codeSubject: "dashboard(5h): landing projection + live remote/PR probe (H1)",
    codeDate: "2026-06-17T16:30:05+02:00",
    memorySubject: "memory(5h): H1 landing-projection onboarding",
    memoryDate: "2026-06-17T16:33:40+02:00",
  },
  ...Array.from({ length: 21 }, (_, i) => {
    const dd = String(14 - (i % 13)).padStart(2, "0");
    const hh = String(9 + (i % 9)).padStart(2, "0");
    return {
      codeCommit: (0x1a2b3c4d + i * 0x01010101).toString(16).slice(-8),
      memoryCommit: (0x9f8e7d6c - i * 0x01010101).toString(16).slice(-8),
      codeSubject: `chore(dashboard): maintenance pass ${21 - i}`,
      codeDate: `2026-06-${dd}T${hh}:30:00+02:00`,
      memorySubject: `memory: routine onboarding refresh ${21 - i}`,
      memoryDate: `2026-06-${dd}T${hh}:32:00+02:00`,
    };
  }),
];
// Per worktree-coupler default: every external-memory worktree has its own memory.md, so the worktree
// coupler is always live (ledgerRowCount > the served window → the "+N more in memory.md" footer).
const LEDGER_TOTAL = 40;

// The repo's main memory ledger (Analytics.ledgers) — the OFFICIAL coupler popover reads this. The newest
// row maps the fixture's official source commit (08e9221a), so the official coupler highlights it too;
// closeoutCount is the full total (> the served window) so the popover's "+N more" footer renders.
export const OFFICIAL_LEDGER: LedgerNode = {
  repository: "agents-remember",
  closeoutCount: LEDGER_TOTAL,
  lastVerifiedCodeCommit: "08e9221a",
  baseCodeCommit: "a59553db",
  rows: LEDGER_ROWS,
};

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

function landingRef(
  kind: string,
  label: string,
  state: string,
  factState: ProcessFactState = "observed",
  detail?: string,
): LandingRefNode {
  return { kind, label, state, factState, ...(detail ? { detail } : {}) };
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
    // Memory mirrors code: the memory worktree integrates back into the feat SOURCE (memory) too, before the
    // carryover (feat → main mem). Only with external memory, and skipped when blocked so the all-or-nothing
    // integration conflict keeps a single code-lane STOP (no second gate on the memory lane).
    if (external && states.integration !== "blocked") {
      out.push({
        id: "integration-mem",
        fromNode: "memory-worktree",
        toNode: "memory-source",
        kind: "integration-mem",
        state: states.integration,
        label: "integrate ff-only → source (memory)",
      });
    }
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
    landing: [],
    ledgerRows: LEDGER_ROWS,
    ledgerRowCount: LEDGER_TOTAL,
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
    // B0 — main only: the official line + workspace engines exist; the enclosure is not yet
    // materialised. Worktree refs are `planned` (not on disk), no providers, every edge planned —
    // so the canvas renders the left world solid and the worktree side at rest (the build-up's
    // honest start, = podstage B0). No missingFacts: a not-yet-created enclosure is not an alarm.
    name: "engine-boot-0-main-only",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "worktree-started",
        health: "running",
        codeWorktree: ref({ branch: `ar/${BOOT_ID}`, exists: false, factState: "planned" }),
        memoryWorktree: ref({ branch: `ar/${BOOT_ID}`, exists: false, factState: "planned" }),
        setupState: undefined,
        completedPhases: [],
        providers: [],
        edges: edges({ worktreeAdd: "planned", ledger: "planned", cgc: "planned", grepai: "planned" }),
        ledgerRows: [],
        ledgerRowCount: 0,
        summary: "worktree_start invoked — the official line is at rest; the enclosure is not yet materialised.",
        nextAction: "continue_work",
        missingFacts: [],
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    // B1 — the code worktree copies in from the official line (branch-copy); memory still planned.
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
        edges: edges({ worktreeAdd: "complete", ledger: "planned", cgc: "planned", grepai: "planned" }),
        summary: "Code worktree forked from the official line; resolving external memory compatibility.",
        nextAction: "continue_work",
        missingFacts: [],
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    // B2 — the memory worktree copies in; the contract coupler binds (code === memory).
    name: "engine-boot-2-memory-contract",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "contract-written",
        health: "running",
        setupState: undefined,
        completedPhases: [],
        providers: [],
        edges: edges({ worktreeAdd: "complete", ledger: "complete", cgc: "planned", grepai: "planned" }),
        summary: "Memory ledger maps the base commit; contract written — the coupler binds.",
        missingFacts: [],
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    // B3 — provider runtime deploys: the engines materialise dim and the clone conduits begin
    // (cloned from main, not re-indexed). Engines `configured` (dim outline, drained charge).
    name: "engine-boot-3-providers-dim",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "provider-setup",
        health: "running",
        setupState: "running",
        currentPhase: "provider runtime deploy",
        heartbeatAgeSeconds: 1,
        completedPhases: [],
        providers: [boot("code", "configured"), boot("memory", "configured")],
        edges: edges({ worktreeAdd: "complete", ledger: "complete", cgc: "running", grepai: "running" }),
        summary: "Provider runtime deploying — the engines materialise dim, cloned from the official line.",
        missingFacts: [],
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    // B4 — seed / clone: both engines charge (the center-out boot-fill) while the clone conduits run.
    name: "engine-boot-4-seeding",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "provider-setup",
        health: "running",
        setupState: "running",
        currentPhase: "codegraphcontext-code seed · grepai-memory clone",
        heartbeatAgeSeconds: 2,
        completedPhases: [],
        providers: [boot("code", "indexing"), boot("memory", "indexing")],
        edges: edges({ worktreeAdd: "complete", ledger: "complete", cgc: "running", grepai: "running" }),
        summary: "Provider runtime booting — CGC seeds and GrepAI clones; the engines charge cyan.",
        missingFacts: [],
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    // B5 — powered: the engines went green and settle nominal; the clone conduits are gone. The
    // idle constellation (= the default engineProcess: all observed, providers nominal, edges complete).
    name: "engine-boot-5-nominal",
    processes: [engineProcess({ ...bootBase })],
    workspace: WORKSPACE,
  },
];

// --- T3B (05o) memory-block arc -----------------------------------------------
// The verify → block beats as ONE boot-demo enclosure (the recover/settle beats reuse the boot frames),
// so the whole arc is the SAME enclosure and the recover animates a prop diff, not a remount. Named
// `engine-boot-*` so the bench GALLERY hides them (scenario-player frames, like the other boot steps).
const memoryBlockStages: EngineRoomScenario[] = [
  {
    // verify — the code worktree is real; the ledger gate is checking the memory side (ledger-map running)
    // and the memory worktree is not yet on disk → the cyan scan ring sweeps the lane. Code lane solid.
    name: "engine-boot-memory-verify",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "code-worktree",
        health: "running",
        memoryWorktree: ref({ branch: `ar/${BOOT_ID}`, exists: false, factState: "planned" }),
        setupState: undefined,
        completedPhases: [],
        providers: [],
        edges: edges({ worktreeAdd: "complete", ledger: "running", cgc: "planned", grepai: "planned" }),
        summary: "Ledger-verify — checking the memory mapping for the selected base commit.",
        nextAction: "continue_work",
        missingFacts: [],
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    // block — no ledger map for the base commit: the memory lane is GATED (steady red) + GHOSTED while the
    // code lane stays solid; `reconciliation` is the recovery. Same boot-demo enclosure as verify/recover.
    name: "engine-boot-memory-blocked",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "worktree-started",
        health: "blocked",
        memoryWorktree: ref({ branch: `ar/${BOOT_ID}`, path: "(not created)", exists: false, factState: "missing" }),
        setupState: undefined,
        completedPhases: [],
        providers: [],
        edges: edges({ worktreeAdd: "complete", ledger: "blocked", cgc: "planned", grepai: "planned" }),
        ledgerRows: [],
        ledgerRowCount: 0,
        summary: "External memory blocked: no ledger mapping for the selected base commit — reconcile to map it.",
        nextAction: "reconciliation",
        missingFacts: [
          "memory worktree not present on disk",
          "no ledger mapping for the selected code base commit",
        ],
      }),
    ],
    workspace: WORKSPACE,
  },
];

// --- T1B (05o) stale-base block arc ------------------------------------------
// The preflight → block beats as ONE boot-demo enclosure (recover reuses the boot frames). The base
// (local main) is behind upstream: the preflight scans the code lane, then a fleeting enclosure is born
// blocked with the main code node pruned (dormant). Named `engine-boot-*` so the bench GALLERY hides them.
const staleBaseStages: EngineRoomScenario[] = [
  {
    // preflight — scanning the base lane: is local main current with upstream? (worktree-add running, code
    // worktree not yet on disk → the cyan scan ring sweeps the code lane). The base staleness isn't decided yet.
    name: "engine-boot-stale-verify",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "worktree-started",
        health: "running",
        codeWorktree: ref({ branch: `ar/${BOOT_ID}`, exists: false, factState: "planned" }),
        memoryWorktree: ref({ branch: `ar/${BOOT_ID}`, exists: false, factState: "planned" }),
        setupState: undefined,
        completedPhases: [],
        providers: [],
        edges: edges({ worktreeAdd: "running", ledger: "planned", cgc: "planned", grepai: "planned" }),
        summary: "Preflight — checking the base (local main) is current with upstream.",
        nextAction: "continue_work",
        missingFacts: [],
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    // block — base behind upstream: a FLEETING enclosure is born blocked (start gated before the contract),
    // the main code node reads pruned/dormant, and two recovery choices (fast-forward / proceed-stale) show.
    name: "engine-boot-stale-blocked",
    processes: [
      engineProcess({
        ...bootBase,
        phase: "worktree-started",
        health: "blocked",
        codeSource: ref({ branch: SOURCE_BRANCH, commit: "08e9221a", path: `/home/dev/Projects/agents-remember`, behindSource: 3 }),
        codeWorktree: ref({ branch: `ar/${BOOT_ID}`, exists: false, factState: "planned" }),
        memoryWorktree: ref({ branch: `ar/${BOOT_ID}`, exists: false, factState: "planned" }),
        setupState: undefined,
        completedPhases: [],
        providers: [],
        edges: edges({ worktreeAdd: "planned", ledger: "planned", cgc: "planned", grepai: "planned" }),
        ledgerRows: [],
        ledgerRowCount: 0,
        actions: [
          { action: "fast-forward", enabled: true },
          { action: "proceed-stale", enabled: true },
        ],
        nextAction: "fast-forward",
        summary: "Stale base — local main is 3 commits behind upstream.",
        missingFacts: [
          "start gated at stale-base preflight — contract not yet written",
          "local main is behind upstream",
        ],
      }),
    ],
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
        ledgerRows: [], // no ledger mapping yet — the coupler popover stays empty (no fake rows)
        ledgerRowCount: 0,
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
        ledgerRows: [], // pre-contract: no ledger mapping yet → empty coupler popover
        ledgerRowCount: 0,
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
        integrationStrategy: "ff-only",
        cleanup: "pending",
        // D5 de-materialise: the work has LANDED (the strip is done), so the WORKTREE side detaches — its
        // branches go `planned` (fading out) and the provider runtime powers down (no boot nodes), while
        // the official line (main) stays solid and the remote dock holds until the row is removed (D6).
        codeWorktree: ref({ branch: "ar/boot-audio", path: "(detaching)", exists: false, factState: "planned" }),
        memoryWorktree: ref({ branch: "ar/boot-audio", path: "(detaching)", exists: false, factState: "planned" }),
        landing: [
          landingRef("origin-feat", "origin/feat-…", "merged"),
          landingRef("pr", "PR #128", "merged"),
          landingRef("origin-main", "origin/main", "tip", "observed", "0a1b2c3"),
          landingRef("origin-mem-main", "origin/mem-main", "pushed"),
        ],
        providers: [],
        actions: [{ action: "cleanup", enabled: true }],
        summary: "Landed; the provider runtime powers down and the worktree detaches (de-materialise).",
        nextAction: "request_cleanup_decision",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    // D6 stack removed: the enclosure is gone. The worktree branches + providers are removed (missing),
    // the landing arc has retired (no remote dock, no feat tier) — only the official line (main) + a dim
    // historical contract chip remain. `cleanup` is `done`, so the row is retiring out of the stack list.
    name: "engine-retired",
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
        integrationStrategy: "ff-only",
        cleanup: "done",
        codeWorktree: ref({ branch: "ar/boot-audio", path: "(removed)", exists: false, factState: "planned" }),
        memoryWorktree: ref({ branch: "ar/boot-audio", path: "(removed)", exists: false, factState: "planned" }),
        landing: [],
        ledgerRows: [],
        ledgerRowCount: 0,
        providers: [],
        actions: [],
        summary: "Retired — worktree removed; only the official line (main) and the historical record remain.",
        nextAction: "",
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
  {
    // 5h T13 — closeout train: the known closeout order plays on closeout-pending (before integration).
    name: "engine-landing-closeout",
    processes: [
      engineProcess({
        id: "boot-audio",
        taskName: "boot-audio-polish",
        repoName: "agents-remember",
        phase: "closeout-pending",
        health: "nominal",
        humanReviewStatus: "approved",
        closeoutStatus: "in-progress",
        landing: [landingRef("origin-feat", "origin/feat-…", "planned", "planned", "pushes after closeout")],
        summary: "Closeout approved; running the closeout order (code → onboarding → quality → memory → ledger).",
        nextAction: "closeout",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    // 5h T14 — successful ff-only landing, mid-arc: source pushed, PR open, memory not yet carried.
    name: "engine-landing-ffonly",
    processes: [
      engineProcess({
        id: "boot-audio",
        taskName: "boot-audio-polish",
        repoName: "agents-remember",
        phase: "integration-pending",
        health: "nominal",
        humanReviewStatus: "approved",
        closeoutStatus: "completed",
        integrationStrategy: "ff-only",
        cleanup: "pending",
        edges: edges({ integration: "running" }),
        landing: [
          landingRef("origin-feat", "origin/feat-…", "pushed", "observed", "a1b2c3d4"),
          landingRef("pr", "PR #128", "open", "observed", "checks green"),
          landingRef("origin-main", "origin/main", "tip", "observed"),
          landingRef("origin-mem-main", "origin/mem-main", "planned", "planned", "after carryover"),
        ],
        summary: "Closeout complete; integrating ff-only — source pushed, PR open.",
        nextAction: "request_integration_decision",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    // 5h T14b/T16 — replay onto a moved main, PR merged, memory carried over (D4). The enclosure is
    // INTACT (integration-pending, not cleanup) — the de-materialise is the next beat (D5).
    name: "engine-landing-merged",
    processes: [
      engineProcess({
        id: "boot-audio",
        taskName: "boot-audio-polish",
        repoName: "agents-remember",
        phase: "integration-pending",
        health: "nominal",
        humanReviewStatus: "approved",
        closeoutStatus: "completed",
        integrationStatus: "completed",
        integrationStrategy: "replay",
        cleanup: "pending",
        edges: edges({ integration: "complete" }),
        landing: [
          landingRef("origin-feat", "origin/feat-…", "merged", "observed"),
          landingRef("pr", "PR #128", "merged", "observed", "merged into main"),
          landingRef("origin-main", "origin/main", "tip", "observed", "advanced"),
          landingRef("origin-mem-main", "origin/mem-main", "pushed", "observed", "carryover done"),
        ],
        actions: [{ action: "cleanup", enabled: true }],
        summary: "Replayed onto moved main; PR merged, memory carried over — cleanup pending.",
        nextAction: "request_cleanup_decision",
      }),
    ],
    workspace: WORKSPACE,
  },
  {
    // 5k D3 — code lands: feat pushed → PR merged → origin/main advanced → local main pulls. The CODE has
    // landed but the MEMORY carryover is the NEXT beat (D4), so origin-mem-main is still `planned` here.
    // Splits the previously-collapsed D2·D3 frame: D2 (`engine-landing-ffonly`) is the integrate/push/PR-open
    // beat; this is the merge beat; D4 (`engine-landing-merged`) is the memory carryover.
    name: "engine-landing-pushed",
    processes: [
      engineProcess({
        id: "boot-audio",
        taskName: "boot-audio-polish",
        repoName: "agents-remember",
        phase: "integration-pending",
        health: "nominal",
        humanReviewStatus: "approved",
        closeoutStatus: "completed",
        integrationStatus: "completed",
        integrationStrategy: "ff-only",
        cleanup: "pending",
        edges: edges({ integration: "complete" }),
        landing: [
          landingRef("origin-feat", "origin/feat-…", "pushed", "observed", "a1b2c3d4"),
          landingRef("pr", "PR #128", "merged", "observed", "merged into main"),
          landingRef("origin-main", "origin/main", "tip", "observed", "advanced"),
          landingRef("origin-mem-main", "origin/mem-main", "planned", "planned", "after carryover"),
        ],
        summary: "Code landed — PR merged, origin/main advanced; memory carryover next.",
        nextAction: "request_integration_decision",
      }),
    ],
    workspace: WORKSPACE,
  },
  ...bootStages,
  ...memoryBlockStages,
  ...staleBaseStages,
];
