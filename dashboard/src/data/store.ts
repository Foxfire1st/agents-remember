import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

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
  lifecycles: Record<string, LifecycleProjection>; // keyed by id
  enclosures: Record<string, EnclosureNode>; // keyed by `enclosure`
  providers: Record<string, ProviderNode>; // keyed by id
  metrics: Metrics | null;
  analytics: Analytics | null;
  setConn: (conn: ConnState) => void;
  applySnapshot: (projection: WorkspaceProjection) => void;
  applyDelta: (event: string, data: unknown) => void;
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
  lifecycles: {},
  enclosures: {},
  providers: {},
  metrics: null,
  analytics: null,
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
}));

export const useDashboard = <T>(selector: (state: DashboardState) => T): T =>
  useStore(dashboardStore, selector);
