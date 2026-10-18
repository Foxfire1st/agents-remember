// Current raw tree captures, never old dataset answers with compatibility fields added.
// Family selectors are served only their own captured bodies. Bounded invariant captures
// additionally exercise FamilyReviewCenter's roster projection directly: this is component
// evidence, not a claim that those bytes answered a family-selector HTTP request.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import { REVIEW_PAGED_COLLECTIONS, REVIEW_WALKABLE_COLLECTIONS } from '../../data/review';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import { FamilyReviewCenter } from './FamilyReviewCenter';
import { emptyRosterSentence } from './FamilyTree';
import { ReviewSurface } from './ReviewSurface';
import { syntheticCatalogue } from './outline.test-utils';
import { treeViewStore } from './triageViewPreference';
import { treeOrderStore } from './triageOrderPreference';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const FAMILY = captured<ReviewResult>('subjectReview.successorFamily.captured.json');
const MEMBER = captured<ReviewResult>('subjectReview.successorInvariant.captured.json');
const COMPLETE = captured<ReviewResult>('familyReview.complete.captured.json');
const TRUNCATED = captured<ReviewResult>('familyReview.truncated.captured.json');
const CONTINUED = captured<ReviewResult>('familyReview.continued.captured.json');
const IDENTICAL = captured<ReviewResult>('familyReview.identical.captured.json');
const ONE_SIDED = captured<ReviewResult>('familyReview.oneSided.captured.json');
const EMPTY = captured<ReviewResult>('familyReview.emptyRoster.captured.json');
const FINAL = captured<ReviewResult>('familyReview.walkFinal.captured.json');
const PAGING = captured<{ before: ReviewResult[]; after: ReviewResult[] }>(
  'familyPaging.captured.json',
);
const CARDS = captured('l86.member-cards.captured.json');
// Historical worklist response, only to isolate chrome; not current full-tree acceptance.
const TREES = captured('l86.trees.captured.json');
const LANE = captured('l86.lane.captured.json');
const SOURCE_REFUSAL = {
  state: 'refused',
  refusal: {
    code: 'not-found',
    detail: 'Source bytes are outside this roster check.',
    next_action: 'Read the served source endpoint.',
  },
};
const family = (body: ReviewResult) => body.payload!.family_context!.entries[0];
const selected = (body: ReviewResult) => body.payload!.knowledge.revision_selection!;
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
let catalogue = syntheticCatalogue([FAMILY, IDENTICAL]);
vi.mock('../../data/useReviewCatalogue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../data/useReviewCatalogue')>();
  return {
    ...actual,
    useReviewCatalogue: () => ({ loading: false, ...catalogue, refresh: () => undefined }),
  };
});

afterEach(() => {
  cleanup();
  resetCardCachesForTests();
  vi.unstubAllGlobals();
});

function serving(first: ReviewResult = FAMILY, next: ReviewResult[] = []) {
  catalogue = syntheticCatalogue([first, IDENTICAL]);
  treeViewStore.getState().setView('all');
  treeOrderStore.getState().setOrder('authored');
  const urls: URL[] = [];
  const subjects = new Map(
    [first, FAMILY, MEMBER, IDENTICAL, ONE_SIDED, EMPTY].map((body) => [
      `${selected(body).record_kind}:${selected(body).record_id}`,
      body,
    ]),
  );
  // The caller's first body wins when its selector shares a complete capture's identity.
  subjects.set(`${selected(first).record_kind}:${selected(first).record_id}`, first);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      urls.push(url);
      if (url.pathname.endsWith('/source-content')) return response(SOURCE_REFUSAL);
      if (url.pathname.endsWith('/trees')) {
        if (!url.searchParams.has('invariants'))
          return response(url.searchParams.has('lane') ? LANE : TREES);
        const expected = [
          ...new Set(
            [...family(FAMILY).before.members, ...family(FAMILY).after.members].map(
              (row) => row.invariant_id,
            ),
          ),
        ]
          .sort()
          .join(',');
        if (url.searchParams.get('invariants') !== expected)
          throw new Error(
            'Focused expressions for this other family are deliberately outside this roster-only check.',
          );
        return response(CARDS);
      }
      if (url.pathname !== '/api/review/intent') throw new Error(`Unexpected route: ${url}`);
      if (url.searchParams.has('continuation')) {
        const answer = next.shift();
        if (!answer) throw new Error(`Uncaptured continuation: ${url}`);
        return response(answer);
      }
      const answer = subjects.get(
        `${url.searchParams.get('selectorKind')}:${url.searchParams.get('selectorId')}`,
      );
      if (!answer) throw new Error(`Uncaptured subject: ${url}`);
      return response(answer);
    }),
  );
  return { urls, reviews: () => urls.filter((url) => url.pathname === '/api/review/intent') };
}
function mount(body: ReviewResult = FAMILY) {
  const { candidate } = body.payload!;
  return render(
    <ReviewSurface
      repo={candidate.repository_id}
      master={candidate.master}
      leaf={candidate.leaf_id}
      history="recorded"
      selectorKind={selected(body).record_kind}
      selectorId={selected(body).record_id}
      onBack={vi.fn()}
    />,
  );
}
// Honest presentation-level probe of family context from an invariant-selector answer.
function roster(body: ReviewResult, memberRevisionId?: string) {
  const onRosterNext = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => response(CARDS)),
  );
  const entry = family(body);
  const view = render(
    <FamilyReviewCenter
      payload={body.payload!}
      selection={{ familyId: entry.family_id, memberRevisionId }}
      layout="split"
      onLayout={vi.fn()}
      fullFile={false}
      onFullFile={vi.fn()}
      onOpenMember={vi.fn()}
      onRosterNext={onRosterNext}
      openPath={null}
      onOpenPath={vi.fn()}
      onOpenFromCenter={vi.fn()}
    />,
  );
  return { ...view, onRosterNext };
}
const familyButton = (view: ReturnType<typeof render>, id: string) =>
  view.getAllByTestId('review-family-open').find((button) => button.dataset.family === id)!;

// Recover the text actually drawn by the word-diff, without its opposite edits
// or screen-reader-only announcements. It must equal the producer's entire statement.
function displayedSide(passage: HTMLElement, side: 'before' | 'after') {
  const prose =
    within(passage).queryByTestId(`intent-diff-${side}`) ??
    within(passage).getByTestId('intent-diff-inline');
  const copy = prose.cloneNode(true) as HTMLElement;
  copy.querySelectorAll(side === 'before' ? 'ins' : 'del').forEach((node) => node.remove());
  copy
    .querySelectorAll('del > span:not([aria-hidden]), ins > span:not([aria-hidden])')
    .forEach((node) => node.remove());
  return copy.textContent!.replace(/·/g, ' ').replace(/→/g, '\t').replace(/↵\n/g, '\n');
}

it('renders the real family, its authored guarantee and full member statements', async () => {
  serving();
  const view = mount();
  const center = await view.findByTestId('review-center-family');
  expect(view.getAllByTestId('review-family')).toHaveLength(2);
  expect(view.getByTestId('review-family-tree').querySelector('details')).toBeNull();
  expect(center.dataset.family).toBe(family(FAMILY).family_id);
  expect(view.getByTestId('review-center-guarantee-label').textContent).toBe(
    'Changed guarantee · revision r1 → r3',
  );
  const members = within(center).getByTestId('review-center-family-members');
  expect(within(members).getAllByTestId('review-center-open-member')).toHaveLength(5);
  for (const before of family(FAMILY).before.members) {
    const after = family(FAMILY).after.members.find(
      (row) => row.invariant_id === before.invariant_id,
    )!;
    const row = within(members)
      .getAllByTestId('review-center-open-member')
      .find((button) => button.dataset.revision === before.invariant_revision_id)!
      .closest('li')!;
    expect(row.textContent).toContain(before.display_label);
    if (before.statement === after.statement) {
      expect(row.textContent).toContain(before.statement);
    } else {
      const passage = within(row).getByTestId('intent-diff');
      expect(displayedSide(passage, 'before')).toBe(before.statement);
      expect(displayedSide(passage, 'after')).toBe(after.statement);
    }
  }
  const guarantee = within(center).getByTestId('review-center-guarantee-changed');
  const passage = within(guarantee).getByTestId('intent-diff');
  expect(displayedSide(passage, 'before')).toBe(family(FAMILY).before.guarantee!.joint_guarantee);
  expect(displayedSide(passage, 'after')).toBe(family(FAMILY).after.guarantee!.joint_guarantee);
});

it('draws changed and unchanged guarantees according to their actual revision pairs', async () => {
  serving();
  const view = mount();
  await view.findByTestId('review-center-family');
  expect(view.getByTestId('review-center-guarantee-changed').dataset.wordDiff).toBe('true');
  fireEvent.click(familyButton(view, family(IDENTICAL).family_id));
  const unchanged = await view.findByTestId('review-center-guarantee-unchanged');
  expect(unchanged.textContent).toContain('Guarantee unchanged · same recorded revision');
  expect(unchanged.textContent).toContain(family(IDENTICAL).after.guarantee!.joint_guarantee);
  expect(view.queryByTestId('review-center-guarantee-changed')).toBeNull();
});

it('reads a member owner, retains its family and keeps the five independent facts distinct', async () => {
  const server = serving();
  const view = mount();
  await view.findByTestId('review-center-family');
  fireEvent.click(
    view
      .getAllByTestId('review-center-open-member')
      .find((button) => button.dataset.revision === selected(MEMBER).after_revision_id)!,
  );
  const center = await view.findByTestId('review-center-member');
  expect(center.dataset.family).toBe(family(MEMBER).family_id);
  expect(
    server
      .reviews()
      .some((url) => url.searchParams.get('selectorId') === selected(MEMBER).record_id),
  ).toBe(true);
  const facts = view.getByTestId('review-center-facts');
  for (const fact of ['guarantee', 'statement', 'membership', 'source', 'judgment'])
    expect(facts.querySelector(`[data-fact="${fact}"]`)?.textContent).toBeTruthy();
  expect(facts.querySelector('[data-fact="judgment"]')?.textContent).toContain(
    'No member, membership or guarantee change creates an authored judgment',
  );
  expect(view.getByTestId('review-center-judgment').dataset.judgmentState).toBe(
    MEMBER.payload!.judgment.state,
  );
});

it('keeps All changes and the complete source population independent of family selection', async () => {
  serving();
  const view = mount();
  const workspace = await view.findByTestId('review-workspace');
  const all = within(view.getByTestId('review-catalogue-navigation')).getByRole('button', {
    name: /^All changes/,
  });
  expect(all.textContent).toContain(String(FAMILY.payload!.source.inventory.listed_total));
  fireEvent.click(familyButton(view, family(IDENTICAL).family_id));
  await view.findByTestId('review-center-guarantee-unchanged');
  expect(
    within(view.getByTestId('review-catalogue-navigation')).getByRole('button', {
      name: /^All changes/,
    }),
  ).toBe(all);
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  expect(view.queryByTestId('review-source-explorer')).toBeNull();
});

it('sends the published family cursor and rejects a response from another walk', async () => {
  const server = serving(TRUNCATED, [CONTINUED]);
  const view = mount(TRUNCATED);
  await view.findByTestId('review-center-member');
  expect(REVIEW_PAGED_COLLECTIONS).toContain('family_members');
  expect(REVIEW_WALKABLE_COLLECTIONS).not.toContain('family_members');
  const picker = view.getByTestId('review-page-collection');
  expect(
    within(picker)
      .getAllByTestId('review-page-option')
      .map((node) => (node as HTMLOptionElement).value),
  ).toEqual([...REVIEW_WALKABLE_COLLECTIONS]);
  const next = view.getAllByTestId('review-center-roster-next')[0];
  const cursor = next.dataset.continuation!;
  expect(cursor).toBe(family(TRUNCATED).before.page!.continuation);
  fireEvent.click(next);
  const failure = await view.findByTestId('review-failure');
  expect(failure.dataset.reviewCode).toBe('comparison_page_unreadable');
  expect(server.reviews()[1].searchParams.get('continuation')).toBe(cursor);
  expect(server.reviews()[1].searchParams.get('pageOf')).toBe('family_members');
  expect(view.getByTestId('review-retained-generation')).toBeTruthy();
});

it('keeps expanded full-file and inline layout state across actual family selections', async () => {
  serving();
  const view = mount();
  await view.findByTestId('review-center-family');
  fireEvent.change(view.getByTestId('review-center-diff-layout'), { target: { value: 'inline' } });
  fireEvent.click(view.getByTestId('review-center-full-file'));
  fireEvent.click(familyButton(view, family(IDENTICAL).family_id));
  await view.findByTestId('review-center-guarantee-unchanged');
  expect(view.getByTestId('review-workspace').dataset.fullFile).toBe('true');
  expect(view.getByTestId('review-workspace').dataset.diffLayout).toBe('inline');
});

it('marks the selection and traverses the intact tree by keyboard', async () => {
  serving();
  const view = mount();
  await view.findByTestId('review-center-family');
  expect(familyButton(view, family(FAMILY).family_id).getAttribute('aria-current')).toBe('true');
  const members = view.getAllByTestId('review-family-member-open');
  members[0].focus();
  fireEvent.keyDown(members[0], { key: 'ArrowDown' });
  expect(document.activeElement).toBe(members[1]);
  fireEvent.keyDown(members[1], { key: 'ArrowUp' });
  expect(document.activeElement).toBe(members[0]);
});

it.each([false, true])('reveals an in-tree selection only inside its panel (narrow=%s)', async (narrow) => {
  // Synthetic geometry tests the mounted caller; real-browser evidence measures actual clipping.
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: narrow && /max-width/.test(query), media: query, onchange: null,
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  }));
  serving();
  const view = mount();
  await view.findByTestId('review-center-family');
  const row = familyButton(view, family(IDENTICAL).family_id);
  const panel = row.closest<HTMLElement>('[data-review-tree-panel]')!;
  const surface = view.getByTestId('review-surface');
  surface.scrollTop = 77;
  const height = () => Math.min(400, Number.parseFloat(panel.style.maxHeight) || 400);
  const surfaceBounds = vi.spyOn(surface, 'getBoundingClientRect').mockReturnValue({ top: 0, bottom: 300 } as DOMRect);
  const panelBounds = vi.spyOn(panel, 'getBoundingClientRect').mockImplementation(() => ({ top: 100, bottom: 100 + height() } as DOMRect));
  const rowBounds = vi.spyOn(row, 'getBoundingClientRect').mockImplementation(() => ({ top: 480 - panel.scrollTop, bottom: 500 - panel.scrollTop } as DOMRect));
  const focused = vi.spyOn(row, 'focus');
  try {
    fireEvent.click(row);
    await view.findByTestId('review-center-guarantee-unchanged');
    await waitFor(() => expect(document.activeElement).toBe(row));
    expect(focused).toHaveBeenCalledWith({ preventScroll: true });
    expect(row.getBoundingClientRect().bottom).toBeLessThanOrEqual(300);
    expect(row.getBoundingClientRect().top).toBeGreaterThanOrEqual(100);
    expect(panel.scrollTop).toBeGreaterThan(0);
    // Keep the existing wide explicit-selection reset; narrow navigation preserves its common
    // reading position. The new regression is the panel-local reveal at BOTH widths.
    expect(surface.scrollTop).toBe(narrow ? 77 : 0);
  } finally {
    focused.mockRestore(); rowBounds.mockRestore(); panelBounds.mockRestore(); surfaceBounds.mockRestore();
  }
});

it('filters by the actual captured label without restating comparison totals', async () => {
  serving();
  const view = mount();
  await view.findByTestId('review-family-tree');
  fireEvent.change(view.getByTestId('review-family-filter'), {
    target: { value: family(FAMILY).display_label!.replaceAll('-', ' ') },
  });
  expect(view.getAllByTestId('review-family')).toHaveLength(1);
  expect(view.getAllByTestId('review-family-member').length).toBeGreaterThan(0);
  expect(view.queryByTestId('review-family-filter-scope')).toBeNull();
  fireEvent.change(view.getByTestId('review-family-filter'), {
    target: { value: 'no-such-family-anywhere' },
  });
  expect(view.getByText('No rows match “no-such-family-anywhere”.')).toBeTruthy();
});

it('states the bounded roster as the part of the measured rows it carried, never zero', () => {
  const view = roster(TRUNCATED);
  for (const [index, side] of (['before', 'after'] as const).entries()) {
    const own = family(TRUNCATED)[side];
    const line = view.getAllByTestId('review-center-roster')[index];
    expect(own.members.length).toBeLessThan(own.members_total);
    expect(line.dataset.rosterComplete).toBe('false');
    expect(line.textContent).toContain(
      `records ${own.members_total} membership row(s); loaded context contains ${own.members.length} of them`,
    );
  }
  expect(view.queryByTestId('review-center-family-empty')).toBeNull();
  expect(view.getAllByTestId('review-center-roster-next')).toHaveLength(2);
});

it('does not call a deliberately uncarried bounded roster a measured zero (synthetic omission probe)', () => {
  const body = structuredClone(TRUNCATED);
  for (const entry of body.payload!.family_context!.entries) {
    entry.before.members = [];
    entry.after.members = [];
  }
  const view = roster(body);
  const text = view.getByTestId('review-center-family-empty').textContent;
  expect(text).toContain('this page carried no member row');
  expect(text).toContain('0 of the 3 recorded membership row(s) it measured');
  expect(text).toContain(
    'The continuation beside each bounded roster reaches the rows this page did not carry.',
  );
  expect(text).not.toContain('measured zero');
});

it('says measured zero only for the genuinely empty scratch-authored family capture', async () => {
  serving(EMPTY);
  const view = mount(EMPTY);
  const empty = await view.findByTestId('review-center-family-empty');
  expect(empty.textContent).toBe(emptyRosterSentence(family(EMPTY)));
  expect(empty.textContent).toContain(
    'the read measured zero memberships for the selected family revision',
  );
  expect(empty.textContent).toContain('0 recorded membership row(s)');
  expect(empty.textContent).not.toContain('this page carried no member row');
});

it('heads a bounded family projection partial, states owner totals, and wires its own cursor control', () => {
  const view = roster(TRUNCATED);
  const entry = family(TRUNCATED);
  expect(view.getByTestId('review-center-member-heading').textContent).toContain(
    'Recorded member context (partial)',
  );
  const text = view.getByTestId('review-center-member-counts').textContent;
  expect(text).toContain(
    '6 recorded membership row(s) measured by the read across 2 recorded side(s) (before 3 + after 3)',
  );
  expect(text).toContain('the continuations beside the bounded rosters reach the rest');
  const loaded = new Set(
    [...entry.before.members, ...entry.after.members].map((row) => row.invariant_revision_id),
  ).size;
  expect(loaded).toBeLessThan(6);
  expect(text).toContain(`loaded context contains ${loaded} member row(s) of them`);
  fireEvent.click(view.getAllByTestId('review-center-roster-next')[0]);
  expect(view.onRosterNext).toHaveBeenCalledWith(
    entry.family_id,
    'before',
    entry.before.page!.continuation,
  );
});

it('states genuinely uncarried member content without inventing a one-sided statement', () => {
  const missing = family(CONTINUED).after.members.find(
    (row) => row.state === 'content_not_on_page',
  )!;
  expect(missing).toBeDefined();
  const view = roster(CONTINUED, missing.invariant_revision_id);
  expect(view.getByTestId('review-center-member-context')).toBeTruthy();
  expect(view.getByTestId('review-center-judgment-unavailable').textContent).toContain(
    'no judgment absence is claimed',
  );
  for (const state of ['one-sided', 'unchanged', 'changed'])
    expect(view.queryByTestId(`review-center-member-${state}`)).toBeNull();
});

it('uses the real owner comparison rather than treating an uncarried roster operand as absent', async () => {
  serving(CONTINUED);
  const view = mount(CONTINUED);
  await view.findByTestId('review-center-member');
  expect(selected(CONTINUED).state).toBe('compared');
  expect(view.getByTestId('review-center-member-changed').dataset.revisionState).toBe('compared');
  expect(view.queryByTestId('review-center-member-one-sided')).toBeNull();
  expect(view.getAllByTestId('review-center-roster-next').length).toBeGreaterThan(0);
});

it('retains a bounded before-only revision without claiming the invariant was removed', async () => {
  const entry = family(CONTINUED);
  const before = entry.before.members.find(
    (row) =>
      !entry.after.members.some(
        (other) => other.invariant_revision_id === row.invariant_revision_id,
      ),
  )!;
  expect(before).toBeDefined();
  serving(CONTINUED);
  const view = mount(CONTINUED);
  const tree = await view.findByTestId('review-family-tree');
  expect(tree.querySelector(`[data-subject-id="${before.invariant_id}"]`)).not.toBeNull();
  expect(view.getByTestId('review-center-member-changed').dataset.revisionState).toBe('compared');
  expect(view.queryByTestId('review-center-member-one-sided')).toBeNull();
});

it('prints the shared empty-roster sentence once in the reading path, never again in the tree', async () => {
  serving(EMPTY);
  const view = mount(EMPTY);
  await view.findByTestId('review-center-family');
  expect(view.getAllByTestId('review-center-family-empty')).toHaveLength(1);
  expect(view.getByTestId('review-center-family-empty').textContent).toBe(
    emptyRosterSentence(family(EMPTY)),
  );
  expect(
    view
      .getByTestId('review-family-tree')
      .querySelector('[data-testid="review-center-family-empty"]'),
  ).toBeNull();
});

it('states a final bounded page as completing its walk, not as the whole selection', () => {
  const view = roster(FINAL);
  const own = family(FINAL).after;
  expect(own.page!.complete).toBe(true);
  expect(own.members.length).toBeLessThan(own.members_total);
  const final = view
    .getAllByTestId('review-center-roster')
    .find((node) => node.textContent?.includes('completes the walk'))!;
  expect(final).toBeDefined();
  expect(final.textContent).toContain('loaded context can retain earlier pages');
  expect(final.textContent).toContain(`loaded context contains ${own.members.length} of them`);
  expect(final.textContent).not.toContain('the page is the whole selection');
});

it('keeps reader state across two admitted same-subject continuation requests', async () => {
  const first = PAGING.before[0];
  const steps = PAGING.before.slice(1, 3);
  const server = serving(first, [...steps]);
  const view = mount(first);
  await view.findByTestId('review-center-member');
  fireEvent.change(view.getByTestId('review-family-filter'), {
    target: { value: family(first).display_label },
  });
  fireEvent.change(view.getByTestId('review-center-diff-layout'), { target: { value: 'inline' } });
  fireEvent.click(view.getByTestId('review-center-full-file'));
  const workspace = view.getByTestId('review-workspace');
  for (const [index, body] of steps.entries()) {
    const next = view
      .getAllByTestId('review-center-roster-next')
      .find((node) => node.dataset.continuation === body.payload!.page!.continued_from)!;
    expect(next).toBeDefined();
    fireEvent.click(next);
    await waitFor(() =>
      expect(view.getByTestId('review-page-bounds').textContent).toContain(
        `returned ${body.payload!.page!.returned}`,
      ),
    );
    expect(server.reviews()[index + 1].searchParams.get('continuation')).toBe(
      body.payload!.page!.continued_from,
    );
    expect(view.getByTestId('review-workspace')).toBe(workspace);
    expect(workspace.dataset.fullFile).toBe('true');
    expect(workspace.dataset.diffLayout).toBe('inline');
    expect((view.getByTestId('review-family-filter') as HTMLInputElement).value).toBe(
      family(first).display_label,
    );
  }
  expect(server.reviews()).toHaveLength(3);
});

it('deduplicates real changed family expressions without losing the recorded membership occurrences', () => {
  const view = roster(COMPLETE);
  const rows = view.getAllByTestId('review-center-family-expression');
  // Current real capture: five changed realization rows collapse to four recorded addresses.
  expect(rows).toHaveLength(4);
  const sizes = rows.map((row) => Number(row.dataset.collapsedRows));
  expect(sizes.reduce((sum, size) => sum + size, 0)).toBe(5);
  expect(Math.max(...sizes)).toBe(2);
  const verdict = view.getByTestId('review-center-family-expressions-verdict').textContent;
  expect(verdict).toContain('5 changed expression row(s)');
  expect(verdict).toContain('collapse to 4 distinct excerpt(s)');
  expect(verdict).toContain("not the comparison's own measured change set");
  expect(view.getAllByTestId('review-center-open-member')).toHaveLength(5);
});

it('states the actual added-family side as not recorded, not unreadable or a measured empty family', async () => {
  serving(ONE_SIDED);
  const view = mount(ONE_SIDED);
  await view.findByTestId('review-center-family');
  expect(family(ONE_SIDED).before.state).toBe('not_recorded');
  expect(view.getByTestId('review-center-guarantee-one-sided').textContent).toContain(
    family(ONE_SIDED).after.guarantee!.joint_guarantee,
  );
  expect(view.queryByTestId('review-center-family-empty')).toBeNull();
});

it('composes the narrow jump route above the intact family tree and focuses the reading column', async () => {
  serving();
  const view = mount();
  const jump = await view.findByTestId('review-jump-to-selection');
  const tree = view.getByTestId('review-family-tree');
  expect(jump.compareDocumentPosition(tree) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(view.getAllByTestId('review-family')).toHaveLength(2);
  expect(view.getAllByTestId('review-family-member-open').length).toBeGreaterThan(0);
  fireEvent.click(jump);
  expect(document.activeElement).toBe(view.getByTestId('review-center-column'));
});

it('renders deliberately omitted family context as unavailable, never as a measured zero', async () => {
  const body = structuredClone(MEMBER);
  delete body.payload!.family_context;
  serving(body);
  const view = mount(body);
  await view.findByTestId('review-family-tree');
  expect(view.getByTestId('review-scope-families').textContent).toContain(
    'Family context was not supplied',
  );
  expect(view.getByTestId('review-center-member')).toBeTruthy();
  expect(view.queryByTestId('review-center-family-empty')).toBeNull();
});

it('labels whole roster selections as whole, not as continued final pages', () => {
  const view = roster(COMPLETE);
  for (const line of view.getAllByTestId('review-center-roster')) {
    expect(line.textContent).toContain('the page is the whole selection');
    expect(line.textContent).not.toContain('completes the walk');
  }
});

// MIK-R86 rule 20 holds one view's closed disclosures at the cap and draws its Identifiers once per
// group, so the count does not grow with rows, proofs, files, cards or members; MIK-R57 rule 3 keeps
// a row's identifiers and history path in that disclosure and its judgment text in the open.
it('keeps a whole family view at the disclosure cap with every judgment and proof identifier listed', async () => {
  serving(ONE_SIDED);
  const view = mount(ONE_SIDED);
  await view.findByTestId('review-center-family');
  await waitFor(() => expect(view.getAllByTestId('review-judgment-proof')).toHaveLength(18));
  const root = view.getByTestId('review-surface');
  const details = [...root.querySelectorAll('details')];
  expect(details.length).toBeLessThanOrEqual(20);
  for (const element of details)
    expect(['identifiers', 'provenance']).toContain(element.dataset.disclosure);
  const listed = [...root.querySelectorAll('details[data-disclosure="identifiers"]')]
    .map((element) => element.textContent ?? '')
    .join('\n');
  for (const proof of ONE_SIDED.payload!.judgment.proofs) expect(listed).toContain(proof.id);
  for (const row of ONE_SIDED.payload!.judgment.rows) expect(listed).toContain(row.row_id);
  for (const node of [
    ...root.querySelectorAll('[data-testid="review-judgment-row"], [data-testid="review-judgment-proof"]'),
  ])
    expect(node.closest('details')).toBeNull();
});

// Labeled synthetic growth probe over the capture's own payload, not a new capture: it separates a
// repair that groups the identifiers from one that only moved the threshold.
function grownJudgment(body: ReviewResult, rowCount: number, proofCount: number): ReviewResult {
  const grown = structuredClone(body);
  const judgment = grown.payload!.judgment;
  const [row] = judgment.rows;
  const [proof] = judgment.proofs;
  judgment.rows = Array.from({ length: rowCount }, (_, index) => ({
    ...row,
    attempt: row.attempt + index,
    row_id: `${row.row_id}-GROWN${index}`,
  }));
  judgment.proofs = Array.from({ length: proofCount }, (_, index) => ({
    ...proof,
    id: `${proof.id}-GROWN${index}`,
  }));
  return grown;
}

it('holds that disclosure count for a subject with far more rows and proofs than the capture', async () => {
  serving(ONE_SIDED);
  const capturedView = mount(ONE_SIDED);
  await capturedView.findByTestId('review-center-family');
  await waitFor(() => expect(capturedView.getAllByTestId('review-judgment-proof')).toHaveLength(18));
  const captured = capturedView.getByTestId('review-surface').querySelectorAll('details').length;
  cleanup();
  const grown = grownJudgment(ONE_SIDED, 9, 57);
  serving(grown);
  const grownView = mount(grown);
  await grownView.findByTestId('review-center-family');
  await waitFor(() => expect(grownView.getAllByTestId('review-judgment-proof')).toHaveLength(57));
  expect(grownView.getByTestId('review-surface').querySelectorAll('details').length).toBe(captured);
});
