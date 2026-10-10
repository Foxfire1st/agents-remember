// Same-origin client for the read-only coordination-notes API (mcp/.../serving/notes.py).
// House style mirrors data/files.ts: a `base` arg (same-origin default), typed results, the
// shared getJson error mapping, and NO store mutation — the task reader's notes view owns its
// own component state. Endpoints (all camelCase JSON):
//   /api/notes/folder?repo&master&path&offset -> one folder: entries in a stable order, in parts
//   /api/notes/read?repo&master&path          -> one note: content + language (binary-tolerant, size-capped)
//   POST /api/notes/resolve {repo, master, references} -> each reference resolved to a notes path or null
//
// MIK-R126: no call here fetches the whole tree. Reference resolution is one request
// holding the document's references; folder answers never grow with the tree.

import { getJson, qs } from "./files";

export interface FolderEntry {
  name: string;
  path: string; // posix path relative to the series' notes/ root (e.g. "reports/r.md")
  kind: "dir" | "file";
  size: number | null; // files only
  language: string; // files only
}
export interface NotesFolder {
  repo: string;
  master: string;
  path: string;
  entries: FolderEntry[];
  total: number;
  offset: number;
  limit: number;
  remaining: number;
  exists: boolean;
  reason: string | null;
}
export interface NoteContent {
  repo: string;
  master: string;
  path: string;
  language: string; // "binary" for an undecodable file (content is then empty)
  size: number;
  truncated: boolean;
  content: string;
}
export interface NoteResolution {
  reference: string;
  path: string | null;
}

export const listFolder = (
  repo: string,
  master: string,
  path = "",
  offset = 0,
  base = "",
): Promise<NotesFolder> =>
  getJson<NotesFolder>(`${base}/api/notes/folder?${qs({ repo, master, path, offset: String(offset) })}`);

export const readNote = (
  repo: string,
  master: string,
  path: string,
  base = "",
): Promise<NoteContent> =>
  getJson<NoteContent>(`${base}/api/notes/read?${qs({ repo, master, path })}`);

export const resolveNoteReferences = async (
  repo: string,
  master: string,
  references: string[],
  base = "",
): Promise<Map<string, string>> => {
  const response = await fetch(`${base}/api/notes/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo, master, references }),
  });
  const value = (await response.json().catch(() => null)) as {
    resolutions?: NoteResolution[];
  } | null;
  if (!response.ok || !value || !Array.isArray(value.resolutions)) throw new Error(`resolve ${response.status}`);
  const resolved = new Map<string, string>();
  for (const row of value.resolutions) {
    if (row.path) resolved.set(row.reference, row.path);
  }
  return resolved;
};
