// Ordinary entry uses the real catalogue/read cycle; captured records supply the semantic content.
//
// The page opens with nothing selected (MIK-R89 v2 rule 2): the opening's one review read is the task
// context's and waits for no catalogue, and a catalogue that answers selects nothing. The first two
// cases answer immediately and prove eventual content. The cases after them hold every review reply
// open until the test releases it, because what they pin happens BETWEEN a selection and its answer:
// the workspace, the tree and the open disclosures are the same DOM nodes throughout; only the
// selected subject's slot is pending, and it names the requested subject; nothing of the previous
// subject is labelled as the new one; a subject already read for this comparison is shown again
// without a request; the latest of several quick selections is the one that settles; a failed or
// refused selection is stated in the slot for that subject while the shell stays usable; focus the
// reader moved while waiting is not taken back; and a catalogue that answers late does not move a
// reader who has started working.
//
// The last two such cases force an order a busy machine produces on its own: the reader selects
// again in the turn in which the previous answer's commit becomes visible, before React has run that
// commit's passive effects. The late effect of the earlier render must leave the new selection's
// focus request alone (`FocusRequest.after` in `ReviewWorkspace.tsx`): focus lands on the row
// selected last, by pointer and by `j` (which walks the blocks, rule 11), and no key press is lost.
//
// MIK-R211 v4 rule 10: a selected family opens its members' overview (`review-family-overview`),
// drawn from the catalogue; its own read is still issued, and the overview's slot holds that read's
// pending state or failure. A selection's read starts from its effects, so a reply is answered once
// it is held.

import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type {
  ReviewPayload,
  ReviewResult,
  ReviewOutline,
  ReviewEntryListResult,
} from '../../data/review';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import { ReviewSurface } from './ReviewSurface';
import { captured as currentCapture, replyFor } from './l86.test-utils';
import { syntheticCatalogue } from './outline.test-utils';
import { treeOrderStore } from './triageOrderPreference';
import { treeViewStore } from './triageViewPreference';
import {
  J,
  press,
  selectedNode,
} from './walk.test-utils';

const captured = currentCapture<ReviewResult>('subjectReview.successorInvariant.captured.json');
const familyBodies = [
  currentCapture<ReviewResult>('subjectReview.successorFamily.captured.json'),
  currentCapture<ReviewResult>('l86.family.captured.json'),
];
const taskBody = currentCapture<ReviewResult>('l86.task.captured.json');
const recorded = captured.payload as ReviewPayload;
const families = familyBodies.map((body) => body.payload!.family_context!.entries[0]);
const synthetic = syntheticCatalogue(familyBodies);
// Explicit synthetic change on the first family keeps these delayed-interaction sequences stable.
synthetic.outline.families[0].change = 'changed';
const catalogue = synthetic.entries;
beforeEach(() => {
  resetCardCachesForTests();
  treeOrderStore.getState().setOrder('authored');
  treeViewStore.getState().setView('all');
});
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
const target = {
  repo: 'agents-remember',
  master: recorded.candidate.master,
  leaf: recorded.candidate.leaf_id,
  history: 'recorded' as const,
  onBack: () => undefined,
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const isReview = (url: URL) => url.pathname === '/api/review/intent';

it('opens with nothing selected from ordinary entry, navigates families and a member in place and preserves the full source population', async () => {
  const urls: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      urls.push(url);
      return response(url.pathname.endsWith('/entries') ? catalogueBody : reviewFor(url));
    }),
  );
  const view = render(<ReviewSurface {...target} />);
  await waitFor(() => expect(view.getAllByTestId('review-family-open')).toHaveLength(families.length));
  await waitFor(() => expect(view.queryByTestId('review-loading')).toBeNull());
  expect(urls.filter(isReview).map((url) => url.searchParams.has('selectorId'))).toEqual([false]);
  expect(view.queryByTestId('review-subject-slot')).toBeNull();
  const workspace = view.getByTestId('review-workspace');
  for (const family of [families[1], families[0]]) {
    fireEvent.click(subjectButton(view, family.family_id));
    await waitFor(() => expect(overviewFamily(view)).toBe(family.family_id));
    expect(urls.some((url) => url.searchParams.get('selectorId') === family.family_id)).toBe(true);
    await waitFor(() => expect(document.activeElement).toBe(subjectButton(view, family.family_id)));
  }
  fireEvent.click(
    within(familyItem(view, families[0].family_id))
      .getAllByTestId('review-family-member-open')
      .find((node) => node.dataset.subjectId === INVARIANT)!,
  );
  await view.findByTestId('review-center-member');
  expect(view.queryByTestId('review-source-explorer')).toBeNull();
  expect(view.getByTestId('review-workspace')).toBe(workspace);
});

it('keeps unreadable knowledge source-only without inventing a family or telling the reader to select one', async () => {
  const unreadable = {
    ...captured,
    payload: {
      ...recorded,
      knowledge: { ...recorded.knowledge, selection_state: 'task_context' },
      family_context: {
        ...recorded.family_context,
        state: 'unavailable',
        entries: [],
        detail: 'Historical snapshot unreadable.',
      },
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      if (url.pathname.endsWith('/entries'))
        return response({
          state: 'refused',
          refusal: {
            code: 'knowledge_unavailable',
            detail: 'The historical knowledge snapshot cannot be read.',
            next_action: 'Inspect source or restore the recorded snapshot.',
          },
        });
      return response(isReview(url) ? unreadable : replyFor(url));
    }),
  );
  const view = render(<ReviewSurface {...target} />);
  await waitFor(() => expect(view.getByTestId('review-change-status').textContent).toContain('cannot be read'));
  // The change set opens as its overview (MIK-R211 v4 rule 10): no family is invented and, with no
  // family or invariant to choose, the reader is not told to select one.
  const overview = await view.findByTestId('review-comparison-overview');
  expect(view.queryByTestId('review-family-open')).toBeNull();
  expect(view.queryAllByTestId('review-subject-section')).toHaveLength(0);
  expect(view.queryByTestId('review-subject-slot')).toBeNull();
  expect(view.queryByTestId('review-source-explorer')).toBeNull();
  expect(view.getByTestId('review-center-column').textContent).not.toMatch(/\bselect\b/i);
  // Read on the overview itself: the column's text runs the tree order's word into the overview's.
  expect(overview.textContent).not.toMatch(/\bselect\b/i);
  // The source stays reachable: its lane opens from the tree.
  fireEvent.click(view.getByTestId('review-tree-lane'));
  await view.findByTestId('review-lane-section');
  expect(view.queryAllByTestId('review-subject-section')).toHaveLength(0);
  // A count that cannot be measured is named as unavailable, never written where a number stands.
  await waitFor(() => expect(view.getByTestId('review-summary-invariants').textContent).toBe('changed invariants: unavailable'));
  expect(view.getByTestId('review-summary').textContent).not.toMatch(/unavailable (invariants|files|changed)/);
  expect(view.getByTestId('review-tree-counts').textContent).not.toMatch(/unavailable files/);
});

// ---------------------------------------------------------------------------------------------------
// Delayed replies.

const INVARIANT = recorded.knowledge.revision_selection!.record_id;
const catalogueBody = {
  state: 'entries',
  entries: catalogue,
  outline: synthetic.outline,
  total_subjects: catalogue.length,
  family_total: catalogue.length,
  invariant_total: 0,
};

function reviewFor(url: URL): ReviewResult {
  if (url.pathname !== '/api/review/intent') return replyFor(url) as ReviewResult;
  const kind = url.searchParams.get('selectorKind');
  if (kind === 'invariant') return captured;
  if (!kind) return taskBody;
  const body = familyBodies.find((item) => item.payload!.knowledge.revision_selection!.record_id === url.searchParams.get('selectorId'));
  if (!body) throw new Error(`Uncaptured family selector: ${url}`);
  return body;
}

// Every review reply waits for `answer`; the catalogue waits only when `holdCatalogue` is set.
function delayedServer({ holdCatalogue = false } = {}) {
  const urls: URL[] = [];
  // Each held reply can be answered, refused by the owner, or lost in transport.
  const held: { url: URL; release: (body?: unknown) => void; lose: () => void }[] = [];
  let releaseCatalogue: (() => void) | undefined;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      urls.push(url);
      if (url.pathname.endsWith('/entries')) {
        if (!holdCatalogue) return response(catalogueBody);
        return await new Promise<Response>((resolve) => {
          releaseCatalogue = () => resolve(response(catalogueBody));
        });
      }
      if (url.pathname !== '/api/review/intent') return response(replyFor(url));
      return await new Promise<Response>((resolve, reject) => {
        held.push({
          url,
          release: (body) => resolve(response(body ?? reviewFor(url))),
          lose: () => reject(new TypeError('fetch failed')),
        });
      });
    }),
  );
  const reviewReads = () => urls.filter((url) => url.pathname === '/api/review/intent');
  return {
    reviewReads,
    contentReads: () => urls.filter((url) => url.pathname.endsWith('/source-content')),
    catalogueReads: () => urls.filter((url) => url.pathname.endsWith('/entries')),
    heldFor: (selectorId: string | null) =>
      held.filter((reply) => reply.url.searchParams.get('selectorId') === selectorId),
    // Release the oldest held reply for this subject (or the oldest of all): with its review, with
    // `outcome.body` (a refusal) or, for `outcome.lost`, as a transport failure.
    answer: async (
      selectorId?: string | null,
      outcome: { body?: unknown; lost?: boolean } = {},
    ) => {
      const find = () => held.findIndex(
        (reply) =>
          selectorId === undefined || reply.url.searchParams.get('selectorId') === selectorId,
      );
      // A selection's read starts from its effects; the reply is answered once it is held.
      await waitFor(() => expect(find()).toBeGreaterThanOrEqual(0));
      const [reply] = held.splice(find(), 1);
      await act(async () => (outcome.lost ? reply.lose() : reply.release(outcome.body)));
    },
    releaseCatalogue: async () => act(async () => releaseCatalogue?.()),
    held,
  };
}

const overviewFamily = (view: ReturnType<typeof render>) =>
  view.queryByTestId('review-family-overview')?.dataset.family;
// The overview is drawn from the catalogue before its family's answer; the answer itself is shown
// when nothing is pending, the surface carries that family's comparison and the overview's slot is
// answered.
function familyShown(view: ReturnType<typeof render>, index: number) {
  expect(overviewFamily(view)).toBe(families[index].family_id);
  expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
  expect(view.getByTestId('review-surface').dataset.comparison).toBe(
    familyBodies[index].payload!.comparison!.reference,
  );
  expect(
    within(view.getByTestId('review-family-overview')).getByTestId('review-subject-slot').dataset.slotState,
  ).toBe('answered');
}

const familyItem = (view: ReturnType<typeof render>, familyId: string) =>
  view.getAllByTestId('review-family').find((item) => item.dataset.family === familyId)!;

const subjectButton = (view: ReturnType<typeof render>, familyId: string) =>
  view
    .getAllByTestId('review-family-open')
    .find((button) => button.dataset.subjectId === familyId)!;

// The opening's task-context read is answered; then `familyId` is chosen and answered.
async function openOn(view: ReturnType<typeof render>, server: ReturnType<typeof delayedServer>, familyId: string) {
  await waitFor(() => expect(server.heldFor(null)).toHaveLength(1));
  await server.answer(null);
  await waitFor(() => expect(view.queryByTestId('review-loading')).toBeNull());
  fireEvent.click(subjectButton(view, familyId));
  await server.answer(familyId);
  await waitFor(() => expect(overviewFamily(view)).toBe(familyId));
  await waitFor(() => expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined());
}


it('keeps the reviewer mounted across family → invariant → family, pending only in the slot, and reuses what it read', async () => {
  const server = delayedServer();
  const view = render(<ReviewSurface {...target} />);
  await waitFor(() => expect(server.held).toHaveLength(1));
  // The opening reads the task context and draws the shell around its loading line.
  expect(server.held[0].url.searchParams.has('selectorId')).toBe(false);
  expect(view.getByTestId('review-loading')).toBeTruthy();
  const workspace = view.getByTestId('review-workspace');
  const navigation = view.getByTestId('review-tree');
  const center = view.getByTestId('review-center-column');
  await openOn(view, server, families[0].family_id);

  // Read B once, then return to cached A. Current invariant answers contain only their
  // owning family; B must be retained from an actual earlier read, not invented in A's reply.
  fireEvent.click(subjectButton(view, families[1].family_id));
  await server.answer(families[1].family_id);
  await waitFor(() => expect(overviewFamily(view)).toBe(families[1].family_id));
  fireEvent.click(subjectButton(view, families[0].family_id));
  const family = familyItem(view, families[0].family_id);
  const rail = view.getByTestId('review-tree-scroll');
  const technical = view.getByTestId('review-details') as HTMLDetailsElement;
  technical.open = true;
  rail.scrollTop = 120;
  const familyComparison = view.getByTestId('review-surface').dataset.comparison;

  // Family → invariant: the member is selected in the tree and its review is held open.
  const member = within(family)
    .getAllByTestId('review-family-member-open')
    .find((button) => button.dataset.subjectId === INVARIANT)!;
  member.focus();
  fireEvent.click(member);

  const pending = view.getByTestId('review-reading-pending');
  expect(pending.dataset.pendingSubject).toBe(`invariant:${INVARIANT}`);
  expect(pending.textContent).toContain('INV-1XKX9ERN');
  expect(view.getByTestId('review-surface').dataset.reviewPending).toBe(`invariant:${INVARIANT}`);
  expect(view.getByTestId('review-subject').textContent).toContain(`invariant ${INVARIANT}`);
  // The previous subject's reading, statements and records are not under the new subject's name.
  expect(view.queryByTestId('review-center')).toBeNull();
  expect(view.queryByTestId('review-center-family')).toBeNull();
  expect(view.queryByTestId('review-family-overview')).toBeNull();
  expect(view.queryByTestId('review-selection')).toBeNull();
  expect(view.queryByTestId('review-scope-families')).toBeNull();
  expect(view.getByTestId('review-surface').dataset.comparison).toBeUndefined();
  expect(view.queryByTestId('review-scope-comparison')?.textContent).not.toContain(
    familyComparison,
  );
  expect(view.queryByTestId('review-loading')).toBeNull();
  // ...while the shell is the same DOM, still expanded, scrolled, focused and open.
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  expect(view.getByTestId('review-tree')).toBe(navigation);
  expect(view.getByTestId('review-center-column')).toBe(center);
  expect(familyItem(view, families[0].family_id)).toBe(family);
  expect(member.getAttribute('aria-current')).toBe('true');
  expect(document.activeElement).toBe(member);
  expect(technical.open).toBe(true);
  expect(rail.scrollTop).toBe(120);
  // Navigation stays usable: every other subject and the whole change set can still be chosen.
  expect(subjectButton(view, families[1].family_id).hasAttribute('disabled')).toBe(false);
  expect(within(navigation).getByTestId('review-all-changes').hasAttribute('disabled')).toBe(false);
  await waitFor(() => expect(server.heldFor(INVARIANT)).toHaveLength(1));
  expect(server.reviewReads().at(-1)!.searchParams.get('selectorKind')).toBe('invariant');

  await server.answer(INVARIANT);
  await view.findByTestId('review-center-member');
  expect(view.queryByTestId('review-reading-pending')).toBeNull();
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  expect(familyItem(view, families[0].family_id)).toBe(family);
  expect(view.getByTestId('review-center-column')).toBe(center);
  expect(document.activeElement).toBe(member);
  expect(technical.open).toBe(true);
  expect(rail.scrollTop).toBe(120);

  // Invariant → the same family: answered from what this comparison already read, with no pending
  // state, no review request and no content request.
  const reviews = server.reviewReads().length;
  const contents = server.contentReads().length;
  const familyNode = within(family).getByTestId('review-family-open');
  fireEvent.click(familyNode);
  expect(view.queryByTestId('review-reading-pending')).toBeNull();
  expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
  expect(overviewFamily(view)).toBe(families[0].family_id);
  expect(view.getByTestId('review-surface').dataset.comparison).toBe(familyComparison);
  await act(async () => {});
  expect(server.reviewReads()).toHaveLength(reviews);
  expect(server.contentReads()).toHaveLength(contents);
  expect(server.held).toHaveLength(0);
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  expect(familyItem(view, families[0].family_id)).toBe(family);
  expect(document.activeElement).toBe(familyNode);
  expect(technical.open).toBe(true);
  // MIK-R39: B was read independently; returning to A leaves B in the tree, tagged as kept.
  const kept = families.slice(1).map((one) => familyItem(view, one.family_id));
  expect(kept.length).toBeGreaterThan(0);
  expect(kept.map((item) => item.dataset.familyKept)).toEqual(kept.map(() => 'true'));
  for (const item of kept)
    expect(within(item).getByTestId('review-family-kept').textContent).toBe('kept');
  expect(familyItem(view, families[0].family_id).dataset.familyKept).toBeUndefined();
});

it('settles on the latest of rapid selections and never shows a superseded answer', async () => {
  const server = delayedServer();
  const view = render(<ReviewSurface {...target} />);
  await openOn(view, server, families[0].family_id);
  const workspace = view.getByTestId('review-workspace');

  // A → B (another family) → C (an invariant of A, chosen from the tree still on screen).
  fireEvent.click(subjectButton(view, families[1].family_id));
  expect(view.getByTestId('review-reading-pending').dataset.pendingSubject).toBe(
    `family:${families[1].family_id}`,
  );
  expect(view.getByTestId('review-reading-pending').textContent).toContain(
    families[1].display_label!,
  );
  expect(overviewFamily(view)).toBe(families[1].family_id);
  // B's read is out before C is chosen, so its answer can arrive last.
  await waitFor(() => expect(server.heldFor(families[1].family_id)).toHaveLength(1));
  fireEvent.click(
    within(familyItem(view, families[0].family_id))
      .getAllByTestId('review-family-member-open')
      .find((button) => button.dataset.subjectId === INVARIANT)!,
  );
  expect(view.getByTestId('review-reading-pending').dataset.pendingSubject).toBe(
    `invariant:${INVARIANT}`,
  );

  // C answers first; B's answer arrives last and must change nothing.
  await server.answer(INVARIANT);
  await view.findByTestId('review-center-member');
  await server.answer(families[1].family_id);
  await act(async () => {});
  expect(view.getByTestId('review-center-member')).toBeTruthy();
  expect(view.queryByTestId('review-center-family')).toBeNull();
  expect(view.queryByTestId('review-family-overview')).toBeNull();
  expect(view.queryByTestId('review-reading-pending')).toBeNull();
  expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
  expect(view.getByTestId('review-subject').textContent).toContain(`invariant ${INVARIANT}`);
  expect(view.getByTestId('review-workspace')).toBe(workspace);

  // The superseded answer was dropped, not kept: choosing B again (now in the invariant's family
  // tree) asks for it again.
  const reviews = server.reviewReads().length;
  fireEvent.click(
    within(familyItem(view, families[1].family_id)).getByTestId('review-family-open'),
  );
  await waitFor(() => expect(server.reviewReads()).toHaveLength(reviews + 1));
  expect(view.getByTestId('review-reading-pending').dataset.pendingSubject).toBe(
    `family:${families[1].family_id}`,
  );
});

// A catalogue that answers late, before or after the task context's answer, fills the tree and
// selects nothing (MIK-R89 v2 rule 2; L177-R1-07).
it.each(['after', 'before'] as const)(
  'does not move a reader who is working when the catalogue answers %s the task context',
  async (order) => {
    const server = delayedServer({ holdCatalogue: true });
    const view = render(<ReviewSurface {...target} />);
    // The opening's one review read waits for no catalogue.
    await waitFor(() => expect(server.heldFor(null)).toHaveLength(1));
    const workspace = view.getByTestId('review-workspace');
    const file = view.getByTestId('review-family-filter');
    fireEvent.pointerDown(file);
    file.focus();
    if (order === 'before') await server.releaseCatalogue();
    await server.answer(null);
    await waitFor(() => expect(view.queryByTestId('review-loading')).toBeNull());
    if (order === 'after') await server.releaseCatalogue();
    await waitFor(() => expect(view.getAllByTestId('review-family-open')).toHaveLength(families.length));
    await act(async () => {});
    // The catalogue filled the tree; the reader's view, selection and focus did not move.
    expect(server.reviewReads()).toHaveLength(1);
    expect(server.held).toHaveLength(0);
    expect(view.queryByTestId('review-subject-slot')).toBeNull();
    expect(
      view.getAllByTestId('review-family-open').some((button) => button.ariaCurrent === 'true'),
    ).toBe(false);
    expect(document.activeElement).toBe(file);
    expect(view.getByTestId('review-workspace')).toBe(workspace);
    expect(server.catalogueReads()).toHaveLength(1);

    // The reader's own choice is still one subject, one read.
    fireEvent.click(subjectButton(view, families[0].family_id));
    await waitFor(() => expect(server.heldFor(families[0].family_id)).toHaveLength(1));
    await server.answer(families[0].family_id);
    await waitFor(() => familyShown(view, 0));
    expect(server.reviewReads()).toHaveLength(2);
  },
);

const SUBJECT_REFUSAL = {
  state: 'refused',
  refusal: {
    code: 'subject_not_found',
    detail: 'no such subject in this comparison',
    offending_input: 'selectorId',
    next_action: 'choose another recorded subject',
  },
};

it.each(['failed', 'refused'] as const)(
  'keeps the workspace and navigation when a newly selected subject is %s, stating it for that subject',
  async (outcome) => {
    const server = delayedServer();
    const view = render(<ReviewSurface {...target} />);
    await openOn(view, server, families[0].family_id);
    const workspace = view.getByTestId('review-workspace');
    const navigation = view.getByTestId('review-tree');
    const center = view.getByTestId('review-center-column');
    const family = familyItem(view, families[0].family_id);
    const rows = view.getAllByTestId('review-family-open');

    fireEvent.click(subjectButton(view, families[1].family_id));
    await server.answer(
      families[1].family_id,
      outcome === 'failed' ? { lost: true } : { body: SUBJECT_REFUSAL },
    );

    // The owner's failure or refusal is in the slot, labelled with the requested subject.
    const area = await view.findByTestId('review-reading-problem');
    expect(area.closest('[data-testid="review-subject-slot"]')?.getAttribute('data-slot-state')).toBe('unavailable');
    expect(area.dataset.problemSubject).toBe(`family:${families[1].family_id}`);
    expect(area.textContent).toContain(families[1].display_label!);

    const block = within(area).getByTestId(
      outcome === 'failed' ? 'review-failure' : 'review-refusal',
    );
    if (outcome === 'failed') expect(block.dataset.reviewState).toBe('network');
    else {
      expect(block.dataset.reviewCode).toBe('subject_not_found');
      expect(within(block).getByTestId('review-next-action').textContent).toContain(
        'choose another recorded subject',
      );
      expect(within(block).getByTestId('review-offending-input')).toBeTruthy();
    }
    // Stated once (this world's lane diffs fail on their own), and nothing of the previous subject
    // is shown under the new one.
    expect(
      view
        .getAllByTestId(outcome === 'failed' ? 'review-failure' : 'review-refusal')
        .filter((node) => !node.closest('[data-testid="review-lane-section"]')),
    ).toHaveLength(1);
    expect(view.queryByTestId('review-center')).toBeNull();
    expect(view.getByTestId('review-surface').dataset.comparison).toBeUndefined();
    expect(view.getByTestId('review-surface').dataset.reviewUnavailable).toBe(
      `family:${families[1].family_id}`,
    );
    expect(view.getByTestId('review-scope-comparison').textContent).toContain('not available');
    expect(view.queryByTestId('review-selection')).toBeNull();
    // The shell is the same DOM and stays usable.
    expect(view.getByTestId('review-workspace')).toBe(workspace);
    expect(view.getByTestId('review-tree')).toBe(navigation);

    expect(view.getByTestId('review-center-column')).toBe(center);
    expect(familyItem(view, families[0].family_id)).toBe(family);
    expect(view.getAllByTestId('review-family-open')).toEqual(rows);

    const reviews = server.reviewReads().length;
    if (outcome === 'failed') {
      // Retry stays available and asks for the requested subject again.
      fireEvent.click(within(block).getByTestId('review-retry'));
      await waitFor(() => expect(server.reviewReads()).toHaveLength(reviews + 1));
      expect(server.reviewReads().at(-1)!.searchParams.get('selectorId')).toBe(
        families[1].family_id,
      );
      expect(view.getByTestId('review-reading-pending').dataset.pendingSubject).toBe(
        `family:${families[1].family_id}`,
      );
      await server.answer(families[1].family_id);
      await waitFor(() => familyShown(view, 1));
      expect(view.getByTestId('review-surface').dataset.reviewUnavailable).toBeUndefined();
    } else {
      // Another subject is chosen directly from the navigation still on screen: its retained answer
      // is shown at once, with no new read.
      fireEvent.click(within(family).getByTestId('review-family-open'));
      familyShown(view, 0);
      expect(server.reviewReads()).toHaveLength(reviews);
    }
    expect(view.queryByTestId('review-reading-problem')).toBeNull();
    expect(view.getByTestId('review-workspace')).toBe(workspace);
  },
);

it('leaves focus where the reader moved it while the selected subject was pending', async () => {
  const server = delayedServer();
  const view = render(<ReviewSurface {...target} />);
  await openOn(view, server, families[0].family_id);


  const chosen = subjectButton(view, families[1].family_id);
  chosen.focus();
  fireEvent.click(chosen);
  expect(view.getByTestId('review-reading-pending')).toBeTruthy();
  const elsewhere = view.getByTestId('review-family-filter');
  elsewhere.focus();
  await server.answer(families[1].family_id);
  await waitFor(() => expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined());
  expect(overviewFamily(view)).toBe(families[1].family_id);
  expect(document.activeElement).toBe(elsewhere);

  // A reader who stays on the selecting control still lands on the selected row (A was read, so its
  // answer is immediate).
  const again = subjectButton(view, families[0].family_id);
  again.focus();
  fireEvent.click(again);
  expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
  expect(overviewFamily(view)).toBe(families[0].family_id);
  expect(document.activeElement).toBe(
    within(familyItem(view, families[0].family_id)).getByTestId('review-family-open'),
  );
});


// ---------------------------------------------------------------------------------------------------
// Forced order: a selection made before the passive effects of the previous answer's commit have run.

it('lands focus on a family chosen in the turn that shows the previous answer, before its effects have run', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      if (url.pathname.endsWith('/entries')) return response(catalogueBody);
      return response(reviewFor(url));
    }),
  );
  const view = render(<ReviewSurface {...target} />);
  await waitFor(() => expect(view.getAllByTestId('review-family-open')).toHaveLength(families.length));
  fireEvent.click(subjectButton(view, families[0].family_id));
  // The second family is chosen inside the wait that first sees the first family. The wait's check
  // runs from a mutation observer, so the click is made after the commit that shows the first family
  // and before React has run that commit's passive effects. The wait ends with the check that clicks.
  // The first family's answer is shown when its overview stands and its read is no longer pending.
  await waitFor(() => {
    expect(overviewFamily(view)).toBe(families[0].family_id);
    expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
    fireEvent.click(subjectButton(view, families[1].family_id));
  });

  await waitFor(() => {
    expect(overviewFamily(view)).toBe(families[1].family_id);
    expect(view.getByTestId('review-surface').dataset.reviewPending).toBeUndefined();
  });
  // Focus lands on the row the tree marks as the selection: the second family's, not the page body
  // (where it falls when the late effect has spent the request on the first family's row).
  await waitFor(() => {
    const selected = selectedNode(view);
    expect(selected?.dataset.family).toBe(families[1].family_id);
    expect(document.activeElement).toBe(selected);
  });
});

it('keeps every j pressed in the turn that shows the previous answer, and lands focus on each selected block', async () => {
  // Current real member answers and the captured cards of the member's family. The tree's location
  // rows come from the main read's complete inventory (MIK-R211 v4 rule 10): l86.trees predates
  // `entry_inventory`, so the captured member cards' own entries stand in for it, and a selected
  // block's owner read (by its entry) is answered with the same cards. Its three changed blocks are two of
  // INV-1XKX9ERN and one of INV-753SKJFF: j walks them (rule 11), each block reads its invariant
  // once, and the additional capture has its exact public request provenance.
  const second = currentCapture<ReviewResult>('navigation.member-753.captured.json');
  const memberCards = currentCapture<{ entries: unknown[] }>('l86.member-cards.captured.json');
  const subjects = new Map([captured, second].map((body) => [
    body.payload!.knowledge.revision_selection!.record_id, body,
  ]));
  const reads: URL[] = [];
  vi.stubGlobal('fetch', vi.fn(async (address: string) => {
    const url = new URL(address, 'http://localhost');
    if (url.pathname.endsWith('/entries')) return response(catalogueBody);
    if (url.pathname === '/api/review/trees' && ['invariants', 'entry'].some((key) => url.searchParams.has(key)))
      return response(memberCards);
    if (url.pathname === '/api/review/trees' && !['lane', 'file'].some((key) => url.searchParams.has(key)))
      return response({ ...(replyFor(url) as object), entry_inventory: { state: 'ready', entries: memberCards.entries } });
    if (url.pathname !== '/api/review/intent') return response(replyFor(url));
    reads.push(url);
    if (!url.searchParams.has('selectorId')) return response(taskBody);
    const body = subjects.get(url.searchParams.get('selectorId')!);
    if (!body) throw new Error(`Uncaptured keyboard selection: ${url}`);
    return response(structuredClone(body));
  }));
  const view = render(<ReviewSurface {...target} />);
  await waitFor(() => expect(view.getAllByTestId('review-tree-location').length).toBeGreaterThanOrEqual(3));
  const blocks = view.getAllByTestId('review-tree-location').slice(0, 3).map((row) => row.dataset.rowKey);
  view.getByTestId('review-center-column').focus();
  const shows = (block: number) =>
    view.getByTestId('review-surface').dataset.reviewPending === undefined &&
    selectedNode(view)?.dataset.rowKey === blocks[block];
  const landedOn = (block: number) =>
    waitFor(() => {
      expect(shows(block)).toBe(true);
      expect(document.activeElement).toBe(selectedNode(view));
    });
  // The second press is made by a mutation observer, in the first microtask in which the first
  // block's answer is shown: after its commit and before React has run that commit's passive effects.
  let pressedAgain = false;
  const observer = new MutationObserver(() => {
    if (pressedAgain || !shows(0)) return;
    pressedAgain = true;
    press(J);
  });
  observer.observe(view.container, {
    subtree: true,
    childList: true,
    attributes: true,
    characterData: true,
  });
  try {
    // The first press, made again until the keymap's binding (itself a passive effect) takes it.
    await waitFor(() => {
      if (selectedNode(view) === null) press(J);
      expect(selectedNode(view)).not.toBeNull();
    });
    await waitFor(() => expect(pressedAgain, 'the second press is made').toBe(true));
  } finally {
    observer.disconnect();
  }
  // The second block is selected and focus is on its row, not back on the first block's row.
  await landedOn(1);
  // So the third press steps from the row the reader is on: to the third block, not to the second
  // again (a press that selects the row already selected is a press lost).
  press(J);
  await landedOn(2);
  expect(reads.map((url) => url.searchParams.get('selectorId'))).toEqual([
    null,
    INVARIANT,
    second.payload!.knowledge.revision_selection!.record_id,
  ]);
});

// F03/F04 adverse sequences use explicit synthetic semantic inputs over immutable served captures.
// The entire mounted ReviewSurface/read cycle is real; fetch is the sole replacement.
function sharedNavigationWorld() {
  const review = structuredClone(currentCapture<ReviewResult>('walkStore.shared.captured.json'));
  const entries = review.payload!.family_context!.entries;
  const sharedId = review.payload!.knowledge.revision_selection!.record_id;
  const unknown = entries[0].after.members.find((row) => row.display_label === 'INV-GGGGGG')!;
  for (const family of entries) {
    family.after.guarantee = structuredClone(family.before.guarantee);
    family.change_kinds!.guarantee = 'unchanged';
    for (const fact of family.change_kinds!.members.filter(
      (row) => row.invariant === 'INV-FFFFFF',
    )) {
      fact.intent = 'established';
      fact.primary = 'intent';
    }
  }
  const outline: ReviewOutline = {
    state: 'outlined',
    detail: 'Synthetic two-family shared changed occurrence.',
    families: entries.map((entry, index) => ({
      selector_id: entry.family_id,
      id: `FAM-${index}`,
      title: `Family ${index}`,
      change: 'unchanged',
      members: [
        { selector_id: sharedId, id: 'INV-FFFFFF', membership: 'both' },
        ...(index === 0
          ? [{ selector_id: unknown.invariant_id!, id: 'INV-GGGGGG', membership: 'both' as const }]
          : []),
      ],
    })),
    invariants: [
      {
        selector_id: sharedId,
        id: 'INV-FFFFFF',
        summary: 'Shared changed invariant',
        change: 'changed',
      },
      {
        selector_id: unknown.invariant_id!,
        id: 'INV-GGGGGG',
        summary: 'Unknown sibling',
        change: 'unknown',
      },
    ],
  };
  return { review, outline, sharedId, familyIds: entries.map((entry) => entry.family_id) };
}
function outlineReply(outline: ReviewOutline): ReviewEntryListResult {
  const entries = [
    ...outline.families.map((row) => ({
      selector_kind: 'family' as const,
      selector_id: row.selector_id,
      label: row.title,
      presence: 'both' as const,
    })),
    ...outline.invariants.map((row) => ({
      selector_kind: 'invariant' as const,
      selector_id: row.selector_id,
      label: row.id,
      presence: 'both' as const,
    })),
  ];
  return {
    state: 'entries',
    operation: 'list_knowledge_review_entries',
    repository_id: 'agents-remember',
    master: '260101_tree_review',
    leaf_id: '260101-TRV-L1',
    entries,
    outline,
    total_subjects: entries.length,
    family_total: outline.families.length,
    invariant_total: outline.invariants.length,
  };
}
const auxiliaryRefusal = {
  state: 'refused',
  refusal: {
    code: 'not-found',
    detail: 'Auxiliary data is outside this focused sequence.',
    next_action: 'Read the mounted comparison.',
  },
};
function mountSharedWorld(world: ReturnType<typeof sharedNavigationWorld>) {
  const candidate = world.review.payload!.candidate;
  return render(
    <ReviewSurface
      repo={candidate.repository_id}
      master={candidate.master}
      leaf={candidate.leaf_id}
      selectorKind="family"
      selectorId={world.familyIds[0]}
      onBack={() => undefined}
    />,
  );
}
it('F03 shows a shared changed occurrence under both families and selects each by its row with one invariant read', async () => {
  const world = sharedNavigationWorld();
  const requests: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requests.push(url);
      if (url.pathname.endsWith('/entries')) return response(outlineReply(world.outline));
      return response(
        url.pathname === '/api/review/intent' ? structuredClone(world.review) : auxiliaryRefusal,
      );
    }),
  );
  const view = mountSharedWorld(world);
  await waitFor(() => expect(overviewFamily(view)).toBe(world.familyIds[0]));
  const rail = view.getByTestId('review-tree');
  const rows = () =>
    within(rail)
      .getAllByTestId('review-family-member-open')
      .filter((row) => row.dataset.subjectId === world.sharedId);
  expect(rows()).toHaveLength(2);
  expect(rows().map((row) => row.dataset.family)).toEqual(world.familyIds);
  expect(
    within(rail)
      .getAllByTestId('review-family-open')
      .map((row) => row.dataset.changePrimary),
  ).toEqual(['unchanged', 'unchanged']);
  for (const family of [world.familyIds[0], world.familyIds[1], world.familyIds[0]]) {
    const row = rows().find((node) => node.dataset.family === family)!;
    fireEvent.click(row);
    await waitFor(() =>
      expect(view.getByTestId('review-center-member').dataset.family).toBe(family),
    );
    await waitFor(() => expect(document.activeElement).toBe(row));
  }
  expect(within(rail).getAllByTestId('review-family')).toHaveLength(2);
  expect(
    requests.filter(
      (url) =>
        url.pathname === '/api/review/intent' &&
        url.searchParams.get('selectorKind') === 'invariant',
    ),
  ).toHaveLength(1);
  expect(view.getByTestId('review-summary').textContent).toContain('1 invariant changed');
});

// Rule 9: a reload restores the selection with the family context it was made in.
it('F03 restores a shared member under the family it was selected in after a reload', async () => {
  const world = sharedNavigationWorld();
  const candidate = world.review.payload!.candidate;
  const place = new URLSearchParams({ repo: candidate.repository_id, master: candidate.master, leaf: candidate.leaf_id });
  window.history.replaceState(null, '', `/#review?${place}`);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      if (url.pathname.endsWith('/entries')) return response(outlineReply(world.outline));
      return response(url.pathname === '/api/review/intent' ? structuredClone(world.review) : auxiliaryRefusal);
    }),
  );
  const shared = (view: ReturnType<typeof render>) =>
    within(view.getByTestId('review-tree'))
      .getAllByTestId('review-family-member-open')
      .filter((row) => row.dataset.subjectId === world.sharedId);
  try {
    const first = mountSharedWorld(world);
    await waitFor(() => expect(overviewFamily(first)).toBe(world.familyIds[0]));
    fireEvent.click(shared(first).find((node) => node.dataset.family === world.familyIds[1])!);
    await waitFor(() => expect(first.getByTestId('review-center-member').dataset.family).toBe(world.familyIds[1]));
    await waitFor(() => expect(window.location.hash).toContain('sel='));
    first.unmount();
    const view = mountSharedWorld(world);
    await waitFor(() => expect(view.getByTestId('review-center-member').dataset.family).toBe(world.familyIds[1]));
    expect(shared(view).find((node) => node.getAttribute('aria-current') === 'true')?.dataset.family).toBe(world.familyIds[1]);
  } finally {
    window.history.replaceState(null, '', '/');
  }
});


function generationWorld(axis: 'memory' | 'code') {
  // Generation quarantine does not require the retired two-family shared-member dataset.
  // Use genuine current family content/judgment, with explicitly perturbed generation/outline facts.
  const world = {
    review: structuredClone(familyBodies[0]),
    outline: structuredClone(synthetic.outline),
    sharedId: INVARIANT,
    familyIds: families.map((entry) => entry.family_id),
  };
  world.outline.families[0].title = 'Old outline title';
  const oldInvariant = world.outline.invariants.find((row) => row.selector_id === INVARIANT)!;
  oldInvariant.summary = 'Old outline summary';
  oldInvariant.change = 'changed';
  const newer = structuredClone(world.review);
  newer.payload!.comparison!.reference = '2'.repeat(64);
  newer.payload!.comparison!.binding_digest = '2'.repeat(64);
  if (axis === 'memory') newer.payload!.comparison!.after_snapshot_digest = '3'.repeat(64);
  else newer.payload!.source.inventory.after_code_tree_id = '4'.repeat(40);

  const entry = newer.payload!.family_context!.entries[0];
  entry.display_label = 'New outline title';
  const shared = entry.change_kinds!.members.find((fact) => fact.invariant === 'INV-1XKX9ERN')!;
  shared.primary = 'implementation';
  shared.implementation = 'established';
  shared.intent = 'not_established';
  const nextOutline = structuredClone(world.outline);
  nextOutline.families[0].title = 'New outline title';
  nextOutline.families[0].members.find((row) => row.selector_id === INVARIANT)!.membership = 'left';
  const nextInvariant = nextOutline.invariants.find((row) => row.selector_id === INVARIANT)!;
  nextInvariant.summary = 'New outline summary';
  nextInvariant.change = 'unchanged';
  return { ...world, newer, nextOutline };
}
// The selected family's overview stands and its own answer is the comparison on screen.
async function familyAnswered(view: ReturnType<typeof render>, world: ReturnType<typeof generationWorld>) {
  await waitFor(() => expect(overviewFamily(view)).toBe(world.familyIds[0]));
  await waitFor(() =>
    expect(view.getByTestId('review-surface').dataset.comparison).toBe(world.review.payload!.comparison!.reference),
  );
}
function holdNextCatalogue(world: ReturnType<typeof generationWorld>) {
  const requests: URL[] = [];
  let refreshed = false;
  const releases: ((response: Response) => void)[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requests.push(url);
      if (url.pathname.endsWith('/entries')) {
        if (requests.filter((item) => item.pathname.endsWith('/entries')).length === 1)
          return response(outlineReply(world.outline));
        return await new Promise<Response>((done) => {
          releases.push(done);
        });
      }
      return response(
        url.pathname === '/api/review/intent'
          ? structuredClone(refreshed ? world.newer : world.review)
          : replyFor(url),
      );
    }),
  );
  return {
    requests,
    advance: () => {
      refreshed = true;
    },
    release: (body: unknown, index = releases.length - 1) => releases[index]?.(response(body)),
  };
}
it.each(['memory', 'code'] as const)(
  'F04 quarantines a prior %s generation outline until the matching catalogue arrives',
  async (axis) => {
    const world = generationWorld(axis);
    const server = holdNextCatalogue(world);
    const view = mountSharedWorld(world);
    await familyAnswered(view, world);
    const rail = view.getByTestId('review-tree');
    expect(rail.textContent).toContain('Old outline title');
    const workspace = view.getByTestId('review-workspace');
    const refresh = view.getByTestId('review-refresh');
    refresh.focus();
    server.advance();
    fireEvent.click(refresh);
    await waitFor(() =>
      expect(server.requests.filter((url) => url.pathname.endsWith('/entries'))).toHaveLength(2),
    );
    expect(view.getByTestId('review-surface').dataset.comparison).toBe('2'.repeat(64));
    expect(rail.textContent).not.toContain('Old outline title');
    expect(rail.textContent).not.toContain('Old outline summary');
    expect(rail.querySelector('[data-testid="review-outline-badge"]')).toBeNull();
    expect(within(rail).queryAllByTestId('review-family-open')).toHaveLength(0);
    // The tree says the next generation's catalogue is being read, in its status line.
    expect(within(rail).getByTestId('review-change-status').textContent).toContain(
      'Reading families and invariants for this comparison',
    );
    expect(document.activeElement).toBe(refresh);
    const reads = server.requests.length;
    await act(async () => server.release(outlineReply(world.nextOutline)));
    await waitFor(() => expect(rail.textContent).toContain('New outline title'));
    expect(rail.textContent).toContain('New outline summary');
    expect(rail.textContent).not.toContain('Old outline summary');
    expect(rail.querySelector('[data-subject-id="' + world.sharedId + '"]')?.textContent).toContain(
      'code changed',
    );
    expect(view.getByTestId('review-workspace')).toBe(workspace);
    expect(document.activeElement).toBe(refresh);
    expect(server.requests).toHaveLength(reads);
  },
);
it('F04 makes a failed generation reread explicit without reviving the prior semantic outline', async () => {
  const world = generationWorld('memory');
  const server = holdNextCatalogue(world);
  const view = mountSharedWorld(world);
  await familyAnswered(view, world);
  server.advance();
  fireEvent.click(view.getByTestId('review-refresh'));
  await waitFor(() =>
    expect(server.requests.filter((url) => url.pathname.endsWith('/entries'))).toHaveLength(2),
  );
  await act(async () =>
    server.release({
      state: 'refused',
      refusal: {
        code: 'knowledge_unavailable',
        detail: 'New generation catalogue could not be read.',
        next_action: 'Retry the comparison.',
      },
    }),
  );
  const rail = view.getByTestId('review-tree');
  await waitFor(() =>
    expect(rail.textContent).toContain('New generation catalogue could not be read.'),
  );
  expect(rail.textContent).not.toContain('Old outline title');
  expect(rail.textContent).not.toContain('Old outline summary');
  expect(rail.querySelector('[data-testid="review-outline-badge"]')).toBeNull();
  expect(within(rail).getByTestId('review-change-status').textContent).toBe('New generation catalogue could not be read.');
  expect(within(rail).queryAllByTestId('review-family-open')).toHaveLength(0);
  expect(server.requests.filter((url) => url.pathname.endsWith('/entries'))).toHaveLength(2);
});

it('F04 ignores a late catalogue answer superseded by another admitted generation', async () => {
  const world = generationWorld('memory');
  const server = holdNextCatalogue(world);
  const view = mountSharedWorld(world);
  await familyAnswered(view, world);
  const refresh = view.getByTestId('review-refresh');
  server.advance();
  fireEvent.click(refresh);
  await waitFor(() =>
    expect(server.requests.filter((url) => url.pathname.endsWith('/entries'))).toHaveLength(2),
  );
  world.newer = structuredClone(world.newer);
  world.newer.payload!.comparison!.reference = '6'.repeat(64);
  world.newer.payload!.comparison!.binding_digest = '6'.repeat(64);
  world.newer.payload!.comparison!.after_snapshot_digest = '5'.repeat(64);
  fireEvent.click(refresh);
  await waitFor(() =>
    expect(server.requests.filter((url) => url.pathname.endsWith('/entries'))).toHaveLength(3),
  );
  const latest = structuredClone(world.nextOutline);
  latest.families[0].title = 'Latest matching catalogue';
  const rail = view.getByTestId('review-tree');
  await act(async () => server.release(outlineReply(latest), 1));
  await waitFor(() => expect(rail.textContent).toContain('Latest matching catalogue'));
  await act(async () => server.release(outlineReply(world.nextOutline), 0));
  expect(rail.textContent).toContain('Latest matching catalogue');
  expect(rail.textContent).not.toContain('New outline title');
  expect(view.getByTestId('review-surface').dataset.comparison).toBe('6'.repeat(64));
});
