// Shared master/sprint index row identities, navigation and status grouping.
import { taskDocSelectionKey } from '../../data/taskIdentity';
import { stripExt } from '../../data/taskHierarchy';
import type { SubTaskRow, TaskDocNode, TaskDocumentRef } from '../../types/projection';
import { shortDocId, sliceForRef, sliceSlug, sprintRowStatus } from './model';
import { RowLink } from './pageParts';

export interface IndexRowHandlers {
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  sliceForRef?: (ref: SubTaskRow) => TaskDocNode | undefined;
}

// Call-local first-match index, with exactly the legacy slug/file spelling.
export function indexSlices(docs: TaskDocNode[]): (ref: SubTaskRow) => TaskDocNode | undefined {
  const bySlug = new Map<string, TaskDocNode>();
  for (const doc of docs) {
    const slug = sliceSlug(doc);
    if (!bySlug.has(slug)) bySlug.set(slug, doc);
  }
  return (ref) => (ref.file ? bySlug.get(stripExt(ref.file)) : undefined);
}

export function indexedRows(handlers: IndexRowHandlers): IndexRowHandlers {
  return { ...handlers, sliceForRef: indexSlices(handlers.sliceDocs) };
}

export interface RowTarget {
  testid: string;
  run: () => void;
  title?: string;
}

// One clickable index target shared by every row: a commanded master opens
// directly, a linked series opens by id, an authored slice opens by slug.
export function resolveRowTarget(
  ref: SubTaskRow,
  position: number,
  handlers: IndexRowHandlers,
): RowTarget | null {
  const masterRef = 'masterRef' in ref ? ref.masterRef : undefined;
  const masterPath =
    masterRef && handlers.docPathForRef ? handlers.docPathForRef(masterRef) : undefined;
  if (masterRef && masterPath) {
    return {
      testid: `subtask-open-master-${position}`,
      run: () => handlers.onJump(taskDocSelectionKey(masterPath)),
      title: `open the ${masterPath} master document`,
    };
  }
  const linkedLifecycleId = 'linkedLifecycleId' in ref ? ref.linkedLifecycleId : undefined;
  if (linkedLifecycleId) {
    return {
      testid: `subtask-open-link-${position}`,
      run: () => handlers.onJump(linkedLifecycleId),
      title: `open the ${linkedLifecycleId} series`,
    };
  }
  const match = handlers.sliceForRef
    ? handlers.sliceForRef(ref)
    : sliceForRef(handlers.sliceDocs, ref);
  if (match) {
    const slug = sliceSlug(match);
    return { testid: `subtask-open-${position}`, run: () => handlers.onOpen(slug) };
  }
  return null;
}

// The id and the name of an index row as one link; a row that leads nowhere
// yet keeps the same two cells and says why.
export function IndexLink({
  id,
  name,
  ref,
  position,
  handlers,
}: {
  id: string;
  name: string;
  ref: SubTaskRow;
  position: number;
  handlers: IndexRowHandlers;
}) {
  const target = resolveRowTarget(ref, position, handlers);
  return (
    <RowLink
      id={id}
      name={name}
      onOpen={target?.run}
      testid={target?.testid ?? `subtask-open-${position}`}
      title={target ? (target.title ?? `${id} — ${name}`) : 'not authored as a task document yet'}
    />
  );
}

export function fullOrderPositions(refs: SubTaskRow[]): Map<SubTaskRow, number> {
  return new Map(refs.map((ref, index) => [ref, index + 1] as const));
}

interface TaskRowFacts {
  id: string;
  name: string;
  match: TaskDocNode | undefined;
}

export function taskRowFacts(
  ref: SubTaskRow,
  sliceDocs: TaskDocNode[],
  parentId: string,
): TaskRowFacts {
  const match = sliceForRef(sliceDocs, ref);
  return {
    id: shortDocId(match?.id || ref.number, parentId),
    name: match?.title || ref.name,
    match,
  };
}

export function resolveSprintMaster(
  ref: SubTaskRow,
  allDocs: TaskDocNode[],
  docPathForRef: ((ref: TaskDocumentRef) => string | undefined) | undefined,
): TaskDocNode | undefined {
  const masterRef = 'masterRef' in ref ? ref.masterRef : undefined;
  if (!masterRef || !docPathForRef) return undefined;
  const path = docPathForRef(masterRef);
  return path ? allDocs.find((doc) => doc.docPath === path) : undefined;
}

export interface SprintEntry {
  ref: SubTaskRow;
  masterDoc: TaskDocNode | undefined;
}

// Every sprint index row with its resolution (rule 3): rows resolving to a
// commanded master show the master's own status; seat rows and rows whose
// master is not projected keep the row as written and stand with their status
// group — and in the rows view — never hidden.
export function sprintEntries(
  doc: Pick<TaskDocNode, 'subTasks'>,
  allDocs: TaskDocNode[],
  docPathForRef: ((ref: TaskDocumentRef) => string | undefined) | undefined,
): SprintEntry[] {
  return doc.subTasks.map((ref) => ({
    ref,
    masterDoc: resolveSprintMaster(ref, allDocs, docPathForRef),
  }));
}

export function sprintEntryStatus(entry: SprintEntry): string {
  return entry.masterDoc ? sprintRowStatus(entry.ref, entry.masterDoc) : entry.ref.status;
}

export interface SprintGroups {
  work: SprintEntry[];
  waiting: SprintEntry[];
  done: SprintEntry[];
  gone: SprintEntry[];
}

export function groupSprintEntries(entries: SprintEntry[]): SprintGroups {
  return {
    work: entries.filter((entry) => sprintEntryStatus(entry) === 'inProgress'),
    waiting: entries.filter((entry) => {
      const status = sprintEntryStatus(entry);
      return status !== 'inProgress' && status !== 'Completed' && status !== 'abandoned';
    }),
    done: entries.filter((entry) => sprintEntryStatus(entry) === 'Completed'),
    gone: entries.filter((entry) => sprintEntryStatus(entry) === 'abandoned'),
  };
}

export function masterRowFacts(entry: SprintEntry): { id: string; name: string } {
  return {
    id: entry.masterDoc?.id ?? entry.ref.number,
    name: entry.masterDoc?.title ?? entry.ref.name,
  };
}
