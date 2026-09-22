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
// <selected-result>` for code + memory (so each file is inspectable, unlike a sum of leaves),
// bound to a generation with a deterministic digest and currentness, plus a per-leaf counter
// breakdown alongside (each row labelled committed/working).
export interface MasterNetPins {
  codeBase?: string;
  codeTip?: string;
  memoryBase?: string;
  memoryTip?: string;
}
export interface MasterNetGeneration {
  codeBase: string;
  codeTip: string;
  memoryBase: string;
  memoryTip: string;
  digest: string;
}
export interface MasterChangeset {
  master: string;
  leaves: {
    leafId: string;
    state?: "committed" | "working";
    counters: { code: ChangeCounters; memory: ChangeCounters };
  }[];
  code: ChangedFile[];
  memory: ChangedFile[];
  counters: { code: ChangeCounters; memory: ChangeCounters };
  generation?: MasterNetGeneration | null;
  currentness?: "current" | "superseded" | "unmeasured";
  scope?: "integrated";
}
export interface MasterChangesetOptions {
  includeLeaves?: boolean;
  pins?: MasterNetPins;
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
  options: MasterChangesetOptions = {},
  base = "",
): Promise<MasterChangeset> => {
  const params: Record<string, string> = { repo, master };
  if (options.includeLeaves !== undefined) {
    params.includeLeaves = String(options.includeLeaves);
  }
  // Generation pins freeze the net to the listed generation; unset pins are omitted so the
  // request selects the declared integrated result.
  for (const [key, value] of Object.entries(options.pins ?? {})) {
    if (value) params[key] = value;
  }
  return getJson<MasterChangeset>(`${base}/api/changeset/master?${qs(params)}`);
};

// `masterFileDiff` — BEFORE (master base) + AFTER (selected result) content for one file in the
// net series diff. Same /api/changeset/file-diff route, with `master` instead of an enclosure
// `scope`. `pins` binds the AFTER side to the generation the listing published, so an opened
// entry stays bound after the branch advances.
export const masterFileDiff = (
  repo: string,
  master: string,
  kind: "code" | "memory",
  path: string,
  pins: MasterNetPins = {},
  base = "",
): Promise<FileDiff> => {
  const params: Record<string, string> = { repo, master, kind, path };
  for (const [key, value] of Object.entries(pins)) {
    if (value) params[key] = value;
  }
  return getJson<FileDiff>(`${base}/api/changeset/file-diff?${qs(params)}`);
};

// L4a leaf views — a single leaf's change-set straight off its enclosure contract (so it works
// with NO live worktree, unlike `taskChangeset`'s scope). `mode`:
//   committed — the leaf's LANDED delta (base -> code_commit); always available.
//   working   — the UNCOMMITTED delta only (worktree HEAD -> dirty tree); live enclosures only.
// `master` qualifies the leaf (scopes the contract search to one series). Returns the
// `taskChangeset` shape (the extra `mode` echo is harmless), so the viewer renders it unchanged.
export type LeafMode = "committed" | "working";

export const leafChangeset = (
  repo: string,
  master: string,
  leaf: string,
  mode: LeafMode,
  base = "",
): Promise<TaskChangeset> =>
  getJson<TaskChangeset>(`${base}/api/changeset/task?${qs({ repo, master, leaf, mode })}`);

// `leafFileDiff` — BEFORE + AFTER for one file in a leaf's committed/working change-set. Same
// /api/changeset/file-diff route, with `leaf` + `mode` (+ the qualifying `master`).
export const leafFileDiff = (
  repo: string,
  master: string,
  leaf: string,
  kind: "code" | "memory",
  path: string,
  mode: LeafMode,
  base = "",
): Promise<FileDiff> =>
  getJson<FileDiff>(`${base}/api/changeset/file-diff?${qs({ repo, master, leaf, kind, path, mode })}`);
