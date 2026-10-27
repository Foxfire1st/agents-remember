import { sameTaskDocumentRef } from '../data/taskIdentity';
import type { TaskDocumentRef,TaskEntry } from '../types/projection';

export type LauncherRole =
  | 'architect'
  | 'investigator'
  | 'system-specialist'
  | 'orchestrator'
  | 'manager'
  | 'worker'
  | 'reviewer'
  | 'curator';
export type RoleAction = 'start' | 'revive';

export const LAUNCHER_ROLES: { id: LauncherRole; label: string }[] = [
  { id: 'architect', label: 'Architect' },
  { id: 'investigator', label: 'Investigator' },
  { id: 'orchestrator', label: 'Orchestrator' },
  { id: 'manager', label: 'Manager' },
  { id: 'worker', label: 'Worker' },
  { id: 'reviewer', label: 'Reviewer' },
  { id: 'curator', label: 'Curator' },
];

export interface RoleTaskOption {
  entry: TaskEntry;
  ref: TaskDocumentRef;
}

export interface RoleEffortChoice {
  id: string;
  label: string;
}

export interface CatalogDiscoveryState {
  source: 'fresh' | 'saved' | 'unknown';
  readAt: string | null;
  error?: string;
}

export interface RoleModelChoice {
  discovery?: { efforts?: CatalogDiscoveryState; features?: CatalogDiscoveryState; serviceTiers?: CatalogDiscoveryState };
  id: string;
  label: string;
  efforts?: RoleEffortChoice[];
  defaultEffort?: string;
  isDefault?: boolean;
  serviceTiers?: RoleEffortChoice[];
  serviceTierError?: string;
  features?: RoleFeatureChoice[];
}

export interface RoleFeatureChoice {
  id: string;
  label: string;
  type: 'toggle' | 'select';
  value?: boolean | string | null;
  options?: RoleEffortChoice[];
  permissionBearing: boolean;
}

export const EMPTY_ROLE_MODELS: RoleModelChoice[] = [];
export const EMPTY_ROLE_EFFORTS: RoleEffortChoice[] = [];

export interface RoleAgentChoice {
  discovery?: { models?: CatalogDiscoveryState; modes?: CatalogDiscoveryState; acceptsToolServers?: CatalogDiscoveryState };
  modelsKnown?: boolean;
  modesKnown?: boolean;
  acceptsToolServers?: boolean;
  id: string;
  label: string;
  models: RoleModelChoice[];
  /** Set when the host could not list this agent's models; the agent stays selectable. */
  listingError?: string;
  modes?: { id: string; label: string; description?: string }[];
}

export interface RoleDefaults {
  stepUp?: { model?: string; harness?: string } | null;
  agent?: string;
  model?: string;
  effort?: string;
  serviceTier?: string;
  mode?: string;
  featureValues?: Record<string, boolean | string>;
  choices?: Record<
    string,
    {
      value?: unknown;
      source?: string;
      present?: boolean;
      baselineReplaced?: boolean;
      [key: string]: unknown;
    }
  >;
  /** False when the host's catalog does not offer this default; it is never replaced. */
  available?: boolean;
}

export interface RoleAgentOverride {
  agentId?: string;
  modelId?: string;
  effortId?: string;
  featureValues?: Record<string, boolean | string>;
}

export interface RoleScopedExecution {
  stepUp?: { model?: string; source?: string } | null;
  status: string;
  detail?: string;
  requestId?: string;
  retryPayload?: RoleLaunchSelection;
  canStart: boolean;
  canRevive: boolean;
  canRetry?: boolean;
  /** The final text of the agent's last finished turn. */
  result?: { summary?: string };
  /** Where the agent was told to write its report, and whether that file exists yet. */
  report?: { path?: string; canonicalPath?: string; available?: boolean };
  /** True on an answer the host could not confirm: the other fields are the last known ones. */
  hostUnreachable?: boolean;
  hostUnreachableReason?: string;
}

export interface RoleLaunchSelection {
  stepUp?: string;
  role: LauncherRole;
  sprintDocumentRef?: TaskDocumentRef;
  masterDocumentRef?: TaskDocumentRef;
  taskDocumentRef?: TaskDocumentRef;
  agentOverride?: RoleAgentOverride;
}

export interface RoleDocumentScope {
  role: LauncherRole;
  sprintDocumentRef?: TaskDocumentRef;
  masterDocumentRef?: TaskDocumentRef;
  taskDocumentRef?: TaskDocumentRef;
}

export interface RoleOptionsScope extends RoleDocumentScope {
  agentId?: string;
}

export interface RoleLauncherOptions {
  catalogDiscovery?: CatalogDiscoveryState;
  roleDefaults: RoleDefaults;
  agents: RoleAgentChoice[];
  execution?: RoleScopedExecution | null;
  executions?: RoleExecutionReceipt[];
  catalogOrigin?: string;
}

export type SavedRoleCatalog = Pick<RoleLauncherOptions, 'agents' | 'catalogDiscovery' | 'catalogOrigin'>;

/** The host fields of an execution: the agent the host runs for it (`kind: "paseo-agent"`). */
export interface RoleExecutionHost {
  kind?: string;
  serverId?: string;
  workspaceId?: string;
  agentId?: string;
}

export interface RoleExecutionReceipt extends RoleScopedExecution {
  execution?: RoleExecutionHost;
}

export interface RoleTasklessActiveRequest {
  requestId: string;
  pending?: boolean;
  retryPayload?: RoleLaunchSelection;
}

export function isUncertainRoleExecution(execution: RoleScopedExecution): boolean {
  return (
    execution.canRetry === true || ['starting', 'unknown'].includes(execution.status.toLowerCase())
  );
}

/** Projects follows its open request; selected tasks keep their exact saved request, including closed ones. */
export function boundTasklessRequest(
  executions: RoleExecutionReceipt[],
  current: RoleTasklessActiveRequest | undefined,
  selectedTask = false,
): RoleTasklessActiveRequest | undefined {
  if (current && (current.pending || selectedTask)) return current;
  const execution = selectedTask
    ? executions[0]
    : executions.find((row) =>
        ['accepted', 'running', 'starting', 'unknown'].includes(row.status.toLowerCase()),
      );
  if (!execution?.requestId) return current;
  return {
    requestId: execution.requestId,
    ...(isUncertainRoleExecution(execution) ? { pending: true } : {}),
    ...(execution.retryPayload ? { retryPayload: execution.retryPayload } : {}),
  };
}

export function taskOptionsForEntries(entries: TaskEntry[]): RoleTaskOption[] {
  return entries.flatMap((entry) =>
    entry.reference && entry.summary ? [{ entry, ref: entry.reference }] : [],
  );
}

export function sprintOptionsForEntries(entries: TaskEntry[]): RoleTaskOption[] {
  return taskOptionsForEntries(entries.filter((entry) => entry.summary?.form === 'sprint'));
}

/** The server supplies ordered children; selectors resolve those references only. */
function childOptions(entries: TaskEntry[], parent: TaskEntry | undefined): RoleTaskOption[] {
  if (!parent) return [];
  const byReference = new Map(entries.map((entry) => [entry.key, entry]));
  return taskOptionsForEntries(
    parent.children.flatMap((reference) => {
      const child = byReference.get(reference);
      return child ? [child] : [];
    }),
  );
}

export function masterOptionsForSprint(
  entries: TaskEntry[],
  sprint: TaskEntry | undefined,
): RoleTaskOption[] {
  return childOptions(entries, sprint).filter((option) => option.entry.summary?.form === 'master');
}

export function taskOptionsForMaster(
  entries: TaskEntry[],
  master: TaskEntry | undefined,
): RoleTaskOption[] {
  return childOptions(entries, master).filter((option) => option.entry.summary?.form === 'task');
}

export function optionIndex(options: RoleTaskOption[], ref: TaskDocumentRef | undefined): string {
  const index = ref ? options.findIndex((option) => sameTaskDocumentRef(option.ref, ref)) : -1;
  return index < 0 ? '' : String(index);
}

export function refAtOptionIndex(
  options: RoleTaskOption[],
  value: string,
): TaskDocumentRef | undefined {
  if (value === '') return undefined;
  const index = Number(value);
  return Number.isInteger(index) ? options[index]?.ref : undefined;
}

export function taskRefIdentity(ref: TaskDocumentRef | undefined): string | undefined {
  return ref ? ref.repository + '/' + ref.path : undefined;
}

export function roleNeedsSprint(role: LauncherRole): boolean {
  return role !== 'architect' && role !== 'investigator' && role !== 'system-specialist';
}

export function isTasklessRole(selection: LauncherRole | RoleDocumentScope): boolean {
  const scope = typeof selection === 'string' ? { role: selection } : selection;
  return (
    scope.role === 'architect' ||
    ((scope.role === 'investigator' || scope.role === 'system-specialist') &&
      !scope.sprintDocumentRef &&
      !scope.masterDocumentRef &&
      !scope.taskDocumentRef)
  );
}

/** Investigator receipts are per request at both Projects and selected-task scope. */
export function usesRequestReceipts(scope: RoleDocumentScope): boolean {
  return isTasklessRole(scope) || canonicalLauncherRole(scope.role) === 'investigator';
}

export function roleRequestKey(scope: RoleDocumentScope): string {
  const role = canonicalLauncherRole(scope.role);
  return isTasklessRole(scope) ? role : JSON.stringify({ ...roleDocumentScope(scope), role });
}

export function roleRequestStorageKey(scope: RoleDocumentScope): string {
  return isTasklessRole(scope)
    ? tasklessRequestStorageKey(canonicalLauncherRole(scope.role))
    : 'ar-role-request-v1:' + roleRequestKey(scope);
}

export function readSelectedRoleRequest(
  scope: RoleDocumentScope,
): RoleTasklessActiveRequest | undefined {
  const stored = sessionStorage.getItem(roleRequestStorageKey(scope));
  if (!stored) return undefined;
  try {
    const request = JSON.parse(stored) as RoleTasklessActiveRequest;
    if (typeof request.requestId !== 'string' || !request.requestId) return undefined;
    if (request.retryPayload && !sameRoleDocumentScope(request.retryPayload, scope))
      return undefined;
    return request;
  } catch {
    return undefined;
  }
}

export function tasklessRequestStorageKey(role: LauncherRole): string {
  // R98 preserves the old browser request slot: rename must not rewrite stored request identity.
  return 'ar-role-taskless-request-v1:' + (role === 'investigator' ? 'system-specialist' : role);
}

function tasklessRetrySelection(
  role: LauncherRole,
  payload: RoleLaunchSelection | undefined,
): RoleLaunchSelection | undefined {
  if (!payload || canonicalLauncherRole(payload.role) !== role) return undefined;
  const stepUp = typeof payload.stepUp === 'string' ? payload.stepUp.trim() : undefined;
  return {
    role: payload.role,
    ...(stepUp ? { stepUp } : {}),
    ...(payload.agentOverride ? { agentOverride: payload.agentOverride } : {}),
  };
}

export function readTasklessActiveRequests(): Partial<
  Record<LauncherRole, RoleTasklessActiveRequest>
> {
  const active: Partial<Record<LauncherRole, RoleTasklessActiveRequest>> = {};
  for (const role of ['architect', 'investigator'] as const) {
    const stored = sessionStorage.getItem(tasklessRequestStorageKey(role));
    if (!stored) continue;
    try {
      const value = JSON.parse(stored) as {
        requestId?: unknown;
        pending?: unknown;
        retryPayload?: unknown;
      };
      if (typeof value.requestId !== 'string' || !value.requestId) continue;
      const retryPayload = tasklessRetrySelection(
        role,
        value.retryPayload as RoleLaunchSelection | undefined,
      );
      active[role] = {
        requestId: value.requestId,
        ...(value.pending === true ? { pending: true } : {}),
        ...(retryPayload ? { retryPayload } : {}),
      };
    } catch {
      // Discard stale or malformed task-local recovery metadata.
    }
  }
  return active;
}

export function roleNeedsMaster(role: LauncherRole): boolean {
  return role === 'manager' || role === 'worker' || role === 'reviewer' || role === 'curator';
}

export function roleNeedsTask(role: LauncherRole): boolean {
  return role === 'worker' || role === 'reviewer' || role === 'curator';
}

export function roleDocumentScope(selection: RoleLaunchSelection): RoleDocumentScope {
  const investigator = canonicalLauncherRole(selection.role) === 'investigator';
  return {
    role: selection.role,
    ...((investigator || roleNeedsSprint(selection.role)) && selection.sprintDocumentRef
      ? { sprintDocumentRef: selection.sprintDocumentRef }
      : {}),
    ...((investigator || roleNeedsMaster(selection.role)) && selection.masterDocumentRef
      ? { masterDocumentRef: selection.masterDocumentRef }
      : {}),
    ...((investigator || roleNeedsTask(selection.role)) && selection.taskDocumentRef
      ? { taskDocumentRef: selection.taskDocumentRef }
      : {}),
  };
}

export function roleOptionsScope(selection: RoleLaunchSelection): RoleOptionsScope {
  return {
    ...roleDocumentScope(selection),
    ...(selection.agentOverride?.agentId ? { agentId: selection.agentOverride.agentId } : {}),
  };
}

export function launchSelectionComplete(selection: RoleLaunchSelection): boolean {
  return (
    (!roleNeedsSprint(selection.role) || Boolean(selection.sprintDocumentRef)) &&
    (!roleNeedsMaster(selection.role) || Boolean(selection.masterDocumentRef)) &&
    (!roleNeedsTask(selection.role) || Boolean(selection.taskDocumentRef))
  );
}

export function sameOptionalTaskDocumentRef(
  left: TaskDocumentRef | undefined,
  right: TaskDocumentRef | undefined,
): boolean {
  return left && right ? sameTaskDocumentRef(left, right) : left === right;
}

export function canonicalLauncherRole(role: LauncherRole): LauncherRole {
  return role === 'system-specialist' ? 'investigator' : role;
}

export function sameRoleDocumentScope(left: RoleDocumentScope, right: RoleDocumentScope): boolean {
  return (
    canonicalLauncherRole(left.role) === canonicalLauncherRole(right.role) &&
    sameOptionalTaskDocumentRef(left.sprintDocumentRef, right.sprintDocumentRef) &&
    sameOptionalTaskDocumentRef(left.masterDocumentRef, right.masterDocumentRef) &&
    sameOptionalTaskDocumentRef(left.taskDocumentRef, right.taskDocumentRef)
  );
}

export function sameRoleOptionsScope(left: RoleOptionsScope, right: RoleOptionsScope): boolean {
  return sameRoleDocumentScope(left, right) && left.agentId === right.agentId;
}

export function sameRoleLaunchSelection(
  left: RoleLaunchSelection,
  right: RoleLaunchSelection,
): boolean {
  const leftOverride = left.agentOverride ?? {};
  const rightOverride = right.agentOverride ?? {};
  return (
    sameRoleDocumentScope(roleDocumentScope(left), roleDocumentScope(right)) &&
    leftOverride.agentId === rightOverride.agentId &&
    leftOverride.modelId === rightOverride.modelId &&
    leftOverride.effortId === rightOverride.effortId &&
    JSON.stringify(Object.entries(leftOverride.featureValues ?? {}).sort()) ===
      JSON.stringify(Object.entries(rightOverride.featureValues ?? {}).sort())
  );
}

/**
 * Why Start must stay disabled for the current agent, model and effort, or null when the host's
 * catalog offers them. Without an override this is the backend's `available` flag. With one it
 * mirrors the backend's launch validation: the role's model applies only on the role's agent, its
 * effort only on the role's model, and a value the catalog does not offer is never replaced.
 */
export function launchChoiceProblem(
  defaults: RoleDefaults,
  agents: RoleAgentChoice[],
  override: RoleAgentOverride | undefined,
  catalogDiscovery?: CatalogDiscoveryState,
): string | null {
  if (catalogDiscovery?.source === 'unknown') return null;
  if (!override) return unofferedRoleDefault(defaults);
  const agentId = override.agentId ?? defaults.agent;
  const agent = agents.find((choice) => matchesChoice(choice, agentId));
  if (!agent) return unofferedAgent(agentId);
  const onRoleAgent = matchesChoice(agent, defaults.agent);
  const modelId = override.modelId || roleValue(onRoleAgent, defaults.model);
  const onRoleModel = onRoleAgent && sameConfiguredModel(agent, modelId, defaults.model);
  return (
    unofferedModelOrEffort(
      agent,
      modelId,
      override.effortId || roleValue(onRoleModel, defaults.effort),
    ) ||
    unofferedServiceTier(
      agent,
      modelId,
      selectedServiceTier(override, roleValue(onRoleAgent, defaults.serviceTier)),
    )
  );
}

function sameConfiguredModel(agent: RoleAgentChoice, selected: string | undefined, configured: string | undefined): boolean {
  const model = agent.models.find((row) => matchesChoice(row, configured));
  return selected === configured || Boolean(model && matchesChoice(model, selected));
}

function selectedServiceTier(override: RoleAgentOverride, configured: string | undefined): string | undefined {
  const selected = override.featureValues?.service_tier;
  return typeof selected === 'string' ? selected : configured;
}

function unknownModels(agent: RoleAgentChoice): boolean {
  return agent.discovery?.models?.source === 'unknown' || agent.modelsKnown === false;
}

export function matchesChoice(choice: { id: string; label: string }, value: string | undefined): boolean {
  const name = value?.toLowerCase();
  return name !== undefined && (choice.id.toLowerCase() === name || choice.label.toLowerCase() === name);
}

function unofferedAgent(agentId: string | undefined): string {
  return 'Agent ' + (agentId ?? 'unset') + ' is not offered by the Paseo runtime.';
}

function unofferedServiceTier(
  agent: RoleAgentChoice,
  modelId: string | undefined,
  tier: string | undefined,
): string | null {
  if (!tier) return null;
  const model = modelId
    ? agent.models.find((row) => matchesChoice(row, modelId))
    : agent.models.find((row) => row.isDefault);
  if (unknownModels(agent) || unknownServiceTiers(model)) return null;
  return model?.serviceTiers?.some((row) => matchesChoice(row, tier))
    ? null
    : serviceTierProblem(agent.label, modelId, tier, model?.serviceTierError);
}

function serviceTierProblem(agent: string, model: string | undefined, tier: string, error: string | undefined): string {
  return 'Service tier ' + tier + ' is not offered for ' + agent + '/' +
    (model ?? 'native default') + (error ? ': ' + error : '.');
}

/** A role default that applies; the backend sends an unset default as null, read here as unset. */
function roleValue(applies: boolean, value: string | undefined): string | undefined {
  return applies && value ? value : undefined;
}

function unofferedRoleDefault(defaults: RoleDefaults): string | null {
  if (defaults.available !== false) return null;
  const configured = [defaults.agent, defaults.model, defaults.effort, defaults.serviceTier]
    .filter(Boolean)
    .join(' · ');
  return configured
    ? 'Role default ' +
        configured +
        ' is not offered by the Paseo runtime; pick an agent, model or effort it offers.'
    : 'No agent is configured for this role; pick an agent the Paseo runtime offers.';
}

function unofferedModelOrEffort(
  agent: RoleAgentChoice,
  modelId: string | undefined,
  effortId: string | undefined,
): string | null {
  if (unknownModels(agent)) return null;
  if (!modelId) return effortId ? 'Effort ' + effortId + ' needs a model; pick a model.' : null;
  const model = agent.models.find((choice) => matchesChoice(choice, modelId));
  if (!model)
    return 'Model ' + modelId + ' is not offered for ' + agent.label + '; pick a model it offers.';
  return unofferedEffort(model, effortId);
}

function unknownServiceTiers(model: RoleModelChoice | undefined): boolean {
  return !model || model.discovery?.serviceTiers?.source === 'unknown';
}

function unofferedEffort(model: RoleModelChoice, effortId: string | undefined): string | null {
  if (!effortId || model.discovery?.efforts?.source === 'unknown') return null;
  if ((model.efforts ?? []).some((choice) => matchesChoice(choice, effortId))) return null;
  return 'Effort ' + effortId + ' is not offered for ' + model.label + '; pick an effort it offers.';
}

export function roleAgentOverrideFor(
  agentId: string | undefined,
  modelId: string | undefined,
  effortId: string | undefined,
  defaults: RoleDefaults,
  featureValues?: RoleAgentOverride['featureValues'],
): RoleAgentOverride | undefined {
  if (!agentId) return undefined;
  const speedValues = Object.keys(featureValues ?? {}).length > 0;
  if (agentId === defaults.agent && !modelId && !effortId && !speedValues) {
    return undefined;
  }
  return {
    agentId,
    ...(modelId ? { modelId } : {}),
    ...(effortId ? { effortId } : {}),
    ...(speedValues ? { featureValues } : {}),
  };
}
