import type { SeriesNode, TaskDocNode } from "../../types/projection";
import type { TaskDocumentRef } from "../../types/terminalCatalog";
import { isOrchestrationDoc, masterCommandNames, orchestratorParentKey, taskDocParentKey } from "../../data/taskHierarchy";
import { sameTaskDocumentRef, taskDocSelectionKey } from "../../data/taskIdentity";
import { taskOptionsForDocs, type LauncherRole, type RoleLaunchSelection, type RoleTaskOption } from "../roleLaunchModel";

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

function grouped<Row>(groups: Map<string, Row[]>, key: string | undefined, row: Row): void {
  if (key === undefined) return;
  const rows = groups.get(key);
  if (rows) rows.push(row);
  else groups.set(key, [row]);
}

/** The key a master is grouped under: its repository and the sprint that commands it there. */
function commandedBy(repository: string, sprintKey: string | undefined): string | undefined {
  return sprintKey === undefined ? undefined : repository + "\n" + sprintKey;
}

/** Each document's parent, worked out once: masters by their commanding sprint, tasks by their master. */
function treeGroups(docs: TaskDocNode[], series: SeriesNode[], options: RoleTaskOption[]) {
  const commanders = new Map<string, TaskDocNode[]>();
  for (const doc of docs) if (isOrchestrationDoc(doc)) grouped(commanders, doc.repository, doc);
  const masterDocPaths = new Set(docs.filter((doc) => doc.kind === "master").map((doc) => doc.docPath));
  const masters = new Map<string, RoleTaskOption[]>();
  const leaves = new Map<string, RoleTaskOption[]>();
  for (const option of options) {
    const { doc } = option;
    if (doc.kind !== "master") grouped(leaves, taskDocParentKey(doc, series, masterDocPaths), option);
    else if (!isOrchestrationDoc(doc)) {
      const sprint = orchestratorParentKey(masterCommandNames(doc), commanders.get(doc.repository) ?? [], doc.docPath);
      grouped(masters, commandedBy(doc.repository, sprint), option);
    }
  }
  return { masters, leaves };
}

/** A master's own row and the rows of its tasks. */
function masterRows(sprint: TaskDocumentRef, master: RoleTaskOption, leaves: RoleTaskOption[]): DocumentBinding[] {
  const scope = { sprintDocumentRef: sprint, masterDocumentRef: master.ref };
  return [
    { ref: master.ref, binding: { selection: { role: "manager", ...scope }, roles: ["manager"] } },
    ...leaves.map((leaf): DocumentBinding => ({ ref: leaf.ref, binding: {
      selection: { role: "worker", ...scope, taskDocumentRef: leaf.ref }, roles: ["worker", "reviewer", "curator"],
    } })),
  ];
}

/**
 * Every sprint, each master a sprint of its repository commands, and each master's tasks, in
 * the tree's order. Each document's parent is worked out once and the tree is read off the
 * groups, so the cost grows with the documents and not with documents times masters.
 */
export function documentBindings(docs: TaskDocNode[], series: SeriesNode[]): DocumentBinding[] {
  const options = taskOptionsForDocs(docs);
  const { masters, leaves } = treeGroups(docs, series, options);
  const tree: DocumentBinding[] = [];
  for (const sprint of options.filter((option) => isOrchestrationDoc(option.doc))) {
    tree.push({ ref: sprint.ref, binding: { selection: { role: "orchestrator", sprintDocumentRef: sprint.ref }, roles: ["orchestrator"] } });
    const commanded = commandedBy(sprint.doc.repository, taskDocSelectionKey(sprint.doc.docPath))!;
    for (const master of masters.get(commanded) ?? []) {
      tree.push(...masterRows(sprint.ref, master, leaves.get(taskDocSelectionKey(master.doc.docPath)) ?? []));
    }
  }
  return tree;
}

/** The binding of one document of the tree; no document is the Projects chat. */
export function bindingInTree(ref: TaskDocumentRef | undefined, tree: DocumentBinding[]): DocumentChatBinding {
  if (!ref) return { selection: { role: "architect" }, roles: ["architect", "investigator"] };
  return tree.find((row) => sameTaskDocumentRef(row.ref, ref))?.binding ?? { selection: { role: "worker" }, roles: [],
    problem: "No role can start here: a leaf needs a master commanded by a sprint, and a master needs its commanding sprint document." };
}

export function documentChatBinding(
  ref: TaskDocumentRef | undefined, docs: TaskDocNode[], series: SeriesNode[],
): DocumentChatBinding {
  return bindingInTree(ref, ref ? documentBindings(docs, series) : []);
}

export function documentChatAgent(ref: TaskDocumentRef | undefined, agents: DocumentAgent[]): DocumentAgent | null {
  const key = ref ? ref.repository + "/" + ref.path : null;
  return agents.filter((agent) => {
    if (agent.archivedAt || !agent.workspaceId) return false;
    const labels = agent.labels;
    if (["investigator", "system-specialist"].includes(labels["ar.role"])) return false;
    if (!key) return labels["ar.role"] === "architect" &&
      !labels["ar.task-ref"] && !labels["ar.master-ref"] && !labels["ar.sprint-ref"];
    return (labels["ar.task-ref"] ?? labels["ar.master-ref"] ?? labels["ar.sprint-ref"]) === key;
  }).sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? "") || a.agentId.localeCompare(b.agentId))[0] ?? null;
}

function agentRecord(item: unknown): DocumentAgent | null {
  if (!item || typeof item !== "object") return null;
  const value = item as Record<string, unknown>;
  if (typeof value.agentId !== "string" || !nullableText(value.workspaceId) || !nullableText(value.archivedAt)) return null;
  if (!value.labels || typeof value.labels !== "object") return null;
  return { agentId: value.agentId, workspaceId: value.workspaceId, archivedAt: value.archivedAt,
    createdAt: typeof value.createdAt === "string" ? value.createdAt : null,
    labels: Object.fromEntries(Object.entries(value.labels).filter((row): row is [string, string] => typeof row[1] === "string")),
  };
}

function nullableText(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

/** Read the record projection by its declared fields. */
export function documentAgents(value: unknown): DocumentAgent[] | null {
  if (!Array.isArray(value)) return null;
  const agents = value.map(agentRecord);
  return agents.every((agent): agent is DocumentAgent => agent !== null) ? agents : null;
}

/** Investigator is an explicit launcher choice; it never participates in the primary chat binding. */
export function documentLauncherRoles(binding: DocumentChatBinding): LauncherRole[] {
  return ["orchestrator", "manager"].includes(binding.selection.role)
    ? [...binding.roles, "investigator"] : binding.roles;
}
