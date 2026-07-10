import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import type { ObserverEvent } from "../types/event";
import type {
  Analytics,
  EnclosureNode,
  LifecycleProjection,
  Metrics,
  ProviderNode,
  ServingBuild,
  SupervisorHeartbeat,
  WorkspaceProjection,
} from "../types/projection";
import { stableEquals, stampServed } from "./servedAges";

export type ConnState = "connecting" | "live" | "signal-lost";

export interface DashboardState {
  conn: ConnState;
  generatedAt: string | null;
  // A monotonic generation counter bumped by `reset()`. The dev bench reuses ONE store across scenarios;
  // keying the engine-room canvas by `gen` forces a clean REMOUNT on a scenario switch, so an exiting
  // failure overlay (e.g. the FleetingEnclosure) from the previous mode can't orphan and bleed through. In
  // production nothing calls `reset()`, so `gen` stays 0 and the canvas is never remounted by it.
  gen: number;
  lifecycles: Record<string, LifecycleProjection>; // keyed by id
  enclosures: Record<string, EnclosureNode>; // keyed by `enclosure`
  providers: Record<string, ProviderNode>; // keyed by id
  activeWorktreeGroups: string[]; // worktree-group basenames with a live enclosure (Topology scope)
  metrics: Metrics | null;
  analytics: Analytics | null;
  // The boot-time serving stamp (260703-L15): which build/process is answering. Rides the
  // snapshot; rendered muted in the top bar so a stale server is visible at a glance.
  servingBuild: ServingBuild | null;
  // The supervisor sweep's self-liveness tick (260707-HFX2-L2 R5). Rendered red in the top bar
  // past its staleness cutoff -- "the last turtle is the developer's glance".
  supervisorHeartbeat: SupervisorHeartbeat | null;
  events: ObserverEvent[]; // raw observer feed retained client-side until reset/reload
  eventsHydrated: boolean;
  suppressedAttentionIds: Record<string, true>;
  setConn: (conn: ConnState) => void;
  applySnapshot: (projection: WorkspaceProjection) => void;
  applyDelta: (event: string, data: unknown) => void;
  pushEvent: (line: string) => void;
  markEventsHydrated: () => void;
  suppressAttention: (ids: readonly string[]) => void;
  releaseAttention: (ids: readonly string[]) => void;
  reset: () => void;
}

// The Event River keeps a bounded sliding window of the raw observer feed in memory: newest
// retained, oldest dropped past the window. This is a memory bound for a long-lived tab (not the
// silent newest-N display cap that masked backend overload) — the backend observer-log retention
// is the real history bound, and EventRiver virtualizes the window so render cost stays flat.
const EVENT_WINDOW = 2000;

// ── The change gate (260703-L15) ────────────────────────────────────────────────────────
// Both apply paths are IDENTITY-PRESERVING: an arriving node that stable-equals the stored
// one (volatile age fields ignored — data/servedAges.ts mirrors serving/delta.py) keeps the
// EXISTING object, and an apply in which nothing changed performs NO store write at all. A
// long-lived tab receiving reconnect snapshots or redundant deltas therefore allocates
// nothing and re-renders nothing; applied nodes are age-anchored via `stampServed` so
// staleness displays advance locally between real emissions.

/** Reuse the current value when the incoming one is stable-equal (keeps identity + anchor). */
const reuse = <T>(current: T, incoming: T): T => (stableEquals(current, incoming) ? current : incoming);

// `stableEquals` deliberately ignores `ageSeconds` (it's in VOLATILE_AGE_FIELDS) -- exactly
// the field the heartbeat tick advances. A field-literal comparison is needed here so a
// genuine tick advance (ageSeconds changing) is still recognized as a change, while a truly
// idle heartbeat (including null/null) still produces zero store writes.
const heartbeatEquals = (a: SupervisorHeartbeat | null, b: SupervisorHeartbeat | null): boolean => {
  if (a === b) return true;
  if (a === null || b === null) return false;
  return (
    a.lastTickAt === b.lastTickAt &&
    a.ageSeconds === b.ageSeconds &&
    a.staleCutoffSeconds === b.staleCutoffSeconds &&
    a.stale === b.stale &&
    a.pendingInboxCount === b.pendingInboxCount &&
    a.redeliverableInboxCount === b.redeliverableInboxCount &&
    a.lastSweepDurationSeconds === b.lastSweepDurationSeconds
  );
};

/**
 * Merge an incoming collection into the id-keyed map, reusing every stable-equal node.
 * Returns the EXISTING map object when nothing changed (added/updated/removed).
 */
const mergeKeyed = <T extends object>(
  existing: Record<string, T>,
  incoming: T[],
  key: (item: T) => string,
): Record<string, T> => {
  const next: Record<string, T> = {};
  let changed = false;
  for (const item of incoming) {
    const ident = key(item);
    const current = existing[ident];
    if (current !== undefined && stableEquals(current, item)) {
      next[ident] = current;
    } else {
      stampServed(item);
      next[ident] = item;
      changed = true;
    }
  }
  if (!changed && Object.keys(existing).length === incoming.length) return existing;
  return next;
};

// The analytics object replaces wholesale (matching the wire protocol), so each replacement
// re-anchors every age-bearing node it carries — their served ages are fresh at that moment.
const stampAnalytics = (analytics: Analytics | null): void => {
  if (!analytics) return;
  const at = Date.now();
  const collections: (readonly object[] | undefined)[] = [
    analytics.driftSnapshots,
    analytics.stalestSidecars,
    analytics.setupSummaries,
    analytics.setupProgress,
    analytics.toolReports,
    analytics.agentPickups,
    analytics.taskDocuments,
    analytics.series,
    analytics.attentionQueue,
    analytics.engineProcesses,
  ];
  for (const list of collections) {
    for (const node of list ?? []) stampServed(node, at);
  }
};

const upsert = <T>(
  collection: Record<string, T>,
  item: T,
  key: (item: T) => string,
): Record<string, T> => ({ ...collection, [key(item)]: item });

const remove = <T>(collection: Record<string, T>, id: string): Record<string, T> => {
  const next = { ...collection };
  delete next[id];
  return next;
};

const pruneSuppressedAttention = (
  suppressed: Record<string, true>,
  analytics: Analytics | null,
): Record<string, true> => {
  const ids = Object.keys(suppressed);
  if (ids.length === 0) return suppressed;
  const liveIds = new Set((analytics?.attentionQueue ?? []).map((item) => item.id));
  const kept = ids.filter((id) => liveIds.has(id));
  if (kept.length === ids.length) return suppressed;
  return Object.fromEntries(kept.map((id) => [id, true]));
};

// The state channel's named deltas, merged into the flat id-keyed maps. The server diffs
// consecutive projections on their STABLE forms (serving/delta.py) and emits upserts /
// `*.removed` markers; `metrics` / `analytics` arrive as whole-object replacements. Returns
// `null` when the delta would not change the store — the caller then skips the write.
function reduceDelta(
  state: DashboardState,
  event: string,
  data: unknown,
): Partial<DashboardState> | null {
  switch (event) {
    case "lifecycle": {
      const node = data as LifecycleProjection;
      if (stableEquals(state.lifecycles[node.id], node)) return null;
      stampServed(node);
      return { lifecycles: upsert(state.lifecycles, node, (x) => x.id) };
    }
    case "lifecycle.removed": {
      const { id } = data as { id: string };
      return id in state.lifecycles ? { lifecycles: remove(state.lifecycles, id) } : null;
    }
    case "enclosure": {
      const node = data as EnclosureNode;
      if (stableEquals(state.enclosures[node.enclosure], node)) return null;
      stampServed(node);
      return { enclosures: upsert(state.enclosures, node, (x) => x.enclosure) };
    }
    case "enclosure.removed": {
      const { enclosure } = data as { enclosure: string };
      return enclosure in state.enclosures
        ? { enclosures: remove(state.enclosures, enclosure) }
        : null;
    }
    case "provider": {
      const node = data as ProviderNode;
      if (stableEquals(state.providers[node.id], node)) return null;
      stampServed(node);
      return { providers: upsert(state.providers, node, (x) => x.id) };
    }
    case "provider.removed": {
      const { id } = data as { id: string };
      return id in state.providers ? { providers: remove(state.providers, id) } : null;
    }
    case "activeWorktreeGroups": {
      // Whole-value replacement (a bare list, no key) — see serving/delta.py.
      const { activeWorktreeGroups } = data as { activeWorktreeGroups: string[] };
      if (stableEquals(state.activeWorktreeGroups, activeWorktreeGroups)) return null;
      return { activeWorktreeGroups };
    }
    case "metrics": {
      const metrics = data as Metrics;
      return stableEquals(state.metrics, metrics) ? null : { metrics };
    }
    case "analytics": {
      const analytics = data as Analytics;
      if (stableEquals(state.analytics, analytics)) return null;
      stampAnalytics(analytics);
      return {
        analytics,
        suppressedAttentionIds: pruneSuppressedAttention(state.suppressedAttentionIds, analytics),
      };
    }
    default:
      return null;
  }
}

export const dashboardStore = createStore<DashboardState>((set, get) => ({
  conn: "connecting",
  generatedAt: null,
  gen: 0,
  lifecycles: {},
  enclosures: {},
  providers: {},
  activeWorktreeGroups: [],
  metrics: null,
  analytics: null,
  servingBuild: null,
  supervisorHeartbeat: null,
  events: [],
  eventsHydrated: false,
  suppressedAttentionIds: {},
  setConn: (conn) => {
    if (get().conn !== conn) set({ conn });
  },
  applySnapshot: (projection) => {
    const state = get();
    const lifecycles = mergeKeyed(state.lifecycles, projection.lifecycles, (x) => x.id);
    const enclosures = mergeKeyed(state.enclosures, projection.enclosures, (x) => x.enclosure);
    const providers = mergeKeyed(state.providers, projection.providers, (x) => x.id);
    const activeWorktreeGroups = reuse(
      state.activeWorktreeGroups,
      projection.activeWorktreeGroups ?? [],
    );
    const metrics = reuse(state.metrics, projection.metrics);
    const analytics = reuse(state.analytics, projection.analytics);
    if (analytics !== state.analytics) stampAnalytics(analytics);
    const servingBuild = reuse(state.servingBuild, projection.servingBuild ?? null);
    // 260707-HFX2-L2 R5: deliberately EXCLUDED from the `unchanged` gate below — it is a live tick
    // age injected at response time (mirroring the backend's own "volatile ages excluded" posture,
    // delta.py), so it is always applied even when nothing else in the snapshot changed.
    const supervisorHeartbeat = projection.supervisorHeartbeat ?? null;
    const suppressedAttentionIds = pruneSuppressedAttention(
      state.suppressedAttentionIds,
      analytics,
    );
    const unchanged =
      lifecycles === state.lifecycles &&
      enclosures === state.enclosures &&
      providers === state.providers &&
      activeWorktreeGroups === state.activeWorktreeGroups &&
      metrics === state.metrics &&
      analytics === state.analytics &&
      servingBuild === state.servingBuild &&
      suppressedAttentionIds === state.suppressedAttentionIds;
    // An unchanged snapshot (a reconnect while idle) is a NO-OP for content, but the heartbeat
    // tick still rides through below -- only when it actually changed, so a truly idle
    // reconnect (e.g. null/null) still performs zero store writes.
    if (unchanged && state.conn === "live") {
      if (!heartbeatEquals(state.supervisorHeartbeat, supervisorHeartbeat)) {
        set({ supervisorHeartbeat });
      }
      return;
    }
    set({
      conn: "live",
      // `generatedAt` is the stamp of the last APPLIED content (the top bar's "@ hh:mm:ss"),
      // so an unchanged re-snapshot must not advance it — ages and stamp stay coherent.
      generatedAt: unchanged ? state.generatedAt : projection.generatedAt,
      lifecycles,
      enclosures,
      providers,
      activeWorktreeGroups,
      metrics,
      analytics,
      servingBuild,
      supervisorHeartbeat,
      suppressedAttentionIds,
    });
  },
  applyDelta: (event, data) => {
    const partial = reduceDelta(get(), event, data);
    if (partial !== null) set(partial);
  },
  pushEvent: (line) =>
    set((state) => {
      try {
        const event = JSON.parse(line) as ObserverEvent;
        const next = [...state.events, event];
        // Slide the window: drop the oldest once past the bound so a long-lived tab never grows
        // the buffer without limit.
        const events = next.length > EVENT_WINDOW ? next.slice(next.length - EVENT_WINDOW) : next;
        return { events, eventsHydrated: true };
      } catch {
        return {}; // ignore malformed lines; never break the feed
      }
    }),
  markEventsHydrated: () => set({ eventsHydrated: true }),
  suppressAttention: (ids) =>
    set((state) => ({
      suppressedAttentionIds: {
        ...state.suppressedAttentionIds,
        ...Object.fromEntries(ids.map((id) => [id, true])),
      },
    })),
  releaseAttention: (ids) =>
    set((state) => {
      const next = { ...state.suppressedAttentionIds };
      for (const id of ids) delete next[id];
      return { suppressedAttentionIds: next };
    }),
  // Clear everything back to an empty workspace and bump `gen` (see the `gen` field). The dev bench calls
  // this when a scenario mounts so the next mode starts from a clean slate with no overlay bleed.
  reset: () =>
    set((state) => ({
      gen: state.gen + 1,
      generatedAt: null,
      lifecycles: {},
      enclosures: {},
      providers: {},
      activeWorktreeGroups: [],
      metrics: null,
      analytics: null,
      servingBuild: null,
      supervisorHeartbeat: null,
      events: [],
      eventsHydrated: false,
      suppressedAttentionIds: {},
    })),
}));

export const useDashboard = <T>(selector: (state: DashboardState) => T): T =>
  useStore(dashboardStore, selector);
