// MIK-R33 in the mounted reviewer: j/k selection moves over the served bodies of the store-authored
// comparison (triage.capture-provenance.json). `ReviewSurface` is the real component and only `fetch`
// is stubbed. A move selects the next change's subject exactly as a click does, and keeps the family
// context, the focus and the mounted workspace (ICR-R32 rule 6, L48).
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type {
  ReviewFamilyContextEntry,
  ReviewFamilyMember,
  ReviewOutline,
} from '../../data/review';
import type { ReviewResult } from '../../data/review';
import { memberChanges } from './changeTriage';
import { ReviewSurface } from './ReviewSurface';
import { treeOrderStore } from './triageOrderPreference';
import { treeViewStore } from './triageViewPreference';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const family = captured<ReviewResult>('triage.family.captured.json');
const memberA = captured<ReviewResult>('triage.memberA.captured.json');
const memberH = captured<ReviewResult>('triage.memberH.captured.json');
const entries = captured<unknown>('triage.entries.captured.json');
const J = { key: 'j', code: 'KeyJ' };

const subjectOf = (result: ReviewResult) => result.payload!.knowledge.revision_selection!.record_id;

// The entries answer carries the tree outline since MIK-R86; this capture predates it, so the
// served answer joins a synthetic outline over the captured family context. Only index fields
// are synthesized (selector ids in authored order, as the server emits them); change facts stay
// the walked ones — cf. FamilyTree.triageReal.test.tsx.
function syntheticOutline(entry: ReviewFamilyContextEntry): ReviewOutline {
  const before = new Set(entry.before.members.map((row) => row.invariant_id));
  const after = new Set(entry.after.members.map((row) => row.invariant_id));
  const facts = memberChanges(entry);
  const seen = new Map<string, ReviewFamilyMember>();
  for (const row of [...entry.before.members, ...entry.after.members])
    if (row.invariant_id && !seen.has(row.invariant_id)) seen.set(row.invariant_id, row);
  const positioned = [...seen.values()].sort(
    (a, b) =>
      (facts.get(a.member_id)?.authored_position ?? Number.MAX_SAFE_INTEGER) -
      (facts.get(b.member_id)?.authored_position ?? Number.MAX_SAFE_INTEGER),
  );
  return {
    state: 'outlined',
    detail: 'Synthetic outline for the captured triage family; change facts are the walked ones.',
    families: [
      {
        selector_id: entry.family_id,
        id: entry.display_label ?? entry.family_id,
        // A spaced title: the tree keeps titles with a space verbatim, and the case below
        // finds the family row by its recorded label.
        title: `${entry.display_label ?? entry.family_id} (captured family)`,
        change: 'unknown',
        members: positioned.map((row) => ({
          selector_id: row.invariant_id!,
          id: row.display_label ?? row.invariant_id!,
          membership: !before.has(row.invariant_id)
            ? 'joined'
            : !after.has(row.invariant_id)
              ? 'left'
              : 'both',
        })),
      },
    ],
    invariants: positioned.map((row) => ({
      selector_id: row.invariant_id!,
      id: row.display_label ?? row.invariant_id!,
      summary: 'Captured member',
      change: facts.get(row.member_id)?.primary === 'intent' ? 'changed' : 'unchanged',
    })),
  };
}
const refused = {
  state: 'refused',
  refusal: {
    code: 'not-found',
    detail: 'The tree view is not part of this check.',
    next_action: 'Open the mounted tree route.',
  },
};

beforeEach(() => {
  localStorage.clear();
  treeOrderStore.getState().setOrder('triage');
  // The order preamble compares the whole rosters, so the tree shows the All view: since
  // MIK-R86 the default Changed view hides rows without a real change.
  treeViewStore.getState().setView('all');
  const bodies = new Map([family, memberA, memberH].map((body) => [subjectOf(body), body]));
  const servedEntries = {
    ...(entries as Record<string, unknown>),
    outline: syntheticOutline(family.payload!.family_context!.entries[0]),
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      const body = url.pathname.endsWith('/entries')
        ? servedEntries
        : url.pathname.endsWith('/trees') || url.pathname.endsWith('/source-content')
          ? refused
          : bodies.get(url.searchParams.get('selectorId') ?? '');
      if (body === undefined) throw new Error(`Unexpected review request: ${address}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const node = (container: HTMLElement, record: string) =>
  Array.from(container.querySelectorAll<HTMLElement>('[data-tree-node]')).find((one) =>
    one.textContent?.includes(record),
  )!;

it('moves the selection to the next change and keeps family context, focus and workspace', async () => {
  const payload = family.payload!;
  const view = render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      selectorKind="family"
      selectorId={subjectOf(family)}
      onBack={() => undefined}
    />,
  );
  await view.findAllByTestId('review-change-badge', undefined);
  const workspace = view.getByTestId('review-workspace');
  // The centre's member list follows the tree's displayed order (triage, then authored). The
  // tree lists one row per invariant while the centre lists one row per member revision, so the
  // comparison runs at invariant granularity: tree rows carry their invariant, centre revisions
  // resolve to theirs through the served family context.
  const invariantOf = new Map(
    (['before', 'after'] as const).flatMap((side) =>
      family.payload!.family_context!.entries[0][side].members.map(
        (row) => [row.invariant_revision_id, row.invariant_id] as const,
      ),
    ),
  );
  const treeOrder = () => [
    ...new Set(
      view
        .getAllByTestId('review-family-member-open')
        .map((node) => (node as HTMLElement).dataset.subjectId),
    ),
  ];
  const centreOrder = () =>
    view.getAllByTestId('review-center-open-member').map((node) => node.dataset.revision);
  const centreInvariants = () => [
    ...new Set(centreOrder().map((revision) => invariantOf.get(revision!))),
  ];
  expect(centreInvariants()).toEqual(treeOrder());
  // The control computes the next order from the one it shows, and it shows the store's order through
  // a subscription made in a passive effect: each click waits until the control shows its order, so
  // the second click cannot repeat the first.
  const orderShown = (order: string) =>
    waitFor(() => expect(view.getByTestId('review-tree-order').dataset.order).toBe(order));
  fireEvent.click(view.getByTestId('review-tree-order'));
  await orderShown('authored');
  expect(centreInvariants()).toEqual(treeOrder());
  fireEvent.click(view.getByTestId('review-tree-order'));
  await orderShown('triage');
  expect(view.getByTestId('review-surface').dataset.kbzone).toBe('review');
  // The family context is on screen, still badged: since MIK-R86 the breakdown by kind lives in
  // the family review (rule 10), not in the sidebar.
  expect(view.getByTestId('review-family-breakdown').textContent).toContain('of 9');
  node(view.container, 'FAM-F00001').focus();

  // The keymap's binding is a passive effect: a press made before it has run is ignored, as in a
  // browser, so the press is made again until the selection moves.
  const current = () => view.container.querySelector('[data-tree-node][aria-current="true"]');
  const before = current();
  await waitFor(() => {
    if (current() === before) fireEvent.keyDown(document.activeElement!, J);
    expect(current()).not.toBe(before);
  });
  // The revised member's subject is read, as a click would read it.
  await view.findByTestId('review-center-member', undefined);
  await waitFor(() => {
    const text = view.getByTestId('review-center-column').textContent ?? '';
    // A compared statement reads word-diffed on a tree comparison (MIK-R35). The assertion
    // pins the actual A operand words through that DOM — the revision label, the shared stem
    // and both word-diff fragments — so wrong or missing wording fails it. Do not weaken this
    // to the label alone (L154-CODE-R1-F1).
    expect(text).toContain('Changed statement · revision r1 → r2');
    expect(text).toContain('INV-AAAAAA holds');
    expect(text).toContain('removed: (r1).');
    expect(text).toContain('added: (r2).');
  });
  const first = node(view.container, 'INV-AAAAAA');
  expect(first.getAttribute('aria-current')).toBe('true');
  expect(document.activeElement).toBe(first);
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  // The family context stays on screen under the member review.
  expect(view.getByTestId('review-center-member-family').textContent).toContain('FAM-F00001');

  fireEvent.keyDown(document.activeElement!, J);
  await waitFor(() =>
    expect(view.getByTestId('review-center-column').textContent).toContain(
      'INV-HHHHHH holds (r1).',
    ),
  );
  const second = node(view.container, 'INV-HHHHHH');
  expect(second.getAttribute('aria-current')).toBe('true');
  expect(document.activeElement).toBe(second);
  expect(view.getByTestId('review-workspace')).toBe(workspace);
});
