// Current raw tree captures, never old dataset answers with compatibility fields added.
// Family selectors are served only their own captured bodies. Bounded invariant captures
// additionally exercise FamilyReviewCenter's roster projection directly: this is component
// evidence, not a claim that those bytes answered a family-selector HTTP request.
// The review page (MIK-R89 v2) retired three cases here: the layout select and Expand full file
// (rule 4: the layout follows the column's width, "Changes only | Full context" replaces the full
// file), the rail's maxHeight reveal (the tree keeps its place after a tree click, rule 9:
// reading/ReviewPage.selection.test.tsx), and the narrow jump route (rule 11: at 390 px the tree
// stacks above the reading column).
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ReviewResult, ReviewRevisionWriter } from '../../data/review';
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
// The summary read (read 1 of the review page, MIK-R89 v2 rule 2) was not captured with these
// bodies: this answer is composed, its change total the first body's own measured source inventory.
function summaryOf(body: ReviewResult) {
  const { candidate, source } = body.payload!;
  return {
    state: 'counted',
    operation: 'review-intent-summary',
    repository_id: candidate.repository_id,
    master: candidate.master,
    leaf_id: candidate.leaf_id,
    counts: {
      added: 0,
      removed: 0,
      invariants: { after_only: 0, before_only: 0 },
      guarantees: { after_only: 0, before_only: 0 },
      realization_only: 0,
      membership_only: 0,
      unresolved: 0,
    },
    attribution: {
      state: 'counted',
      changed_total: source.inventory.listed_total,
      attributed: source.inventory.listed_total,
      unexplained: 0,
      attribution_unknown: 0,
      unmeasured: [],
    },
  };
}
const family = (body: ReviewResult) => body.payload!.family_context!.entries[0];
const selected = (body: ReviewResult) => body.payload!.knowledge.revision_selection!;
// Explicitly synthetic member answers, not captures: IDENTICAL's and ONE_SIDED's members were never
// read by invariant selector. MEMBER's real invariant answer is re-addressed to the family's first
// member and carries that family's own captured context, so the member's reading, opened from the
// family's overview on the page, draws that family's guarantee after its code.
function memberAnswerOf(body: ReviewResult): ReviewResult {
  const answer = structuredClone(MEMBER);
  const entry = family(body);
  const id = [...entry.after.members, ...entry.before.members][0].invariant_id;
  const revision = (side: 'before' | 'after') =>
    entry[side].members.find((row) => row.invariant_id === id)?.invariant_revision_id ?? null;
  answer.payload!.family_context = structuredClone(body.payload!.family_context);
  Object.assign(answer.payload!.knowledge.revision_selection!, {
    record_id: id,
    before_revision_id: revision('before'),
    after_revision_id: revision('after'),
  });
  return answer;
}
const IDENTICAL_MEMBER = memberAnswerOf(IDENTICAL);
const ONE_SIDED_MEMBER = memberAnswerOf(ONE_SIDED);
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
    [first, FAMILY, MEMBER, IDENTICAL, ONE_SIDED, EMPTY, IDENTICAL_MEMBER, ONE_SIDED_MEMBER].map((body) => [
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
      // A real Response: the summary's conditional read keeps the answer's ETag header.
      if (url.pathname === '/api/review/intent/summary')
        return new Response(JSON.stringify(summaryOf(first)), { status: 200 });
      if (url.pathname.endsWith('/trees')) {
        // A filter reads the complete location inventory first (MIK-R211 v4 rule 10;
        // useReviewSearch.ts). TREES predates `entry_inventory`: an explicitly synthetic ready,
        // empty inventory stands in for it on that read only.
        if (!url.searchParams.has('invariants') && !url.searchParams.has('lane') && url.searchParams.get('view') !== 'overview')
          return response({ ...(TREES as object), entry_inventory: { state: 'ready', entries: [] } });
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
      onOpenMember={vi.fn()}
      onRosterNext={onRosterNext}
      onOpenFromCenter={vi.fn()}
    />,
  );
  return { ...view, onRosterNext };
}
const familyButton = (view: ReturnType<typeof render>, id: string) =>
  view.getAllByTestId('review-family-open').find((button) => button.dataset.family === id)!;
// A family selection opens its members' overview (MIK-R211 v4 rule 10); a member's row opens that
// invariant's own reading, which carries the family's guarantee after its code.
const overviewOf = async (view: ReturnType<typeof render>, id: string) => {
  await waitFor(() => expect(view.getByTestId('review-family-overview').dataset.family).toBe(id));
  return view.getByTestId('review-family-overview');
};
const openOverviewMember = (view: ReturnType<typeof render>, invariant: string) =>
  fireEvent.click(
    within(view.getByTestId('review-family-overview'))
      .getAllByTestId('review-member-summary')
      .find((row) => row.dataset.subject === invariant)!
      .querySelector('button')!,
  );
const memberIds = (body: ReviewResult) =>
  [...new Set([...family(body).before.members, ...family(body).after.members].map((row) => row.invariant_id))];

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
  const overview = await overviewOf(view, family(FAMILY).family_id);
  expect(view.getAllByTestId('review-family')).toHaveLength(2);
  expect(view.getByTestId('review-tree').querySelector('details')).toBeNull();
  // The family's overview names every member invariant by its label, and mounts no member reading.
  const rows = within(overview).getAllByTestId('review-member-summary');
  expect(rows.map((row) => row.dataset.subject).sort()).toEqual(memberIds(FAMILY).sort());
  for (const member of [...family(FAMILY).before.members, ...family(FAMILY).after.members])
    expect(rows.find((row) => row.dataset.subject === member.invariant_id)!.textContent).toContain(member.display_label);
  expect(within(overview).queryByTestId('review-subject-meta')).toBeNull();
  expect(view.queryByTestId('review-center-member')).toBeNull();
  // A member's own reading holds its full statement, and after its code the family's guarantee.
  openOverviewMember(view, selected(MEMBER).record_id);
  const own = await view.findByTestId('review-center-member');
  const before = family(MEMBER).before.members.find((row) => row.invariant_id === selected(MEMBER).record_id)!;
  const after = family(MEMBER).after.members.find((row) => row.invariant_id === selected(MEMBER).record_id)!;
  if (before.statement === after.statement) {
    expect(own.textContent).toContain(before.statement);
  } else {
    const statement = within(own)
      .getAllByTestId('intent-diff')
      .find((node) => node.dataset.field === 'statement')!;
    expect(displayedSide(statement, 'before')).toBe(before.statement);
    expect(displayedSide(statement, 'after')).toBe(after.statement);
  }
  const details = await view.findByTestId('review-center-member-details');
  expect(details.dataset.family).toBe(family(FAMILY).family_id);
  expect(within(details).getByTestId('review-center-guarantee-label').textContent).toBe(
    'Changed guarantee · revision r1 → r3',
  );
  const guarantee = within(details).getByTestId('review-center-guarantee-changed');
  const passage = within(guarantee).getByTestId('intent-diff');
  expect(displayedSide(passage, 'before')).toBe(family(FAMILY).before.guarantee!.joint_guarantee);
  expect(displayedSide(passage, 'after')).toBe(family(FAMILY).after.guarantee!.joint_guarantee);
});

it('draws changed and unchanged guarantees according to their actual revision pairs', async () => {
  serving();
  const view = mount();
  await overviewOf(view, family(FAMILY).family_id);
  openOverviewMember(view, selected(MEMBER).record_id);
  const details = await view.findByTestId('review-center-member-details');
  expect(within(details).getByTestId('review-center-guarantee-changed').dataset.wordDiff).toBe('true');
  // Another family opens its own overview; the previous member's guarantee goes with its reading.
  fireEvent.click(familyButton(view, family(IDENTICAL).family_id));
  await overviewOf(view, family(IDENTICAL).family_id);
  expect(view.queryByTestId('review-center-guarantee-changed')).toBeNull();
  // Its member's reading (synthetic answer, above) draws the unchanged guarantee after its code.
  openOverviewMember(view, selected(IDENTICAL_MEMBER).record_id);
  const identical = await view.findByTestId('review-center-member-details');
  expect(identical.dataset.family).toBe(family(IDENTICAL).family_id);
  const after = identical.closest<HTMLElement>('[data-testid="review-subject-after-code"]')!;
  expect(after).not.toBeNull();
  const unchanged = within(after).getByTestId('review-center-guarantee-unchanged');
  expect(unchanged.textContent).toContain('Guarantee unchanged · same recorded revision');
  expect(unchanged.textContent).toContain(family(IDENTICAL).after.guarantee!.joint_guarantee);
  expect(view.queryByTestId('review-center-guarantee-changed')).toBeNull();
});

it('reads a member owner, retains its family and keeps the five independent facts distinct', async () => {
  const server = serving();
  const view = mount();
  await overviewOf(view, family(FAMILY).family_id);
  openOverviewMember(view, selected(MEMBER).record_id);
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
  // The tree's one All changes link (rule 7), counted by the summary read.
  const all = view.getByTestId('review-all-changes');
  const total = `All changes ${FAMILY.payload!.source.inventory.listed_total}`;
  await waitFor(() => expect(all.textContent).toBe(total));
  fireEvent.click(familyButton(view, family(IDENTICAL).family_id));
  await overviewOf(view, family(IDENTICAL).family_id);
  expect(view.getByTestId('review-all-changes')).toBe(all);
  expect(all.textContent).toBe(total);
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  expect(view.queryByTestId('review-source-explorer')).toBeNull();
});

it('sends the published family cursor and rejects a response from another walk', async () => {
  const server = serving(TRUNCATED, [CONTINUED]);
  const view = mount(TRUNCATED);
  await view.findByTestId('review-center-member');
  expect(REVIEW_PAGED_COLLECTIONS).toContain('family_members');
  expect(REVIEW_WALKABLE_COLLECTIONS).not.toContain('family_members');
  // The family owns its roster continuation; unpaged whole-review collections need no picker.
  expect(view.queryByTestId('review-page-collection')).toBeNull();
  const next = view.getAllByTestId('review-center-roster-next')[0];
  const cursor = next.dataset.continuation!;
  expect(cursor).toBe(family(TRUNCATED).before.page!.continuation);
  fireEvent.click(next);
  // The page's other reads may state their own failures; this one is the walk's.
  await waitFor(() =>
    expect(
      view.getAllByTestId('review-failure').map((node) => node.dataset.reviewCode),
    ).toContain('comparison_page_unreadable'),
  );
  expect(server.reviews()[1].searchParams.get('continuation')).toBe(cursor);
  expect(server.reviews()[1].searchParams.get('pageOf')).toBe('family_members');
  expect(view.getByTestId('review-retained-generation')).toBeTruthy();
});

// MIK-R89 v2 rules 1 and 6, amended 9dde2b09 (D396): a selected subject reads content first. Under
// MIK-R211 v4 rule 10 a selected family opens its members' overview without its members' readings;
// after the member summaries the family read's own slot draws its guarantee change, recorded steps
// and judgment, then the read's facts (FamilyState), and its record stays in the closed Technical
// details. A member's meta line, statement diff, recorded steps and judgment stand under its
// heading, then its code, then the family's guarantee; its state sentences, its origin and its
// revision ids are in Technical details.
const follows = (first: Node, second: Node) =>
  Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);

describe('a selected family reads content first', () => {
  it('opens as its members\' overview, and a member with its meta line, statement, steps and judgment before its code and the family guarantee', async () => {
    serving();
    const view = mount();
    const entry = family(FAMILY);
    const overview = await overviewOf(view, entry.family_id);
    // The family's own read answers, for its record on demand.
    await waitFor(() => expect(view.getByTestId('review-surface').dataset.comparison).toBe(FAMILY.payload!.comparison!.reference));
    const heading = within(overview).getByTestId('review-family-heading');
    const counts = within(overview).getByTestId('review-family-counts');
    const summaries = within(overview).getByTestId('review-member-summaries');
    expect(follows(heading, counts)).toBe(true);
    expect(follows(counts, summaries)).toBe(true);
    expect(counts.textContent).toContain(`${memberIds(FAMILY).length} invariants`);
    // After the members, the family read's answered slot: its guarantee change, steps and judgment.
    const familySlot = within(overview).getByTestId('review-subject-slot');
    expect(familySlot.dataset.slotState).toBe('answered');
    const familyOrder = [
      summaries,
      within(familySlot).getByTestId('review-guarantee-change'),
      within(familySlot).getByTestId('review-recorded-steps'),
      within(familySlot).getByTestId('review-family-judgment'),
      // The read's facts close it, as an invariant's facts follow its code.
      within(familySlot).getByTestId('review-main-facts'),
    ];
    for (let index = 1; index < familyOrder.length; index += 1)
      expect(follows(familyOrder[index - 1], familyOrder[index])).toBe(true);
    expect(within(familySlot).getAllByTestId('review-guarantee-change')).toHaveLength(1);
    expect(within(familySlot).getByTestId('review-guarantee-change').dataset.changeKind).toBe(entry.change_kinds!.guarantee);
    expect(within(familySlot).getAllByTestId('review-family-judgment')).toHaveLength(1);
    // No meta line, family reading or member reading.
    for (const id of ['review-subject-meta', 'review-center-family', 'review-center-member', 'review-subject-section'])
      expect(within(overview).queryByTestId(id)).toBeNull();
    // The overview up to those facts, its slot's guarantee, steps and judgment included: no state
    // sentence, origin, revision id, frame or writer line.
    const ahead = overview.cloneNode(true) as HTMLElement;
    ahead.querySelector('[data-testid="review-main-facts"]')!.remove();
    for (const text of [entry.detail, entry.selection.statement, entry.after.family_revision_id!, 'origin', 'introduced by', 'Family comparison details', 'family contexts read', 'Selected'])
      expect(ahead.textContent).not.toContain(text);
    expect(within(overview).queryByTestId('review-subject-family-chip')).toBeNull();
    expect(within(overview).queryByTestId('review-subject-carried')).toBeNull();
    // All of the family's record stays reachable in the closed fold.
    const fold = view.getByTestId('review-details') as HTMLDetailsElement;
    expect(fold.open).toBe(false);
    const provenance = within(fold).getByTestId('review-subject-provenance').textContent!;
    for (const text of [entry.detail, entry.selection.statement, entry.after.family_revision_id!, 'introduced by'])
      expect(provenance).toContain(text);

    openOverviewMember(view, selected(MEMBER).record_id);
    const content = await view.findByTestId('review-center-member');
    const section = content.closest<HTMLElement>('[data-testid="review-subject-section"]')!;
    const slot = within(section).getByTestId('review-subject-slot');
    const meta = within(slot).getByTestId('review-subject-meta');
    const after = await within(section).findByTestId('review-subject-after-code');
    const order = [
      meta,
      within(content).getByTestId('review-center-statement-label'),
      within(content).getByTestId('intent-diff-layout-control'),
      within(content).getByTestId('review-recorded-steps'),
      within(content).getByTestId('review-center-judgment'),
      after,
      within(after).getByTestId('review-center-guarantee-changed'),
    ];
    for (let index = 1; index < order.length; index += 1) expect(follows(order[index - 1], order[index])).toBe(true);
    // Before the code: no state sentence, revision id, frame or writer line.
    const before = slot.textContent!;
    for (const text of [family(MEMBER).detail, family(MEMBER).selection.statement, selected(MEMBER).after_revision_id!, 'origin', 'introduced by', 'Family comparison details', 'family contexts read', 'Selected'])
      expect(before).not.toContain(text);
    expect(within(slot).queryByTestId('review-subject-family-chip')).toBeNull();
    expect(within(slot).queryByTestId('review-subject-carried')).toBeNull();
  });

  it('shows a partial family read as one short chip that continues the roster walk', async () => {
    const server = serving(TRUNCATED, [CONTINUED]);
    const view = mount(TRUNCATED);
    await view.findByTestId('review-center-member');
    const meta = within(view.getByTestId('review-subject-slot')).getByTestId('review-subject-meta');
    const chip = within(meta).getByTestId('review-subject-family-chip');
    const page = family(TRUNCATED).before.page!;
    expect(chip.textContent).toBe(`family context partial · ${page.counts.primary_items_remaining} of ${page.counts.primary_items_total} not shown`);
    expect(chip.dataset.familyState).toBe('partial');
    // The family's full sentence is the fold's, not the slot's.
    expect(view.getByTestId('review-subject-slot').textContent).not.toContain(TRUNCATED.payload!.family_context!.detail);
    expect(view.getByTestId('review-family-context-detail').textContent).toContain(TRUNCATED.payload!.family_context!.detail);
    fireEvent.click(chip);
    await waitFor(() => expect(server.reviews()).toHaveLength(2));
    expect(server.reviews()[1].searchParams.get('continuation')).toBe(page.continuation);
    expect(server.reviews()[1].searchParams.get('pageOf')).toBe('family_members');
  });

  // The source is the after revision's writer, as its tree's history files record it (MIK-R89 v2
  // rules 1 and 6, 9dde2b09); a writer no file records is said as that, never guessed. A family's
  // overview carries no meta line (MIK-R211 v4 rule 10), so the chip is read on a member's.
  const CARRIED: [string, ReviewRevisionWriter, string][] = [
    ['its recorded writer', { state: 'recorded', owners: ['260928-MIK-L181'], detail: 'the after revision of INV-1XKX9ERN is the changed row of 260928-MIK-L181' }, 'carried in from 260928-MIK-L181'],
    ['no recorded writer', { state: 'not_recorded', owners: [], detail: 'no history file records writing the after revision of INV-1XKX9ERN' }, 'carried in · source not recorded'],
    ['an unread history', { state: 'unavailable', owners: [], detail: 'the after memory index is incomplete (1 file(s) failed)' }, 'carried in · source unavailable'],
  ];

  function carriedMember(writer: ReviewRevisionWriter | null): ReviewResult {
    const carried = structuredClone(MEMBER);
    const payload = carried.payload!;
    payload.judgment = { ...payload.judgment, state: 'none', rows: [], detail: 'This leaf recorded no judgment about this record' };
    const steps = payload.meaning_effects!.records.find((record) => record.record === selected(MEMBER).record_id)!;
    Object.assign(steps, { state: 'none', steps: [], detail: 'No meaning effect recorded by this leaf' });
    if (writer) payload.knowledge.revision_selection!.after_writer = writer;
    return carried;
  }

  const memberMeta = async (view: ReturnType<typeof render>) => {
    await view.findByTestId('review-center-member');
    return within(view.getByTestId('review-subject-slot')).getByTestId('review-subject-meta');
  };
  const MEMBER_META = 'invariant · knowledge-write-and-publication-foundation · revision 2 → 4';

  it.each(CARRIED)('names a change the reviewed task did not write by its source: %s', async (_, writer, text) => {
    const carried = carriedMember(writer);
    serving(carried);
    const view = mount(carried);
    const meta = await memberMeta(view);
    const chip = within(meta).getByTestId('review-subject-carried');
    expect(chip.textContent).toBe(text);
    expect(chip.getAttribute('title')).toBe(writer.detail);
    expect(meta.textContent).toBe(`${MEMBER_META} · ${text}`);
  });

  it('shows no chip for a revision the reviewed task wrote, nor a writer', async () => {
    const carried = carriedMember(null);
    const leaf = carried.payload!.candidate.leaf_id;
    carried.payload!.knowledge.revision_selection!.after_writer = { state: 'recorded', owners: [leaf], detail: `the after revision is the changed row of ${leaf}` };
    serving(carried);
    const view = mount(carried);
    const meta = await memberMeta(view);
    expect(within(meta).queryByTestId('review-subject-carried')).toBeNull();
    expect(meta.textContent).toBe(MEMBER_META);
  });

  it('names an unread source when the read carries no writer', async () => {
    const carried = carriedMember(null);
    serving(carried);
    const view = mount(carried);
    const meta = await memberMeta(view);
    expect(within(meta).getByTestId('review-subject-carried').textContent).toBe('carried in · source not read');
  });
});

it('marks the selection and traverses the intact tree by keyboard', async () => {
  serving();
  const view = mount();
  await overviewOf(view, family(FAMILY).family_id);
  expect(familyButton(view, family(FAMILY).family_id).getAttribute('aria-current')).toBe('true');
  const members = view.getAllByTestId('review-family-member-open');
  members[0].focus();
  fireEvent.keyDown(members[0], { key: 'ArrowDown' });
  expect(document.activeElement).toBe(members[1]);
  fireEvent.keyDown(members[1], { key: 'ArrowUp' });
  expect(document.activeElement).toBe(members[0]);
});

it('filters by the actual captured label without restating comparison totals', async () => {
  serving();
  const view = mount();
  await view.findByTestId('review-tree');
  fireEvent.change(view.getByTestId('review-family-filter'), {
    target: { value: family(FAMILY).display_label!.replaceAll('-', ' ') },
  });
  // The filter first reads the complete location inventory (MIK-R211 v4 rule 10).
  await waitFor(() => expect(view.getAllByTestId('review-family')).toHaveLength(1));
  expect(view.getAllByTestId('review-family-member').length).toBeGreaterThan(0);
  expect(view.queryByTestId('review-family-filter-scope')).toBeNull();
  fireEvent.change(view.getByTestId('review-family-filter'), {
    target: { value: 'no-such-family-anywhere' },
  });
  expect(view.getByTestId('review-tree-scroll').textContent).toContain('Nothing matches “no-such-family-anywhere”.');
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
  const page = mount(EMPTY);
  // The overview counts the outline's members (MIK-R211 v4 rule 10); the family read's answered
  // slot after them says the zero was measured.
  const overview = await overviewOf(page, family(EMPTY).family_id);
  expect(within(overview).getByTestId('review-family-counts').textContent).toBe('0 invariants · 0 changed locations');
  expect(within(overview).queryAllByTestId('review-member-summary')).toHaveLength(0);
  const empty = await within(overview).findByTestId('review-center-family-empty');
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
  const tree = await view.findByTestId('review-tree');
  await waitFor(() => expect(tree.querySelector(`[data-subject-id="${before.invariant_id}"]`)).not.toBeNull());
  expect(view.getByTestId('review-center-member-changed').dataset.revisionState).toBe('compared');
  expect(view.queryByTestId('review-center-member-one-sided')).toBeNull();
});

it('prints the shared empty-roster sentence once in the reading path, never again in the tree', async () => {
  serving(EMPTY);
  const page = mount(EMPTY);
  await overviewOf(page, family(EMPTY).family_id);
  await waitFor(() => expect(page.getByTestId('review-surface').dataset.comparison).toBe(EMPTY.payload!.comparison!.reference));
  // The reading path, the family's overview (MIK-R211 v4 rule 10), prints it once; the tree never.
  expect(page.getAllByTestId('review-center-family-empty')).toHaveLength(1);
  expect(page.getByTestId('review-center-family-empty').textContent).toBe(
    emptyRosterSentence(family(EMPTY)),
  );
  expect(page.getByTestId('review-center-family-empty').closest('[data-testid="review-family-overview"]')).not.toBeNull();
  expect(
    page.getByTestId('review-tree').querySelector('[data-testid="review-center-family-empty"]'),
  ).toBeNull();
  expect(page.getByTestId('review-tree').textContent).not.toContain(emptyRosterSentence(family(EMPTY)));
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
  const page = mount(ONE_SIDED);
  const overview = await overviewOf(page, family(ONE_SIDED).family_id);
  expect(within(overview).getAllByTestId('review-member-summary').map((row) => row.dataset.subject).sort()).toEqual(memberIds(ONE_SIDED).sort());
  expect(page.queryByTestId('review-center-family-empty')).toBeNull();
  // The added family's guarantee is drawn after a member's code (synthetic member answer, above).
  openOverviewMember(page, selected(ONE_SIDED_MEMBER).record_id);
  const details = await page.findByTestId('review-center-member-details');
  expect(details.dataset.family).toBe(family(ONE_SIDED).family_id);
  expect(details.closest('[data-testid="review-subject-after-code"]')).not.toBeNull();
  expect(family(ONE_SIDED).before.state).toBe('not_recorded');
  expect(within(details).getByTestId('review-center-guarantee-one-sided').textContent).toContain(
    family(ONE_SIDED).after.guarantee!.joint_guarantee,
  );
  expect(page.queryByTestId('review-center-family-empty')).toBeNull();
});

it('renders deliberately omitted family context as unavailable, never as a measured zero', async () => {
  const body = structuredClone(MEMBER);
  delete body.payload!.family_context;
  serving(body);
  const view = mount(body);
  await view.findByTestId('review-tree');
  // An unread family context is the meta line's chip; its sentence is in Technical details
  // (MIK-R89 v2 rules 1 and 6, amended 9dde2b09).
  expect((await view.findByTestId('review-subject-family-chip')).textContent).toBe('family context not read');
  expect(view.getByTestId('review-family-context-detail').textContent).toContain('Family context was not supplied');
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
// a row's identifiers and history path in that disclosure and its judgment text in the open. A
// family's overview draws the family read's judgment after its member summaries (FamilyState,
// MIK-R211 v4 rule 10): the captured added family's 18-proof judgment on its own page.
function disclosureCap(view: ReturnType<typeof render>, judged: ReviewResult) {
  const root = view.getByTestId('review-surface');
  const details = [...root.querySelectorAll('details')];
  expect(details.length).toBeLessThanOrEqual(20);
  for (const element of details)
    expect(['identifiers', 'provenance']).toContain(element.dataset.disclosure);
  const listed = [...root.querySelectorAll('details[data-disclosure="identifiers"]')]
    .map((element) => element.textContent ?? '')
    .join('\n');
  for (const proof of judged.payload!.judgment.proofs) expect(listed).toContain(proof.id);
  for (const row of judged.payload!.judgment.rows) expect(listed).toContain(row.row_id);
  for (const node of [
    ...root.querySelectorAll('[data-testid="review-judgment-row"], [data-testid="review-judgment-proof"]'),
  ])
    expect(node.closest('details')).toBeNull();
}

it('keeps a whole family view at the disclosure cap with every judgment and proof identifier listed', async () => {
  serving(ONE_SIDED);
  const view = mount(ONE_SIDED);
  const overview = await overviewOf(view, family(ONE_SIDED).family_id);
  await waitFor(() =>
    expect(within(within(overview).getByTestId('review-family-judgment')).getAllByTestId('review-judgment-proof')).toHaveLength(18),
  );
  expect(view.getAllByTestId('review-judgment-proof')).toHaveLength(18);
  disclosureCap(view, ONE_SIDED);
});

// The same cap for an invariant's whole view: the captured added family's judgment, moved onto the
// member's answer (labelled synthetic).
function withJudgmentOf(body: ReviewResult, judged: ReviewResult): ReviewResult {
  const moved = structuredClone(body);
  moved.payload!.judgment = structuredClone(judged.payload!.judgment);
  return moved;
}
const JUDGED = withJudgmentOf(MEMBER, ONE_SIDED);

it('keeps a whole invariant view at the disclosure cap with every judgment and proof identifier listed', async () => {
  serving(JUDGED);
  const view = mount(JUDGED);
  await view.findByTestId('review-center-member');
  await waitFor(() => expect(view.getAllByTestId('review-judgment-proof')).toHaveLength(18));
  disclosureCap(view, JUDGED);
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
  serving(JUDGED);
  const capturedView = mount(JUDGED);
  await capturedView.findByTestId('review-center-member');
  await waitFor(() => expect(capturedView.getAllByTestId('review-judgment-proof')).toHaveLength(18));
  const captured = capturedView.getByTestId('review-surface').querySelectorAll('details').length;
  cleanup();
  const grown = grownJudgment(JUDGED, 9, 57);
  serving(grown);
  const grownView = mount(grown);
  await grownView.findByTestId('review-center-member');
  await waitFor(() => expect(grownView.getAllByTestId('review-judgment-proof')).toHaveLength(57));
  expect(grownView.getByTestId('review-surface').querySelectorAll('details').length).toBe(captured);
});
