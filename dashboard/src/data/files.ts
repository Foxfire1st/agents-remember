// Same-origin client for the L1 read-only files API (mcp/.../serving/files.py).
// House style mirrors data/stream.ts / data/terminal.ts: a `base` arg (same-origin
// default), typed results, a thrown status error, and NO store mutation — the File
// Viewer owns its own component state. Endpoints (all GET, camelCase JSON):
//   /api/files/repos                                  -> the repo + {mainline ∪ enclosures} catalog
//   /api/files/list?repo&scope&path                   -> one dir level: code[] + onboarding[]
//   /api/files/read?repo&scope&path                   -> one file: content + language + onboarding meta
//   /api/files/onboarding?repo&scope&path&direction   -> 1:1 pairing (forward: code->sidecar; reverse: sidecar->code)

export type Scope = string; // "mainline" | a worktree-group basename

export interface RepoScope {
  scope: Scope;
  label: string;
  branch?: string | null;
  leafId?: string | null;
  taskName?: string;
}
export interface RepoCatalogEntry {
  repo: string;
  mainline: { scope: "mainline"; label: string; exists: boolean };
  worktrees: RepoScope[];
}
export interface RepoCatalog {
  repos: RepoCatalogEntry[];
}

export interface DirEntry {
  name: string;
  path: string;
  kind: "file" | "dir";
  language?: string; // code files only
  hasSidecar?: boolean; // code files only
}
export interface DirListing {
  scope: Scope;
  dir: string;
  code: DirEntry[];
  onboarding: DirEntry[];
}

export interface OnboardingMeta {
  status: "found" | "missing";
  path?: string;
  lastVerifiedCommitHash?: string;
  lastVerifiedCommitDate?: string;
}
export interface FileContent {
  scope: Scope;
  path: string;
  language: string;
  size: number;
  truncated: boolean;
  content: string;
  onboarding: OnboardingMeta;
}

// Forward pairing: a code path -> its sidecar (status + markdown body).
export interface ForwardPairing extends OnboardingMeta {
  scope: Scope;
  codePath: string;
  body: string | null;
}
// Reverse pairing: a sidecar -> its partner code path, an overview node, or nothing.
export type ReversePairing =
  | { scope: Scope; onboardingPath: string; kind: "sidecar"; codePath: string; exists: boolean }
  // A partnerless overview/entities/index doc carries its own markdown so the reader can render it.
  | { scope: Scope; onboardingPath: string; kind: "overview"; route: string; body: string | null }
  | { scope: Scope; onboardingPath: string; kind: "none" };

// The serving layer maps domain errors to a status-string JSONResponse
// (404 unknown-repo/unknown-scope/not-found, 400 bad-path). Surface that to the UI.
export class FilesApiError extends Error {
  constructor(
    readonly httpStatus: number,
    readonly code: string,
  ) {
    super(`${httpStatus} ${code}`);
    this.name = "FilesApiError";
  }
}

// Shared by the L1 files client and the L3 change-set client (data/changeset.ts) so the
// serving error idiom (404 unknown-repo/unknown-scope/not-found, 400 bad-path) is mapped once.
export async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { status?: string };
    throw new FilesApiError(res.status, body.status ?? res.statusText);
  }
  return (await res.json()) as T;
}

export const qs = (params: Record<string, string>): string =>
  new URLSearchParams(params).toString();

export const fetchRepos = (base = ""): Promise<RepoCatalog> =>
  getJson<RepoCatalog>(`${base}/api/files/repos`);

export const listDir = (repo: string, scope: Scope, path = "", base = ""): Promise<DirListing> =>
  getJson<DirListing>(`${base}/api/files/list?${qs({ repo, scope, path })}`);

export const readFile = (
  repo: string,
  scope: Scope,
  path: string,
  base = "",
): Promise<FileContent> => getJson<FileContent>(`${base}/api/files/read?${qs({ repo, scope, path })}`);

export const resolveForward = (
  repo: string,
  scope: Scope,
  path: string,
  base = "",
): Promise<ForwardPairing> =>
  getJson<ForwardPairing>(
    `${base}/api/files/onboarding?${qs({ repo, scope, path, direction: "forward" })}`,
  );

export const resolveReverse = (
  repo: string,
  scope: Scope,
  path: string,
  base = "",
): Promise<ReversePairing> =>
  getJson<ReversePairing>(
    `${base}/api/files/onboarding?${qs({ repo, scope, path, direction: "reverse" })}`,
  );
