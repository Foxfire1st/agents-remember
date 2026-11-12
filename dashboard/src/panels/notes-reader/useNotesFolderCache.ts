import { useCallback, useRef, useState } from "react";

import { listFolder, type FolderEntry } from "../../data/notes";
import { type ExplorerRow } from "../../grammar/ExplorerTree";

// A next-part row is a synthetic leaf row: its path carries the folder and the next offset.
const MORE_MARKER = "\0more:";

export function isMoreRow(row: ExplorerRow): boolean {
  return row.path.includes(MORE_MARKER);
}

function moreFolder(path: string): string {
  return path.split(MORE_MARKER)[0];
}

export function ancestorsOf(path: string): string[] {
  const parts = path.split("/");
  parts.pop();
  const ancestors: string[] = [];
  for (let depth = 1; depth <= parts.length; depth++) ancestors.push(parts.slice(0, depth).join("/"));
  return ancestors;
}

function toRows(entries: FolderEntry[]): ExplorerRow[] {
  return entries.map((entry) => ({ path: entry.path, name: entry.name, kind: entry.kind }));
}

// One loaded folder's accumulated parts, so the next-part row appends without refetching.
// `gone` carries the server's reason when the folder vanished; null while present.
interface FolderCache {
  entries: ExplorerRow[];
  nextOffset: number | null; // null when the folder answered whole
  gone: string | null;
}

// The working set one retained reader lifetime may hold. Each reply is already capped
// at 200 rows; this caps how many folders stay cached, evicting least-recently-used
// folders that do not hold the open note. The number is stated here and pinned by the
// eviction tests; no elapsed-time threshold is imposed.
const MAX_CACHED_FOLDERS = 32;

function parentOf(note: string): string {
  const slash = note.lastIndexOf("/");
  return slash < 0 ? "" : note.slice(0, slash);
}

interface FolderPage {
  entries: FolderEntry[];
  total: number;
}

// One fetched part merged onto the settled rows: the settled rows first, then the new
// rows, then a next-part row while the folder still has more. Shared by the initial
// load, the open note's paging and the next-part handler, so all three agree on shape.
function accumulateFolderEntries(
  settled: ExplorerRow[],
  folder: FolderPage,
  directory: string,
  offset: number,
): { entries: ExplorerRow[]; nextOffset: number | null } {
  const kept = settled.filter((row) => !isMoreRow(row));
  const entries = [...kept, ...toRows(folder.entries)];
  const remaining = folder.total - offset - folder.entries.length;
  if (remaining > 0) {
    entries.push({
      path: `${directory}${MORE_MARKER}${offset + folder.entries.length}`,
      name: `load next (${remaining} more)`,
      kind: "file",
    });
  }
  return { entries, nextOffset: remaining > 0 ? offset + folder.entries.length : null };
}

// Folders on the way to the open note are never evicted; everything else is
// least-recently-used (Map insertion order, refreshed on every access).
function evictFolders(folders: Map<string, FolderCache>, directory: string, open: string | undefined): void {
  const pinned = new Set(open ? ancestorsOf(open) : []);
  while (folders.size > MAX_CACHED_FOLDERS) {
    const oldest = folders.keys().next().value as string | undefined;
    if (oldest === undefined) break;
    if (oldest !== directory && !pinned.has(oldest)) {
      folders.delete(oldest);
      continue;
    }
    const victim = [...folders.keys()].find((key) => key !== directory && !pinned.has(key));
    if (victim === undefined) break;
    folders.delete(victim);
  }
}

function insertFolderInto(
  folders: Map<string, FolderCache>,
  directory: string,
  cache: FolderCache,
  open: string | undefined,
): void {
  folders.delete(directory);
  folders.set(directory, cache);
  evictFolders(folders, directory, open);
}

interface FolderStore {
  folders: Map<string, FolderCache>;
  currentRef: { current: string | undefined };
  insertFolder: (directory: string, cache: FolderCache) => void;
  touchFolder: (directory: string, cache: FolderCache) => void;
  markGone: (directory: string, reason: string) => void;
}

// The retained folder map with its refresh-safe writers. The writers touch only the
// stable map and the live-note ref, so they are stable callbacks of the tree's lifetime.
function useFolderStore(current: string | undefined): FolderStore {
  const folders = useRef(new Map<string, FolderCache>()).current;
  // loadChildren runs without `current` in scope; the ref carries the live address.
  const currentRef = useRef(current);
  currentRef.current = current;
  const insertFolder = useCallback(
    (directory: string, cache: FolderCache) => {
      insertFolderInto(folders, directory, cache, currentRef.current);
    },
    [folders],
  );
  const touchFolder = useCallback(
    (directory: string, cache: FolderCache) => {
      folders.delete(directory);
      folders.set(directory, cache);
    },
    [folders],
  );
  const markGone = useCallback(
    (directory: string, reason: string) => {
      insertFolder(directory, { entries: [], nextOffset: null, gone: reason });
    },
    [insertFolder],
  );
  return { folders, currentRef, insertFolder, touchFolder, markGone };
}

interface FolderOps {
  insertFolder: (directory: string, cache: FolderCache) => void;
  touchFolder: (directory: string, cache: FolderCache) => void;
  markGone: (directory: string, reason: string) => void;
}

// One bounded part appended to its folder's cache. Answers the gone reason when the
// folder vanished, null when the part landed; a network failure rejects.
async function appendFolderPart(
  folders: Map<string, FolderCache>,
  repo: string,
  master: string,
  directory: string,
  offset: number,
  ops: FolderOps,
): Promise<string | null> {
  const folder = await listFolder(repo, master, directory, offset);
  if (!folder.exists) {
    const reason = folder.reason ?? "that folder is gone";
    ops.markGone(directory, reason);
    return reason;
  }
  const cached = folders.get(directory);
  const { entries, nextOffset } = accumulateFolderEntries(cached?.entries ?? [], folder, directory, offset);
  if (cached) {
    cached.entries = entries;
    cached.nextOffset = nextOffset;
    ops.touchFolder(directory, cached);
  } else {
    ops.insertFolder(directory, { entries, nextOffset, gone: null });
  }
  return null;
}

// The paged reads over the store: one part at a time for the open note's folder, and
// whole levels for the tree. Every callback lists only the stable store writers plus
// the repo and master props, so the tree re-reads only when its address changes.
function useFolderPaging(repo: string, master: string, store: FolderStore): {
  loadChildren: (directory: string) => Promise<ExplorerRow[]>;
  fillToCurrent: (directory: string) => Promise<void>;
  appendPart: (directory: string, offset: number) => Promise<string | null>;
} {
  const { folders, currentRef, insertFolder, touchFolder, markGone } = store;
  const appendPart = useCallback(
    (directory: string, offset: number) =>
      appendFolderPart(folders, repo, master, directory, offset, { insertFolder, touchFolder, markGone }),
    [folders, insertFolder, markGone, master, repo, touchFolder],
  );
  // The open note may live beyond the loaded parts of its folder: fetch successive
  // parts until its row exists or the folder is exhausted. Each reply stays bounded;
  // only the open note's own folder is touched, unrelated folders stay closed.
  const fillToCurrent = useCallback(
    async (directory: string): Promise<void> => {
      const wanted = currentRef.current;
      if (!wanted || parentOf(wanted) !== directory) return;
      const cached = folders.get(directory);
      while (
        cached &&
        cached.gone === null &&
        cached.nextOffset !== null &&
        currentRef.current === wanted &&
        !cached.entries.some((row) => !isMoreRow(row) && row.path === wanted)
      ) {
        const gone = await appendPart(directory, cached.nextOffset ?? 0);
        if (gone !== null) throw new Error(gone);
      }
    },
    [appendPart, currentRef, folders],
  );
  const loadChildren = useCallback(
    async (directory: string): Promise<ExplorerRow[]> => {
      // Refresh-safe: the tree re-asks loaded folders after a revision bump, and the
      // cache answers without refetching. Next parts are fetched only by the more-row
      // handler, which accumulates into the same cache before refreshing.
      const cached = folders.get(directory);
      if (cached) {
        if (cached.gone !== null) throw new Error(cached.gone);
        touchFolder(directory, cached);
        await fillToCurrent(directory);
        return folders.get(directory)?.entries ?? cached.entries;
      }
      const folder = await listFolder(repo, master, directory, 0);
      if (!folder.exists) {
        const reason = folder.reason ?? "that folder is gone";
        markGone(directory, reason);
        throw new Error(reason);
      }
      const { entries, nextOffset } = accumulateFolderEntries([], folder, directory, 0);
      const cache: FolderCache = { entries, nextOffset, gone: null };
      insertFolder(directory, cache);
      await fillToCurrent(directory);
      return folders.get(directory)?.entries ?? cache.entries;
    },
    [fillToCurrent, folders, insertFolder, markGone, master, repo, touchFolder],
  );
  return { loadChildren, fillToCurrent, appendPart };
}

// The tree's folder cache: loaded folders with their accumulated parts, answering from
// the cache on refresh and fetching one bounded part per demand. loadChildren feeds the
// shared ExplorerTree; openEntry opens a file row or appends a next part, then bumps the
// revision so the tree re-reads the accumulated cache.
export function useNotesFolderCache(
  repo: string,
  master: string,
  current: string | undefined,
  onSelectNote: (path: string) => void,
): {
  loadChildren: (directory: string) => Promise<ExplorerRow[]>;
  openEntry: (entry: ExplorerRow) => void;
  revision: number;
} {
  const store = useFolderStore(current);
  const { loadChildren, appendPart } = useFolderPaging(repo, master, store);
  const [revision, setRevision] = useState(0);
  // A next-part row appends the next part to its folder, then refreshes the tree, which
  // re-reads the accumulated cache; a file row opens its note. A vanished folder empties
  // its cached children and keeps the server's reason; the refresh then surfaces it.
  const openEntry = useCallback(
    (entry: ExplorerRow) => {
      if (isMoreRow(entry)) {
        const directory = moreFolder(entry.path);
        const cached = store.folders.get(directory);
        if (!cached || cached.nextOffset === null || cached.gone !== null) return;
        void appendPart(directory, cached.nextOffset).then(
          () => setRevision((n: number) => n + 1),
          () => {},
        );
        return;
      }
      if (entry.kind === "file") onSelectNote(entry.path);
    },
    [appendPart, onSelectNote, store.folders],
  );
  return { loadChildren, openEntry, revision };
}
