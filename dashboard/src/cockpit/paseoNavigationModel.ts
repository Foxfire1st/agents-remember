import type { SeriesNode, TaskDocNode } from '../types/projection';
import { sameTaskDocumentRef } from '../data/taskIdentity';
import {
  masterOptionsForSprint,
  sprintOptionsForDocs,
  taskOptionsForDocs,
  taskOptionsForMaster,
  type RoleTaskOption,
} from './roleLaunchModel';
import type { PaseoHierarchyAgent, PaseoHierarchySnapshot } from './paseoFrameModel';

export interface PaseoChatGroup {
  key: string;
  title: string;
  kind: 'projects' | 'sprint' | 'master' | 'task' | 'unresolved';
  repository?: string;
  documentId?: string;
  chats: PaseoHierarchyAgent[];
  children: PaseoChatGroup[];
}

const refKey = (option: RoleTaskOption) => option.ref.repository + '/' + option.ref.path;
const TASK_BOUND_ROLES = new Set(['orchestrator', 'manager', 'worker', 'reviewer', 'curator']);

function scopeGroup(
  parent: PaseoChatGroup,
  option: RoleTaskOption,
  kind: 'sprint' | 'master' | 'task',
): PaseoChatGroup {
  const key = refKey(option);
  let group = parent.children.find((item) => item.key === key);
  if (!group) {
    group = {
      key,
      title: option.doc.title || option.doc.id,
      kind,
      repository: option.ref.repository,
      documentId: option.doc.id,
      chats: [],
      children: [],
    };
    parent.children.push(group);
  }
  return group;
}

function canonicalScopes(
  chat: PaseoHierarchyAgent,
  byRef: Map<string, RoleTaskOption>,
  sprints: Map<string, RoleTaskOption>,
  documents: TaskDocNode[],
  series: SeriesNode[],
): RoleTaskOption[] | null {
  const sprint = sprints.get(chat.labels['ar.sprint-ref']);
  if (!sprint) return null;
  const path = [sprint];
  const masterKey = chat.labels['ar.master-ref'];
  const master = byRef.get(masterKey);
  if (masterKey) {
    const options = masterOptionsForSprint(documents, sprint.doc);
    if (!master || !options.some((option) => sameTaskDocumentRef(option.ref, master.ref)))
      return null;
    path.push(master);
  }
  const taskKey = chat.labels['ar.task-ref'];
  if (taskKey) {
    const task = byRef.get(taskKey);
    if (!master || !task) return null;
    const options = taskOptionsForMaster(documents, series, master.doc);
    if (!options.some((option) => sameTaskDocumentRef(option.ref, task.ref))) return null;
    path.push(task);
  }
  return path;
}

/** Canonical AR references establish scope; native membership and spawn ancestry do not. */
export function groupPaseoChats(
  snapshot: PaseoHierarchySnapshot,
  taskDocuments: TaskDocNode[],
  series: SeriesNode[],
): PaseoChatGroup[] {
  const projects: PaseoChatGroup = {
    key: 'projects',
    title: 'Projects',
    kind: 'projects',
    chats: [],
    children: [],
  };
  const scopes: PaseoChatGroup = {
    key: 'scopes',
    title: 'Scopes',
    kind: 'projects',
    chats: [],
    children: [],
  };
  const unresolved: PaseoChatGroup = {
    key: 'unresolved',
    title: 'Unresolved AR scope',
    kind: 'unresolved',
    chats: [],
    children: [],
  };
  const byRef = new Map(
    taskOptionsForDocs(taskDocuments).map((option) => [refKey(option), option]),
  );
  const sprints = new Map(
    sprintOptionsForDocs(taskDocuments).map((option) => [refKey(option), option]),
  );
  for (const chat of snapshot.agents) {
    if (chat.archivedAt) continue;
    const sprintKey = chat.labels['ar.sprint-ref'];
    const masterKey = chat.labels['ar.master-ref'];
    const taskKey = chat.labels['ar.task-ref'];
    if (!sprintKey && !masterKey && !taskKey) {
      (TASK_BOUND_ROLES.has(chat.labels['ar.role']) ? unresolved : projects).chats.push(chat);
      continue;
    }
    const path = canonicalScopes(chat, byRef, sprints, taskDocuments, series);
    if (!path) {
      unresolved.chats.push(chat);
      continue;
    }
    let group = scopes;
    const kinds = ['sprint', 'master', 'task'] as const;
    path.forEach((option, index) => {
      group = scopeGroup(group, option, kinds[index]);
    });
    group.chats.push(chat);
  }
  return [projects, ...scopes.children, ...(unresolved.chats.length ? [unresolved] : [])];
}
