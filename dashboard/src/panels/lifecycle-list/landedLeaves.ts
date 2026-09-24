// What a master's LANDED leaves are, and the one bound that keeps listing them a list (ICR-R33).
//
// Before this module, a leaf row was admitted only while its worktree physically existed, so a master
// whose leaves had all closed out rendered as a bare row: its finished work could not be opened from
// the operations list at all. The rules here are the ones that make those leaves reachable WITHOUT
// turning the list into one row per task document — the projection carries ~534 documents against
// ~57 entries, and every rule below exists to keep that gap a decision rather than an accident.
import type { EnclosureNode, SeriesNode, TaskDocNode } from "../../types/projection";
import { pathDir, pathStem, taskDocParentKey } from "../../data/taskHierarchy";
import { taskDocSelectionKey } from "../../data/taskIdentity";

// The reader's collapse state, both halves (see `useCollapsedTaskGroups`): the keys they collapsed,
// and the keys they opened past a row's own default.
export interface CollapseState {
  collapsedKeys: ReadonlySet<string>;
  openedKeys: ReadonlySet<string>;
}

export interface ChildFacts {
  live: number;
  landed: number;
}

export const NO_CHILDREN: ChildFacts = { live: 0, landed: 0 };

// A leaf that RECORDS LANDED WORK: its task document is Completed. That is the durable record the
// requirement names ("a leaf whose task document records it as landed"), and the only one that
// survives closeout — which removes the worktree, liveness being exactly what a landed leaf no
// longer has. `DocStatus` is planning | inProgress | Completed, so this admits finished work only: a
// planned, reopened or abandoned leaf stays hidden exactly as it did before.
export function leafRecordsLandedWork(doc: Pick<TaskDocNode, "kind" | "status">): boolean {
  return doc.kind !== "master" && doc.status.toLowerCase() === "completed";
}

function enclosuresByRoot(items: EnclosureNode[]): Map<string, EnclosureNode[]> {
  const byRoot = new Map<string, EnclosureNode[]>();
  for (const item of items) {
    const list = byRoot.get(item.taskRoot);
    if (list) list.push(item);
    else byRoot.set(item.taskRoot, [item]);
  }
  return byRoot;
}

function enclosuresFor(
  byRoot: Map<string, EnclosureNode[]>,
  doc: Pick<TaskDocNode, "docPath">,
): EnclosureNode[] {
  return byRoot.get(pathDir(doc.docPath)) ?? [];
}

// What each row carries as children, read from the projection BEFORE any row is materialized —
// because a closed master's children are exactly the rows it does not build. A leaf is counted for
// the parent its series index resolves (`taskDocParentKey`), the same link the rendered rows nest by,
// so a landed leaf no index resolves is never floated to the top of the list instead.
export function childFactsByParent(
  docs: TaskDocNode[],
  seriesList: SeriesNode[],
  docPaths: Set<string>,
  activeEnclosures: EnclosureNode[],
): Map<string, ChildFacts> {
  const activeByRoot = enclosuresByRoot(activeEnclosures);
  const facts = new Map<string, ChildFacts>();
  for (const doc of docs) {
    if (doc.kind === "master") continue;
    const parentKey = taskDocParentKey(doc, seriesList, docPaths);
    if (!parentKey) continue;
    const live = enclosureForDoc(doc, enclosuresFor(activeByRoot, doc)) !== undefined;
    const landed = !live && leafRecordsLandedWork(doc);
    if (!live && !landed) continue;
    const current = facts.get(parentKey) ?? { live: 0, landed: 0 };
    if (live) current.live += 1;
    else current.landed += 1;
    facts.set(parentKey, current);
  }
  return facts;
}

// The enclosure a task document belongs to — the list's one join, owned here so the admission rule
// (this module) and the row builders (the list) cannot disagree about which leaf is which. Enclosure
// leaf ids are lowercase directory names while doc ids are uppercase, so every comparison is
// case-insensitive; exact joins only, because since task_reopen a leaf keeps its EXACT id.
export function enclosureForDoc(
  doc: Pick<TaskDocNode, "id" | "docPath" | "lifecycleId">,
  enclosures: EnclosureNode[],
): EnclosureNode | undefined {
  const dir = pathDir(doc.docPath);
  const stem = pathStem(doc.docPath).toLowerCase();
  const docId = doc.id ? doc.id.toLowerCase() : undefined;
  return enclosures.find((enclosure) => {
    if (enclosure.taskRoot !== dir) return false;
    const leafId = enclosure.leafId.toLowerCase();
    return leafId === stem || (docId !== undefined && leafId === docId);
  });
}

export function rowChildFacts(facts: ChildFacts | undefined): {
  childCount: number;
  landedCount: number;
} {
  const live = facts?.live ?? 0;
  const landed = facts?.landed ?? 0;
  return { childCount: live + landed, landedCount: landed };
}

// A row the list may reach a landed leaf from, as the marking pass below needs it.
export interface LandedParent {
  key: string;
  parentKey?: string;
  childCount: number;
  landedCount: number;
  autoCollapsed: boolean;
}

// The default collapse rule (R33.4), decided once the NON-LANDED rows exist: a row is held closed
// only when EVERY child it has is one of its own landed leaves. Two exclusions make that safe rather
// than merely bounded:
//
//   * live worktree work keeps a row open, exactly as it always was — the reader is still reviewing
//     that master's live sheets, and its landed leaves belong beside them;
//   * a row that carries OTHER rows — masters it commands (the orchestration tier), or an orphaned
//     lifecycle nested by its enclosure — is never closed by this rule. Closing it would hide other
//     masters' work behind a disclosure, which is the opposite of reachable: measured on the live
//     projection one such command row owns a 156-row subtree.
export function markAutoCollapsed(rows: LandedParent[]): void {
  const structuralChildren = new Map<string, number>();
  for (const row of rows) {
    if (!row.parentKey) continue;
    structuralChildren.set(row.parentKey, (structuralChildren.get(row.parentKey) ?? 0) + 1);
  }
  for (const row of rows) {
    row.autoCollapsed =
      row.landedCount > 0 &&
      row.childCount === row.landedCount &&
      (structuralChildren.get(row.key) ?? 0) === 0;
  }
}

// The row's collapse state as the reader sees it: an explicit collapse wins; otherwise the row's own
// default decides, and the reader's explicit open overrides that default.
export function rowIsCollapsed(
  item: Pick<LandedParent, "key" | "autoCollapsed">,
  collapse: CollapseState,
): boolean {
  if (collapse.collapsedKeys.has(item.key)) return true;
  return item.autoCollapsed && !collapse.openedKeys.has(item.key);
}

export interface LandedLeafInput {
  docs: TaskDocNode[];
  seriesList: SeriesNode[];
  docPaths: Set<string>;
  // The task-document keys that already have a row (masters, live leaves, series).
  materialized: ReadonlySet<string>;
  // The parent rows, by selection key, with their child facts already computed.
  parents: ReadonlyMap<string, LandedParent>;
  collapse: CollapseState;
}

// The landed leaves to materialize, in projection order. A leaf qualifies when it records landed
// work, its series index resolves a parent that HAS a row, and that parent is open — the reader's own
// open past the default, or the default itself while the parent still has live work. Everything else
// stays out: no parent, no row (it is not "under its master" if there is no master to be under).
export function landedLeafDocs(input: LandedLeafInput): TaskDocNode[] {
  const out: TaskDocNode[] = [];
  for (const doc of input.docs) {
    const key = taskDocSelectionKey(doc.docPath);
    if (input.materialized.has(key)) continue;
    if (!leafRecordsLandedWork(doc)) continue;
    const parentKey = taskDocParentKey(doc, input.seriesList, input.docPaths);
    const parent = parentKey ? input.parents.get(parentKey) : undefined;
    if (!parent || parent.childCount === 0) continue;
    if (rowIsCollapsed(parent, input.collapse)) continue;
    out.push(doc);
  }
  return out;
}
