// MIK-R25 at the client: the tree view adapter over the REAL route body of a converted leaf.
//
// WHERE THE BODY COMES FROM. `reviewTrees.captured.json` is the measured body of GET
// /api/review/trees served by `create_app(config, collaborators=serving_collaborators(config))` over
// the 260928-MIK-L25 worker's scratch copy: the real memory repository converted with the leaf's own
// `knowledge-convert`, and a leaf that edits `_not_listed` in review_source_admission.py and
// re-anchors RLZ-CXH58B4W (provenance: ../panels/review/gitTrees.capture-provenance.json).
// Only `fetch` is stubbed, so the URL and the body travel the way the browser's do.

import { readFileSync } from 'node:fs';
import path from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  type ReviewTreesResult,
  degradedKnowledgeSides,
  invariantCurrentness,
  reviewTrees,
  reviewTreesRead,
  unexplainedHunks,
} from './reviewTrees';

const captured = JSON.parse(
  readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), 'reviewTrees.captured.json'),
    'utf8',
  ),
) as ReviewTreesResult;

const SOURCE = 'mcp/src/agents_remember/application/review_source_admission.py';

afterEach(() => {
  vi.unstubAllGlobals();
});

function serve(body: unknown, status = 200): URL[] {
  const requests: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      requests.push(new URL(address, 'http://localhost'));
      return { ok: status < 400, status, json: async () => body } as Response;
    }),
  );
  return requests;
}

describe('the tree view of a converted leaf', () => {
  it('reads the four trees, the pinning refs and both knowledge sides from the real body', async () => {
    const requests = serve(captured);
    const result = await reviewTrees(
      'agents-remember',
      '260928_maintained-invariant-knowledge',
      '260928-MIK-L25',
    );
    expect(requests[0].pathname).toBe('/api/review/trees');
    expect(requests[0].searchParams.get('leaf')).toBe('260928-MIK-L25');
    const read = reviewTreesRead(result);
    expect(read.phase).toBe('trees');
    const comparison = result.comparison!;
    expect(comparison.schema).toBe('ar-review-tree-comparison/v1');
    // Committed sides name their commits; the uncommitted candidates are pinned by one ref name.
    expect(comparison.code_base.commit).toBe('a4eba7b7b5b5ffee7277f6c19086697925a22df2');
    expect(comparison.code_candidate.ref).toBe(
      'refs/ar/review/260928_maintained-invariant-knowledge/260928-MIK-L25/2',
    );
    expect(comparison.memory_candidate.ref).toBe(comparison.code_candidate.ref);
    expect(result.knowledge_sides.map((side) => [side.side, side.state, side.index_state])).toEqual(
      [
        ['before', 'available', 'complete'],
        ['after', 'available', 'complete'],
      ],
    );
    expect(degradedKnowledgeSides(result)).toEqual([]);
  });

  it('groups the memory diff by record and by source path, with currentness per side', () => {
    const diff = captured.knowledge_diff!;
    const record = diff.records.find((group) => group.record_id === 'INV-2TQGXFAX')!;
    expect(record.entries).toContainEqual(['RLZ-CXH58B4W', SOURCE, 'changed']);
    const source = diff.sources.find((group) => group.source_path === SOURCE)!;
    expect(source.records).toEqual(['INV-2TQGXFAX']);
    expect(source.files[0].patch).toContain('@@');
    expect(diff.history.map((file) => [file.path, file.status])).toEqual([
      ['knowledge/history/260928-MIK-L25.json', 'added'],
    ]);
    expect(invariantCurrentness(captured, 'INV-2TQGXFAX')).toEqual({
      before: 'current',
      after: 'current',
    });
    expect(captured.currentness!.before!.codeTree!.treeId).toBe(
      captured.comparison!.code_base.tree,
    );
    expect(captured.currentness!.after!.codeTree!.treeId).toBe(
      captured.comparison!.code_candidate.tree,
    );
  });

  it('shows the worklist items, their history rows without a currency mark, and the gate linkage', () => {
    const worklist = captured.worklist!;
    expect(worklist.source).toBe('computed');
    expect(worklist.bound).toBe(true);
    expect(worklist.items.map((item) => [item.kind, item.subject])).toContainEqual([
      'touched_invariant',
      'INV-2TQGXFAX',
    ]);
    expect(worklist.history_rows.map((row) => [row.owner, row.subject, row.disposition])).toEqual([
      ['260928-MIK-L25', 'FAM-2HBJREC2', 'no_impact'],
    ]);
    expect(Object.keys(worklist.history_rows[0])).not.toContain('current');
    const change = worklist.changes.find((one) => one.path === SOURCE)!;
    expect(change.hunks!.map((hunk) => hunk.linked)).toEqual([true]);
    expect(unexplainedHunks(worklist)).toEqual([]);
  });
});

describe('the answers that are not a tree view', () => {
  it('keeps an unconverted leaf, a refusal and an unreadable body apart', async () => {
    serve({
      state: 'not-converted',
      repository_id: 'r',
      master: 'm',
      leaf_id: 'l',
      knowledge_sides: [],
    });
    expect(reviewTreesRead(await reviewTrees('r', 'm', 'l')).phase).toBe('not-converted');
    const refusal = {
      code: 'candidate_dataset_absent',
      detail: 'the after knowledge side is unavailable-history: Git can no longer produce the tree',
      next_action: 'open the source review',
      offending_input: 'after:unavailable-history:52a371e817dfefd071a5742b6266d7724d1a54d7',
    };
    const refused = reviewTreesRead({
      state: 'refused',
      repository_id: 'r',
      master: 'm',
      leaf_id: 'l',
      knowledge_sides: [],
      refusal,
    });
    expect(refused.phase).toBe('unavailable');
    expect(reviewTreesRead({ ...captured, comparison: undefined }).phase).toBe('unavailable');
  });

  it('addresses a recorded comparison by number and never by path', async () => {
    const requests = serve(captured);
    await reviewTrees('r', 'm', 'l', { comparison: 1 });
    await reviewTrees('r', 'm', 'l', { recorded: true });
    expect(requests[0].searchParams.get('comparison')).toBe('1');
    expect(requests[1].searchParams.get('history')).toBe('recorded');
    expect([...requests[0].searchParams.keys()].sort()).toEqual([
      'comparison',
      'leaf',
      'master',
      'repo',
    ]);
  });

  it('names a side read from a partial index or lost to history', () => {
    const partial: ReviewTreesResult = {
      ...captured,
      knowledge_sides: [
        { ...captured.knowledge_sides[0] },
        {
          side: 'after',
          state: 'available',
          tree: 'a'.repeat(40),
          index_state: 'partial',
          problems: [['knowledge/x.json', 'bad']],
        },
      ],
    };
    expect(degradedKnowledgeSides(partial).map((side) => side.side)).toEqual(['after']);
  });
});
