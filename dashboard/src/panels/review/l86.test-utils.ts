// Exact leaf-built L37 responses; source URLs and hashes are in l86.capture-provenance.json.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { vi } from 'vitest';
import type { FileDiff } from '../../data/changeset';
import type { ReviewEntryListResult, ReviewResult } from '../../data/review';
export const captured = <T>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
export const catalogue = captured<ReviewEntryListResult>('l86.entries.captured.json');
const family = captured<ReviewResult>('l86.family.captured.json');
const member = captured<ReviewResult>('l86.member.captured.json');
const task = captured<ReviewResult>('l86.task.captured.json');
const trees = captured('l86.trees.captured.json');
const cards = captured('l86.cards.captured.json');
const memberCards = captured('l86.member-cards.captured.json');
const lane = captured('l86.lane.captured.json');
const changes = captured('l86.changeset.captured.json');
const diffs = captured<Record<string, FileDiff>>('l86.file-diffs.captured.json');
export const selectedMemberId = captured<{ selected_member: { selector_id: string } }>(
  'l86.capture-provenance.json',
).selected_member.selector_id;
const fullFile = captured('l86.full-file.captured.json');
const classification = captured('l86.classification.captured.json');
export const target = {
  repo: 'agents-remember',
  master: '260928_maintained-invariant-knowledge',
  leaf: '260928-MIK-L37',
  history: 'recorded' as const,
  onBack: () => undefined,
};
export function serve(entries = catalogue, hold?: 'lane' | 'worklist') {
  const asked: URL[] = [];
  let release: (() => void) | undefined;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      asked.push(url);
      const body = replyFor(url, entries);
      const response = new Response(JSON.stringify(body), { status: 200 });
      if (isHeld(url, hold))
        return await new Promise<Response>((resolve) => {
          release = () => resolve(response);
        });
      return response;
    }),
  );
  return { asked, release: () => release?.() };
}

function intentReply(url: URL): unknown {
  if (url.searchParams.get('selectorKind') === 'invariant') return member;
  return url.searchParams.get('selectorId') ? family : task;
}
function treeReply(url: URL): unknown {
  if (url.searchParams.has('file')) return classification;
  if (url.searchParams.has('lane')) return lane;
  if (!url.searchParams.has('invariants')) return trees;
  const selected = url.searchParams.get('invariants')?.split(',').sort().join(',');
  return selected === [...member.payload!.knowledge.invariant_ids].sort().join(',')
    ? memberCards
    : cards;
}
function replyFor(url: URL, entries: ReviewEntryListResult): unknown {
  switch (url.pathname) {
    case '/api/review/intent/entries':
      return entries;
    case '/api/review/intent':
      return intentReply(url);
    case '/api/review/trees':
      return treeReply(url);
    case '/api/review/intent/source-content':
      return fullFile;
    case '/api/changeset/task':
      return changes;
    case '/api/changeset/file-diff':
      return diffs[`${url.searchParams.get('kind') ?? 'code'}:${url.searchParams.get('path')}`];
    default:
      throw new Error(`Uncaptured route ${url}`);
  }
}
function isHeld(url: URL, hold?: 'lane' | 'worklist'): boolean {
  if (!hold || url.pathname !== '/api/review/trees') return false;
  if (hold === 'lane') return url.searchParams.has('lane');
  return !['lane', 'invariants', 'file'].some((name) => url.searchParams.has(name));
}
