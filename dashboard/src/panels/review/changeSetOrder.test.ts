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

  it('shows the producer line measurement rather than deriving it from bounded excerpts', () => {
    const entries = l181Entries().map((entry) => entry.id === 'RLZ-C1JYFW'
      ? { ...entry, line_changes: { added: 2, removed: 1 } } : entry);
    const result = model(l181Inputs({ cards: cardsOf(l181Answer, entries) }));
    const location = result.subjects[0].changed.find((node) => node.entry.id === 'RLZ-C1JYFW')!;
    expect(location.reason).toBe('present · +2 −1');
  });

  it('uses producer measurements for added files without inferring counts from their excerpt', () => {
    const entries = l181Entries().map((entry) => {
      if (entry.id === 'RLZ-M930WX') return { ...entry, line_changes: { added: 35, removed: 0 } };
      if (entry.id === 'PRF-6MFPDJ') return { ...entry, line_changes: { added: 59, removed: 0 } };
      return entry;
    });
    const result = model(l181Inputs({ cards: cardsOf(l181Answer, entries) }));
    const byId = new Map(result.subjects[0].changed.map((node) => [node.entry.id, node]));
    expect(byId.get('RLZ-M930WX')?.reason).toBe('persist · +35 −0');
    expect(byId.get('PRF-6MFPDJ')?.reason).toBe('proof · +59 −0');
  });

  it('keeps exact producer counts for a long truncated excerpt and uses the range when counts are absent', () => {
    const comparison = l181Answer.comparison!;
    const entry = l181Entries().find((one) => one.id === 'RLZ-C1JYFW')!;
    const counts = (changed: ReviewTreeEntry) => lineCounts(changed, blockIdentityOf(changed, comparison));
    const truncated: ReviewTreeEntry = {
      ...entry,
      after: { ...entry.after, end_line: 4000, excerpt: 'only the bounded beginning', excerpt_truncated: true },
      line_changes: { added: 3200, removed: 2400 },
    };
    expect(counts(truncated)).toBe('+3200 −2400');
    const unmeasured = { ...truncated };
    delete unmeasured.line_changes;
    expect(counts(unmeasured)).toBe('L1473–4000');
    const unresolved: ReviewTreeEntry = { ...entry, before: { ...entry.before, state: 'unresolved', excerpt: undefined } };
    const unread: ReviewTreeEntry = { ...entry, before: { ...entry.before, excerpt: undefined } };
    expect([counts(unresolved), counts(unread)]).toEqual(['L1473–1477', 'L1473–1477']);
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

describe('per-invariant card coverage', () => {
  const outline = l104Inputs().outline!;
  const entries = l104Entries();
  const firstId = l104Selector('INV-9ZKYWQ');
  const secondId = l104Selector('INV-EWGTN0');
  const partial = (loaded: string[], pending: string[] = [], failures: Record<string, string> = {}) => ({
    ...cardsOf(l104Answer, entries.filter((entry) => loaded.includes(entry.invariant_key))),
    coverage: { loaded, pending, failures },
  });
  const subject = (result: ChangeSetModel, id: string) => result.subjects.find((node) => node.row.selectorId === id)!;

  it('distinguishes a loaded empty subject from unread, pending and failed subjects', () => {
    const emptyId = 'synthetic-empty';
    const input = l104Inputs({
      outline: { ...outline, invariants: [
        ...outline.invariants,
        { selector_id: emptyId, id: 'INV-EMPTY', summary: 'No recorded locations', change: 'added', meaning: { state: 'none', labels: [] } },
      ] },
      cards: partial([firstId, emptyId], [secondId], { [l104Selector('INV-N7SK8E')]: 'This invariant could not be read' }),
    });
    const result = model(input);
    expect(subject(result, firstId).cards).toBe('ready');
    expect(subject(result, firstId).changed.length).toBeGreaterThan(0);
    expect(subject(result, emptyId)).toMatchObject({ cards: 'ready', changed: [] });
    expect(subject(result, secondId).cards).toBe('reading');
    expect(subject(result, l104Selector('INV-N7SK8E'))).toMatchObject({ cards: 'unavailable', reason: 'This invariant could not be read' });
    expect(subject(result, l104Selector('INV-ESF2CT'))).toMatchObject({ cards: 'unread', reason: 'clarify · … loc.' });
    expect(result.counts).toMatchObject({ changedLocations: undefined, changedFiles: undefined, folded: undefined });
  });

  it('publishes aggregate counts only after all shown invariants have answered', () => {
    const shownIds = outline.invariants.map((row) => row.selector_id);
    const complete = model(l104Inputs({ cards: partial(shownIds) }));
    const legacy = model(l104Inputs());
    expect(complete.counts).toEqual(legacy.counts);
    const incomplete = model(l104Inputs({ cards: partial(shownIds.slice(1)) }));
    expect(incomplete.counts).toMatchObject({ changedLocations: undefined, changedFiles: undefined, folded: undefined });
  });

  it('requires coverage of shown subjects, including a selected unchanged subject, without requiring hidden rows', () => {
    const cards = { ...cardsOf(l181Answer), coverage: { loaded: [L181_SUBJECT], pending: [], failures: {} } };
    expect(model(l181Inputs({ cards })).counts.changedLocations).toBe(15);
    const selected = changeSetModel(l181Inputs({ cards }), 'priority', 'changed', {
      selection: { kind: 'subject', subject: { kind: 'invariant', id: 'synthetic-unchanged-0' } },
    });
    expect(selected.counts.changedLocations).toBeUndefined();
    expect(selected.subjects.find((node) => node.row.selectorId === 'synthetic-unchanged-0')?.cards).toBe('unread');
  });

  it.each<ReviewSort>(['priority', 'currentness', 'judgment:risk'])(
    'keeps %s subject and family order stable as individual cards arrive', (sort) => {
      const family = { selector_id: 'family', id: 'FAM-TEST', title: 'One family', change: 'unchanged' as const,
        members: [firstId, secondId].map((selector_id) => ({ selector_id, id: selector_id === firstId ? 'INV-9ZKYWQ' : 'INV-EWGTN0', membership: 'both' as const })) };
      const options = { categories: [{ name: 'risk', scale: [] }], outline: { ...outline, families: [family] }, currentness: { after: { invariants: [
        { id: 'INV-9ZKYWQ', state: 'current' as const, entries: [] },
        { id: 'INV-EWGTN0', state: 'stale' as const, entries: [] },
      ] } } };
      const empty = model(l104Inputs({ ...options, cards: partial([]) }), sort);
      const one = model(l104Inputs({ ...options, cards: partial([firstId]) }), sort);
      const all = model(l104Inputs({ ...options, cards: partial(outline.invariants.map((row) => row.selector_id)) }), sort);
      expect(all.sort).toBe(sort);
      expect(ids(one)).toEqual(ids(empty));
      expect(ids(all)).toEqual(ids(empty));
      expect(all.groups.map((group) => group.key)).toEqual(empty.groups.map((group) => group.key));
    },
  );

  it('does not let a visited stale excerpt change the currentness order based on the comparison summary', () => {
    const staleEntries = entries.map((entry) => entry.invariant_key === firstId
      ? { ...entry, after: { ...entry.after, currentness: 'stale' as const } } : entry);
    const cards = { ...cardsOf(l104Answer, staleEntries.filter((entry) => entry.invariant_key === firstId)), coverage: { loaded: [firstId], pending: [], failures: {} } };
    const currentness = { after: { invariants: [
      { id: 'INV-9ZKYWQ', state: 'current' as const, entries: [] },
      { id: 'INV-EWGTN0', state: 'unrealized' as const, entries: [] },
    ] } };
    const result = model(l104Inputs({ currentness, cards }), 'currentness');
    expect(ids(result)[0]).toBe('INV-EWGTN0');
    expect(subject(result, firstId).currentness).toBe('stale');
  });

  it('keeps memory sorts actionable and applies them only after all shown values are loaded', () => {
    const memory = { state: 'available' as const, tree: 'd4eb8510' };
    const input = (loaded: string[], pending: string[] = []) => l104Inputs({ cards: { ...partial(loaded, pending), memory } });
    const unread = model(input([firstId]), 'incoming-links');
    expect(unread.sort).toBe('priority');
    expect(unread.requested).toBe('incoming-links');
    expect(unread.sorts.find((option) => option.sort === 'incoming-links')).toMatchObject({ available: true, reason: 'Loads location values for this comparison' });
    const reading = model(input([firstId], [secondId]), 'incoming-links');
    expect(reading.sorts.find((option) => option.sort === 'incoming-links')).toMatchObject({ available: true, reason: 'Reading memory values for the shown invariants' });
    const complete = model(input(outline.invariants.map((row) => row.selector_id)), 'incoming-links');
    expect(complete.sort).toBe('incoming-links');
  });

  it('keeps a real memory-value failure disabled even with partial coverage', () => {
    const cards = { ...partial([firstId]), memory: { state: 'unavailable' as const, tree: 'd4eb8510', reason: 'This tree is not indexed' } };
    const result = model(l104Inputs({ cards }), 'incoming-links');
    expect(result.sort).toBe('priority');
    expect(result.sorts.find((option) => option.sort === 'incoming-links')).toMatchObject({ available: false, reason: 'This tree is not indexed' });
  });
});

describe('complete location inventory before detail reads', () => {
  const inventory = l104Entries().map((source) => {
    const entry = structuredClone(source);
    delete entry.memory;
    for (const side of [entry.before, entry.after]) {
      delete side.excerpt;
      delete side.excerpt_truncated;
      delete side.facet;
      delete side.rationale;
      delete side.content;
    }
    return entry;
  });
  const emptyDetails = { ...cardsOf(l104Answer, []), coverage: { loaded: [], pending: [], failures: {} } };

  it('preserves every sidebar location and aggregate count without loading any invariant details', () => {
    const result = model(l104Inputs({ inventory, comparison: l104Answer.comparison!, cards: emptyDetails }));
    const full = model(l104Inputs());
    expect(result.counts).toEqual(full.counts);
    expect(ids(result)).toEqual(ids(full));
    expect(result.subjects.map((node) => [node.row.id, node.changed.length, node.unchanged.length, node.notComparable.length]))
      .toEqual(full.subjects.map((node) => [node.row.id, node.changed.length, node.unchanged.length, node.notComparable.length]));
    expect(result.subjects.every((node) => node.cards === 'unread' && node.locationsKnown)).toBe(true);
    expect(result.subjects[0].reason).toBe(full.subjects[0].reason);
    expect(result.blocks.map((node) => node.key)).toEqual(full.blocks.map((node) => node.key));
    expect(result.blocks.every((node) => node.entry.before.excerpt === undefined && node.entry.after.excerpt === undefined)).toBe(true);
  });

  it('replaces metadata by entry ID when detail arrives without duplicating counts or changing the tree order', () => {
    const selected = l104Selector('INV-9ZKYWQ');
    const entries = l104Entries().filter((entry) => entry.invariant_key === selected);
    const cards = { ...cardsOf(l104Answer, entries), coverage: { loaded: [selected], pending: [], failures: {} } };
    const before = model(l104Inputs({ inventory, cards: emptyDetails }));
    const after = model(l104Inputs({ inventory, cards }));
    expect(after.counts).toEqual(before.counts);
    expect(ids(after)).toEqual(ids(before));
    expect(after.blocks.map((node) => node.key)).toEqual(before.blocks.map((node) => node.key));
    expect(after.subjects.find((node) => node.row.selectorId === selected)?.cards).toBe('ready');
    const node = after.subjects.find((subject) => subject.row.selectorId === selected)!;
    const detailed = [...node.changed, ...node.unchanged, ...node.notComparable]
      .find((location) => location.entry.id === entries[0].id)!;
    expect(detailed.entry).toEqual(entries[0]);
    expect(detailed.entry.after.excerpt).toBeDefined();
  });

  it('shows complete metadata while a detail request fails or is still pending', () => {
    const pending = model(l104Inputs({ inventory, comparison: l104Answer.comparison!, cards: { state: 'reading' } }));
    const failed = model(l104Inputs({ inventory, comparison: l104Answer.comparison!, cards: { state: 'unavailable', reason: 'Detail read failed' } }));
    expect(pending.counts).toEqual(model(l104Inputs()).counts);
    expect(failed.counts).toEqual(pending.counts);
    expect(pending.subjects.every((node) => node.cards === 'reading' && node.locationsKnown)).toBe(true);
    expect(failed.subjects.every((node) => node.cards === 'unavailable' && node.locationsKnown)).toBe(true);
  });

  it('does not treat the complete inventory as complete memory-sort values', () => {
    const result = model(l104Inputs({ inventory, cards: emptyDetails }), 'incoming-links');
    expect(result.sort).toBe('priority');
    expect(result.requested).toBe('incoming-links');
    expect(result.sorts.find((option) => option.sort === 'incoming-links')?.available).toBe(true);
  });

  it('exposes inventory failure without presenting unknown global counts as zero', () => {
    const result = model(l104Inputs({ inventoryProblem: 'Inventory could not be read', cards: emptyDetails }));
    expect(result.locations).toBe('unavailable');
    expect(result.counts.changedLocations).toBeUndefined();
    expect(result.subjects.every((node) => !node.locationsKnown)).toBe(true);
  });

  it('keeps a block-selected unchanged invariant visible using inventory before details arrive', () => {
    const entry = inventory[0];
    const source = l104Inputs();
    const outline: ReviewOutline = {
      ...source.outline!,
      invariants: source.outline!.invariants.map((row) => ({ ...row, change: 'unchanged' })),
    };
    const result = changeSetModel({ ...source, outline, inventory, cards: emptyDetails }, 'priority', 'changed', {
      selection: { kind: 'block', block: blockIdentityOf(entry, l104Answer.comparison!) },
    });
    expect(result.shownInvariantIds).toEqual([entry.invariant_key]);
    expect(result.subjects[0].cards).toBe('unread');
  });
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
