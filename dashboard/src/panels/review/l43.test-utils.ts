// Exact leaf-built MIK-R43 responses; source URLs and hashes are in l43.capture-provenance.json.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { vi } from 'vitest';
import type { ReviewEntryListResult, ReviewResult } from '../../data/review';
import type { ReviewTreesResult } from '../../data/reviewTrees';
export const captured = <T>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
export const catalogue = captured<ReviewEntryListResult>('l43.entries.captured.json');
export const taskBody = captured<ReviewResult>('l43.task.captured.json');
const invariant4 = captured<ReviewResult>('l43.invariant4.captured.json');
const invariant2 = captured<ReviewResult>('l43.invariant2.captured.json');
const invariant1 = captured<ReviewResult>('l43.invariant1.captured.json');
const none = captured<ReviewResult>('l43.none.captured.json');
const family = captured<ReviewResult>('l43.family.captured.json');
const oldfamily = captured<ReviewResult>('l43.oldfamily.captured.json');
const task = taskBody;
const unavailable = captured<ReviewResult>('l43.unavailable.captured.json');
const trees = captured<ReviewTreesResult>('l43.trees.captured.json');
export const target = {
  repo: '<repository_id>',
  master: '260101_tree_review',
  leaf: '260101-TRV-L1',
  onBack: () => undefined,
};
// Each intent capture's selected subject is its meaning answer's first record: the
// composition answers the selector first, then the context's families and members.
const selectedOf = (body: ReviewResult): string =>
  body.payload!.meaning_effects!.records[0].record;
export const subject = {
  invariant4: selectedOf(invariant4),
  invariant2: selectedOf(invariant2),
  invariant1: selectedOf(invariant1),
  none: selectedOf(none),
  family: selectedOf(family),
  oldfamily: selectedOf(oldfamily),
};
const bySelector: Record<string, unknown> = {
  [subject.invariant4]: invariant4,
  [subject.invariant2]: invariant2,
  [subject.invariant1]: invariant1,
  [subject.none]: none,
  [subject.family]: family,
  [subject.oldfamily]: oldfamily,
};
function intentReply(url: URL, unavailableFor?: string, taskReply?: unknown): unknown {
  const selectorId = url.searchParams.get('selectorId');
  if (selectorId && unavailableFor && selectorId === unavailableFor) return unavailable;
  if (selectorId) return bySelector[selectorId];
  return taskReply ?? task;
}
export function serve(options?: {
  unavailableFor?: string;
  treesReply?: unknown;
  taskReply?: unknown;
  catalogueReply?: unknown;
}) {
  const asked: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      asked.push(url);
      let body: unknown;
      if (url.pathname === '/api/review/intent/entries')
        body = options?.catalogueReply ?? catalogue;
      else if (url.pathname === '/api/review/intent')
        body = intentReply(url, options?.unavailableFor, options?.taskReply);
      else if (url.pathname === '/api/review/trees') body = options?.treesReply ?? trees;
      else throw new Error(`unexpected request ${url.pathname}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
  return { asked };
}
