import { describe, expect, it } from 'vitest';
import type { ReviewOverview } from '../../data/reviewTrees';
import { changeSetModel, filterTree, type ChangeSetInputs, type ChangeSetModel } from './changeSetOrder';
import { changeSetModel as oracle } from './changeSetOrder';
import { l104Inputs, l181Inputs, l104Selector } from './changeSetOrder.test-utils';
import l104Summary from './compact-l104.fixture.json';
import l181Summary from './compact-l181.fixture.json';
import type { ReviewSort } from './reviewSelection';

function compact(input: ChangeSetInputs, overview: ReviewOverview): ChangeSetInputs {
  if (input.cards.state !== 'ready') throw new Error('fixture must have cards');
  return { ...input, inventory: undefined, comparison: input.cards.comparison, overview,
    cards: { state: 'ready', entries: [], comparison: input.cards.comparison, coverage: { loaded: [], pending: [], failures: {} } } };
}
function order(model: ChangeSetModel) {
  return model.groups.map((group) => [group.family?.row.selectorId,
    group.subjects.map((node) => [node.row.selectorId, node.reason, node.currentness])]);
}

function sharedFamilies(): ChangeSetInputs {
  const input = l104Inputs();
  const family = (id: string, members: string[]) => ({ selector_id: id, id, title: id, change: 'unchanged' as const,
    members: members.map((member) => ({ selector_id: l104Selector(member), id: member, membership: 'both' as const })) });
  return { ...input, outline: { ...input.outline!, families: [family('FAM-A', ['INV-9ZKYWQ', 'INV-EWGTN0']), family('FAM-B', ['INV-9ZKYWQ'])] } };
}

describe('server compact summaries against the complete model oracle', () => {
  for (const [name, inputs, summary] of [['L104', l104Inputs, l104Summary], ['L181', l181Inputs, l181Summary]] as const)
    for (const sort of ['priority', 'authored', 'currentness'] satisfies ReviewSort[])
      for (const view of ['changed', 'all'] as const)
        it(`${name} ${sort} ${view}: same counts/order without location nodes`, () => {
          const full = inputs();
          const expected = oracle(full, sort, view, { selection: null });
          const reduced = changeSetModel(compact(full, summary as unknown as ReviewOverview), sort, view, { selection: null });
          expect(reduced.counts).toEqual(expected.counts);
          expect(order(reduced)).toEqual(order(expected));
          expect(reduced.blocks).toEqual([]);
          expect(reduced.subjects.every((node) => !node.locationsKnown)).toBe(true);
          expect(reduced.subjects.every((node) => node.counts !== undefined)).toBe(true);
        });

  for (const view of ['changed', 'all'] as const)
    it(`counts shared family membership once in ${view}`, () => {
      const full = sharedFamilies();
      const expected = oracle(full, 'priority', view, { selection: null });
      const reduced = changeSetModel(compact(full, l104Summary as unknown as ReviewOverview), 'priority', view, { selection: null });
      expect(reduced.counts).toEqual(expected.counts);
      expect(order(reduced)).toEqual(order(expected));
      expect(reduced.groups.flatMap((group) => group.subjects).filter((node) => node.row.id === 'INV-9ZKYWQ')).toHaveLength(2);
      expect(reduced.subjects.filter((node) => node.row.id === 'INV-9ZKYWQ')).toHaveLength(1);
    });

  it('keeps same-region entries counted and restores their single drawn row only on selection', () => {
    const full = l181Inputs();
    const reduced = compact(full, l181Summary as unknown as ReviewOverview);
    const before = changeSetModel(reduced, 'priority', 'changed', { selection: null });
    const after = changeSetModel({ ...reduced, cards: full.cards }, 'priority', 'changed', { selection: null });
    expect(before.counts).toEqual(after.counts);
    expect(before.counts.changedLocations).toBe(15);
    expect(after.blocks).toHaveLength(13);
  });

  it('does not turn an unavailable summary into known zero', () => {
    const reduced = compact(l181Inputs(), { state: 'unavailable', subjects: [], reason: 'missing index' });
    const result = changeSetModel(reduced, 'priority', 'changed', { selection: null });
    expect(result.counts.changedLocations).toBeUndefined();
    expect(result.subjects.every((node) => node.counts === undefined)).toBe(true);
  });

  it('uses complete metadata for filters, including unvisited path and symbol', () => {
    const full = l181Inputs();
    if (full.cards.state !== 'ready') throw new Error('fixture');
    const reduced = compact(full, l181Summary as unknown as ReviewOverview);
    const inventory = full.cards.entries.map((entry) => ({ ...entry, after: { ...entry.after, symbol: 'uniqueSymbol' } }));
    const loadedMetadata = changeSetModel({ ...reduced, inventory }, 'priority', 'changed', { selection: null });
    const expected = oracle({ ...full, cards: { ...full.cards, entries: inventory } }, 'priority', 'changed', { selection: null });
    const projection = (model: ChangeSetModel, query: string) => filterTree(model, query).groups.map((group) => group.subjects.map((item) => ({ id: item.node.row.selectorId, entries: item.locations.map((node) => node.entry.id), groups: item.groups })));
    for (const query of ['turn_ended.ts', 'uniqueSymbol', 'proof', 'enforce'])
      expect(projection(loadedMetadata, query)).toEqual(projection(expected, query));
  });
});
