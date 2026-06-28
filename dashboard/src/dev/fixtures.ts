// Hand-authored gallery fixtures: the full grammar-state × attention-taxonomy matrix the
// `--sim` replay can't produce (sim emits one lifecycle). Each is a valid WorkspaceProjection
// the bench hydrates into the store. The attention queues here mirror what the reducer's
// build_attention_queue would compute for the same tree — kept in sync by eye (sidecar-free,
// dashboard/** is out of memory scope).

import { ENGINE_ROOM_SCENARIOS, OFFICIAL_LEDGER } from "../panels/engine-room/fixtures";
import type { ObserverEvent } from "../types/event";
import type {
  EnclosureNode,
  LifecycleProjection,
  ProviderNode,
  WorkspaceProjection,
} from "../types/projection";

const EMPTY_ANALYTICS: WorkspaceProjection["analytics"] = {
  driftSnapshots: [],
  stalestSidecars: [],
  setupSummaries: [],
  setupProgress: [],
  routeCoverage: [],
  toolReports: [],
  ledgers: [],
  taskDocuments: [],
  series: [],
  attentionQueue: [],
  engineProcesses: [],
};

function lifecycle(
  over: Partial<LifecycleProjection> & Pick<LifecycleProjection, "id">,
): LifecycleProjection {
  return {
    state: "running",
    phase: "build",
    fleeting: false,
    tokens: 0,
    startedAt: "2026-06-14T09:00:00+00:00",
    lastEventTs: "2026-06-14T09:00:30+00:00",
    inferred: false,
    actions: [],
    tokenSeries: [],
    ...over,
  };
}

function project(over: Partial<WorkspaceProjection> = {}): WorkspaceProjection {
  const { lifecycles = [], analytics, ...rest } = over;
  return {
    version: 2,
    generatedAt: "2026-06-14T09:01:00+00:00",
    enclosures: [],
    providers: [],
    activeWorktreeGroups: [],
    ...rest,
    lifecycles,
    metrics: {
      lifecycleCount: lifecycles.length,
      runningCount: lifecycles.filter((entry) => entry.state === "running").length,
      blockedCount: lifecycles.filter((entry) => entry.state === "blocked").length,
      pausedCount: lifecycles.filter((entry) => entry.state === "paused").length,
      totalTokens: lifecycles.reduce((sum, entry) => sum + entry.tokens, 0),
      stalenessHistogram: {},
    },
    analytics: { ...EMPTY_ANALYTICS, ...analytics },
  };
}

const ok = (id: string): ProviderNode => ({
  id,
  state: "ready",
  ok: true,
  watcherUp: true,
  indexingState: "indexed",
  scope: "workspace",
  role: id.includes("memory") || id.includes("grepai") ? "memory" : "code",
});
const down = (id: string): ProviderNode => ({
  id,
  state: "stopped",
  ok: false,
  watcherUp: false,
  indexingState: "unknown",
  scope: "workspace",
  role: id.includes("memory") || id.includes("grepai") ? "memory" : "code",
});
const indexing = (id: string): ProviderNode => ({
  id,
  state: "ready",
  ok: true,
  watcherUp: true,
  indexingState: "indexing",
  snapshotStaleSeconds: 3,
  scope: "workspace",
  role: id.includes("memory") || id.includes("grepai") ? "memory" : "code",
});

function enclosure(
  over: Partial<EnclosureNode> & Pick<EnclosureNode, "enclosure" | "repoName" | "taskName">,
): EnclosureNode {
  return {
    taskId: over.enclosure,
    enclosureId: over.enclosure,
    leafId: over.taskName,
    taskRoot: over.enclosure,
    lifecycleId: "",
    worktreeGroup: over.enclosure,
    humanReviewStatus: "pending",
    closeoutStatus: "pending",
    integrationStatus: "not-started",
    cleanup: "pending",
    actions: [],
    ...over,
  };
}

const evt = (
  id: string,
  kind: string,
  trust: ObserverEvent["trust"],
  actor: ObserverEvent["actor"],
  over: Partial<ObserverEvent> = {},
): ObserverEvent => ({
  schema: "ar-observer-event/v1",
  id,
  ts: "2026-06-14T09:00:30+00:00",
  kind,
  trust,
  actor,
  ...over,
});

export interface GalleryEntry {
  name: string;
  projection: WorkspaceProjection;
  events?: ObserverEvent[];
}

// Wrap one engine-room scenario (its processes + workspace stack) into a full WorkspaceProjection — the
// shared mapping behind both the GALLERY entries and the slice-5i scenario-player frames.
export function engineRoomProjection(scenario: (typeof ENGINE_ROOM_SCENARIOS)[number]): WorkspaceProjection {
  return project({
    providers: scenario.workspace,
    analytics: {
      ...EMPTY_ANALYTICS,
      engineProcesses: scenario.processes,
      ledgers: [OFFICIAL_LEDGER], // the official coupler resolves its repo's ledger from analytics.ledgers
    },
  });
}

export const GALLERY: GalleryEntry[] = [
  {
    name: "calm",
    projection: project({
      lifecycles: [
        lifecycle({
          id: "build-001",
          state: "running",
          phase: "build",
          repoId: "agents-remember",
          enclosure: "wt-a",
          tokens: 4200,
          staleSeconds: 12,
          tokenSeries: [
            { ts: "2026-06-14T09:00:10+00:00", cumulative: 1200 },
            { ts: "2026-06-14T09:00:25+00:00", cumulative: 2600 },
            { ts: "2026-06-14T09:00:40+00:00", cumulative: 4200 },
          ],
        }),
        lifecycle({ id: "fleeting-9", fleeting: true, phase: "reframe-research", staleSeconds: 4 }),
      ],
      providers: [ok("codegraphcontext-code"), ok("grepai-memory")],
    }),
  },
  {
    name: "blocked",
    projection: project({
      lifecycles: [
        lifecycle({
          id: "plan-002",
          state: "blocked",
          phase: "reframe-research",
          repoId: "agents-remember",
          enclosure: "wt-a",
          staleSeconds: 95,
          tokens: 800,
          ask: { question: "Approve the plan?" },
          actions: [{ action: "resume", enabled: true }],
          tokenSeries: [{ ts: "2026-06-14T09:00:20+00:00", cumulative: 800 }],
        }),
      ],
      providers: [ok("codegraphcontext-code"), ok("grepai-memory")],
      analytics: {
        ...EMPTY_ANALYTICS,
        attentionQueue: [
          {
            id: "blocked-gate:plan-002",
            kind: "blocked-gate",
            severity: "warn",
            lane: "lifecycle",
            title: "Gate — input needed",
            detail: "Approve the plan?",
            waitSeconds: 95,
            lifecycleId: "plan-002",
            enclosure: "wt-a",
            repoId: "agents-remember",
          },
        ],
      },
    }),
  },
  {
    name: "alarm",
    projection: project({
      lifecycles: [
        lifecycle({ id: "build-007", state: "running", repoId: "repo-b", staleSeconds: 30 }),
        lifecycle({
          id: "old-003",
          state: "paused",
          inferred: true,
          repoId: "repo-b",
          staleSeconds: 5400,
        }),
      ],
      providers: [down("codegraphcontext-code"), ok("grepai-memory")],
      analytics: {
        ...EMPTY_ANALYTICS,
        attentionQueue: [
          {
            id: "provider-down:codegraphcontext-code",
            kind: "provider-down",
            severity: "alarm",
            lane: "repo",
            title: "Provider codegraphcontext-code down",
            detail: "stopped",
            providerId: "codegraphcontext-code",
          },
          {
            id: "failed-setup:wt-b",
            kind: "failed-setup",
            severity: "alarm",
            lane: "worktree",
            title: "Provider setup needs attention",
            detail: "cgc index",
            waitSeconds: 40,
            enclosure: "wt-b",
          },
          {
            id: "actionable-drift:repo-b",
            kind: "actionable-drift",
            severity: "warn",
            lane: "repo",
            title: "3 actionable drift",
            waitSeconds: 7200,
            repoId: "repo-b",
          },
          {
            id: "stale-session:old-003",
            kind: "stale-session",
            severity: "info",
            lane: "lifecycle",
            title: "Session gone quiet",
            waitSeconds: 5400,
            lifecycleId: "old-003",
            repoId: "repo-b",
          },
        ],
      },
    }),
  },
  {
    name: "full",
    events: [
      evt("e1", "lifecycle.started", "observed", "system", { lifecycleId: "build-001" }),
      evt("e2", "lifecycle.promoted", "approved", "developer", {
        lifecycleId: "build-001",
        enclosure: "wt-a",
        repoId: "agents-remember",
      }),
      evt("e3", "tool.completed", "observed", "model", { lifecycleId: "build-001" }),
      evt("e4", "lifecycle.blocked", "observed", "model", { lifecycleId: "plan-002" }),
      evt("e5", "correction.recorded", "inferred", "system", { lifecycleId: "old-003" }),
    ],
    projection: project({
      lifecycles: [
        lifecycle({
          id: "build-001",
          state: "running",
          phase: "build",
          repoId: "agents-remember",
          enclosure: "wt-a",
          tokens: 4200,
          staleSeconds: 12,
          tokenSeries: [
            { ts: "2026-06-14T09:00:10+00:00", cumulative: 1200 },
            { ts: "2026-06-14T09:00:25+00:00", cumulative: 2600 },
            { ts: "2026-06-14T09:00:40+00:00", cumulative: 4200 },
          ],
        }),
        lifecycle({
          id: "plan-002",
          state: "blocked",
          phase: "reframe-research",
          repoId: "agents-remember",
          enclosure: "wt-a2",
          staleSeconds: 140,
          tokens: 800,
          ask: { question: "Approve the plan?" },
          actions: [{ action: "resume", enabled: true }],
        }),
        lifecycle({
          id: "old-003",
          state: "paused",
          inferred: true,
          repoId: "repo-b",
          enclosure: "wt-b",
          staleSeconds: 6200,
        }),
        lifecycle({ id: "fleeting-9", fleeting: true, phase: "trust-checkpoint", staleSeconds: 8 }),
        lifecycle({
          id: "done-004",
          state: "completed",
          phase: "close",
          repoId: "repo-b",
          enclosure: "wt-b",
          tokens: 9000,
        }),
      ],
      enclosures: [
        enclosure({
          enclosure: "wt-a",
          repoName: "agents-remember",
          taskName: "browser-dashboard",
          lifecycleId: "build-001",
          closeoutStatus: "completed",
          cleanup: "pending",
          actions: [
            { action: "integrate", enabled: true },
            { action: "cleanup", enabled: false, disabledReason: "integration not complete" },
          ],
        }),
        enclosure({
          enclosure: "wt-a2",
          repoName: "agents-remember",
          taskName: "gate-control",
          lifecycleId: "plan-002",
        }),
        enclosure({
          enclosure: "wt-b",
          repoName: "repo-b",
          taskName: "fission-spike",
          lifecycleId: "old-003",
          integrationStatus: "completed",
          closeoutStatus: "completed",
          cleanup: "pending",
          actions: [{ action: "cleanup", enabled: true }],
        }),
      ],
      providers: [indexing("codegraphcontext-code"), ok("grepai-memory")],
      analytics: {
        ...EMPTY_ANALYTICS,
        driftSnapshots: [
          {
            repository: "agents-remember",
            branch: "main",
            counts: { current: 372, drifted: 4, missingVerification: 2 },
            actionableCount: 6,
            snapshotStaleSeconds: 900,
          },
          {
            repository: "repo-b",
            branch: "main",
            counts: { current: 88 },
            actionableCount: 0,
            snapshotStaleSeconds: 120,
          },
        ],
        ledgers: [
          {
            repository: "agents-remember",
            closeoutCount: 95,
            lastVerifiedCodeCommit: "c041ff5fade1",
            baseCodeCommit: "85af25823437",
            rows: [
              { codeCommit: "c041ff5fade1", memoryCommit: "dd436bcae902" },
              { codeCommit: "08e9221abcd0", memoryCommit: "d60a0511ef34" },
            ],
          },
          {
            repository: "repo-b",
            closeoutCount: 12,
            lastVerifiedCodeCommit: "abc1234def56",
            baseCodeCommit: "abc1234def56",
            rows: [{ codeCommit: "abc1234def56", memoryCommit: "fed4321cba98" }],
          },
        ],
        stalestSidecars: [
          {
            onboardingFile: "observer/reducer.py.md",
            repository: "agents-remember",
            lastVerifiedDate: "2026-05-20",
            ageSeconds: 2_160_000,
          },
          {
            onboardingFile: "serving/events.py.md",
            repository: "agents-remember",
            lastVerifiedDate: "2026-06-01",
            ageSeconds: 1_120_000,
          },
        ],
        attentionQueue: [
          {
            id: "blocked-gate:plan-002",
            kind: "blocked-gate",
            severity: "warn",
            lane: "lifecycle",
            title: "Gate — input needed",
            detail: "Approve the plan?",
            waitSeconds: 140,
            lifecycleId: "plan-002",
            enclosure: "wt-a2",
            repoId: "agents-remember",
          },
          {
            id: "actionable-drift:agents-remember",
            kind: "actionable-drift",
            severity: "warn",
            lane: "repo",
            title: "6 actionable drift",
            waitSeconds: 900,
            repoId: "agents-remember",
          },
          {
            id: "stale-session:old-003",
            kind: "stale-session",
            severity: "info",
            lane: "lifecycle",
            title: "Session gone quiet",
            waitSeconds: 6200,
            lifecycleId: "old-003",
            repoId: "repo-b",
          },
        ],
      },
    }),
  },
  {
    name: "gate-review",
    projection: project({
      lifecycles: [
        lifecycle({
          id: "closeout-005",
          state: "blocked",
          phase: "close",
          repoId: "agents-remember",
          enclosure: "wt-a",
          staleSeconds: 30,
          gate: {
            id: "01HGATE",
            kind: "closeout-approval",
            state: "open",
            decisions: ["approve", "cancel", "reject", "request-revision"],
            packet: { changedPaths: 6 },
            ts: "2026-06-14T09:00:30+00:00",
          },
        }),
      ],
      providers: [ok("codegraphcontext-code"), ok("grepai-memory")],
      analytics: {
        ...EMPTY_ANALYTICS,
        attentionQueue: [
          {
            id: "gate:01HGATE",
            kind: "gate-open",
            severity: "warn",
            lane: "lifecycle",
            title: "Gate — closeout-approval",
            detail: "awaiting your decision",
            lifecycleId: "closeout-005",
          },
        ],
      },
    }),
  },
  { name: "empty", projection: project() },
  // The boot-step frames are hidden from the bench strip (slice 5i's scenario player plays the build-up
  // instead); the fixtures stay in ENGINE_ROOM_SCENARIOS for that reuse + the render tests.
  ...ENGINE_ROOM_SCENARIOS.filter((scenario) => !scenario.name.startsWith("engine-boot-")).map(
    (scenario) => ({
      name: scenario.name,
      projection: engineRoomProjection(scenario),
    }),
  ),
];
