// Option A dependent truth view and tree listing: deferred dependents carry their derived
// `deferred` badge, open deferral cards, and moved links, and appear under Records -> Dependents.
import { fireEvent, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import { captured, open, overrides, type Body } from './readerTestSupport';

const DPD_ID = 'DPD-X6G7K3';

function dependentRecordAnswer(
  overridesPart: Partial<{
    state: 'current' | 'stale' | 'unverifiable' | 'deferred';
    deferred: boolean;
    deferrals: Record<string, unknown>[];
    movedLinks: Record<string, unknown>[];
  }> = {},
): Body {
  const deferrals = overridesPart.deferrals ?? [
    {
      id: 'DFR-A1B2C3',
      passage: 'Section: knowledge_read dependent view fields',
      reason: 'Wait until the sibling writer leaf lands the deferral payload schema.',
      condition: {
        task: '261112-BUG-L53',
        file: 'mcp/src/agents_remember/application/knowledge_reader/marks.py',
        note: 'Update once both dependent fields and writer schema settle',
      },
      fileBlob: 'a'.repeat(40),
      owner: 'curator',
      createdAt: '2026-11-23T12:00:00Z',
      createdByLeaf: '261112-BUG-L52',
      age: '2d',
      state: 'open',
    },
    {
      id: 'DFR-D4E5F6',
      passage: 'Section: closeout gate warning',
      reason: 'Target task finished before this doc was refreshed.',
      condition: { task: '261110-OLD-L01' },
      owner: 'worker',
      createdAt: '2026-11-20T12:00:00Z',
      createdByLeaf: '261112-BUG-L51',
      age: '5d',
      state: 'orphaned',
    },
  ];
  const movedLinks = overridesPart.movedLinks ?? [
    {
      position: 1,
      kind: 'file',
      path: 'mcp/src/agents_remember/application/knowledge_reader/marks.py',
      state: 'stale',
      reason: 'recorded blob changed',
    },
  ];
  return {
    state: 'view',
    view: 'record',
    selection: captured['record-invariant'].selection,
    record: {
      id: DPD_ID,
      kind: 'dependent',
      status: '',
      revision: 3,
      title: 'docs/reference/mcp-tools.md',
      recordState: 'reviewed',
      writer: { role: 'curator', harness: 'claude', model: 'claude-opus-5-5' },
      path: `knowledge/dependents/${DPD_ID}-mcp-tools.json`,
      document: {
        schema: 'ar-dependent/v1',
        id: DPD_ID,
        revision: 3,
        document: 'docs/reference/mcp-tools.md',
        confirmed: { task: '261112-BUG-L52', role: 'curator', commit: 'b'.repeat(40) },
        stands_on: [
          {
            kind: 'file',
            path: 'mcp/src/agents_remember/application/knowledge_reader/marks.py',
            blob: 'a'.repeat(40),
          },
        ],
        deferrals,
      },
    },
    prose: { path: `knowledge/dependents/${DPD_ID}-mcp-tools.md`, state: 'absent' },
    outgoing: [],
    incoming: [],
    facet: {
      document: 'docs/reference/mcp-tools.md',
      revision: 3,
      confirmed: { task: '261112-BUG-L52', role: 'curator', commit: 'b'.repeat(40) },
    },
    dependent: {
      document: 'docs/reference/mcp-tools.md',
      state: overridesPart.state ?? 'deferred',
      deferred: overridesPart.deferred ?? true,
      ...(deferrals.length ? { deferrals } : {}),
      ...(movedLinks.length ? { movedLinks } : {}),
    },
    timeline: { sources: { record: { state: 'read', events: 0 } }, events: [] },
  };
}

it('renders a deferred dependent truth view with deferred badge, deferrals, and moved links', async () => {
  overrides[`record:${DPD_ID}`] = dependentRecordAnswer();
  const truth = await open({ view: 'record', id: DPD_ID }).findByTestId('reader-truth-view');

  expect(within(truth).getByTestId('record-state').dataset.state).toBe('deferred');
  expect(within(truth).getByTestId('record-lead').textContent).toBe('docs/reference/mcp-tools.md');

  // Deferrals are rendered by DependentPart, not duplicated inside generic RecordFields.
  expect(within(truth).getByTestId('record-fields').querySelector('[data-field="deferrals"]')).toBeNull();

  const deferralSection = within(truth).getByTestId('dependent-deferrals');
  const cards = within(deferralSection).getAllByTestId('dependent-deferral');
  expect(cards).toHaveLength(2);

  expect(cards[0].dataset.deferral).toBe('DFR-A1B2C3');
  expect(cards[0].dataset.state).toBe('open');
  expect(within(cards[0]).getByTestId('deferral-state').dataset.state).toBe('open');
  expect(cards[0].textContent).toContain('curator');
  expect(cards[0].textContent).toContain('2d');
  expect(cards[0].textContent).toContain('Section: knowledge_read dependent view fields');
  expect(within(cards[0]).getByTestId('deferral-condition').textContent).toContain('261112-BUG-L53');
  expect(within(cards[0]).getByTestId('deferral-condition').textContent).toContain(
    'mcp/src/agents_remember/application/knowledge_reader/marks.py',
  );
  expect(within(cards[0]).getByTestId('deferral-condition').textContent).toContain(
    'Update once both dependent fields and writer schema settle',
  );

  expect(cards[1].dataset.deferral).toBe('DFR-D4E5F6');
  expect(within(cards[1]).getByTestId('deferral-state').dataset.state).toBe('orphaned');

  const movedSection = within(truth).getByTestId('dependent-moved-links');
  expect(within(movedSection).getAllByTestId('dependent-moved-link')).toHaveLength(1);
  expect(movedSection.textContent).toContain('recorded blob changed');
});

it('renders a non-deferred stale dependent without a deferrals section', async () => {
  overrides[`record:${DPD_ID}`] = dependentRecordAnswer({
    state: 'stale',
    deferred: false,
    deferrals: [],
  });
  const truth = await open({ view: 'record', id: DPD_ID }).findByTestId('reader-truth-view');

  expect(within(truth).getByTestId('record-state').dataset.state).toBe('stale');
  expect(within(truth).queryByTestId('dependent-deferrals')).toBeNull();
  expect(within(truth).getByTestId('dependent-moved-links')).toBeTruthy();
});

it('lists Dependents under Records in KnowledgeTree and opens the dependent truth view', async () => {
  const recordsAnswer = captured.records as Body & { kinds: Record<string, Record<string, unknown>[]> };
  overrides['records:'] = {
    ...recordsAnswer,
    kinds: {
      ...recordsAnswer.kinds,
      dependent: [
        {
          id: DPD_ID,
          kind: 'dependent',
          status: '',
          revision: 3,
          title: 'docs/reference/mcp-tools.md',
          recordState: 'reviewed',
          writer: { role: 'curator', harness: 'claude', model: 'claude-opus-5-5' },
        },
      ],
    },
  };
  overrides[`record:${DPD_ID}`] = dependentRecordAnswer();

  const view = open({ view: 'path', path: '.' });
  await view.findByTestId('reader-path-view');
  const tree = view.getByTestId('knowledge-tree');

  fireEvent.click(await within(tree).findByRole('treeitem', { name: /Records/ }));
  const dependentsBranch = await within(tree).findByRole('treeitem', { name: /Dependents/ });
  fireEvent.click(dependentsBranch);

  const dependentItem = await within(tree).findByRole('treeitem', {
    name: /docs\/reference\/mcp-tools\.md/,
  });
  fireEvent.click(dependentItem);

  const truth = await view.findByTestId('reader-truth-view');
  expect(truth.dataset.record).toBe(DPD_ID);
  expect(within(truth).getByTestId('record-state').dataset.state).toBe('deferred');
});
