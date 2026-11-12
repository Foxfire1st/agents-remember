import { sameTaskDocumentRef } from '../../data/taskIdentity';
import type { TaskDocumentRef, TaskEntry } from '../../types/projection';
import { type LauncherRole, type RoleLaunchSelection } from '../roleLaunchModel';

export interface DocumentChatBinding {
  selection: RoleLaunchSelection;
  roles: LauncherRole[];
  problem?: string;
}

export interface DocumentAgent {
  agentId: string;
  workspaceId: string | null;
  archivedAt: string | null;
  createdAt: string | null;
  labels: Record<string, string>;
}

/** A document of the tree a role chat can belong to, with the selection that names it. */
export interface DocumentBinding {
  ref: TaskDocumentRef;
  binding: DocumentChatBinding;
}

function masterBindings(
  master: TaskEntry,
  sprintDocumentRef: TaskDocumentRef,
  entries: Map<string, TaskEntry>,
): DocumentBinding[] {
  if (!master.reference || master.summary?.form !== 'master') return [];
  const scope = { sprintDocumentRef, masterDocumentRef: master.reference };
  const bindings: DocumentBinding[] = [
    {
      ref: master.reference,
      binding: { selection: { role: 'manager', ...scope }, roles: ['manager'] },
    },
  ];
  for (const key of master.children) {
    const leaf = entries.get(key);
    if (!leaf?.reference || leaf.summary?.form !== 'task') continue;
    bindings.push({
      ref: leaf.reference,
      binding: {
        selection: { role: 'worker', ...scope, taskDocumentRef: leaf.reference },
        roles: ['worker', 'reviewer', 'curator'],
      },
    });
  }
  return bindings;
}

/** Follow the served task tree in order. Identity and parentage are never rebuilt from documents. */
export function documentBindings(entries: TaskEntry[]): DocumentBinding[] {
  const byReference = new Map(entries.map((entry) => [entry.key, entry]));
  const tree: DocumentBinding[] = [];
  for (const sprint of entries.filter((entry) => entry.summary?.form === 'sprint')) {
    if (!sprint.reference) continue;
    tree.push({
      ref: sprint.reference,
      binding: {
        selection: { role: 'orchestrator', sprintDocumentRef: sprint.reference },
        roles: ['orchestrator'],
      },
    });
    for (const masterRef of sprint.children) {
      const master = byReference.get(masterRef);
      if (master) tree.push(...masterBindings(master, sprint.reference, byReference));
    }
  }
  return tree;
}

/** The binding of one document of the tree; no document is the Projects chat. */
export function bindingInTree(
  ref: TaskDocumentRef | undefined,
  tree: DocumentBinding[],
): DocumentChatBinding {
  if (!ref) return { selection: { role: 'architect' }, roles: ['architect', 'investigator'] };
  return (
    tree.find((row) => sameTaskDocumentRef(row.ref, ref))?.binding ?? {
      selection: { role: 'worker' },
      roles: [],
      problem:
        'No role can start here: a leaf needs a master commanded by a sprint, and a master needs its commanding sprint document.',
    }
  );
}

export function documentChatBinding(
  ref: TaskDocumentRef | undefined,
  entries: TaskEntry[],
): DocumentChatBinding {
  return bindingInTree(ref, ref ? documentBindings(entries) : []);
}

export function documentChatAgent(
  ref: TaskDocumentRef | undefined,
  agents: DocumentAgent[],
): DocumentAgent | null {
  const key = ref ? ref.repository + '/' + ref.path : null;
  return (
    agents
      .filter((agent) => {
        if (agent.archivedAt || !agent.workspaceId) return false;
        const labels = agent.labels;
        if (['investigator', 'system-specialist'].includes(labels['ar.role'])) return false;
        if (!key)
          return (
            labels['ar.role'] === 'architect' &&
            !labels['ar.task-ref'] &&
            !labels['ar.master-ref'] &&
            !labels['ar.sprint-ref']
          );
        return (
          (labels['ar.task-ref'] ?? labels['ar.master-ref'] ?? labels['ar.sprint-ref']) === key
        );
      })
      .sort(
        (a, b) =>
          (b.createdAt ?? '').localeCompare(a.createdAt ?? '') ||
          a.agentId.localeCompare(b.agentId),
      )[0] ?? null
  );
}

function agentRecord(item: unknown): DocumentAgent | null {
  if (!item || typeof item !== 'object') return null;
  const value = item as Record<string, unknown>;
  if (
    typeof value.agentId !== 'string' ||
    !nullableText(value.workspaceId) ||
    !nullableText(value.archivedAt)
  )
    return null;
  if (!value.labels || typeof value.labels !== 'object') return null;
  return {
    agentId: value.agentId,
    workspaceId: value.workspaceId,
    archivedAt: value.archivedAt,
    createdAt: typeof value.createdAt === 'string' ? value.createdAt : null,
    labels: Object.fromEntries(
      Object.entries(value.labels).filter(
        (row): row is [string, string] => typeof row[1] === 'string',
      ),
    ),
  };
}

function nullableText(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

/** Read the record projection by its declared fields. */
export function documentAgents(value: unknown): DocumentAgent[] | null {
  if (!Array.isArray(value)) return null;
  const agents = value.map(agentRecord);
  return agents.every((agent): agent is DocumentAgent => agent !== null) ? agents : null;
}

/** Investigator is an explicit launcher choice; it never participates in the primary chat binding. */
export function documentLauncherRoles(binding: DocumentChatBinding): LauncherRole[] {
  return ['orchestrator', 'manager'].includes(binding.selection.role)
    ? [...binding.roles, 'investigator']
    : binding.roles;
}
