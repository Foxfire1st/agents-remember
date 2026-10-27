import { useEffect } from 'react';

import type { TaskEntry } from '../../types/projection';
import { sameTaskDocumentRef } from '../../data/taskIdentity';
import {
  masterOptionsForSprint,
  sprintOptionsForEntries,
  taskOptionsForMaster,
  type RoleLaunchSelection,
} from '../roleLaunchModel';

// A seeded sprint the catalogs no longer list prunes the selection back to the role.
function pruneSeededSprint(
  tasks: TaskEntry[],
  role: RoleLaunchSelection['role'],
  sprintDocumentRef: RoleLaunchSelection['sprintDocumentRef'],
): RoleLaunchSelection | undefined {
  const currentSprints = sprintOptionsForEntries(tasks);
  if (
    sprintDocumentRef &&
    !currentSprints.some((option) => sameTaskDocumentRef(option.ref, sprintDocumentRef))
  ) {
    return { role };
  }
  return undefined;
}

// A seeded master the catalogs no longer list prunes the selection back to its sprint.
function pruneSeededMaster(
  tasks: TaskEntry[],
  role: RoleLaunchSelection['role'],
  sprintDocumentRef: RoleLaunchSelection['sprintDocumentRef'],
  masterDocumentRef: RoleLaunchSelection['masterDocumentRef'],
): RoleLaunchSelection | undefined {
  const currentSprints = sprintOptionsForEntries(tasks);
  const currentSprint = currentSprints.find((option) =>
    sameTaskDocumentRef(option.ref, sprintDocumentRef),
  )?.entry;
  const currentMasters = masterOptionsForSprint(tasks, currentSprint);
  if (
    masterDocumentRef &&
    !currentMasters.some((option) => sameTaskDocumentRef(option.ref, masterDocumentRef))
  ) {
    return { role, ...(sprintDocumentRef ? { sprintDocumentRef } : {}) };
  }
  return undefined;
}

// A seeded task the catalogs no longer list prunes the selection back to its master.
function pruneSeededTask(
  tasks: TaskEntry[],
  role: RoleLaunchSelection['role'],
  sprintDocumentRef: RoleLaunchSelection['sprintDocumentRef'],
  masterDocumentRef: RoleLaunchSelection['masterDocumentRef'],
  taskDocumentRef: RoleLaunchSelection['taskDocumentRef'],
): RoleLaunchSelection | undefined {
  const currentSprints = sprintOptionsForEntries(tasks);
  const currentSprint = currentSprints.find((option) =>
    sameTaskDocumentRef(option.ref, sprintDocumentRef),
  )?.entry;
  const currentMasters = masterOptionsForSprint(tasks, currentSprint);
  const currentMaster = currentMasters.find((option) =>
    sameTaskDocumentRef(option.ref, masterDocumentRef),
  )?.entry;
  const currentTasks = taskOptionsForMaster(tasks, currentMaster);
  if (
    taskDocumentRef &&
    !currentTasks.some((option) => sameTaskDocumentRef(option.ref, taskDocumentRef))
  ) {
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
  tasks: TaskEntry[],
  selection: RoleLaunchSelection,
): RoleLaunchSelection | undefined {
  const { role, sprintDocumentRef, masterDocumentRef, taskDocumentRef } = selection;
  return (
    pruneSeededSprint(tasks, role, sprintDocumentRef) ??
    pruneSeededMaster(tasks, role, sprintDocumentRef, masterDocumentRef) ??
    pruneSeededTask(tasks, role, sprintDocumentRef, masterDocumentRef, taskDocumentRef)
  );
}

// A seeded selection that names documents the catalogs no longer list is pruned back
// to the closest live scope as soon as the documents arrive.
export function usePruneSeededSelection(args: {
  tasks: TaskEntry[];
  role: RoleLaunchSelection['role'];
  sprintDocumentRef: RoleLaunchSelection['sprintDocumentRef'];
  masterDocumentRef: RoleLaunchSelection['masterDocumentRef'];
  taskDocumentRef: RoleLaunchSelection['taskDocumentRef'];
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}): void {
  const { tasks, role, sprintDocumentRef, masterDocumentRef, taskDocumentRef, onSelectionChange } =
    args;
  useEffect(() => {
    const next = repairedSeededSelection(tasks, {
      role,
      sprintDocumentRef,
      masterDocumentRef,
      taskDocumentRef,
    });
    if (next) onSelectionChange(next);
  }, [tasks, role, sprintDocumentRef, masterDocumentRef, taskDocumentRef, onSelectionChange]);
}
