import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import type { ObserverEvent } from "../types/event";
import type {
  Analytics,
  EnclosureNode,
  LifecycleProjection,
  Metrics,
  ProviderNode,
  WorkspaceProjection,
} from "../types/projection";

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
  metrics: Metrics | null;
  analytics: Analytics | null;
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

const byKey = <T>(items: T[], key: (item: T) => string): Record<string, T> =>
  Object.fromEntries(items.map((item) => [key(item), item]));

const upsert = <T>(
  collection: Record<string, T>,
  item: T,
  key: (item: T) => string,
): Record<string, T> => ({ ...collection, [key(item)]: item });

const remove = <T>(collection: Record<string, T>, id: string): Record<string, T> => {
  if (!(id in collection)) return collection;
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
// consecutive projections (serving/delta.py) and emits upserts / `*.removed` markers;
// `metrics` / `analytics` arrive as whole-object replacements.
function reduceDelta(
  state: DashboardState,
  event: string,
  data: unknown,
): Partial<DashboardState> {
  switch (event) {
    case "lifecycle":
      return { lifecycles: upsert(state.lifecycles, data as LifecycleProjection, (x) => x.id) };
    case "lifecycle.removed":
      return { lifecycles: remove(state.lifecycles, (data as { id: string }).id) };
    case "enclosure":
      return { enclosures: upsert(state.enclosures, data as EnclosureNode, (x) => x.enclosure) };
    case "enclosure.removed":
      return { enclosures: remove(state.enclosures, (data as { enclosure: string }).enclosure) };
    case "provider":
      return { providers: upsert(state.providers, data as ProviderNode, (x) => x.id) };
    case "provider.removed":
      return { providers: remove(state.providers, (data as { id: string }).id) };
    case "metrics":
      return { metrics: data as Metrics };
    case "analytics": {
      const analytics = data as Analytics;
      return {
        analytics,
        suppressedAttentionIds: pruneSuppressedAttention(state.suppressedAttentionIds, analytics),
      };
    }
    default:
      return {};
  }
}

export const dashboardStore = createStore<DashboardState>((set) => ({
  conn: "connecting",
  generatedAt: null,
  gen: 0,
  lifecycles: {},
  enclosures: {},
  providers: {},
  metrics: null,
  analytics: null,
  events: [],
  eventsHydrated: false,
  suppressedAttentionIds: {},
  setConn: (conn) => set({ conn }),
  applySnapshot: (projection) =>
    set((state) => ({
      conn: "live",
      generatedAt: projection.generatedAt,
      lifecycles: byKey(projection.lifecycles, (x) => x.id),
      enclosures: byKey(projection.enclosures, (x) => x.enclosure),
      providers: byKey(projection.providers, (x) => x.id),
      metrics: projection.metrics,
      analytics: projection.analytics,
      suppressedAttentionIds: pruneSuppressedAttention(
        state.suppressedAttentionIds,
        projection.analytics,
      ),
    })),
  applyDelta: (event, data) => set((state) => reduceDelta(state, event, data)),
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
      metrics: null,
      analytics: null,
      events: [],
      eventsHydrated: false,
      suppressedAttentionIds: {},
    })),
}));

export const useDashboard = <T>(selector: (state: DashboardState) => T): T =>
  useStore(dashboardStore, selector);
