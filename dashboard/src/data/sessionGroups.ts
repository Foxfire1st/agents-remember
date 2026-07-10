import type { EnclosureNode, TaskDocNode } from "../types/projection";
import { sessionSeatRole, type OpenSession } from "./sessions";
import { isOrchestrationDoc, pathDir } from "./taskHierarchy";
import { qualifiedLeafKey } from "./taskIdentity";

// L16 derives one rail box per repo-qualified sprint. Sessions with a valid leaf claim stay in
// that sprint regardless of worktree liveness; landed sessions use the existing archive, while
// malformed claims use an explicit error box. Pure derivation; collapse state remains UI-local.

/** The l-01 seats whose chats belong on the command deck. */
const COMMAND_ROLES = new Set(["architect", "orchestrator", "strategist", "manager"]);

export interface SessionGroup {
  key: string; // `sprint:${repo}/${master}` | "landed" | "error"
  kind: "command" | "master" | "landed" | "error";
  label: string;
  /** Rank insignia tier for the group header; unset = unmarked (landed / uncommanded master). */
  tier?: "orchestration" | "management";
  /** One 22px indent step (a master group commanded by the orchestration task). */
  nested: boolean;
  defaultCollapsed: boolean;
  sessions: OpenSession[];
  /** "n chats · n live" (live from the existing session state; archive groups read "· archived"). */
  countLabel: string;
}

export interface GroupedSessions {
  groups: SessionGroup[];
  /** Sessions with no claim and no command provenance — today's flat placement below the groups. */
  ungrouped: OpenSession[];
}

function isLive(session: OpenSession): boolean {
  return (session.status ?? "running") === "running";
}

function countLabel(sessions: OpenSession[], kind: SessionGroup["kind"]): string {
  const chats = `${sessions.length} chat${sessions.length === 1 ? "" : "s"}`;
  if (kind === "landed") return `${chats} · archived`;
  const live = sessions.filter(isLive).length;
  return live > 0 ? `${chats} · ${live} live` : chats;
}

function folderOf(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? "";
}

/** The `repo/master/leaf-id` segments of a leaf claim, or undefined when malformed. */
function leafKeySegments(
  leafKey: string,
): { repo: string; master: string; leafId: string } | undefined {
  const parts = leafKey.split("/").filter(Boolean);
  if (parts.length < 3) return undefined;
  return { repo: parts[0], master: parts[1], leafId: parts[parts.length - 1] };
}

export function groupSessions(input: {
  sessions: OpenSession[];
  taskDocuments: TaskDocNode[];
  enclosures: EnclosureNode[];
}): GroupedSessions {
  const docs = input.taskDocuments;
  const orchestrationDocs = docs.filter(isOrchestrationDoc);
  const deckExists = orchestrationDocs.length > 0;
  const orchestrationKeys = new Set(
    orchestrationDocs.map((doc) => qualifiedLeafKey(doc)).filter(Boolean),
  );
  const masterDocsBySprint = new Map<string, TaskDocNode>();
  for (const doc of docs) {
    if (doc.kind !== "master") continue;
    const folder = folderOf(pathDir(doc.docPath));
    const key = folder && doc.repository ? `${doc.repository}/${folder}` : undefined;
    if (key && !masterDocsBySprint.has(key)) masterDocsBySprint.set(key, doc);
  }

  const landed: OpenSession[] = [];
  const ungrouped: OpenSession[] = [];
  const invalid: OpenSession[] = [];
  const bySprint = new Map<string, OpenSession[]>();

  for (const session of input.sessions) {
    if (session.status === "landed") {
      landed.push(session);
      continue;
    }
    const segments = session.leafKey ? leafKeySegments(session.leafKey) : undefined;
    if (session.leafKey && !segments) {
      invalid.push(session);
      continue;
    }
    if (!segments) {
      ungrouped.push(session);
      continue;
    }
    const key = `${segments.repo}/${segments.master}`;
    const members = bySprint.get(key);
    if (members) members.push(session);
    else bySprint.set(key, [session]);
  }

  const groups: SessionGroup[] = [];
  const sprintGroups = [...bySprint.entries()].map(([sprintKey, members]) => {
    const [repo, folder] = sprintKey.split("/");
    const masterDoc = masterDocsBySprint.get(sprintKey);
    // `orchestrates` contains bare folder names, so it is safe only when the declaring
    // orchestration document belongs to this same repository. Never let a same-named folder in
    // another repository inherit command styling or indentation.
    const commanded = orchestrationDocs.some((doc) => {
      const key = qualifiedLeafKey(doc);
      return key?.startsWith(`${sprintKey}/`) ||
        (doc.repository === repo && (doc.orchestrates ?? []).includes(folder));
    });
    const hasCommand = deckExists && members.some((session) =>
      COMMAND_ROLES.has(sessionSeatRole(session)) ||
      (session.leafKey !== undefined && orchestrationKeys.has(session.leafKey)),
    );
    const kind = hasCommand ? "command" : "master";
    const group: SessionGroup = {
      key: `sprint:${sprintKey}`,
      kind,
      label: hasCommand
        ? `${masterDoc?.title || `${repo}/${folder}`} · command deck`
        : masterDoc?.title || `${repo}/${folder}`,
      ...(hasCommand ? { tier: "orchestration" as const } : commanded ? { tier: "management" as const } : {}),
      nested: commanded,
      defaultCollapsed: false,
      sessions: members,
      countLabel: countLabel(members, kind),
    };
    return { order: masterDoc?.createdAt ?? "", sprintKey, group };
  });
  sprintGroups.sort(
    (left, right) => left.order.localeCompare(right.order) || left.sprintKey.localeCompare(right.sprintKey),
  );
  for (const entry of sprintGroups) groups.push(entry.group);
  if (landed.length > 0) {
    groups.push({
      key: "landed",
      kind: "landed",
      label: "landed archive",
      nested: false,
      defaultCollapsed: true,
      sessions: landed,
      countLabel: countLabel(landed, "landed"),
    });
  }
  if (invalid.length > 0) {
    groups.push({
      key: "error",
      kind: "error",
      label: "unresolvable session claims",
      tier: "management",
      nested: false,
      defaultCollapsed: false,
      sessions: invalid,
      countLabel: `${invalid.length} error${invalid.length === 1 ? "" : "s"}`,
    });
  }
  return { groups, ungrouped };
}
