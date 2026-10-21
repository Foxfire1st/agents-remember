// The change-set model over captured card reads (MIK-R89 v2 evidence 3 and 5; MIK-R86 v2 rules 3, 4
// and 9): the sorts' sequences, one order for the tree and the column, families nesting, the order
// inside a subject, Changed | All, the groups, the filter and the j/k stops.
import { describe, expect, it } from 'vitest';

import type { ReviewOutline } from '../../data/review';
import type { ReviewTreeEntry } from '../../data/reviewTrees';
import { blockIdentityOf, blockKey } from './blockIdentity';
import {
  changeSetModel,
  filterTree,
  laneFileMatches,
  lineChanges,
  lineCounts,
  type ChangeSetInputs,
  type ChangeSetModel,
} from './changeSetOrder';
import {
  L104_IDS,
  L181_SUBJECT,
  cardsOf,
  l104Answer,
  l104Entries,
  l104Inputs,
  l104Selector,
  l181Answer,
  l181Entries,
  l181Inputs,
  withMemory,
  withSymbol,
} from './changeSetOrder.test-utils';
import type { ReviewSort } from './reviewSelection';
import { TREE_ORDER_KEY, readTreeOrder } from './triageOrderPreference';

const ids = (model: ChangeSetModel) => model.subjects.map((node) => node.row.id);
const treeIds = (model: ChangeSetModel) =>
  filterTree(model, '').groups.flatMap((group) => group.subjects.map((item) => item.node.row.id));
const entryIds = (nodes: { entry: { id: string } }[]) => nodes.map((node) => node.entry.id);
const model = (inputs: ChangeSetInputs, sort: ReviewSort = 'priority', view: 'changed' | 'all' = 'changed') =>
  changeSetModel(inputs, sort, view, { selection: null });

describe('L181 #8: one subject, its locations and its two groups', () => {
  const l181 = model(l181Inputs());
  const subject = l181.subjects[0];

  it('shows the one changed invariant with 15 changed, 31 unchanged and 28 not comparable entries', () => {
    expect(ids(l181)).toEqual(['INV-1ZKP4R']);
    expect([subject.changed.length, subject.unchanged.length, subject.notComparable.length]).toEqual([15, 31, 28]);
    expect(subject.reason).toBe('weaken · 15 loc.');
    expect(l181.counts).toMatchObject({ invariantsChanged: 1, effects: ['weaken'], changedLocations: 15, folded: 59 });
    expect(l181.shownInvariantIds).toEqual([L181_SUBJECT]);
  });

  it('orders the changed locations by role (app.js:120), then the largest span', () => {
    expect(entryIds(subject.changed)).toEqual([
      'RLZ-EC5NRM', 'RLZ-ZA95M4', 'RLZ-ZT2RRK', 'RLZ-78JFRR', 'RLZ-1V0Y9T', 'RLZ-H3YF9Z', 'RLZ-C1KP4R',
      'RLZ-D21Q7D', 'RLZ-M930WX', 'RLZ-1HAN2T', 'RLZ-C1JYFW', 'PRF-6MFPDJ', 'PRF-GQ2EVM', 'PRF-ZXQ4R7',
      'PRF-VEZBDV',
    ]);
    expect(subject.changed.map((node) => node.roleLabel).join(' ')).toBe(
      'enforce enforce enforce enforce enforce enforce enforce enforce persist support present proof proof proof proof',
    );
    // The groups keep the same order: primary authority first, proofs last.
    expect(entryIds(subject.unchanged.slice(0, 4))).toEqual(['RLZ-712BJ3', 'RLZ-DKJY6H', 'RLZ-29Q98S', 'RLZ-42490J']);
    expect(entryIds(subject.notComparable.slice(-2))).toEqual(['PRF-CWKV66', 'PRF-DT4XZG']);
  });

  it('names every location by its block identity and makes the drawn changed ones the j/k stops', () => {
    const comparison = l181Answer.comparison!;
    for (const node of [...subject.changed, ...subject.unchanged])
      expect(node.key).toBe(blockKey(blockIdentityOf(node.entry, comparison)));
    expect(l181.blocks.map((node) => node.key)).toEqual(subject.changed.filter((node) => node.draws.length).map((node) => node.key));
    // Three entries of one region stay three locations (W1), drawn by the first of them.
    expect(new Set(subject.changed.slice(0, 3).map((node) => node.range))).toEqual(new Set(['L1697–1817']));
  });

  it('counts a location row\'s lines from its two excerpts, else shows its span (W10)', () => {
    const byId = new Map(subject.changed.map((node) => [node.entry.id, node]));
    expect(byId.get('RLZ-C1JYFW')?.reason).toMatch(/^present · \+\d+ −\d+$/);
    expect(lineChanges(['a', 'b', 'c'], ['a', 'x', 'c', 'd'])).toEqual({ added: 2, removed: 1 });
  });

  it('counts a side with no file as no lines: an added file\'s lines are all added (MIK-R86 v2 rule 2)', () => {
    const byId = new Map(subject.changed.map((node) => [node.entry.id, node]));
    // Captured: no file on the before side of either, so L60–94 and L16–74 are every line added.
    expect(byId.get('RLZ-M930WX')?.reason).toBe('persist · +35 −0');
    expect(byId.get('PRF-6MFPDJ')?.reason).toBe('proof · +59 −0');
  });

  it('counts a removed file\'s lines as removed, and keeps the range where a side\'s text is not known', () => {
    const comparison = l181Answer.comparison!;
    const entry = l181Entries().find((one) => one.id === 'RLZ-C1JYFW')!;
    const counts = (changed: ReviewTreeEntry) => lineCounts(changed, blockIdentityOf(changed, comparison));
    // Synthetic: the captured entry's after side is a file that is gone; the block names the before side.
    const removed: ReviewTreeEntry = {
      ...entry,
      before: { ...entry.before, recorded: true },
      after: { recorded: false, state: 'absent', path: entry.after.path },
    };
    const before = entry.before.excerpt!.replace(/\n$/, '').split('\n').length;
    expect(counts(removed)).toBe(`+0 −${before}`);
    // Not known is not empty: an unresolved side, an excerpt not read, a cut excerpt keep the range.
    const unresolved: ReviewTreeEntry = { ...entry, before: { ...entry.before, state: 'unresolved', excerpt: undefined } };
    const unread: ReviewTreeEntry = { ...entry, before: { ...entry.before, excerpt: undefined } };
    const cut: ReviewTreeEntry = { ...removed, before: { ...removed.before, excerpt_truncated: true } };
    expect([counts(unresolved), counts(unread), counts(cut)]).toEqual(['L1473–1477', 'L1473–1477', 'L737–740']);
  });
});

// Captured: RLZ-EC5NRM, RLZ-ZA95M4 and RLZ-ZT2RRK all name turn_ended.ts L750–795 → L1697–1817.
const TRIPLE = ['RLZ-EC5NRM', 'RLZ-ZA95M4', 'RLZ-ZT2RRK'];

describe('one location row and one card per shared region (MIK-R86 v2 normative bullet 1; MIK-R31)', () => {
  const l181 = model(l181Inputs());
  const subject = l181.subjects[0];
  const rows = filterTree(l181, '').groups[0].subjects[0].locations;

  it('draws the L181 triple as one row whose one card lists its three entries', () => {
    expect(rows).toHaveLength(13);
    const shared = rows.filter((row) => row.card.entries.length > 1);
    expect(shared.map((row) => row.card.entries.map((entry) => entry.id))).toEqual([TRIPLE]);
    expect(rows.filter((row) => row.path === shared[0].path && row.range === shared[0].range)).toHaveLength(1);
    // Distinct ranges of one file stay apart: turn_ended.ts keeps seven rows for its nine entries.
    expect(rows.filter((row) => row.path.endsWith('/turn_ended.ts'))).toHaveLength(7);
  });

  it('keeps each entry with its own identity, and every count by entry', () => {
    const triple = subject.changed.filter((node) => TRIPLE.includes(node.entry.id));
    expect(new Set(triple.map((node) => node.key)).size).toBe(3);
    expect(rows.find((row) => row.entry.id === 'RLZ-EC5NRM')?.draws).toEqual(triple.map((node) => node.key));
    expect(triple.slice(1).map((node) => node.draws)).toEqual([[], []]);
    // One j/k stop per drawn row; the counts and the groups count entries.
    expect(l181.blocks.map((node) => node.key)).toEqual(rows.map((row) => row.key));
    expect([subject.changed.length, subject.unchanged.length, subject.notComparable.length]).toEqual([15, 31, 28]);
    expect(subject.reason).toBe('weaken · 15 loc.');
    expect(l181.counts).toMatchObject({ changedLocations: 15, folded: 59 });
  });
});

describe('L104 #25: each deterministic sort', () => {
  const PRIORITY = [
    'INV-9ZKYWQ', 'INV-R38XGA', 'INV-N7SK8E', 'INV-GWMA1V', 'INV-RKY270', 'INV-ESF2CT', 'INV-K302DH',
    'INV-HDXPMA', 'INV-6V0ET6', 'INV-40VGBY', 'INV-FQC46N', 'INV-M6Q82M', 'INV-YCMZGZ', 'INV-SE4ECQ',
    'INV-EWGTN0',
  ];

  it('Priority: effect, then change kind, then changed locations and their spans, then the id', () => {
    const sorted = model(l104Inputs());
    expect(ids(sorted)).toEqual(PRIORITY);
    expect(sorted.counts.effects).toEqual(['clarify']);
  });

  it('Authored: the outline order', () => {
    expect(ids(model(l104Inputs(), 'authored'))).toEqual(L104_IDS);
  });

  it('Memory currentness: the subject\'s shown state over read 4 and its entries, then Priority', () => {
    const entries = l104Entries().map((entry) => {
      if (entry.invariant === 'INV-EWGTN0') return { ...entry, after: { ...entry.after, currentness: 'unverifiable' as const } };
      if (entry.id === l104Entries().find((item) => item.invariant === 'INV-YCMZGZ')?.id)
        return { ...entry, after: { ...entry.after, currentness: 'stale' as const } };
      return entry;
    });
    const currentness = {
      after: { invariants: [{ id: 'INV-M6Q82M', state: 'unrealized' as const, entries: [] }] },
    };
    const sorted = model(l104Inputs({ cards: cardsOf(l104Answer, entries), currentness }), 'currentness');
    expect(ids(sorted).slice(0, 4)).toEqual(['INV-YCMZGZ', 'INV-EWGTN0', 'INV-M6Q82M', 'INV-9ZKYWQ']);
    expect(sorted.subjects.slice(0, 3).map((node) => node.reason)).toEqual(['stale', 'unverifiable', 'unrealized']);
    expect(sorted.subjects[0].changed.find((node) => node.currentness === 'stale')?.reason).toBe('stale');
  });

  it('Memory currentness breaks a tie of the shown state by Priority, with no read-4 stage (e9b9d515), in the tree and the column', () => {
    // Synthetic currentness on the captured entries: one stale entry each for Priority's first and
    // last subject; read 4 calls the first current and the last stale. Both show stale.
    const stale = ['INV-9ZKYWQ', 'INV-EWGTN0'].map((id) => l104Entries().find((entry) => entry.invariant === id && entry.after.recorded)!.id);
    const entries = l104Entries().map((entry) =>
      stale.includes(entry.id) ? { ...entry, after: { ...entry.after, currentness: 'stale' as const } } : entry,
    );
    const currentness = {
      after: {
        invariants: [
          { id: 'INV-9ZKYWQ', state: 'current' as const, entries: [] },
          { id: 'INV-EWGTN0', state: 'stale' as const, entries: [] },
        ],
      },
    };
    const sorted = model(l104Inputs({ cards: cardsOf(l104Answer, entries), currentness }), 'currentness');
    expect(sorted.subjects.slice(0, 2).map((node) => [node.row.id, node.reason])).toEqual([
      ['INV-9ZKYWQ', 'stale'],
      ['INV-EWGTN0', 'stale'],
    ]);
    expect(ids(sorted)).toEqual(['INV-9ZKYWQ', 'INV-EWGTN0', ...PRIORITY.slice(1, -1)]);
    expect(treeIds(sorted)).toEqual(ids(sorted));
  });

  it('the four memory sorts rank a subject by its highest entry value, ties in Priority order', () => {
    const memory = { state: 'available' as const, tree: 'd4eb8510' };
    const cards = (values: Parameters<typeof withMemory>[1]) => ({
      ...cardsOf(l104Answer, withMemory(l104Entries(), values)),
      memory,
    });
    const first = (sort: ReviewSort, values: Parameters<typeof withMemory>[1]) =>
      ids(model(l104Inputs({ cards: cards(values) }), sort)).slice(0, 3);
    expect(first('invariants-at-location', { 'INV-EWGTN0': { invariants_at_path: 9 }, 'INV-SE4ECQ': { invariants_at_path: 4 } }))
      .toEqual(['INV-EWGTN0', 'INV-SE4ECQ', 'INV-9ZKYWQ']);
    expect(first('incoming-links', { 'INV-FQC46N': { incoming_links: 3 } })).toEqual(['INV-FQC46N', 'INV-9ZKYWQ', 'INV-R38XGA']);
    expect(first('without-proof', { 'INV-YCMZGZ': { without_proof: true }, 'INV-K302DH': { without_proof: true } }))
      .toEqual(['INV-K302DH', 'INV-YCMZGZ', 'INV-9ZKYWQ']);
    expect(first('open-weaknesses', { 'INV-6V0ET6': { open_weaknesses: 2 } })).toEqual(['INV-6V0ET6', 'INV-9ZKYWQ', 'INV-R38XGA']);
    // A subject ranks by its highest location, not by its first.
    const one = l104Entries().map((entry, index) =>
      entry.invariant === 'INV-ESF2CT' && index % 2 === 1
        ? { ...entry, memory: { path: entry.after.path, invariants_at_path: 5, incoming_links: 0, without_proof: false, open_weaknesses: 0 } }
        : entry,
    );
    expect(ids(model(l104Inputs({ cards: { ...cardsOf(l104Answer, one), memory } }), 'invariants-at-location'))[0]).toBe('INV-ESF2CT');
  });

  it('shows the memory sorts unavailable with the reason and keeps Priority while they are', () => {
    const cards = { ...cardsOf(l104Answer), memory: { state: 'unavailable' as const, tree: 'd4eb8510', reason: 'the after side is not indexed' } };
    const sorted = model(l104Inputs({ cards }), 'incoming-links');
    expect(sorted.requested).toBe('incoming-links');
    expect(sorted.sort).toBe('priority');
    expect(ids(sorted)).toEqual(PRIORITY);
    expect(sorted.sorts.filter((option) => !option.available).map((option) => [option.sort, option.reason])).toEqual([
      ['invariants-at-location', 'the after side is not indexed'],
      ['incoming-links', 'the after side is not indexed'],
      ['without-proof', 'the after side is not indexed'],
      ['open-weaknesses', 'the after side is not indexed'],
    ]);
  });

  it('reads an unknown stored sort as Priority and the earlier Order values by their meaning (W7)', () => {
    const stored = (value: string | null) => ({ getItem: (key: string) => (key === TREE_ORDER_KEY ? value : null) });
    expect(readTreeOrder(stored('bogus'))).toBe('priority');
    expect(readTreeOrder(stored('triage'))).toBe('priority');
    expect(readTreeOrder(stored('authored'))).toBe('authored');
    expect(readTreeOrder(stored('open-weaknesses'))).toBe('open-weaknesses');
    expect(readTreeOrder({ getItem: () => { throw new Error('denied'); } })).toBe('priority');
    expect(readTreeOrder(undefined)).toBe('priority');
  });
});

it('Memory currentness ranks an unrealized subject without entries before current entries (e9b9d515), in the tree and the column', () => {
  // L181 #8 in All: the captured entries are current on every recorded side. Synthetic read 4: the
  // synthetic INV-0SYNTH, which has no entries, is unrealized; INV-1ZKP4R is current.
  expect(l181Entries().every((entry) => entry.after.currentness === 'current')).toBe(true);
  const currentness = {
    after: {
      invariants: [
        { id: 'INV-0SYNTH', state: 'unrealized' as const, entries: [] },
        { id: 'INV-1ZKP4R', state: 'current' as const, entries: [] },
      ],
    },
  };
  const sorted = model(l181Inputs({ currentness }), 'currentness', 'all');
  expect(sorted.subjects.map((node) => [node.row.id, node.reason, node.changed.length])).toEqual([
    ['INV-0SYNTH', 'unrealized', 0],
    ['INV-1ZKP4R', 'current', 15],
    ['INV-ZSYNTH', 'not measured', 0],
  ]);
  expect(treeIds(sorted)).toEqual(ids(sorted));
});

describe('one order for the tree and the column', () => {
  const family = (id: string, title: string, members: string[], change: 'unchanged' | 'changed' = 'unchanged') => ({
    selector_id: `family-${id}`,
    id,
    title,
    change,
    members: members.map((member) => ({ selector_id: l104Selector(member), id: member, membership: 'both' as const })),
  });
  const nested: ReviewOutline = {
    ...l104Inputs().outline!,
    families: [
      family('FAM-AAAA', 'first authored family', ['INV-EWGTN0', 'INV-N7SK8E']),
      family('FAM-BBBB', 'second authored family', ['INV-K302DH', 'INV-9ZKYWQ']),
    ],
  };

  it('nests subjects under their family, which takes the place of its first member', () => {
    const sorted = model(l104Inputs({ outline: nested }));
    expect(sorted.groups.map((group) => group.family?.row.id ?? 'Without a family').slice(0, 3)).toEqual([
      'FAM-BBBB', 'Without a family', 'FAM-AAAA',
    ]);
    expect(sorted.groups[0].subjects.map((node) => node.row.id)).toEqual(['INV-9ZKYWQ', 'INV-K302DH']);
    expect(sorted.groups[2].subjects.map((node) => node.row.id)).toEqual(['INV-N7SK8E', 'INV-EWGTN0']);
    // The reading column reads the same sequence the tree draws.
    expect(ids(sorted)).toEqual(treeIds(sorted));
    expect(sorted.blocks.map((node) => node.subject)).toEqual(
      sorted.subjects.flatMap((node) => node.changed.filter((location) => location.draws.length).map(() => node.row.selectorId)),
    );
  });

  it('keeps the outline order under Authored, families first', () => {
    const sorted = model(l104Inputs({ outline: nested }), 'authored');
    expect(sorted.groups.map((group) => group.family?.row.id ?? '-')).toEqual(['FAM-AAAA', 'FAM-BBBB', '-']);
    expect(sorted.groups[0].subjects.map((node) => node.row.id)).toEqual(['INV-EWGTN0', 'INV-N7SK8E']);
    expect(ids(sorted)).toEqual(treeIds(sorted));
  });

  it('ranks several recorded effects by the highest', () => {
    const outline: ReviewOutline = {
      ...l104Inputs().outline!,
      invariants: l104Inputs().outline!.invariants.map((row) =>
        row.id === 'INV-EWGTN0'
          ? { ...row, meaning: { state: 'steps', labels: ['clarify', 'weaken'] } }
          : row.id === 'INV-SE4ECQ'
            ? { ...row, meaning: { state: 'steps', labels: ['strengthen'] } }
            : row,
      ),
    };
    const sorted = model(l104Inputs({ outline }));
    expect(ids(sorted).slice(0, 2)).toEqual(['INV-EWGTN0', 'INV-SE4ECQ']);
    expect(sorted.subjects[0].reason).toBe('clarify → weaken · 2 loc.');
    expect(sorted.counts.effects).toEqual(['weaken', 'strengthen', 'clarify']);
  });

  it('shows a member of two families once in the column, at its first place', () => {
    const shared: ReviewOutline = {
      ...nested,
      families: [...nested.families, family('FAM-CCCC', 'third family', ['INV-9ZKYWQ'])],
    };
    const sorted = model(l104Inputs({ outline: shared }));
    const places = sorted.groups.flatMap((group) => group.subjects).filter((node) => node.row.id === 'INV-9ZKYWQ');
    expect(places.map((node) => [node.first, node.changed.length])).toEqual([[true, 1], [false, 0]]);
    expect(ids(sorted).filter((id) => id === 'INV-9ZKYWQ')).toHaveLength(1);
  });
});

describe('Changed | All', () => {
  it('Changed shows the real changes, All every subject, and read 5 asks for the shown ones', () => {
    const changed = model(l181Inputs());
    const all = model(l181Inputs(), 'priority', 'all');
    expect(ids(changed)).toEqual(['INV-1ZKP4R']);
    expect(ids(all)).toEqual(['INV-1ZKP4R', 'INV-0SYNTH', 'INV-ZSYNTH']);
    expect(all.shownInvariantIds).toEqual([L181_SUBJECT, 'synthetic-unchanged-0', 'synthetic-unchanged-z'].sort());
    expect(changed.noChange).toBe(false);
  });

  it('keeps a selected unchanged subject shown in Changed', () => {
    const selection = { kind: 'subject' as const, subject: { kind: 'invariant' as const, id: 'synthetic-unchanged-z' } };
    const kept = changeSetModel(l181Inputs(), 'priority', 'changed', { selection });
    expect(ids(kept)).toEqual(['INV-1ZKP4R', 'INV-ZSYNTH']);
  });

  it('opens a leaf that changed no recorded meaning on the one line, and an outline-less one in All', () => {
    const unchanged: ReviewOutline = {
      ...l181Inputs().outline!,
      invariants: l181Inputs().outline!.invariants.map((row) => ({ ...row, change: 'unchanged' as const })),
    };
    expect(model(l181Inputs({ outline: unchanged })).noChange).toBe(true);
    const plain = model(l181Inputs({
      outline: undefined,
      catalogue: [{ selector_kind: 'invariant', selector_id: L181_SUBJECT, label: 'INV-1ZKP4R', presence: 'both' }],
    }));
    // The catalogue's label names the row; the selector id stays its key (finding 2).
    expect([plain.view, plain.outlined, ids(plain), plain.subjects.map((node) => node.row.selectorId)]).toEqual([
      'all',
      false,
      ['INV-1ZKP4R'],
      [L181_SUBJECT],
    ]);
  });

  it('reads "…" for the location count while read 5 is pending', () => {
    const pending = model(l181Inputs({ cards: { state: 'reading' } }));
    expect(pending.subjects[0].reason).toBe('weaken · … loc.');
    expect(pending.counts.changedLocations).toBeUndefined();
    expect(pending.blocks).toEqual([]);
  });
});

describe('the filter narrows the tree only (W12)', () => {
  const sorted = model(l104Inputs());
  const shown = (query: string, from: ChangeSetModel = sorted) =>
    filterTree(from, query).groups.flatMap((group) =>
      group.subjects.map((item) => `${item.node.row.id}:${item.locations.length}`),
    );

  // The rows of the drawn locations: INV-RKY270's 6 changed entries lie at 5 regions, INV-R38XGA's 16 at 5.
  it('matches the id', () => expect(shown('rky270')).toEqual(['INV-RKY270:5']));
  it('matches the title', () => expect(shown('offline toolbox')).toEqual(['INV-R38XGA:5']));
  it('matches the effect', () => expect(shown('clarify')).toHaveLength(6));
  it('matches the path and keeps only the matching locations', () => {
    const path = sorted.subjects.find((node) => node.row.id === 'INV-SE4ECQ')!.changed[0].path;
    const result = filterTree(sorted, path);
    expect(result.groups.flatMap((group) => group.subjects).every((item) => item.locations.every((node) => node.path === path))).toBe(true);
    // A group row shows only for a folded location the path matches.
    for (const item of result.groups.flatMap((group) => group.subjects))
      for (const group of item.groups)
        expect((group === 'unchanged' ? item.node.unchanged : item.node.notComparable).some((node) => node.path === path)).toBe(true);
  });
  it('matches the role', () => {
    const roles = filterTree(sorted, 'propagation-persistence').groups.flatMap((group) =>
      group.subjects.flatMap((item) => item.locations.map((node) => node.role)),
    );
    expect(roles).toHaveLength(3);
    expect(new Set(roles)).toEqual(new Set(['propagation-persistence']));
  });
  it('matches the symbol', () => {
    const entries = withSymbol(l181Entries(), 'RLZ-C1JYFW', 'TurnEndedOutcome');
    const withSymbols = model(l181Inputs({ cards: cardsOf(l181Answer, entries) }));
    expect(shown('turnendedoutcome', withSymbols)).toEqual(['INV-1ZKP4R:1']);
  });
  it('says nothing matches, and matches a lane file on its path', () => {
    expect(filterTree(sorted, 'no such thing').empty).toBe(true);
    expect(filterTree(sorted, '').empty).toBe(false);
    expect(laneFileMatches('dashboard/src/a.ts', 'code without')).toBe(true);
    expect(laneFileMatches('dashboard/src/a.ts', 'b.ts')).toBe(false);
  });

  describe('finds unchanged and not comparable locations, and keeps their subject and group row', () => {
    const all = model(l181Inputs(), 'priority', 'all');
    const found = (query: string, from: ChangeSetModel = all) => {
      const result = filterTree(from, query);
      expect(result.empty).toBe(false);
      return result.groups.flatMap((group) =>
        group.subjects.map((item) => [item.node.row.id, item.locations.length, item.groups]),
      );
    };

    it('by the path of an unchanged location', () => {
      // Captured: RLZ-42490J is unchanged at role_agents.ts, which no changed location names.
      expect(found('mcp/src/agents_remember/package_data/paseo_plugin/server/role_agents.ts')).toEqual([
        ['INV-1ZKP4R', 0, ['unchanged']],
      ]);
    });

    it('by the symbol of a not comparable location', () => {
      // Synthetic: a symbol on the first captured undetermined entry.
      const id = l181Entries().find((entry) => entry.change === 'undetermined')!.id;
      const symbols = model(l181Inputs({ cards: cardsOf(l181Answer, withSymbol(l181Entries(), id, 'onlyNotComparable')) }), 'priority', 'all');
      expect(found('onlynotcomparable', symbols)).toEqual([['INV-1ZKP4R', 0, ['undetermined']]]);
    });

    it('by a role that only folded locations hold', () => {
      expect(found('primary-authority')).toEqual([['INV-1ZKP4R', 0, ['unchanged']]]);
    });

    it('beside the changed locations that match, and in Changed too', () => {
      // turn_ended.ts: changed, unchanged and not comparable locations of INV-1ZKP4R.
      expect(found('server/turn_ended.ts')).toEqual([['INV-1ZKP4R', 7, ['unchanged', 'undetermined']]]);
      expect(found('role_agents.ts', model(l181Inputs()))).toEqual([['INV-1ZKP4R', 0, ['unchanged']]]);
    });
  });
});
