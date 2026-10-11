// Ordinary entry uses the real catalogue/read cycle; captured records supply the semantic content.
//
// The first two cases answer immediately and prove eventual content. The cases after them hold every
// review reply open until the test releases it, because what they pin happens BETWEEN a selection and
// its answer: the workspace, its navigation, the family tree and the open disclosures are the same DOM
// nodes throughout; only the reading area is pending, and it names the requested subject; nothing of
// the previous subject is labelled as the new one; a subject already read for this comparison is
// shown again without a request; the latest of several quick selections is the one that settles; a
// failed or refused selection is stated in the reading area for that subject while the shell stays
// usable; focus the reader moved while waiting is not taken back; and a catalogue that answers after
// the bounded wait does not move a reader who has started working.
//
// The last two cases force an order a busy machine produces on its own: the reader selects again in
// the turn in which the previous answer's commit becomes visible, before React has run that commit's
// passive effects. The late effect of the earlier render must leave the new selection's focus request
// alone (`FocusRequest.after` in `ReviewWorkspace.tsx`): focus lands on the row selected last, by
// pointer and by `j`, and no key press is lost. Both fail every time without that guard.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type {
  ReviewPayload,
  ReviewResult,
  ReviewSourceContentResult,
  ReviewOutline,
  ReviewEntryListResult,
} from '../../data/review';
import { SUBJECT_HOLD_MS } from './ReviewNavigation';
import { ReviewSurface } from './ReviewSurface';
import { syntheticCatalogue } from './outline.test-utils';
import { treeOrderStore } from './triageOrderPreference';
import { treeViewStore } from './triageViewPreference';
import {
  captured as readCaptured,
  CHANGES,
  J,
  R6R,
  R6R_ID,
  familyBlock,
  open,
  press,
  selectedName,
  selectedNode,
  serveWorld,
} from './walk.test-utils';

const captured = JSON.parse(
  readFileSync(
    path.join(
      path.dirname(new URL(import.meta.url).pathname),
      'familyReview.complete.captured.json',
    ),
    'utf8',
  ),
) as ReviewResult;
const recorded = captured.payload as ReviewPayload;
const families = recorded.family_context!.entries;
const synthetic = syntheticCatalogue([captured]);
// Explicit synthetic change on the first family keeps these delayed-interaction sequences stable.
synthetic.outline.families[0].change = 'changed';
const catalogue = synthetic.entries;
beforeEach(() => {
  treeOrderStore.getState().setOrder('authored');
  treeViewStore.getState().setView('all');
});
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
const target = {
  repo: 'agents-remember',
  master: 'review-navigation',
  leaf: 'recorded-task',
  onBack: () => undefined,
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('opens a real family from ordinary entry, navigates another family in place and preserves the full source population', async () => {
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
  await waitFor(() =>
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id),
  );
  const workspace = view.getByTestId('review-workspace');
  fireEvent.click(subjectButton(view, families[1].family_id));
  await waitFor(() =>
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[1].family_id),
  );
  expect(urls.some((url) => url.searchParams.get('selectorId') === families[1].family_id)).toBe(
    true,
  );
  await waitFor(() =>
    expect(document.activeElement).toBe(subjectButton(view, families[1].family_id)),
  );
  const member = families[1].after.members.find(
    (row) => row.invariant_id === recorded.knowledge.revision_selection?.record_id,
  )!;
  fireEvent.click(
    view
      .getAllByTestId('review-family-member-open')
      .find((node) => node.dataset.revision === member.invariant_revision_id)!,
  );
  await view.findByTestId('review-center-member');
  fireEvent.click(view.getByTestId('review-center-full-file'));
  fireEvent.change(view.getByTestId('review-center-diff-layout'), { target: { value: 'inline' } });
  expect(workspace.dataset.fullFile).toBe('true');
  expect(workspace.dataset.diffLayout).toBe('inline');
  const region = view.getByTestId('review-center-column');
  const focus = vi.spyOn(region, 'focus');
  const scroll = vi.spyOn(region, 'scrollIntoView');
  fireEvent.click(view.getByTestId('review-jump-to-selection'));
  expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  expect(scroll).toHaveBeenCalledWith({ block: 'start' });
  expect(view.queryByTestId('review-source-explorer')).toBeNull();
  expect(view.getByTestId('review-workspace')).toBe(workspace);
});

it('keeps unreadable knowledge source-only without inventing a family or telling the reader to select one', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) =>
      address.includes('/entries')
        ? response({
            state: 'refused',
            refusal: {
              code: 'knowledge_unavailable',
              detail: 'The historical knowledge snapshot cannot be read.',
              next_action: 'Inspect source or restore the recorded snapshot.',
            },
          })
        : response({
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
          }),
    ),
  );
  const view = render(<ReviewSurface {...target} />);
  await view.findByTestId('review-family-tree');
  expect(view.getByTestId('review-outline-unavailable').textContent).toContain('cannot be read');
  expect(view.queryByTestId('review-family-open')).toBeNull();
  expect(view.queryByTestId('review-source-explorer')).toBeNull();
  expect(view.getByTestId('review-center-unselected').textContent).toContain('Source');
});

// ---------------------------------------------------------------------------------------------------
// Delayed replies.

const INVARIANT = '5a18787e-9f23-44ce-9573-41d93972a274';
const catalogueBody = {
  state: 'entries',
  entries: catalogue,
  outline: synthetic.outline,
  total_subjects: catalogue.length,
  family_total: catalogue.length,
  invariant_total: 0,
};

function reviewFor(url: URL): ReviewResult {
  const kind = url.searchParams.get('selectorKind');
  if (kind === 'invariant') return captured;
  const family = families.find((item) => item.family_id === url.searchParams.get('selectorId'));
  return {
    ...captured,
    payload: {
      ...recorded,
      knowledge: family
        ? recorded.knowledge
        : { ...recorded.knowledge, selection_state: 'task_context' },
      family_context: {
        ...recorded.family_context!,
        entries: family ? [family] : [],
        families_returned: family ? 1 : 0,
        state: family ? 'recorded' : 'no_subject_selected',
      },
    },
  };
}

function contentFor(url: URL): ReviewSourceContentResult {
  const file = url.searchParams.get('path')!;
  return {
    state: 'content',
    operation: 'read_review_source_content',
    repository_id: target.repo,
    expansion: {
      path: file,
      status: 'added',
      mode_change: false,
      language: 'python',
      before: { state: 'absent', detail: 'no entry at this path', truncated: false },
      after: { state: 'present', text: `# ${file}\n`, detail: 'regular file', truncated: false },
      before_code_tree_id: url.searchParams.get('beforeCodeTreeId')!,
      after_code_tree_id: url.searchParams.get('afterCodeTreeId')!,
      currentness: 'current',
      currentness_detail: 'the bound pair',
      path_bound: 'requested_generation',
      path_bound_detail: 'listed by the change set',
      admission: 'changed',
      admission_detail: 'listed as changed',
      reference: 'review:source-content',
      command: 'git cat-file',
    },
  };
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
      if (url.pathname.endsWith('/source-content')) return response(contentFor(url));
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
      const index = held.findIndex(
        (reply) =>
          selectorId === undefined || reply.url.searchParams.get('selectorId') === selectorId,
      );
      expect(index).toBeGreaterThanOrEqual(0);
      const [reply] = held.splice(index, 1);
      await act(async () => (outcome.lost ? reply.lose() : reply.release(outcome.body)));
    },
    releaseCatalogue: async () => act(async () => releaseCatalogue?.()),
    held,
  };
}

const familyItem = (view: ReturnType<typeof render>, familyId: string) =>
  view.getAllByTestId('review-family').find((item) => item.dataset.family === familyId)!;

const subjectButton = (view: ReturnType<typeof render>, familyId: string) =>
  view
    .getAllByTestId('review-family-open')
    .find((button) => button.dataset.subjectId === familyId)!;

it('keeps the reviewer mounted across family → invariant → family, pending only in the reading area, and reuses what it read', async () => {
  const server = delayedServer();
  const view = render(<ReviewSurface {...target} />);
  await waitFor(() => expect(server.held).toHaveLength(1));
  // Nothing was read yet, so there is no shell to keep: the surface-level loading line stands.
  expect(view.getByTestId('review-loading')).toBeTruthy();
  await server.answer(families[0].family_id);
  await waitFor(() =>
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id),
  );

  const workspace = view.getByTestId('review-workspace');
  const navigation = view.getByTestId('review-catalogue-navigation');
  const center = view.getByTestId('review-center-column');
  const family = familyItem(view, families[0].family_id);
  const rail = workspace.querySelector('aside')!;
  const technical = view.getByTestId('review-details') as HTMLDetailsElement;
  technical.open = true;
  rail.scrollTop = 120;
  const familyComparison = view.getByTestId('review-surface').dataset.comparison;

  // Family → invariant: the member is selected in the tree and its review is held open.
  const member = within(family)
    .getAllByTestId('review-family-member-open')
    .find((button) => button.dataset.revision?.startsWith('30000000'))!;
  member.focus();
  fireEvent.click(member);

  const pending = view.getByTestId('review-reading-pending');
  expect(pending.dataset.pendingSubject).toBe(`invariant:${INVARIANT}`);
  expect(pending.textContent).toContain('invariant shared-retry-budget');
  expect(view.getByTestId('review-surface').dataset.reviewPending).toBe(`invariant:${INVARIANT}`);
  expect(view.getByTestId('review-subject').textContent).toContain(`invariant ${INVARIANT}`);
  // The previous subject's reading, statements and records are not under the new subject's name.
  expect(view.queryByTestId('review-center')).toBeNull();
  expect(view.queryByTestId('review-center-family')).toBeNull();
  expect(view.queryByTestId('review-selection')).toBeNull();
  expect(view.queryByTestId('review-unassessed')).toBeNull();
  expect(view.getByTestId('review-details-pending').textContent).toContain('shared-retry-budget');
  expect(view.getByTestId('review-surface').dataset.comparison).toBeUndefined();
  expect(view.getByTestId('review-scope-families').textContent).toContain('being read');
  expect(view.queryByTestId('review-scope-comparison')?.textContent).not.toContain(
    familyComparison,
  );
  expect(view.queryByTestId('review-currentness-status')).toBeNull();
  expect(view.queryByTestId('review-loading')).toBeNull();
  // ...while the shell is the same DOM, still expanded, scrolled, focused and open.
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  expect(view.getByTestId('review-catalogue-navigation')).toBe(navigation);
  expect(view.getByTestId('review-center-column')).toBe(center);
  expect(familyItem(view, families[0].family_id)).toBe(family);
  expect(member.getAttribute('aria-current')).toBe('true');
  expect(document.activeElement).toBe(member);
  expect(technical.open).toBe(true);
  expect(rail.scrollTop).toBe(120);
  // Navigation stays usable: every other subject and the whole-task view can still be chosen.
  expect(subjectButton(view, families[1].family_id).hasAttribute('disabled')).toBe(false);
  expect(
    within(navigation)
      .getByRole('button', { name: /^All changes/ })
      .hasAttribute('disabled'),
  ).toBe(false);
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
  expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id);
  expect(view.getByTestId('review-surface').dataset.comparison).toBe(familyComparison);
  await act(async () => {});
  expect(server.reviewReads()).toHaveLength(reviews);
  expect(server.contentReads()).toHaveLength(contents);
  expect(server.held).toHaveLength(0);
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  expect(familyItem(view, families[0].family_id)).toBe(family);
  expect(document.activeElement).toBe(familyNode);
  expect(technical.open).toBe(true);
  // MIK-R39: the invariant's review showed the other families too; clicking the first family's row
  // leaves them in the tree, tagged as kept and last read for the invariant.
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
  await waitFor(() => expect(server.held).toHaveLength(1));
  await server.answer(families[0].family_id);
  await view.findByTestId('review-center-family');
  const workspace = view.getByTestId('review-workspace');

  // A → B (another family) → C (an invariant of A, chosen from the tree still on screen).
  fireEvent.click(subjectButton(view, families[1].family_id));
  expect(view.getByTestId('review-reading-pending').dataset.pendingSubject).toBe(
    `family:${families[1].family_id}`,
  );
  expect(view.getByTestId('review-reading-pending').textContent).toContain(
    families[1].display_label!,
  );
  fireEvent.click(
    within(familyItem(view, families[0].family_id))
      .getAllByTestId('review-family-member-open')
      .find((button) => button.dataset.revision?.startsWith('30000000'))!,
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
  expect(view.queryByTestId('review-reading-pending')).toBeNull();
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

it('does not move a reader who is working when the catalogue answers after the bounded wait', async () => {
  vi.useFakeTimers();
  const server = delayedServer({ holdCatalogue: true });
  const view = render(<ReviewSurface {...target} />);
  // The bounded wait releases the whole-task read.
  await act(async () => { await vi.advanceTimersByTimeAsync(SUBJECT_HOLD_MS); });
  expect(server.heldFor(null)).toHaveLength(1);
  vi.useRealTimers();
  await server.answer(null);
  await view.findByTestId('review-center-unselected');
  const workspace = view.getByTestId('review-workspace');
  const file = view.getByTestId('review-family-filter');
  fireEvent.pointerDown(file);
  file.focus();

  await server.releaseCatalogue();
  await waitFor(() => expect(view.getAllByTestId('review-family-open')).toHaveLength(2));
  await act(async () => {});
  // The catalogue filled the navigation; the reader's view, selection and focus did not move.
  expect(server.reviewReads()).toHaveLength(1);
  expect(view.getByTestId('review-center-unselected')).toBeTruthy();
  expect(view.queryByTestId('review-reading-pending')).toBeNull();
  expect(
    view.getAllByTestId('review-family-open').some((button) => button.ariaCurrent === 'true'),
  ).toBe(false);
  expect(document.activeElement).toBe(file);
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  expect(server.catalogueReads()).toHaveLength(1);
});

it('lands a reader who has not acted on the first family when the catalogue answers late, without remounting', async () => {
  vi.useFakeTimers();
  const server = delayedServer({ holdCatalogue: true });
  const view = render(<ReviewSurface {...target} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(SUBJECT_HOLD_MS); });
  expect(server.heldFor(null)).toHaveLength(1);
  vi.useRealTimers();
  await server.answer(null);
  await view.findByTestId('review-center-unselected');
  const workspace = view.getByTestId('review-workspace');

  await server.releaseCatalogue();
  const pending = await view.findByTestId('review-reading-pending');
  expect(pending.dataset.pendingSubject).toBe(`family:${families[0].family_id}`);
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  await server.answer(families[0].family_id);
  await waitFor(() =>
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id),
  );
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  expect(server.catalogueReads()).toHaveLength(1);
});

it('keeps the task-context answer the bounded wait released when the catalogue answers before it (MIK-R40 rule 5)', async () => {
  vi.useFakeTimers();
  const server = delayedServer({ holdCatalogue: true });
  const view = render(<ReviewSurface {...target} />);
  // The catalogue is slower than the bound: the whole-task read is released and still in flight.
  await act(async () => { await vi.advanceTimersByTimeAsync(SUBJECT_HOLD_MS); });
  expect(server.heldFor(null)).toHaveLength(1);
  vi.useRealTimers();
  // The catalogue answers first: the reader, who has not acted, is taken to the first family.
  await server.releaseCatalogue();
  await waitFor(() => expect(server.heldFor(families[0].family_id)).toHaveLength(1));
  await server.answer(families[0].family_id);
  await waitFor(() =>
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id),
  );
  // The superseded whole-task answer arrives: it never writes the panes.
  await server.answer(null);
  await act(async () => {});
  expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id);
  expect(view.queryByTestId('review-center-unselected')).toBeNull();
  expect(server.reviewReads().filter((url) => !url.searchParams.has('selectorKind'))).toHaveLength(
    1,
  );
  expect(server.held).toHaveLength(0);
  expect(view.queryByTestId('review-reading-pending')).toBeNull();
});

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
    await waitFor(() => expect(server.held).toHaveLength(1));
    await server.answer(families[0].family_id);
    await view.findByTestId('review-center-family');
    const workspace = view.getByTestId('review-workspace');
    const navigation = view.getByTestId('review-catalogue-navigation');
    const center = view.getByTestId('review-center-column');
    const family = familyItem(view, families[0].family_id);
    const rows = view.getAllByTestId('review-family-open');

    fireEvent.click(subjectButton(view, families[1].family_id));
    await server.answer(
      families[1].family_id,
      outcome === 'failed' ? { lost: true } : { body: SUBJECT_REFUSAL },
    );

    // The owner's failure or refusal is in the reading area, labelled with the requested subject.
    const area = await view.findByTestId('review-reading-problem');
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
    // Stated once, and nothing of the previous subject is shown under the new one.
    expect(
      view.getAllByTestId(outcome === 'failed' ? 'review-failure' : 'review-refusal'),
    ).toHaveLength(1);
    expect(view.queryByTestId('review-center')).toBeNull();
    expect(view.getByTestId('review-surface').dataset.comparison).toBeUndefined();
    expect(view.getByTestId('review-surface').dataset.reviewUnavailable).toBe(
      `family:${families[1].family_id}`,
    );
    expect(view.getByTestId('review-scope-comparison').textContent).toContain('not available');
    expect(view.getByTestId('review-details-unavailable')).toBeTruthy();
    expect(view.queryByTestId('review-selection')).toBeNull();
    // The shell is the same DOM and stays usable.
    expect(view.getByTestId('review-workspace')).toBe(workspace);
    expect(view.getByTestId('review-catalogue-navigation')).toBe(navigation);
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
      await waitFor(() =>
        expect(view.getByTestId('review-center-family').dataset.family).toBe(families[1].family_id),
      );
    } else {
      // Another subject is chosen directly from the navigation still on screen.
      fireEvent.click(within(family).getByTestId('review-family-open'));
      expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id);
      expect(server.reviewReads()).toHaveLength(reviews);
    }
    expect(view.queryByTestId('review-reading-problem')).toBeNull();
    expect(view.getByTestId('review-workspace')).toBe(workspace);
  },
);

it('leaves focus where the reader moved it while the selected subject was pending', async () => {
  const server = delayedServer();
  const view = render(<ReviewSurface {...target} />);
  await waitFor(() => expect(server.held).toHaveLength(1));
  await server.answer(families[0].family_id);
  await view.findByTestId('review-center-family');

  const chosen = subjectButton(view, families[1].family_id);
  chosen.focus();
  fireEvent.click(chosen);
  expect(view.getByTestId('review-reading-pending')).toBeTruthy();
  const elsewhere = view.getByTestId('review-family-filter');
  elsewhere.focus();
  await server.answer(families[1].family_id);
  await waitFor(() =>
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[1].family_id),
  );
  expect(document.activeElement).toBe(elsewhere);

  // A reader who stays on the selecting control still lands on the selected node (A was read, so its
  // answer is immediate and its catalogue button gives way to its tree node).
  const again = subjectButton(view, families[0].family_id);
  again.focus();
  fireEvent.click(again);
  expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id);
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
      if (url.pathname.endsWith('/source-content')) return response(contentFor(url));
      return response(reviewFor(url));
    }),
  );
  const view = render(<ReviewSurface {...target} />);
  // The second family is chosen inside the wait that first sees the first family. The wait's check
  // runs from a mutation observer, so the click is made after the commit that shows the first family
  // and before React has run that commit's passive effects. The wait ends with the check that clicks.
  await waitFor(() => {
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id);
    fireEvent.click(subjectButton(view, families[1].family_id));
  });
  await waitFor(() =>
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[1].family_id),
  );
  // Focus lands on the row the tree marks as the selection: the second family's, not the page body
  // (where it falls when the late effect has spent the request on the first family's row).
  await waitFor(() => {
    const selected = selectedNode(view);
    expect(selected?.dataset.family).toBe(families[1].family_id);
    expect(document.activeElement).toBe(selected);
  });
});

it('keeps every j pressed in the turn that shows the previous answer, and lands focus on each selected row', async () => {
  // The real-data world of the MIK-R39 tests: family FAM-R6R095RW with its seven changes.
  serveWorld();
  const view = open(R6R);
  await view.findAllByTestId('review-change-badge', undefined);
  within(familyBlock(view, R6R_ID)).getByTestId('review-family-open').focus();
  const shows = (change: string) =>
    view.getByTestId('review-surface').dataset.reviewPending === undefined &&
    selectedNode(view) !== null &&
    selectedName(view) === change;
  const landedOn = (change: string) =>
    waitFor(() => {
      expect(shows(change)).toBe(true);
      expect(document.activeElement).toBe(selectedNode(view));
    });
  // The second press is made by a mutation observer, in the first microtask in which the first
  // change's answer is shown: after its commit and before React has run that commit's passive effects.
  let pressedAgain = false;
  const observer = new MutationObserver(() => {
    if (pressedAgain || !shows(CHANGES[0])) return;
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
    const before = selectedNode(view);
    await waitFor(() => {
      if (selectedNode(view) === before) press(J);
      expect(selectedNode(view)).not.toBe(before);
    });
    await waitFor(() => expect(pressedAgain, 'the second press is made').toBe(true));
  } finally {
    observer.disconnect();
  }
  // The second change is selected and focus is on its row, not back on the first change's row.
  await landedOn(CHANGES[1]);
  // So the third press steps from the row the reader is on: to the third change, not to the second
  // again (a press that selects the row already selected is a press lost).
  press(J);
  await landedOn(CHANGES[2]);
});

// F03/F04 adverse sequences use explicit synthetic semantic inputs over immutable served captures.
// The entire mounted ReviewSurface/read cycle is real; fetch is the sole replacement.
function sharedNavigationWorld() {
  const review = structuredClone(readCaptured<ReviewResult>('walkStore.shared.captured.json'));
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
it('F03 visits shared changed occurrences under both families by keys and controls, skipping aggregates and unknown', async () => {
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
  await view.findByTestId('review-center-family');
  const rail = view.getByTestId('review-catalogue-navigation');
  const rows = () =>
    within(rail)
      .getAllByTestId('review-family-member-open')
      .filter((row) => row.dataset.subjectId === world.sharedId);
  expect(rows()).toHaveLength(2);
  expect(new Set(rows().map((row) => row.dataset.occurrence)).size).toBe(2);
  expect(
    within(rail)
      .getAllByTestId('review-family-open')
      .map((row) => row.dataset.changePrimary),
  ).toEqual(['unchanged', 'unchanged']);
  within(rail).getAllByTestId('review-family-open')[0].focus();
  const move = async (key: 'j' | 'k', family: string) => {
    fireEvent.keyDown(document.activeElement!, { key, code: key === 'j' ? 'KeyJ' : 'KeyK' });
    await waitFor(() =>
      expect(view.getByTestId('review-center-member').dataset.family).toBe(family),
    );
    expect((document.activeElement as HTMLElement).dataset.family).toBe(family);
  };
  await move('j', world.familyIds[0]);
  await move('j', world.familyIds[1]);
  await move('k', world.familyIds[0]);
  fireEvent.click(within(rail).getByTestId('review-next-change'));
  await waitFor(() =>
    expect(view.getByTestId('review-center-member').dataset.family).toBe(world.familyIds[1]),
  );
  fireEvent.click(within(rail).getByTestId('review-previous-change'));
  await waitFor(() =>
    expect(view.getByTestId('review-center-member').dataset.family).toBe(world.familyIds[0]),
  );
  expect(within(rail).getAllByTestId('review-family')).toHaveLength(2);
  expect(
    requests.filter(
      (url) =>
        url.pathname === '/api/review/intent' &&
        url.searchParams.get('selectorKind') === 'invariant',
    ),
  ).toHaveLength(1);
  expect(view.getByTestId('review-navigation-summary').textContent).toContain(
    '1 invariants changed',
  );
});

function generationWorld(axis: 'memory' | 'code') {
  const world = sharedNavigationWorld();
  world.outline.families[0].title = 'Old outline title';
  world.outline.invariants[0].summary = 'Old outline summary';
  world.outline.invariants[0].change = 'changed';
  const newer = structuredClone(world.review);
  newer.payload!.comparison!.reference = '2'.repeat(64);
  newer.payload!.comparison!.binding_digest = '2'.repeat(64);
  if (axis === 'memory') newer.payload!.comparison!.after_snapshot_digest = '3'.repeat(64);
  else newer.payload!.source.inventory.after_code_tree_id = '4'.repeat(40);
  newer.payload!.limitations = newer.payload!.limitations.map((value) =>
    value === 'review:trees:1' ? 'review:trees:2' : value,
  );
  const entry = newer.payload!.family_context!.entries[0];
  entry.display_label = 'New outline title';
  const shared = entry.change_kinds!.members.find((fact) => fact.invariant === 'INV-FFFFFF')!;
  shared.primary = 'implementation';
  shared.implementation = 'established';
  shared.intent = 'not_established';
  const nextOutline = structuredClone(world.outline);
  nextOutline.families[0].title = 'New outline title';
  nextOutline.families[0].members[0].membership = 'left';
  nextOutline.invariants[0].summary = 'New outline summary';
  nextOutline.invariants[0].change = 'unchanged';
  return { ...world, newer, nextOutline };
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
          : auxiliaryRefusal,
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
    await view.findByTestId('review-center-family');
    const rail = view.getByTestId('review-catalogue-navigation');
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
    expect(rail.textContent).toContain('Reading families and invariants for this comparison');
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
  await view.findByTestId('review-center-family');
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
  const rail = view.getByTestId('review-catalogue-navigation');
  await waitFor(() =>
    expect(rail.textContent).toContain('New generation catalogue could not be read.'),
  );
  expect(rail.textContent).not.toContain('Old outline title');
  expect(rail.textContent).not.toContain('Old outline summary');
  expect(rail.querySelector('[data-testid="review-outline-badge"]')).toBeNull();
  expect(rail.textContent).toContain('catalogue for this comparison is unavailable');
  expect(server.requests.filter((url) => url.pathname.endsWith('/entries'))).toHaveLength(2);
});

it('F04 ignores a late catalogue answer superseded by another admitted generation', async () => {
  const world = generationWorld('memory');
  const server = holdNextCatalogue(world);
  const view = mountSharedWorld(world);
  await view.findByTestId('review-center-family');
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
  const rail = view.getByTestId('review-catalogue-navigation');
  await act(async () => server.release(outlineReply(latest), 1));
  await waitFor(() => expect(rail.textContent).toContain('Latest matching catalogue'));
  await act(async () => server.release(outlineReply(world.nextOutline), 0));
  expect(rail.textContent).toContain('Latest matching catalogue');
  expect(rail.textContent).not.toContain('New outline title');
  expect(view.getByTestId('review-surface').dataset.comparison).toBe('6'.repeat(64));
});
