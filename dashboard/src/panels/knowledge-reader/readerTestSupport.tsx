// MIK-R29 on real data: the Knowledge reader's views over the REAL served answers of
// /api/knowledge/reader for a converted scratch copy of the real repositories (provenance: the
// `_provenance` key of knowledgeReader.captured.json). The memory was converted by L24's
// knowledge-convert; the decisions, incident, family routes, history rows, proof and census on top
// of it are SCRATCH-AUTHORED (their text says so), because converted memory holds none yet.
// `KnowledgeReader` is the real component; only `fetch` is stubbed.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';

import { readerHash, type ReaderAddress } from '../../data/knowledgeReader';
import { KnowledgeReader } from './KnowledgeReader';

export type Body = Record<string, unknown> & { state: string };
export const captured = JSON.parse(
  readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), 'knowledgeReader.captured.json'),
    'utf8',
  ),
) as Record<string, Body>;

export const REPO = 'agents-remember';
export const WORKTREES = 'mcp/src/agents_remember/worktrees';
export const INTEGRATE = `${WORKTREES}/modules/integrate.py`;
export const LEDGER_TEST = 'mcp/tests/test_memory_ledger.py';

export let requests: URL[] = [];
export let overrides: Record<string, Body> = {};
// Tree listings held back until the test releases them (the stale-response test, review F13).
export let heldTree: { commit: string; waiting: (() => void)[] } | null = null;

function readerOverride(url: URL, view: string, key: string) {
  if (url.searchParams.get('continuation') && overrides[`${view}:next`])
    return overrides[`${view}:next`];
  return overrides[key];
}

function emptyTree(url: URL): Body | null {
  return url.pathname.endsWith('/tree')
    ? {
        state: 'view',
        directory: url.searchParams.get('path'),
        code: { state: 'listed' },
        children: [],
      }
    : null;
}

function bodyFor(url: URL): Body | null {
  const view = url.pathname.split('/').pop() ?? '';
  const key = `${view}:${url.searchParams.get('path') ?? url.searchParams.get('id') ?? ''}`;
  const override = readerOverride(url, view, key);
  if (override) return override;
  const pathBodies: Record<string, string> = {
    '.': 'path-root',
    [WORKTREES]: 'path-worktrees-dir',
    [INTEGRATE]: 'path-integrate-file',
    [LEDGER_TEST]: 'path-test-file',
  };
  const recordBodies: Record<string, string> = {
    'INV-N213W04A': 'record-invariant',
    'FAM-QWVGDSYX': 'record-family',
    'DEC-R29DEC': 'record-decision',
    'DEC-R29AAA': 'record-decision-superseded',
    'INC-R29NC1': 'record-incident',
  };
  const name = {
    selections: 'selections',
    records: 'records',
    census: 'census',
    code: 'code-integrate',
    'without-proof': 'without-proof-worktrees',
    subtree: 'subtree-worktrees',
    tree: { '': 'tree-root', [WORKTREES]: 'tree-worktrees' }[url.searchParams.get('path') ?? ''],
    path: pathBodies[url.searchParams.get('path') ?? ''],
    record: recordBodies[url.searchParams.get('id') ?? ''],
  }[view];
  return name ? captured[name] : null;
}

beforeEach(() => {
  requests = [];
  overrides = {};
  heldTree = null;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requests.push(url);
      if (url.pathname === '/api/files/repos') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ repos: [{ repo: REPO }] }),
        } as Response;
      }
      let body = bodyFor(url) ?? emptyTree(url);
      if (body && url.pathname.endsWith('/tree') && Array.isArray(body.children))
        body = {
          ...body,
          children: (body.children as Record<string, unknown>[]).map((child) => ({
            ...child,
            hasKnowledge: Boolean(child.onboarding || child.entries),
          })),
        };
      if (body === null) throw new Error(`unexpected reader request: ${address}`);
      if (
        heldTree &&
        url.pathname.endsWith('/tree') &&
        url.searchParams.get('commit') === heldTree.commit
      ) {
        const held = heldTree;
        await new Promise<void>((release) => held.waiting.push(release));
      }
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = '';
});

export function open(address: Omit<ReaderAddress, 'repo' | 'commit'> & { commit?: string }) {
  window.location.hash = readerHash({ repo: REPO, commit: 'published', ...address });
  return render(<KnowledgeReader />);
}

export function holdTree(commit: string) {
  heldTree = { commit, waiting: [] };
}
