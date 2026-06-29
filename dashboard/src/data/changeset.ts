// Same-origin client for the L3 read-only change-set API (mcp/.../serving/changeset.py).
// Mirrors data/files.ts: a `base` arg (same-origin default), typed results, a thrown
// FilesApiError, and NO store mutation — the Change-Set Viewer owns its component state.
// Endpoints (all GET, camelCase JSON):
//   /api/changeset/task?repo&scope                 -> one active enclosure's changed code + memory + counters
//   /api/changeset/file-diff?repo&scope&kind&path  -> BEFORE + AFTER content for one changed file (MergeView a/b)
//   /api/changeset/master?repo&master              -> the master's accumulated change-set (dedup by path, sum counts)
import { getJson, qs } from "./files";

// One changed file: insertion/deletion counts (null for binary) + the git status letter
// (A/M/D/R). `hasSidecar` (code files) drives the L4 code->sidecar split affordance.
export interface ChangedFile {
  path: string;
  insertions: number | null;
  deletions: number | null;
  status: string;
  hasSidecar?: boolean;
}
// One changed set's totals: file count + summed insertions/deletions (binary -> 0 server-side).
export interface ChangeCounters {
  files: number;
  insertions: number;
  deletions: number;
}
// `taskChangeset` — one active enclosure's base->worktree code + memory change-set.
export interface TaskChangeset {
  scope: string;
  code: ChangedFile[];
  memory: ChangedFile[];
  counters: { code: ChangeCounters; memory: ChangeCounters };
}
// `fileDiff` — BEFORE (base) + AFTER (current) content for one file: feeds CodeMirror MergeView
// a=before, b=after. `before` is null for an added file, `after` is null for a deleted one.
export interface FileDiff {
  scope: string;
  kind: "code" | "memory";
  path: string;
  language: string;
  before: { content: string } | null;
  after: { content: string } | null;
}
// `masterChangeset` — the series master's NET change-set: the single `git diff <master-base> ->
// <series-tip>` for code + memory (so each file is inspectable, unlike a sum of leaves), plus a
// per-leaf counter breakdown alongside.
export interface MasterChangeset {
  master: string;
  leaves: { leafId: string; counters: { code: ChangeCounters; memory: ChangeCounters } }[];
  code: ChangedFile[];
  memory: ChangedFile[];
  counters: { code: ChangeCounters; memory: ChangeCounters };
}

export const taskChangeset = (repo: string, scope: string, base = ""): Promise<TaskChangeset> =>
  getJson<TaskChangeset>(`${base}/api/changeset/task?${qs({ repo, scope })}`);

export const fileDiff = (
  repo: string,
  scope: string,
  kind: "code" | "memory",
  path: string,
  base = "",
): Promise<FileDiff> =>
  getJson<FileDiff>(`${base}/api/changeset/file-diff?${qs({ repo, scope, kind, path })}`);

export const masterChangeset = (
  repo: string,
  master: string,
  base = "",
): Promise<MasterChangeset> =>
  getJson<MasterChangeset>(`${base}/api/changeset/master?${qs({ repo, master })}`);

// `masterFileDiff` — BEFORE (master base) + AFTER (series tip) content for one file in the net
// series diff. Same /api/changeset/file-diff route, with `master` instead of an enclosure `scope`.
export const masterFileDiff = (
  repo: string,
  master: string,
  kind: "code" | "memory",
  path: string,
  base = "",
): Promise<FileDiff> =>
  getJson<FileDiff>(`${base}/api/changeset/file-diff?${qs({ repo, master, kind, path })}`);
