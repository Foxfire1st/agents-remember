import type { SeriesNode, TaskDocNode } from "../../types/projection";
import type { TaskDocumentRef } from "../../types/terminalCatalog";
import { sameTaskDocumentRef, taskDocumentRefForDoc } from "../../data/taskIdentity";
import {
  masterOptionsForSprint, sprintOptionsForDocs, taskOptionsForMaster,
  type LauncherRole, type RoleLaunchSelection,
} from "../roleLaunchModel";

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

export function documentChatBinding(
  ref: TaskDocumentRef | undefined, docs: TaskDocNode[], series: SeriesNode[],
): DocumentChatBinding {
  if (!ref) return { selection: { role: "architect" }, roles: ["architect", "investigator"] };
  const matches = (doc: TaskDocNode) => sameTaskDocumentRef(taskDocumentRefForDoc(doc), ref);
  for (const sprint of sprintOptionsForDocs(docs)) {
    if (matches(sprint.doc)) return {
      selection: { role: "orchestrator", sprintDocumentRef: sprint.ref }, roles: ["orchestrator"],
    };
    for (const master of masterOptionsForSprint(docs, sprint.doc)) {
      const scope = { sprintDocumentRef: sprint.ref, masterDocumentRef: master.ref };
      if (matches(master.doc)) return { selection: { role: "manager", ...scope }, roles: ["manager"] };
      for (const leaf of taskOptionsForMaster(docs, series, master.doc)) {
        if (matches(leaf.doc)) return {
          selection: { role: "worker", ...scope, taskDocumentRef: leaf.ref },
          roles: ["worker", "reviewer", "curator"],
        };
      }
    }
  }
  return { selection: { role: "worker" }, roles: [],
    problem: "No role can start here: a leaf needs a master commanded by a sprint, and a master needs its commanding sprint document." };
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
