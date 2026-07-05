// Same-origin client for the L9 read-only coordination-notes API (mcp/.../serving/notes.py).
// House style mirrors data/files.ts: a `base` arg (same-origin default), typed results, the
// shared getJson error mapping, and NO store mutation — the task reader's notes view owns its
// own component state. Endpoints (all GET, camelCase JSON):
//   /api/notes/list?repo&master        -> every note file under tasks/<repo>/<master>/notes/
//   /api/notes/read?repo&master&path   -> one note: content + language (binary-tolerant, size-capped)

import { getJson, qs } from "./files";

export interface NoteEntry {
  name: string;
  path: string; // posix path relative to the series' notes/ root (e.g. "reports/…-report.md")
  size: number;
  language: string;
}
export interface NotesListing {
  repo: string;
  master: string;
  notes: NoteEntry[];
  truncated: boolean; // the server's walk depth cap pruned something — the list is honest about it
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

export const listNotes = (repo: string, master: string, base = ""): Promise<NotesListing> =>
  getJson<NotesListing>(`${base}/api/notes/list?${qs({ repo, master })}`);

export const readNote = (
  repo: string,
  master: string,
  path: string,
  base = "",
): Promise<NoteContent> =>
  getJson<NoteContent>(`${base}/api/notes/read?${qs({ repo, master, path })}`);

// A task-doc reference is free prose that may NAME a notes file — "notes/friction-ledger.md
// (F-M — the finding this leaf closes)". Path-like tokens: contiguous path characters ending
// in a dotted extension, so surrounding prose never bleeds into the candidate.
const PATH_TOKEN = /[\w./-]+\.[A-Za-z0-9]+/g;

// Resolve a reference string against the series' note paths: the first token that names an
// existing notes file — by notes-relative path (with or without the "notes/" prefix) or by an
// UNAMBIGUOUS bare filename — wins. Anything else resolves to undefined, so a non-matching
// reference stays plain text (never a dead link, never a guessed one).
export function resolveNoteReference(
  reference: string,
  notePaths: readonly string[],
): string | undefined {
  for (const token of reference.match(PATH_TOKEN) ?? []) {
    const relative = token.startsWith("notes/") ? token.slice("notes/".length) : token;
    if (notePaths.includes(relative)) return relative;
    if (!token.includes("/")) {
      const byName = notePaths.filter((path) => path.split("/").pop() === token);
      if (byName.length === 1) return byName[0];
    }
  }
  return undefined;
}
