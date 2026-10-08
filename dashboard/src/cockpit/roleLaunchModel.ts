import {
  isOrchestrationDoc,
  masterCommandNames,
  orchestratorParentKey,
  taskDocParentKey,
} from "../data/taskHierarchy";
import { sameTaskDocumentRef, taskDocSelectionKey, taskDocumentRefForDoc } from "../data/taskIdentity";
import type { SeriesNode, TaskDocNode } from "../types/projection";
import type { TaskDocumentRef } from "../types/terminalCatalog";

export type LauncherRole = "architect" | "investigator" | "system-specialist" | "orchestrator" | "manager" | "worker" | "reviewer" | "curator";
export type RoleAction = "start" | "revive";

export const LAUNCHER_ROLES: { id: LauncherRole; label: string }[] = [
  { id: "architect", label: "Architect" },
  { id: "investigator", label: "Investigator" },
  { id: "orchestrator", label: "Orchestrator" },
  { id: "manager", label: "Manager" },
  { id: "worker", label: "Worker" },
  { id: "reviewer", label: "Reviewer" },
  { id: "curator", label: "Curator" },
];

export interface RoleTaskOption {
  doc: TaskDocNode;
  ref: TaskDocumentRef;
}

export interface RoleEffortChoice {
  id: string;
  label: string;
}

export interface RoleModelChoice {
  id: string;
  label: string;
  efforts?: RoleEffortChoice[];
  defaultEffort?: string;
  isDefault?: boolean;
  serviceTiers?: RoleEffortChoice[];
  serviceTierError?: string;
}

export const EMPTY_ROLE_MODELS: RoleModelChoice[] = [];
export const EMPTY_ROLE_EFFORTS: RoleEffortChoice[] = [];

export interface RoleAgentChoice {
  id: string;
  label: string;
  models: RoleModelChoice[];
  /** Set when the host could not list this agent's models; the agent stays selectable. */
  listingError?: string;
}

export interface RoleDefaults {
  agent?: string;
  model?: string;
  effort?: string;
  serviceTier?: string;
  /** False when the host's catalog does not offer this default; it is never replaced. */
  available?: boolean;
}

export interface RoleAgentOverride {
  agentId: string;
  modelId?: string;
  effortId?: string;
}

export interface RoleScopedExecution {
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
  roleDefaults: RoleDefaults;
  agents: RoleAgentChoice[];
  execution?: RoleScopedExecution | null;
  executions?: RoleExecutionReceipt[];
  catalogOrigin?: string;
}

export interface RoleAgentInventory {
  catalogOrigin: string;
  agents: RoleAgentChoice[];
}

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
  return execution.canRetry === true || ["starting", "unknown"].includes(execution.status.toLowerCase());
}

/** Projects follows its open request; selected tasks keep their exact saved request, including closed ones. */
export function boundTasklessRequest(
  executions: RoleExecutionReceipt[], current: RoleTasklessActiveRequest | undefined, selectedTask = false,
): RoleTasklessActiveRequest | undefined {
  if (current && (current.pending || selectedTask)) return current;
  const execution = selectedTask ? executions[0]
    : executions.find((row) => ["accepted", "running", "starting", "unknown"].includes(row.status.toLowerCase()));
  if (!execution?.requestId) return current;
  return { requestId: execution.requestId,
    ...(isUncertainRoleExecution(execution) ? { pending: true } : {}),
    ...(execution.retryPayload ? { retryPayload: execution.retryPayload } : {}),
  };
}

export function taskOptionsForDocs(docs: TaskDocNode[]): RoleTaskOption[] {
  return docs.flatMap((doc) => {
    const ref = taskDocumentRefForDoc(doc);
    return ref ? [{ doc, ref }] : [];
  });
}

export function sprintOptionsForDocs(taskDocuments: TaskDocNode[]): RoleTaskOption[] {
  return taskOptionsForDocs(taskDocuments.filter(isOrchestrationDoc));
}

export function masterOptionsForSprint(
  taskDocuments: TaskDocNode[],
  sprint: TaskDocNode | undefined,
): RoleTaskOption[] {
  if (!sprint) return [];
  const sprintSelection = taskDocSelectionKey(sprint.docPath);
  const repositoryDocs = taskDocuments.filter((doc) => doc.repository === sprint.repository);
  return taskOptionsForDocs(repositoryDocs.filter((doc) =>
    doc.kind === "master" &&
    !isOrchestrationDoc(doc) &&
    orchestratorParentKey(masterCommandNames(doc), repositoryDocs, doc.docPath) === sprintSelection,
  ));
}

export function taskOptionsForMaster(
  taskDocuments: TaskDocNode[],
  series: SeriesNode[],
  master: TaskDocNode | undefined,
): RoleTaskOption[] {
  if (!master) return [];
  const masterDocPaths = new Set(taskDocuments.filter((doc) => doc.kind === "master").map((doc) => doc.docPath));
  const parentSelection = taskDocSelectionKey(master.docPath);
  return taskOptionsForDocs(taskDocuments.filter((doc) =>
    doc.kind !== "master" &&
    taskDocParentKey(doc, series, masterDocPaths) === parentSelection,
  ));
}

export function optionIndex(options: RoleTaskOption[], ref: TaskDocumentRef | undefined): string {
  const index = ref ? options.findIndex((option) => sameTaskDocumentRef(option.ref, ref)) : -1;
  return index < 0 ? "" : String(index);
}

export function refAtOptionIndex(options: RoleTaskOption[], value: string): TaskDocumentRef | undefined {
  if (value === "") return undefined;
  const index = Number(value);
  return Number.isInteger(index) ? options[index]?.ref : undefined;
}

export function taskRefIdentity(ref: TaskDocumentRef | undefined): string | undefined {
  return ref ? ref.repository + "/" + ref.path : undefined;
}

export function roleNeedsSprint(role: LauncherRole): boolean {
  return role !== "architect" && role !== "investigator" && role !== "system-specialist";
}

export function isTasklessRole(selection: LauncherRole | RoleDocumentScope): boolean {
  const scope = typeof selection === "string" ? { role: selection } : selection;
  return scope.role === "architect" ||
    ((scope.role === "investigator" || scope.role === "system-specialist") &&
      !scope.sprintDocumentRef && !scope.masterDocumentRef && !scope.taskDocumentRef);
}

/** Investigator receipts are per request at both Projects and selected-task scope. */
export function usesRequestReceipts(scope: RoleDocumentScope): boolean {
  return isTasklessRole(scope) || canonicalLauncherRole(scope.role) === "investigator";
}

export function roleRequestKey(scope: RoleDocumentScope): string {
  const role = canonicalLauncherRole(scope.role);
  return isTasklessRole(scope) ? role : JSON.stringify({ ...roleDocumentScope(scope), role });
}

export function roleRequestStorageKey(scope: RoleDocumentScope): string {
  return isTasklessRole(scope) ? tasklessRequestStorageKey(canonicalLauncherRole(scope.role))
    : "ar-role-request-v1:" + roleRequestKey(scope);
}

export function readSelectedRoleRequest(scope: RoleDocumentScope): RoleTasklessActiveRequest | undefined {
  const stored = sessionStorage.getItem(roleRequestStorageKey(scope));
  if (!stored) return undefined;
  try {
    const request = JSON.parse(stored) as RoleTasklessActiveRequest;
    if (typeof request.requestId !== "string" || !request.requestId) return undefined;
    if (request.retryPayload && !sameRoleDocumentScope(request.retryPayload, scope)) return undefined;
    return request;
  } catch {
    return undefined;
  }
}

export function tasklessRequestStorageKey(role: LauncherRole): string {
  // R98 preserves the old browser request slot: rename must not rewrite stored request identity.
  return "ar-role-taskless-request-v1:" + (role === "investigator" ? "system-specialist" : role);
}

export function readTasklessActiveRequests(): Partial<Record<LauncherRole, RoleTasklessActiveRequest>> {
  const active: Partial<Record<LauncherRole, RoleTasklessActiveRequest>> = {};
  for (const role of ["architect", "investigator"] as const) {
    const stored = sessionStorage.getItem(tasklessRequestStorageKey(role));
    if (!stored) continue;
    try {
      const value = JSON.parse(stored) as {
        requestId?: unknown;
        pending?: unknown;
        retryPayload?: unknown;
      };
      if (typeof value.requestId !== "string" || !value.requestId) continue;
      const payload = value.retryPayload as RoleLaunchSelection | undefined;
      active[role] = {
        requestId: value.requestId,
        ...(value.pending === true ? { pending: true } : {}),
        ...(payload && canonicalLauncherRole(payload.role) === role
          ? {
              retryPayload: {
                role: payload.role,
                ...(payload.agentOverride ? { agentOverride: payload.agentOverride } : {}),
              },
            }
          : {}),
      };
    } catch {
      // Discard stale or malformed task-local recovery metadata.
    }
  }
  return active;
}

export function roleNeedsMaster(role: LauncherRole): boolean {
  return role === "manager" || role === "worker" || role === "reviewer" || role === "curator";
}

export function roleNeedsTask(role: LauncherRole): boolean {
  return role === "worker" || role === "reviewer" || role === "curator";
}

export function roleDocumentScope(selection: RoleLaunchSelection): RoleDocumentScope {
  const investigator = canonicalLauncherRole(selection.role) === "investigator";
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
  return (!roleNeedsSprint(selection.role) || Boolean(selection.sprintDocumentRef)) &&
    (!roleNeedsMaster(selection.role) || Boolean(selection.masterDocumentRef)) &&
    (!roleNeedsTask(selection.role) || Boolean(selection.taskDocumentRef));
}

export function roleDefaultsCacheKey(scope: RoleDocumentScope): string {
  const effectiveRef = scope.taskDocumentRef ?? scope.masterDocumentRef ?? scope.sprintDocumentRef;
  return scope.role + "::" + (effectiveRef?.repository ?? "projects");
}

export function mergeRoleAgentInventory(
  previous: RoleAgentInventory | null,
  options: RoleLauncherOptions,
  selectedAgentId: string | undefined,
  refresh: boolean,
): RoleAgentInventory {
  const catalogOrigin = options.catalogOrigin ?? previous?.catalogOrigin ?? "active-runtime";
  if (!previous || previous.catalogOrigin !== catalogOrigin || refresh) {
    return { catalogOrigin, agents: options.agents };
  }
  const previousById = new Map(previous.agents.map((agent) => [agent.id, agent]));
  return {
    catalogOrigin,
    agents: options.agents.map((agent) => {
      const cached = previousById.get(agent.id);
      const models = agent.id === selectedAgentId || agent.models.length > 0
        ? agent.models
        : cached?.models ?? [];
      return { ...agent, models };
    }),
  };
}

export function sameOptionalTaskDocumentRef(
  left: TaskDocumentRef | undefined,
  right: TaskDocumentRef | undefined,
): boolean {
  return left && right ? sameTaskDocumentRef(left, right) : left === right;
}

export function canonicalLauncherRole(role: LauncherRole): LauncherRole {
  return role === "system-specialist" ? "investigator" : role;
}

export function sameRoleDocumentScope(left: RoleDocumentScope, right: RoleDocumentScope): boolean {
  return canonicalLauncherRole(left.role) === canonicalLauncherRole(right.role) &&
    sameOptionalTaskDocumentRef(left.sprintDocumentRef, right.sprintDocumentRef) &&
    sameOptionalTaskDocumentRef(left.masterDocumentRef, right.masterDocumentRef) &&
    sameOptionalTaskDocumentRef(left.taskDocumentRef, right.taskDocumentRef);
}

export function sameRoleOptionsScope(left: RoleOptionsScope, right: RoleOptionsScope): boolean {
  return sameRoleDocumentScope(left, right) && left.agentId === right.agentId;
}

export function sameRoleLaunchSelection(left: RoleLaunchSelection, right: RoleLaunchSelection): boolean {
  return sameRoleDocumentScope(roleDocumentScope(left), roleDocumentScope(right)) &&
    left.agentOverride?.agentId === right.agentOverride?.agentId &&
    left.agentOverride?.modelId === right.agentOverride?.modelId &&
    left.agentOverride?.effortId === right.agentOverride?.effortId;
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
): string | null {
  if (!override) return unofferedRoleDefault(defaults);
  const agent = agents.find((choice) => choice.id === override.agentId);
  if (!agent) return "Agent " + override.agentId + " is not offered by the Paseo runtime.";
  const modelId = override.modelId || roleValue(override.agentId === defaults.agent, defaults.model);
  const onRoleModel = override.agentId === defaults.agent && modelId === roleValue(true, defaults.model);
  return unofferedModelOrEffort(agent, modelId, override.effortId || roleValue(onRoleModel, defaults.effort)) ||
    unofferedServiceTier(agent, modelId, roleValue(override.agentId === defaults.agent, defaults.serviceTier));
}

function unofferedServiceTier(agent: RoleAgentChoice, modelId: string | undefined, tier: string | undefined): string | null {
  if (!tier) return null;
  const model = modelId ? agent.models.find((row) => row.id === modelId) : agent.models.find((row) => row.isDefault);
  const native = tier === "fast" ? "priority" : tier;
  return model?.serviceTiers?.some((row) => row.id === native) ? null :
    "Service tier " + tier + " is not offered for " + agent.label + "/" + (modelId ?? "native default") +
    (model?.serviceTierError ? ": " + model.serviceTierError : ".");
}

/** A role default that applies; the backend sends an unset default as null, read here as unset. */
function roleValue(applies: boolean, value: string | undefined): string | undefined {
  return applies && value ? value : undefined;
}

function unofferedRoleDefault(defaults: RoleDefaults): string | null {
  if (defaults.available !== false) return null;
  const configured = [defaults.agent, defaults.model, defaults.effort, defaults.serviceTier].filter(Boolean).join(" · ");
  return configured
    ? "Role default " + configured + " is not offered by the Paseo runtime; pick an agent, model or effort it offers."
    : "No agent is configured for this role; pick an agent the Paseo runtime offers.";
}

function unofferedModelOrEffort(
  agent: RoleAgentChoice,
  modelId: string | undefined,
  effortId: string | undefined,
): string | null {
  if (!modelId) return effortId ? "Effort " + effortId + " needs a model; pick a model." : null;
  const model = agent.models.find((choice) => choice.id === modelId);
  if (!model) return "Model " + modelId + " is not offered for " + agent.label + "; pick a model it offers.";
  if (effortId && !(model.efforts ?? []).some((choice) => choice.id === effortId)) {
    return "Effort " + effortId + " is not offered for " + model.label + "; pick an effort it offers.";
  }
  return null;
}

export function roleAgentOverrideFor(
  agentId: string | undefined,
  modelId: string | undefined,
  effortId: string | undefined,
  defaults: RoleDefaults,
): RoleAgentOverride | undefined {
  if (!agentId) return undefined;
  if (agentId === defaults.agent && (!modelId || modelId === defaults.model) && (!effortId || effortId === defaults.effort)) {
    return undefined;
  }
  return {
    agentId,
    ...(modelId ? { modelId } : {}),
    ...(effortId ? { effortId } : {}),
  };
}
