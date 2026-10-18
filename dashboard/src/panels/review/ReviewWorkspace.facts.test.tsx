// Synthetic leaf facts over a current public task answer: verifies workspace wiring, not classification.
import { render, cleanup } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import task from './l86.member.captured.json';
import trees from './l86.trees.captured.json';
import type { ReviewPayload, ReviewOutline } from '../../data/review';
import type { ReviewTreesRead } from '../../data/reviewTrees';
import { ReviewWorkspace, useWorkspaceState, type ReadingStatus } from './ReviewWorkspace';

// Synthetic presentation composition over genuine captured data; not producer/classification evidence.
const payload = task.payload as unknown as ReviewPayload;
const outline: ReviewOutline = { state: 'outlined', detail: 'Synthetic', families: [{
  id: 'FAM-AAAAAA', selector_id: 'family', title: 'A family', change: 'unchanged',
  members: [{ id: 'INV-AAAAAA', selector_id: 'one', membership: 'both' }],
}], invariants: [{ id: 'INV-AAAAAA', selector_id: 'one', summary: 'Code-only member', change: 'unchanged' }] };
const ready: ReviewTreesRead = { phase: 'trees', trees: { ...trees,
  code_changes: { state: 'facts', invariants: [{ invariant: 'INV-AAAAAA', invariant_key: 'one',
    implementation: 'established', proof: false, range_unresolved: false, evidence: [], unknown_reasons: [] }] },
  worklist: { source: 'computed', state: 'complete', bound: true, history_rows: [], changes: [], incomplete: [],
    items: [{ id: 'open', kind: 'touched_invariant', subject: 'INV-AAAAAA', facts: {} }],
    item_states: { open: { state: 'open', reason: 'No row' } } },
} as Extract<ReviewTreesRead, { phase: 'trees' }>['trees'] };
function Probe({ read = ready, body = payload, reading = null }: {
  read?: ReviewTreesRead; body?: ReviewPayload; reading?: ReadingStatus | null;
}) {
  const state = useWorkspaceState();
  const candidate = body.candidate;
  return <ReviewWorkspace payload={body} reading={reading} repo={candidate.repository_id} master={candidate.master}
    leaf={candidate.leaf_id} state={state} leafTrees={read} onPageSelect={() => {}}
    navigation={{ catalogue: { loading: false, entries: [], outline, refresh: () => {} }, onSelect: () => {} }} />;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

type MovementState = NonNullable<ReviewPayload['sync_movement']>['binding_state'];
function movement(owner: 'sync' | 'external', state: MovementState, epoch = '') {
  const external = owner === 'external';
  const reason = state === 'not-measured'
    ? external ? 'The input was an uncommitted tree.' : 'The sync input was not measured.'
    : state === 'unavailable' ? `The ${owner} movement could not be read.` : undefined;
  return {
    binding_state: state,
    statement: `${epoch}${external ? 'No observed work-branch head.' : 'A sync moved the compared tree.'}`,
    reason: reason === undefined ? undefined : `${epoch}${reason}`,
    moved_identities: state === 'stale' ? [`code-work-branch:${epoch}synthetic-identity`] : [],
    successor_action: external ? 'Review the exact pair.' : 'Review a successor comparison.',
    owner_specific: { channel: owner, epoch, retained: ['owner-specific fact', { count: 2 }] },
  };
}
function movementBody(sync: MovementState | null | undefined, external: MovementState | null | undefined, epoch = '') {
  const body = structuredClone(payload);
  body.sync_movement = sync == null ? sync : movement('sync', sync, epoch);
  body.external_git_movement = external == null ? external : movement('external', external, epoch);
  return body;
}
function expectOpenState(view: ReturnType<typeof render>, text: string) {
  const state = view.getByTestId('review-movement-status');
  expect(state.textContent).toBe(text);
  expect(state.dataset.reviewContent).toBe('movement-state');
  expect(state.closest('details:not([open])')).toBeNull();
  expect(state.textContent).not.toContain('synthetic-identity');
}
function expectOpenFacts(view: ReturnType<typeof render>, body: ReviewPayload) {
  const facts = view.getByTestId('review-movement-facts');
  expect(facts.closest('details:not([open])')).toBeNull();
  expect(facts.dataset.reviewContent).toBe('movement-facts');
  const supplied = { sync_movement: body.sync_movement, external_git_movement: body.external_git_movement };
  expect(JSON.parse(facts.textContent!)).toEqual(JSON.parse(JSON.stringify(supplied)));
  expect(view.getAllByTestId('review-movement-facts')).toHaveLength(1);
  expectOpenState(view, `Sync movement: ${body.sync_movement?.binding_state ?? 'not-measured'} · External Git movement: ${body.external_git_movement?.binding_state ?? 'not-measured'}`);
  const identities = view.getByTestId('review-scope-task').closest('details')!;
  expect(identities.open).toBe(false);
  expect(identities.dataset.disclosure).toBe('provenance');
  for (const closed of view.container.querySelectorAll('details:not([open])')) {
    expect(closed.querySelector('[data-review-content]')).toBeNull();
    for (const owner of [body.sync_movement, body.external_git_movement]) {
      if (!owner) continue;
      for (const text of [owner.statement, owner.reason, owner.successor_action, `"binding_state": "${owner.binding_state}"`]) {
        if (text) expect(closed.textContent).not.toContain(text);
      }
    }
  }
}
it.each([
  ['current sync and current external', 'current', 'current'],
  ['stale sync and stale external', 'stale', 'stale'],
  ['not-measured sync and not-measured external', 'not-measured', 'not-measured'],
  ['unavailable sync and unavailable external', 'unavailable', 'unavailable'],
  ['mixed stale sync and not-measured external', 'stale', 'not-measured'],
] as const)('keeps both movement owners open for %s', (_label, sync, external) => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}')));
  const body = movementBody(sync, external);
  const view = render(<Probe body={body} />);
  expectOpenFacts(view, body);
  expect(view.getByTestId('review-movement-facts').textContent).toContain('A sync moved the compared tree.');
  if (external === 'not-measured')
    expect(view.getByTestId('review-movement-facts').textContent).toContain('The input was an uncommitted tree.');
});
it.each([
  ['sync only', 'stale', null],
  ['external only', undefined, 'unavailable'],
  ['neither owner', null, undefined],
] as const)('keeps the movement presence boundary for %s', (_label, sync, external) => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}')));
  const body = movementBody(sync, external);
  const view = render(<Probe body={body} />);
  if (sync || external) expectOpenFacts(view, body);
  else {
    expect(view.queryByTestId('review-movement-facts')).toBeNull();
    expectOpenState(view, 'Sync movement: not-measured · External Git movement: not-measured');
  }
});
it('does not reveal previous movement facts while the next subject is pending or failed', () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}')));
  const previous = movementBody('stale', 'not-measured', 'PREVIOUS-OWNER-EPOCH ');
  const next = movementBody('current', 'unavailable', 'NEXT-OWNER-EPOCH ');
  const view = render(<Probe body={previous} />);
  expectOpenFacts(view, previous);
  const reading: ReadingStatus = { key: 'next-subject', subject: 'INV-NEXT', label: 'Next subject', problem: null };
  view.rerender(<Probe body={previous} reading={reading} />);
  expectOpenState(view, 'Sync movement: being read · External Git movement: being read');
  expect(view.queryByTestId('review-movement-facts')).toBeNull();
  expect(view.container.textContent).not.toContain('PREVIOUS-OWNER-EPOCH');
  view.rerender(<Probe body={previous} reading={{ ...reading, problem: <p>The next subject could not be read.</p> }} />);
  expectOpenState(view, 'Sync movement: unavailable · External Git movement: unavailable');
  expect(view.queryByTestId('review-movement-facts')).toBeNull();
  expect(view.container.textContent).not.toContain('PREVIOUS-OWNER-EPOCH');
  view.rerender(<Probe body={next} />);
  expectOpenFacts(view, next);
  expect(view.container.textContent).not.toContain('PREVIOUS-OWNER-EPOCH');
  expect(view.getByTestId('review-movement-facts').textContent).toContain('NEXT-OWNER-EPOCH');
});

it('wires the one held leaf read into summary, unread tree rows and Worklist status', () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}')));
  const view = render(<Probe />);
  expect(view.getByTestId('review-navigation-summary').textContent).toContain('1 code changes');
  expect(view.getByRole('button', { name: 'INV-AAAAAA Code-only member code changed' })).toBeTruthy();
  expect(view.getByRole('button', { name: 'Worklist 1 open' })).toBeTruthy();
});

it('separates file attribution from open work and does not call an unmeasured inventory empty', () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}')));
  const body = structuredClone(payload);
  body.source.inventory = { ...body.source.inventory, state: 'unavailable', listed_total: 0, entries: [] };
  const view = render(<Probe read={{ phase: 'loading' }} body={body} />);
  expect(view.getByRole('button', { name: /Changes without an invariant/ })).toBeTruthy();
  expect(view.getByRole('button', { name: /Changes of unknown attribution/ })).toBeTruthy();
  expect(view.getByRole('button', { name: 'Worklist …' })).toBeTruthy();
  expect(view.getByTestId('review-navigation-summary').textContent).toContain('the change set could not be measured');
});
