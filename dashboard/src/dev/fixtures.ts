// Hand-authored gallery fixtures: the full grammar-state × attention-taxonomy matrix the
// `--sim` replay can't produce (sim emits one lifecycle). Each is a valid WorkspaceProjection
// the bench hydrates into the store. The attention queues here mirror what the reducer's
// build_attention_queue would compute for the same tree — kept in sync by eye (sidecar-free,
// dashboard/** is out of memory scope).

import type {
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
  attentionQueue: [],
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
    version: 1,
    generatedAt: "2026-06-14T09:01:00+00:00",
    enclosures: [],
    providers: [],
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
});
const down = (id: string): ProviderNode => ({
  id,
  state: "stopped",
  ok: false,
  watcherUp: false,
  indexingState: "unknown",
});

export interface GalleryEntry {
  name: string;
  projection: WorkspaceProjection;
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
  { name: "empty", projection: project() },
];
