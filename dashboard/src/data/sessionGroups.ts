import type { EnclosureNode, TaskDocNode } from "../types/projection";
import { hasLiveWorktree } from "./selectors";
import type { OpenSession } from "./sessions";
import {
  isOrchestrationDoc,
  masterCommandNames,
  orchestratorParentKey,
  pathDir,
} from "./taskHierarchy";
import { qualifiedLeafKey } from "./taskIdentity";

// The G1 COMMAND TREE for the Chats pane (L14): the chats sidebar mirrors operations. Membership,
// in precedence order, per session:
//   1. COMMAND DECK (gold, top; exists ONLY when an orchestration task exists — D3): sessions with
//      command-role spawn provenance (AR_SPAWN_ROLE architect/orchestrator/strategist/manager recorded on the
//      catalog row) plus any session whose leaf claim resolves INTO the orchestration task itself
//      (its own qualified leaf, or a leaf in its folder) — the developer-facing architect chat.
//   2. MASTER GROUPS (one per claimed master folder): sessions whose qualified leaf key
//      (`repo/master/leaf-id`) resolves to a live enclosure of that master; the group takes the
//      purple management badge + one 22px indent step only when that master is commanded by an
//      orchestration task — mirroring the tasks-tab grammar exactly.
//   3. LANDED ARCHIVE (one group, collapsed by default, unmarked): sessions attached to a leaf
//      whose enclosure is gone or absent — landed work rolls up instead of cluttering the rail.
//   4. UNGROUPED: sessions with no leaf claim keep today's flat placement below the groups.
// Pure derivation — no persistence; collapse state is UI-local in the SessionList.

/** The l-01 seats whose chats belong on the command deck. */
const COMMAND_ROLES = new Set(["architect", "orchestrator", "strategist", "manager"]);

export interface SessionGroup {
  key: string; // "command" | `master:${folder}` | "landed"
  kind: "command" | "master" | "landed";
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

// The enclosure a leaf claim resolves to: master folder = basename(taskRoot), leaf ids compared
// case-insensitively (enclosure leaf ids are slugified lowercase, doc ids authored uppercase —
// the same normalization the tasks tab uses), repo checked when the enclosure carries one.
function enclosureForLeafKey(
  leafKey: string,
  enclosures: EnclosureNode[],
): EnclosureNode | undefined {
  const segments = leafKeySegments(leafKey);
  if (!segments) return undefined;
  return enclosures.find((enclosure) => {
    if (folderOf(enclosure.taskRoot) !== segments.master) return false;
    if (enclosure.repoName && enclosure.repoName !== segments.repo) return false;
    return enclosure.leafId.toLowerCase() === segments.leafId.toLowerCase();
  });
}

export function groupSessions(input: {
  sessions: OpenSession[];
  taskDocuments: TaskDocNode[];
  enclosures: EnclosureNode[];
}): GroupedSessions {
  const docs = input.taskDocuments;
  const orchestrationDocs = docs.filter(isOrchestrationDoc);
  const deckExists = orchestrationDocs.length > 0;
  const orchestrationFolders = new Set(orchestrationDocs.map((doc) => folderOf(pathDir(doc.docPath))));
  const orchestrationLeafKeys = new Set(
    orchestrationDocs.map((doc) => qualifiedLeafKey(doc)).filter(Boolean),
  );
  const masterDocsByFolder = new Map<string, TaskDocNode>();
  for (const doc of docs) {
    if (doc.kind !== "master") continue;
    const folder = folderOf(pathDir(doc.docPath));
    if (folder && !masterDocsByFolder.has(folder)) masterDocsByFolder.set(folder, doc);
  }

  const deck: OpenSession[] = [];
  const landed: OpenSession[] = [];
  const ungrouped: OpenSession[] = [];
  const byMaster = new Map<string, OpenSession[]>();

  for (const session of input.sessions) {
    const segments = session.leafKey ? leafKeySegments(session.leafKey) : undefined;
    const claimsOrchestration =
      (session.leafKey !== undefined && orchestrationLeafKeys.has(session.leafKey)) ||
      (segments !== undefined && orchestrationFolders.has(segments.master));
    if (
      deckExists &&
      ((session.spawnRole !== undefined && COMMAND_ROLES.has(session.spawnRole)) ||
        claimsOrchestration)
    ) {
      deck.push(session);
      continue;
    }
    if (!segments) {
      ungrouped.push(session);
      continue;
    }
    const enclosure = enclosureForLeafKey(session.leafKey as string, input.enclosures);
    if (enclosure && hasLiveWorktree(enclosure)) {
      const members = byMaster.get(segments.master);
      if (members) members.push(session);
      else byMaster.set(segments.master, [session]);
    } else {
      // Landed or absent enclosure: the work left the hangar, the chat rolls into the archive.
      landed.push(session);
    }
  }

  const groups: SessionGroup[] = [];
  if (deck.length > 0) {
    const sprint = orchestrationDocs[0];
    groups.push({
      key: "command",
      kind: "command",
      label: sprint?.title ? `${sprint.title} · command deck` : "command deck",
      tier: "orchestration",
      nested: false,
      defaultCollapsed: false,
      sessions: deck,
      countLabel: countLabel(deck, "command"),
    });
  }
  const masterGroups = [...byMaster.entries()].map(([folder, members]) => {
    const masterDoc = masterDocsByFolder.get(folder);
    const commanded = masterDoc
      ? orchestratorParentKey(masterCommandNames(masterDoc), docs, masterDoc.docPath) !== undefined
      : orchestrationDocs.some((doc) => (doc.orchestrates ?? []).includes(folder));
    const group: SessionGroup = {
      key: `master:${folder}`,
      kind: "master",
      label: masterDoc?.title || folder,
      ...(commanded ? { tier: "management" as const } : {}),
      nested: commanded,
      defaultCollapsed: false,
      sessions: members,
      countLabel: countLabel(members, "master"),
    };
    return { order: masterDoc?.createdAt ?? "", folder, group };
  });
  masterGroups.sort(
    (left, right) => left.order.localeCompare(right.order) || left.folder.localeCompare(right.folder),
  );
  for (const entry of masterGroups) groups.push(entry.group);
  if (landed.length > 0) {
    groups.push({
      key: "landed",
      kind: "landed",
      label: "landed",
      nested: false,
      defaultCollapsed: true,
      sessions: landed,
      countLabel: countLabel(landed, "landed"),
    });
  }
  return { groups, ungrouped };
}
