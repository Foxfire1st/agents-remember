import { useEffect } from "react";

import type { SeriesNode, TaskDocNode } from "../../types/projection";
import { sameTaskDocumentRef } from "../../data/taskIdentity";
import {
  masterOptionsForSprint,
  roleDocumentScope,
  sprintOptionsForDocs,
  taskOptionsForMaster,
  type RoleAgentChoice,
  type RoleEffortChoice,
  type RoleLaunchSelection,
  type RoleModelChoice,
} from "../roleLaunchModel";

// A seeded sprint the catalogs no longer list prunes the selection back to the role.
function pruneSeededSprint(
  taskDocuments: TaskDocNode[],
  role: RoleLaunchSelection["role"],
  sprintDocumentRef: RoleLaunchSelection["sprintDocumentRef"],
): RoleLaunchSelection | undefined {
  const currentSprints = sprintOptionsForDocs(taskDocuments);
  if (sprintDocumentRef && !currentSprints.some((option) => sameTaskDocumentRef(option.ref, sprintDocumentRef))) {
    return { role };
  }
  return undefined;
}

// A seeded master the catalogs no longer list prunes the selection back to its sprint.
function pruneSeededMaster(
  taskDocuments: TaskDocNode[],
  role: RoleLaunchSelection["role"],
  sprintDocumentRef: RoleLaunchSelection["sprintDocumentRef"],
  masterDocumentRef: RoleLaunchSelection["masterDocumentRef"],
): RoleLaunchSelection | undefined {
  const currentSprints = sprintOptionsForDocs(taskDocuments);
  const currentSprint = currentSprints.find((option) => sameTaskDocumentRef(option.ref, sprintDocumentRef))?.doc;
  const currentMasters = masterOptionsForSprint(taskDocuments, currentSprint);
  if (masterDocumentRef && !currentMasters.some((option) => sameTaskDocumentRef(option.ref, masterDocumentRef))) {
    return { role, ...(sprintDocumentRef ? { sprintDocumentRef } : {}) };
  }
  return undefined;
}

// A seeded task the catalogs no longer list prunes the selection back to its master.
function pruneSeededTask(
  taskDocuments: TaskDocNode[],
  series: SeriesNode[],
  role: RoleLaunchSelection["role"],
  sprintDocumentRef: RoleLaunchSelection["sprintDocumentRef"],
  masterDocumentRef: RoleLaunchSelection["masterDocumentRef"],
  taskDocumentRef: RoleLaunchSelection["taskDocumentRef"],
): RoleLaunchSelection | undefined {
  const currentSprints = sprintOptionsForDocs(taskDocuments);
  const currentSprint = currentSprints.find((option) => sameTaskDocumentRef(option.ref, sprintDocumentRef))?.doc;
  const currentMasters = masterOptionsForSprint(taskDocuments, currentSprint);
  const currentMaster = currentMasters.find((option) => sameTaskDocumentRef(option.ref, masterDocumentRef))?.doc;
  const currentTasks = taskOptionsForMaster(taskDocuments, series, currentMaster);
  if (taskDocumentRef && !currentTasks.some((option) => sameTaskDocumentRef(option.ref, taskDocumentRef))) {
    return {
      role,
      ...(sprintDocumentRef ? { sprintDocumentRef } : {}),
      ...(masterDocumentRef ? { masterDocumentRef } : {}),
    };
  }
  return undefined;
}

// The selection repaired against the loaded catalogs, or undefined when it still names
// live documents: a seeded sprint, master or task that no longer exists is pruned back
// to the closest live scope, keeping the role.
export function repairedSeededSelection(
  taskDocuments: TaskDocNode[],
  series: SeriesNode[],
  selection: RoleLaunchSelection,
): RoleLaunchSelection | undefined {
  const { role, sprintDocumentRef, masterDocumentRef, taskDocumentRef } = selection;
  return pruneSeededSprint(taskDocuments, role, sprintDocumentRef) ??
    pruneSeededMaster(taskDocuments, role, sprintDocumentRef, masterDocumentRef) ??
    pruneSeededTask(taskDocuments, series, role, sprintDocumentRef, masterDocumentRef, taskDocumentRef);
}

// An override naming an unoffered agent prunes back to no override over the document scope.
function pruneUnofferedAgent(
  activeScope: RoleLaunchSelection,
  selection: RoleLaunchSelection,
  agents: RoleAgentChoice[],
): RoleLaunchSelection | undefined {
  if (selection.agentOverride?.agentId && !agents.some((agent) => agent.id === selection.agentOverride?.agentId)) {
    return { ...activeScope, agentOverride: undefined };
  }
  return undefined;
}

// An override naming an unoffered model prunes back to the agent alone.
function pruneUnofferedModel(
  activeScope: RoleLaunchSelection,
  selection: RoleLaunchSelection,
  models: RoleModelChoice[],
): RoleLaunchSelection | undefined {
  if (selection.agentOverride?.modelId && !models.some((model) => model.id === selection.agentOverride?.modelId)) {
    return {
      ...activeScope,
      agentOverride: { agentId: selection.agentOverride.agentId },
    };
  }
  return undefined;
}

// An override naming an unoffered effort prunes back to the agent and model.
function pruneUnofferedEffort(
  activeScope: RoleLaunchSelection,
  selection: RoleLaunchSelection,
  efforts: RoleEffortChoice[],
): RoleLaunchSelection | undefined {
  if (selection.agentOverride?.effortId && !efforts.some((effort) => effort.id === selection.agentOverride?.effortId)) {
    return {
      ...activeScope,
      agentOverride: {
        agentId: selection.agentOverride.agentId,
        ...(selection.agentOverride.modelId ? { modelId: selection.agentOverride.modelId } : {}),
      },
    };
  }
  return undefined;
}

// The agent override repaired against the loaded catalog, or undefined when it still
// names offered values: an unoffered agent, model or effort choice is pruned back to
// the closest offered scope over the selection's document scope.
export function repairedOverride(
  selection: RoleLaunchSelection,
  agents: RoleAgentChoice[],
  models: RoleModelChoice[],
  efforts: RoleEffortChoice[],
): RoleLaunchSelection | undefined {
  const activeScope = roleDocumentScope(selection);
  return pruneUnofferedAgent(activeScope, selection, agents) ??
    pruneUnofferedModel(activeScope, selection, models) ??
    pruneUnofferedEffort(activeScope, selection, efforts);
}

// A seeded selection that names documents the catalogs no longer list is pruned back
// to the closest live scope as soon as the documents arrive.
export function usePruneSeededSelection(args: {
  taskDocuments: TaskDocNode[];
  series: SeriesNode[];
  role: RoleLaunchSelection["role"];
  sprintDocumentRef: RoleLaunchSelection["sprintDocumentRef"];
  masterDocumentRef: RoleLaunchSelection["masterDocumentRef"];
  taskDocumentRef: RoleLaunchSelection["taskDocumentRef"];
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}): void {
  const { taskDocuments, series, role, sprintDocumentRef, masterDocumentRef, taskDocumentRef, onSelectionChange } = args;
  useEffect(() => {
    const next = repairedSeededSelection(
      taskDocuments,
      series,
      { role, sprintDocumentRef, masterDocumentRef, taskDocumentRef },
    );
    if (next) onSelectionChange(next);
  }, [taskDocuments, series, role, sprintDocumentRef, masterDocumentRef, taskDocumentRef, onSelectionChange]);
}

// An override that names values the loaded catalog no longer offers is pruned back to
// the closest offered scope once the options are ready.
export function usePruneStaleOverride(args: {
  optionsReady: boolean;
  agents: RoleAgentChoice[];
  models: RoleModelChoice[];
  efforts: RoleEffortChoice[];
  selection: RoleLaunchSelection;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}): void {
  const { optionsReady, agents, models, efforts, selection, onSelectionChange } = args;
  useEffect(() => {
    if (!optionsReady) return;
    const next = repairedOverride(selection, agents, models, efforts);
    if (next) onSelectionChange(next);
  }, [optionsReady, agents, models, efforts, selection, onSelectionChange]);
}
