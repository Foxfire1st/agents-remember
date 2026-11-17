// Shared fixtures for the notes reader's two test files: the API stubs and the note bodies.
// Keeping them out of each test file is what lets the content pane and the tree rail stay
// separately readable while using the same wire shapes.
import { vi } from 'vitest';

import { dashboardStore } from '../../data/store';
import type { FolderEntry, NoteContent } from '../../data/notes';

export const REPO = 'agents-remember';
export const MASTER = '260703_agent-orchestration';

export function entry(path: string, over: Partial<FolderEntry> = {}): FolderEntry {
  const isDir = path.endsWith('/');
  const clean = isDir ? path.slice(0, -1) : path;
  return {
    name: clean.split('/').pop() ?? clean,
    path: clean,
    kind: isDir ? 'dir' : 'file',
    size: isDir ? null : 100,
    language: isDir ? '' : 'markdown',
    ...over,
  };
}

export function content(path: string, over: Partial<NoteContent> = {}): NoteContent {
  return {
    repo: REPO,
    master: MASTER,
    path,
    language: 'markdown',
    size: 100,
    truncated: false,
    content: `# ${path}`,
    ...over,
  };
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200 });
}

function folderAnswer(
  folder: string,
  offset: number,
  pageSize: number,
  folders: Record<string, FolderEntry[]>,
  opts: { gone?: Record<string, string>; vanish?: { folder: string; fromOffset: number; reason: string } },
): Response {
  const goneReason = opts.gone?.[folder];
  if (goneReason !== undefined) {
    return json({ repo: REPO, master: MASTER, path: folder, entries: [], total: 0, offset, limit: 200, remaining: 0, exists: false, reason: goneReason });
  }
  if (opts.vanish && opts.vanish.folder === folder && offset >= opts.vanish.fromOffset) {
    return json({ repo: REPO, master: MASTER, path: folder, entries: [], total: 0, offset, limit: 200, remaining: 0, exists: false, reason: opts.vanish.reason });
  }
  const all = folders[folder] ?? [];
  const page = all.slice(offset, offset + pageSize);
  return json({ repo: REPO, master: MASTER, path: folder, entries: page, total: all.length, offset, limit: 200, remaining: Math.max(0, all.length - offset - page.length), exists: true, reason: null });
}

function taskAnswer(params: URLSearchParams): Response {
  const docPath = params.get('path') ?? '';
  const doc = dashboardStore.getState().tasks[docPath]?.summary;
  return new Response(JSON.stringify(doc ?? null), { status: doc ? 200 : 404 });
}

function readAnswer(contents: Record<string, NoteContent>, params: URLSearchParams): Response {
  const body = contents[params.get('path') ?? ''];
  return body ? json(body) : new Response(JSON.stringify({ status: 'not-found' }), { status: 404 });
}

function resolveAnswer(resolve: Record<string, string> | undefined): Response {
  return json({
    repo: REPO,
    master: MASTER,
    resolutions: Object.entries(resolve ?? {}).map(([reference, path]) => ({ reference, path })),
  });
}

function reportsAnswer(): Response {
  return json({ repo: REPO, master: MASTER, doc: '', reports: [], total: 0, explorer: null });
}

// /api/notes/folder → the seeded level in parts of two (so the next-part row is exercised);
// /api/notes/read → the seeded per-path content (404 otherwise); /api/notes/resolve and
// /api/role-launch/reports answer empty so task surfaces stay inert.
// opts.gone answers exists:false with the given reason for those folders; opts.vanish
// answers exists:false from the given offset on (a folder disappearing mid-pagination);
// opts.pageSize overrides the part size.
export function stubNotesApi(
  folders: Record<string, FolderEntry[]>,
  contents: Record<string, NoteContent> = {},
  opts: {
    gone?: Record<string, string>;
    vanish?: { folder: string; fromOffset: number; reason: string };
    pageSize?: number;
    resolve?: Record<string, string>;
  } = {},
) {
  const urls: string[] = [];
  const pageSize = opts.pageSize ?? 2;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      urls.push(url);
      const params = new URLSearchParams(url.split('?')[1] ?? '');
      if (url.startsWith('/api/task-document')) return taskAnswer(params);
      if (url.startsWith('/api/notes/folder')) {
        return folderAnswer(params.get('path') ?? '', Number(params.get('offset') ?? '0'), pageSize, folders, opts);
      }
      if (url.startsWith('/api/notes/read')) return readAnswer(contents, params);
      if (url.startsWith('/api/notes/resolve')) return resolveAnswer(opts.resolve);
      if (url.startsWith('/api/role-launch/reports')) return reportsAnswer();
      // Unrelated cockpit fetches (e.g. the hidden File Viewer layer's repo catalog) — a `{repos:[]}`
      // shape keeps that panel happy without a network hit.
      return json({ repos: [] });
    }),
  );
  return urls;
}

export function stubRequirementsApi(path: string, contentBody: string) {
  const fetch = vi.fn(async (url: string) => {
    const params = new URLSearchParams(url.split('?')[1] ?? '');
    if (url.startsWith('/api/requirements/list')) {
      return json({
        repo: REPO,
        master: MASTER,
        document: params.get('document'),
        registered: true,
        requirements: [{ name: path, path, address: `requirements/${path}`, size: 100, sha256: 'abc' }],
      });
    }
    if (url.startsWith('/api/requirements/read')) {
      return json({
        repo: REPO,
        master: MASTER,
        document: params.get('document'),
        name: path,
        path,
        address: `requirements/${path}`,
        size: contentBody.length,
        sha256: 'abc',
        content: contentBody,
      });
    }
    return new Response(JSON.stringify({ status: 'not-found' }), { status: 404 });
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}
