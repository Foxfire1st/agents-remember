import {
  isOrchestrationDoc,
  masterCommandNames,
  orchestratorParentKey,
  taskDocParentKey,
} from "../data/taskHierarchy";
import { sameTaskDocumentRef, taskDocSelectionKey, taskDocumentRefForDoc } from "../data/taskIdentity";
import type { SeriesNode, TaskDocNode } from "../types/projection";
import type { TaskDocumentRef } from "../types/terminalCatalog";

export type OrcaRole = "architect" | "system-specialist" | "orchestrator" | "manager" | "worker" | "reviewer" | "curator";
export type OrcaAction = "start" | "revive";

export const ORCA_ROLES: { id: OrcaRole; label: string }[] = [
  { id: "architect", label: "Architect" },
  { id: "system-specialist", label: "System specialist" },
  { id: "orchestrator", label: "Orchestrator" },
  { id: "manager", label: "Manager" },
  { id: "worker", label: "Worker" },
  { id: "reviewer", label: "Reviewer" },
  { id: "curator", label: "Curator" },
];

export interface OrcaTaskOption {
  doc: TaskDocNode;
  ref: TaskDocumentRef;
}

export interface OrcaEffortChoice {
  id: string;
  label: string;
}

export interface OrcaModelChoice {
  id: string;
  label: string;
  efforts?: OrcaEffortChoice[];
  defaultEffort?: string;
}

export const EMPTY_ORCA_MODELS: OrcaModelChoice[] = [];
export const EMPTY_ORCA_EFFORTS: OrcaEffortChoice[] = [];

export interface OrcaAgentChoice {
  id: string;
  label: string;
  models: OrcaModelChoice[];
}

export interface OrcaRoleDefaults {
  agent?: string;
  model?: string;
  effort?: string;
}

export interface OrcaAgentOverride {
  agentId: string;
  modelId?: string;
  effortId?: string;
}

export interface OrcaScopedExecution {
  status: string;
  detail?: string;
  requestId?: string;
  retryPayload?: OrcaLaunchSelection;
  canStart: boolean;
  canRevive: boolean;
  canRetry?: boolean;
}

export interface OrcaLaunchSelection {
  role: OrcaRole;
  sprintDocumentRef?: TaskDocumentRef;
  masterDocumentRef?: TaskDocumentRef;
  taskDocumentRef?: TaskDocumentRef;
  agentOverride?: OrcaAgentOverride;
}

export interface OrcaDocumentScope {
  role: OrcaRole;
  sprintDocumentRef?: TaskDocumentRef;
  masterDocumentRef?: TaskDocumentRef;
  taskDocumentRef?: TaskDocumentRef;
}

export interface OrcaOptionsScope extends OrcaDocumentScope {
  agentId?: string;
}

export interface OrcaLauncherOptions {
  roleDefaults: OrcaRoleDefaults;
  agents: OrcaAgentChoice[];
  execution?: OrcaScopedExecution | null;
  executions?: OrcaExecutionReceipt[];
  catalogOrigin?: string;
}

export interface OrcaAgentInventory {
  catalogOrigin: string;
  agents: OrcaAgentChoice[];
}

export interface OrcaExecutionReceipt extends OrcaScopedExecution {
  result?: { summary?: string; status?: string; outcome?: string; filesModified?: string[] };
  execution?: { kind?: string; handle?: string; sessionId?: string; worktreeId?: string };
}

export interface OrcaTasklessActiveRequest {
  requestId: string;
  pending?: boolean;
  retryPayload?: OrcaLaunchSelection;
}

export function isUncertainOrcaExecution(execution: OrcaScopedExecution): boolean {
  return execution.canRetry === true || ["starting", "unknown"].includes(execution.status.toLowerCase());
}

export function taskOptionsForDocs(docs: TaskDocNode[]): OrcaTaskOption[] {
  return docs.flatMap((doc) => {
    const ref = taskDocumentRefForDoc(doc);
    return ref ? [{ doc, ref }] : [];
  });
}

export function sprintOptionsForDocs(taskDocuments: TaskDocNode[]): OrcaTaskOption[] {
  return taskOptionsForDocs(taskDocuments.filter(isOrchestrationDoc));
}

export function masterOptionsForSprint(
  taskDocuments: TaskDocNode[],
  sprint: TaskDocNode | undefined,
): OrcaTaskOption[] {
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
): OrcaTaskOption[] {
  if (!master) return [];
  const masterDocPaths = new Set(taskDocuments.filter((doc) => doc.kind === "master").map((doc) => doc.docPath));
  const parentSelection = taskDocSelectionKey(master.docPath);
  return taskOptionsForDocs(taskDocuments.filter((doc) =>
    doc.kind !== "master" &&
    taskDocParentKey(doc, series, masterDocPaths) === parentSelection,
  ));
}

export function optionIndex(options: OrcaTaskOption[], ref: TaskDocumentRef | undefined): string {
  const index = ref ? options.findIndex((option) => sameTaskDocumentRef(option.ref, ref)) : -1;
  return index < 0 ? "" : String(index);
}

export function refAtOptionIndex(options: OrcaTaskOption[], value: string): TaskDocumentRef | undefined {
  if (value === "") return undefined;
  const index = Number(value);
  return Number.isInteger(index) ? options[index]?.ref : undefined;
}

export function taskRefIdentity(ref: TaskDocumentRef | undefined): string | undefined {
  return ref ? ref.repository + "/" + ref.path : undefined;
}

export function roleNeedsSprint(role: OrcaRole): boolean {
  return role !== "architect" && role !== "system-specialist";
}

export function isTasklessOrcaRole(role: OrcaRole): boolean {
  return role === "architect" || role === "system-specialist";
}

export function tasklessRequestStorageKey(role: OrcaRole): string {
  return "ar-orca-taskless-request-v1:" + role;
}

export function readTasklessActiveRequests(): Partial<Record<OrcaRole, OrcaTasklessActiveRequest>> {
  const active: Partial<Record<OrcaRole, OrcaTasklessActiveRequest>> = {};
  for (const role of ["architect", "system-specialist"] as const) {
    const stored = sessionStorage.getItem(tasklessRequestStorageKey(role));
    if (!stored) continue;
    try {
      const value = JSON.parse(stored) as {
        requestId?: unknown;
        pending?: unknown;
        retryPayload?: unknown;
      };
      if (typeof value.requestId !== "string" || !value.requestId) continue;
      const payload = value.retryPayload as OrcaLaunchSelection | undefined;
      active[role] = {
        requestId: value.requestId,
        ...(value.pending === true ? { pending: true } : {}),
        ...(payload?.role === role
          ? {
              retryPayload: {
                role,
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

export function roleNeedsMaster(role: OrcaRole): boolean {
  return role === "manager" || role === "worker" || role === "reviewer" || role === "curator";
}

export function roleNeedsTask(role: OrcaRole): boolean {
  return role === "worker" || role === "reviewer" || role === "curator";
}

export function orcaDocumentScope(selection: OrcaLaunchSelection): OrcaDocumentScope {
  return {
    role: selection.role,
    ...(roleNeedsSprint(selection.role) && selection.sprintDocumentRef
      ? { sprintDocumentRef: selection.sprintDocumentRef }
      : {}),
    ...(roleNeedsMaster(selection.role) && selection.masterDocumentRef
      ? { masterDocumentRef: selection.masterDocumentRef }
      : {}),
    ...(roleNeedsTask(selection.role) && selection.taskDocumentRef
      ? { taskDocumentRef: selection.taskDocumentRef }
      : {}),
  };
}

export function orcaOptionsScope(selection: OrcaLaunchSelection): OrcaOptionsScope {
  return {
    ...orcaDocumentScope(selection),
    ...(selection.agentOverride?.agentId ? { agentId: selection.agentOverride.agentId } : {}),
  };
}

export function launchSelectionComplete(selection: OrcaLaunchSelection): boolean {
  return (!roleNeedsSprint(selection.role) || Boolean(selection.sprintDocumentRef)) &&
    (!roleNeedsMaster(selection.role) || Boolean(selection.masterDocumentRef)) &&
    (!roleNeedsTask(selection.role) || Boolean(selection.taskDocumentRef));
}

export function roleDefaultsCacheKey(scope: OrcaDocumentScope): string {
  const effectiveRef = scope.taskDocumentRef ?? scope.masterDocumentRef ?? scope.sprintDocumentRef;
  return scope.role + "::" + (effectiveRef?.repository ?? "projects");
}

export function mergeOrcaAgentInventory(
  previous: OrcaAgentInventory | null,
  options: OrcaLauncherOptions,
  selectedAgentId: string | undefined,
  refresh: boolean,
): OrcaAgentInventory {
  const catalogOrigin = options.catalogOrigin ?? previous?.catalogOrigin ?? "active-orca-runtime";
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

export function sameOrcaDocumentScope(left: OrcaDocumentScope, right: OrcaDocumentScope): boolean {
  return left.role === right.role &&
    sameOptionalTaskDocumentRef(left.sprintDocumentRef, right.sprintDocumentRef) &&
    sameOptionalTaskDocumentRef(left.masterDocumentRef, right.masterDocumentRef) &&
    sameOptionalTaskDocumentRef(left.taskDocumentRef, right.taskDocumentRef);
}

export function sameOrcaOptionsScope(left: OrcaOptionsScope, right: OrcaOptionsScope): boolean {
  return sameOrcaDocumentScope(left, right) && left.agentId === right.agentId;
}

export function sameOrcaLaunchSelection(left: OrcaLaunchSelection, right: OrcaLaunchSelection): boolean {
  return sameOrcaDocumentScope(orcaDocumentScope(left), orcaDocumentScope(right)) &&
    left.agentOverride?.agentId === right.agentOverride?.agentId &&
    left.agentOverride?.modelId === right.agentOverride?.modelId &&
    left.agentOverride?.effortId === right.agentOverride?.effortId;
}

export function orcaAgentOverrideFor(
  agentId: string | undefined,
  modelId: string | undefined,
  effortId: string | undefined,
  defaults: OrcaRoleDefaults,
): OrcaAgentOverride | undefined {
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
