// Same-origin client for the L3 read-only change-set API (mcp/.../serving/changeset.py).
// Mirrors data/files.ts: a `base` arg (same-origin default), typed results, a thrown
// FilesApiError, and NO store mutation — the Change-Set Viewer owns its component state.
// The thrown error is a `ReviewTransportError` (which IS a `FilesApiError`, so `code`/`httpStatus`
// catchers are unaffected) and it carries the route's own refusal, reason included — see
// `getChangeSetJson` below.
// Endpoints (all GET, camelCase JSON):
//   /api/changeset/task?repo&scope                 -> one active enclosure's changed code + memory + counters
//   /api/changeset/file-diff?repo&scope&kind&path  -> BEFORE + AFTER content for one changed file (MergeView a/b)
//   /api/changeset/master?repo&master              -> the master's accumulated change-set (dedup by path, sum counts)
import { qs } from "./files";
import {
  type ReviewFailure,
  ReviewTransportError,
  reviewFailureToken,
} from "./reviewTransport";

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

// The refusal bodies this route family publishes (serving/response_contract.py): `StatusRefusal`
// `{status, detail}`, `UnknownScopeRefusal` `{status, scope}`, and `MissingPathRefusal`
// `{status, path}`. For THIS family the 404's `path` carries the reason in the owner's own words --
// `str(FileNotFoundError)`, which for a missing recorded endpoint is the whole explicit refusal
// sentence (`serving/master_net_generation.py`, `changeset_endpoints.py`) -- so it is read here as a
// reason. `repo`/`scope` are the identifiers a 404 echoes back when it has no separate reason.
interface ChangeSetRefusalBody {
  status?: unknown;
  detail?: unknown;
  path?: unknown;
  repo?: unknown;
  scope?: unknown;
}

const text = (value: unknown): string | undefined =>
  typeof value === "string" && value !== "" ? value : undefined;

// `404 Not Found`, or just `404` when the response carries no reason phrase (HTTP/2, a test stub).
const statusLine = (response: Response): string =>
  `${response.status}${response.statusText ? ` ${response.statusText}` : ""}`;

// A non-2xx (or unreadable) answer as the failure it is. A body that names a refusal keeps the
// owner's code, reason and echoed identifier; a body that names none is reported as a response this
// route did not produce rather than guessed into a refusal.
function changeSetFailure(response: Response, body: ChangeSetRefusalBody | null): ReviewFailure {
  const status = statusLine(response);
  const named = text(body?.status);
  if (named === undefined) {
    return {
      token: "unreadable",
      code: status,
      detail: `the change-set route answered ${status} with a body that names no refusal, so no change-set was read`,
      httpStatus: response.status,
    };
  }
  return {
    token: reviewFailureToken(named),
    code: named,
    detail:
      text(body?.detail) ??
      text(body?.path) ??
      `the change-set route refused this read (${named}) without publishing a reason`,
    offendingInput: text(body?.repo) ?? text(body?.scope),
    httpStatus: response.status,
  };
}

// THE ONE DECODE EVERY READ IN THIS FILE MAKES, so a refusal reaches its reader instead of stopping
// at the status line. This family answers with its typed body and maps a refusal onto a 400/404
// status, so the refusal IS the body of a non-2xx response; the shared `getJson` reads only
// `body.status` and throws, which dropped the reason before any caller could see it. The body is
// therefore read whatever the status, exactly as the review client reads its own route
// (`data/reviewTransport.ts`, same doctrine and the same `ReviewFailure`/token vocabulary), and the
// thrown error stays a `FilesApiError` with the same `code`/`httpStatus`/message it always had.
async function getChangeSetJson<T>(url: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url);
  } catch (cause) {
    throw new ReviewTransportError({
      token: "network",
      code: "network",
      detail: `the change-set read could not reach the server: ${
        cause instanceof Error ? cause.message : String(cause)
      }`,
    });
  }
  const body = (await response.json().catch(() => null)) as (ChangeSetRefusalBody & T) | null;
  if (response.ok && body !== null) return body as T;
  throw new ReviewTransportError(changeSetFailure(response, body));
}

export const taskChangeset = (repo: string, scope: string, base = ""): Promise<TaskChangeset> =>
  getChangeSetJson<TaskChangeset>(`${base}/api/changeset/task?${qs({ repo, scope })}`);

export const fileDiff = (
  repo: string,
  scope: string,
  kind: "code" | "memory",
  path: string,
  base = "",
): Promise<FileDiff> =>
  getChangeSetJson<FileDiff>(`${base}/api/changeset/file-diff?${qs({ repo, scope, kind, path })}`);

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
  return getChangeSetJson<MasterChangeset>(`${base}/api/changeset/master?${qs(params)}`);
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
  return getChangeSetJson<FileDiff>(`${base}/api/changeset/file-diff?${qs(params)}`);
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
  getChangeSetJson<TaskChangeset>(`${base}/api/changeset/task?${qs({ repo, master, leaf, mode })}`);

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
  getChangeSetJson<FileDiff>(`${base}/api/changeset/file-diff?${qs({ repo, master, leaf, kind, path, mode })}`);
