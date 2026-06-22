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
  events: ObserverEvent[]; // bounded tail of the raw observer feed (Event River)
  setConn: (conn: ConnState) => void;
  applySnapshot: (projection: WorkspaceProjection) => void;
  applyDelta: (event: string, data: unknown) => void;
  pushEvent: (line: string) => void;
  reset: () => void;
}

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

// The Event River keeps a bounded tail of the raw observer feed (newest last).
const EVENT_CAP = 200;

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
    case "analytics":
      return { analytics: data as Analytics };
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
  setConn: (conn) => set({ conn }),
  applySnapshot: (projection) =>
    set({
      conn: "live",
      generatedAt: projection.generatedAt,
      lifecycles: byKey(projection.lifecycles, (x) => x.id),
      enclosures: byKey(projection.enclosures, (x) => x.enclosure),
      providers: byKey(projection.providers, (x) => x.id),
      metrics: projection.metrics,
      analytics: projection.analytics,
    }),
  applyDelta: (event, data) => set((state) => reduceDelta(state, event, data)),
  pushEvent: (line) =>
    set((state) => {
      try {
        const event = JSON.parse(line) as ObserverEvent;
        return { events: [...state.events.slice(-(EVENT_CAP - 1)), event] };
      } catch {
        return {}; // ignore malformed lines; never break the feed
      }
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
    })),
}));

export const useDashboard = <T>(selector: (state: DashboardState) => T): T =>
  useStore(dashboardStore, selector);
