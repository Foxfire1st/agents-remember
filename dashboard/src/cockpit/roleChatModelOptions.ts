import { useEffect, useMemo, useSyncExternalStore } from 'react';

import { resolveAgentChoice } from './role-launcher/roleLauncherDisplay';
import {
  launchChoiceProblem,
  type RoleAgentChoice,
  type RoleDocumentScope,
  type RoleLaunchSelection,
  type RoleModelChoice,
  type SavedRoleCatalog,
  type RoleDefaults,
} from './roleLaunchModel';

interface ModelSelection {
  agentId: string;
  modelId: string;
}

interface ModelOptions extends ModelSelection {
  features?: RoleModelChoice['features'];
  serviceTiers?: RoleModelChoice['serviceTiers'];
  discovery?: Pick<NonNullable<RoleModelChoice['discovery']>, 'features' | 'serviceTiers'>;
}

type ModelOptionsRequest = RoleDocumentScope & ModelSelection;
const launchInProgress = Symbol('launch in progress');

async function readOptions(
  body: ModelOptionsRequest,
  signal: AbortSignal,
): Promise<ModelOptions | typeof launchInProgress | null> {
  try {
    const response = await fetch('/api/role-launch/options', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    const value = await response.json() as { modelOptions?: ModelOptions; launchInProgress?: boolean };
    if (response.status === 409 && value.launchInProgress === true) return launchInProgress;
    const answer = value.modelOptions;
    return response.ok && answer?.agentId === body.agentId && answer.modelId === body.modelId
      ? answer : null;
  } catch {
    return null;
  }
}

function createCache(generation: number) {
  // Each shown provider/model gets one entry per pane and refresh generation.
  // Requests for hidden choices can finish so a return to that choice reuses the answer.
  const answers = new Map<string, ModelOptions | null | undefined>();
  const listeners = new Set<() => void>();
  let controller = new AbortController();
  return {
    generation,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    answer(key: string) {
      return answers.get(key);
    },
    async request(key: string, body: ModelOptionsRequest, waitForLaunch: () => void) {
      if (answers.has(key)) return;
      answers.set(key, undefined);
      const { signal } = controller;
      const answer = await readOptions(body, signal);
      if (signal.aborted) return;
      if (answer === launchInProgress) {
        answers.delete(key);
        waitForLaunch();
        return;
      }
      answers.set(key, answer);
      for (const listener of listeners) listener();
    },
    clear() {
      controller.abort();
      answers.clear();
      controller = new AbortController();
    },
  };
}

function withModelOptions(
  agents: RoleAgentChoice[],
  selection: ModelSelection,
  answer: ModelOptions | null | undefined,
): RoleAgentChoice[] {
  return agents.map((agent) => agent.id === selection.agentId
    ? { ...agent, models: agent.models.map((model) => model.id === selection.modelId
      ? {
          ...model,
          features: answer?.features,
          serviceTiers: answer?.serviceTiers,
          discovery: {
            ...model.discovery,
            features: answer?.discovery?.features ?? { source: 'unknown', readAt: null },
            serviceTiers: answer?.discovery?.serviceTiers ?? { source: 'unknown', readAt: null },
          },
        }
      : model) }
    : agent);
}

interface ShownModelOptions {
  catalog: SavedRoleCatalog & { roleDefaults: RoleDefaults };
  selection: RoleLaunchSelection;
  scope: RoleDocumentScope;
  metadataGeneration?: number;
  active: boolean;
  catalogRefresh: number;
  optionsRefresh: number;
  waitForLaunch: () => void;
}

export function useShownModelOptions({
  catalog,
  selection,
  scope,
  metadataGeneration,
  active,
  catalogRefresh,
  optionsRefresh,
  waitForLaunch,
}: ShownModelOptions) {
  const { agentId, modelId } = shownModel(catalog, selection);
  const key = JSON.stringify([agentId, modelId]);
  const cache = useMemo(() => createCache(catalogRefresh), [catalogRefresh]);
  const settled = metadataGeneration === cache.generation;
  const answer = useSyncExternalStore(cache.subscribe, () => cache.answer(key));
  useEffect(() => () => cache.clear(), [cache]);
  useEffect(() => {
    if (!active || !settled || !agentId || !modelId) return;
    void cache.request(key, { ...scope, agentId, modelId }, waitForLaunch);
    // An options reread can retry a request the launch lock deferred.
  }, [active, settled, agentId, modelId, key, cache, scope, optionsRefresh, waitForLaunch]);

  // Once Refresh clears the choices, saved metadata can show the model but
  // only this generation's answer can supply its options.
  const requireCurrentOptions = settled || catalogRefresh > 0;
  return presentModelOptions(catalog, {
    target: { agentId, modelId }, requireCurrentOptions, answer, selection,
  });
}

function shownModel(catalog: ShownModelOptions['catalog'], selection: RoleLaunchSelection): Partial<ModelSelection> {
  const { selectedAgent, selectedModel } = resolveAgentChoice(catalog.agents, catalog.roleDefaults, selection);
  return { agentId: selectedAgent?.id, modelId: selectedModel?.id };
}

function presentModelOptions(
  catalog: ShownModelOptions['catalog'],
  { target: { agentId, modelId }, requireCurrentOptions, answer, selection }: {
    target: Partial<ModelSelection>;
    requireCurrentOptions: boolean;
    answer: ModelOptions | null | undefined;
    selection: RoleLaunchSelection;
  },
) {
  const fresh = answer?.discovery?.features?.source === 'fresh';
  const { agents, roleDefaults } = catalog;
  if (!agentId || !modelId || (!requireCurrentOptions && !fresh)) {
    return { agents, roleDefaults, pending: false, fresh };
  }
  const offered = withModelOptions(agents, { agentId, modelId }, fresh ? answer : null);
  return {
    agents: offered,
    roleDefaults: resolveRoleDefaults(catalog, offered, selection, fresh),
    pending: answer === undefined,
    fresh,
  };
}

function resolveRoleDefaults(
  { roleDefaults, catalogDiscovery }: ShownModelOptions['catalog'],
  agents: RoleAgentChoice[],
  selection: RoleLaunchSelection,
  fresh: boolean,
): RoleDefaults {
  if (!fresh || selection.agentOverride || roleDefaults.available === false) return roleDefaults;
  const problem = launchChoiceProblem(roleDefaults, agents, {}, catalogDiscovery);
  return problem ? { ...roleDefaults, available: false } : roleDefaults;
}

export function withoutModelFeatureValues(selection: RoleLaunchSelection): RoleLaunchSelection {
  if (!selection.agentOverride?.featureValues) return selection;
  const agentOverride = { ...selection.agentOverride };
  delete agentOverride.featureValues;
  const next = { ...selection };
  if (Object.keys(agentOverride).length) next.agentOverride = agentOverride;
  else delete next.agentOverride;
  return next;
}
