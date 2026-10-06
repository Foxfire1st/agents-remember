// MIK-R39 rule 9 in the mounted reviewer, on real data (walkReal.capture-provenance.json): which
// selections keep the walked tree and which start it afresh. The reader has walked to the second
// family (FAM-2HBJREC2) of the shared member, so the first family (FAM-R6R095RW) is a kept family;
// each test then makes one selection and reads what the tree shows. `ReviewSurface` is the real
// component and only `fetch` is stubbed.
import { act, fireEvent, render, waitFor, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ReviewSurface } from './ReviewSurface';
import {
  HBJ,
  HBJ_ID,
  K,
  R6R,
  R6R_ID,
  SHARED,
  WAIT,
  type View,
  bodyOf,
  captured,
  revisionOf,
  clickedSecondFamily,
  families,
  familyBlock,
  installWorld,
  keptTags,
  open,
  reviewCount,
  selectedName,
  selectedNode,
  settled,
  step,
  subjectOf,
  world,
} from './walk.test-utils';

installWorld();

const ZS9 = bodyOf('INV-ZS9ZS878');
const ZS9_SUBJECT = ZS9.payload!.knowledge.revision_selection!.record_id;

// The kept state: the second family selected, the first one kept.
async function keptState() {
  const view = open(SHARED);
  await clickedSecondFamily(view);
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(keptTags(view)).toHaveLength(1);
  return view;
}

const catalogue = (view: View, subject: string) =>
  view.getAllByTestId('review-catalogue-subject').find((row) => row.dataset.subjectId === subject)!;
const noKept = (view: View) => expect(keptTags(view)).toEqual([]);

it('starts afresh at a row of the list of all invariants, at the moment of the selection', async () => {
  const view = await keptState();
  const all = view.getByTestId('review-all-invariants') as HTMLDetailsElement;
  all.open = true;
  fireEvent.click(
    within(all)
      .getAllByTestId('review-catalogue-subject')
      .find((row) => row.dataset.subjectId === ZS9_SUBJECT)!,
  );
  // Before the answer: the kept family is already gone; the tree is the answer on screen's own.
  expect(view.getByTestId('review-reading-pending')).toBeTruthy();
  noKept(view);
  expect(families(view)).toEqual([HBJ_ID]);
  await waitFor(() => expect(families(view)).toEqual([R6R_ID]), WAIT);
  noKept(view);
});

it('starts afresh at the family row of the subject already selected', async () => {
  const view = await keptState();
  const before = reviewCount();
  const row = catalogue(view, HBJ_ID);
  row.focus(); // a pointer focuses the row it clicks
  fireEvent.click(row);
  expect(families(view)).toEqual([HBJ_ID]);
  noKept(view);
  expect(reviewCount() - before).toBe(0);
  // The clicked row is gone with the kept family; focus ends on the selected row, not on the body.
  await waitFor(() => expect(document.activeElement).toBe(selectedNode(view)), WAIT);
  expect(selectedNode(view)!.dataset.family).toBe(HBJ_ID);
});

it('starts afresh at "All source changes"', async () => {
  const view = await keptState();
  fireEvent.click(view.getByTestId('review-task-source'));
  await waitFor(() => expect(view.getByTestId('review-center-unselected')).toBeTruthy(), WAIT);
  expect(families(view)).toEqual([]);
  noKept(view);
  // With no family to stand for, the tree's place is after the catalogue's rows, still as a child of
  // the navigation itself: no element of its own is put around it.
  expect(view.getByTestId('review-family-tree').parentElement).toBe(
    view.getByTestId('review-catalogue-navigation'),
  );
});

it('starts afresh at a refresh of the review', async () => {
  const view = await keptState();
  const before = reviewCount();
  fireEvent.click(view.getByTestId('review-refresh'));
  expect(families(view)).toEqual([HBJ_ID]);
  noKept(view);
  await waitFor(() => expect(reviewCount() - before).toBe(1), WAIT);
  await settled(view);
  expect(families(view)).toEqual([HBJ_ID]);
  noKept(view);
});

it('starts afresh at an answer of another comparison and says so', async () => {
  const view = await keptState();
  const other = structuredClone(ZS9);
  other.payload!.source.inventory.after_code_tree_id = '0'.repeat(40);
  const previous = world.answer;
  world.answer = (url, asked) => (asked === subjectOf(ZS9) ? other : previous(url, asked));
  // A member row of the kept family: an in-tree selection, answered by another comparison.
  fireEvent.click(memberRow(view, R6R_ID, 'INV-ZS9ZS878'));
  await waitFor(() => expect(view.getByTestId('review-family-walk-notice')).toBeTruthy(), WAIT);
  expect(view.getByTestId('review-family-walk-notice').textContent).toContain('comparison changed');
  expect(families(view)).toEqual([R6R_ID]);
  noKept(view);
});

const memberRow = (view: View, familyId: string, label: string) =>
  within(familyBlock(view, familyId))
    .getAllByTestId('review-family-member-open')
    .find((node) => node.dataset.revision === revisionOf(label))!;

const refusal = {
  state: 'refused',
  operation: 'read_knowledge_review',
  repository_id: 'agents-remember',
  refusal: {
    code: 'subject_unresolved',
    detail: 'The subject cannot be compared.',
    next_action: 'Review the source changes instead.',
  },
};

it('keeps every row when a selected subject is refused, and starts afresh at the offer of the task context', async () => {
  const view = await keptState();
  const previous = world.answer;
  world.answer = (url, asked) => (asked === subjectOf(ZS9) ? refusal : previous(url, asked));
  const row = memberRow(view, R6R_ID, 'INV-ZS9ZS878');
  fireEvent.click(row);
  await waitFor(() => expect(view.getByTestId('review-reading-problem')).toBeTruthy(), WAIT);
  // The tree keeps every row, the selection mark is on the requested row, and k works from it.
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(keptTags(view)).toHaveLength(1);
  expect(selectedNode(view)).toBe(row);
  row.focus(); // a pointer moves focus to the row it selects
  const before = reviewCount();
  await step(view, K, 1);
  expect(selectedName(view)).toBe('INV-2E8MG43K');
  expect(reviewCount() - before).toBe(1);
});

it('starts afresh at the offer to open the task context after a refusal', async () => {
  const view = await keptState();
  const previous = world.answer;
  world.answer = (url, asked) => (asked === subjectOf(ZS9) ? refusal : previous(url, asked));
  fireEvent.click(memberRow(view, R6R_ID, 'INV-ZS9ZS878'));
  const offer = await view.findByTestId('review-source-instead', undefined, WAIT);
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  fireEvent.click(offer);
  noKept(view);
  expect(families(view)).toEqual([HBJ_ID]);
  await waitFor(() => expect(families(view)).toEqual([]), WAIT);
  noKept(view);
});

it('keeps every row when the read of a selected subject fails', async () => {
  const view = await keptState();
  const previous = world.answer;
  world.answer = (url, asked) =>
    asked === subjectOf(ZS9) ? new TypeError('fetch failed') : previous(url, asked);
  const row = memberRow(view, R6R_ID, 'INV-ZS9ZS878');
  fireEvent.click(row);
  await waitFor(() => expect(view.getByTestId('review-reading-problem')).toBeTruthy(), WAIT);
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(keptTags(view)).toHaveLength(1);
  expect(selectedNode(view)).toBe(row);
  // Reading again after the failure: the retry answers, and the tree is still whole.
  world.answer = previous;
  fireEvent.click(view.getByTestId('review-retry'));
  await waitFor(() => expect(view.queryByTestId('review-reading-problem')).toBeNull(), WAIT);
  // The answer is the subject's own: the second family is now the kept one.
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(familyBlock(view, HBJ_ID).dataset.familyKept).toBe('true');
  expect(familyBlock(view, R6R_ID).dataset.familyKept).toBeUndefined();
});

const MERGE_FILE = 'dashboard/src/panels/review/familyWalkMerge.ts';

// The unexplained-changes lane, the file's classification and its content, as served for the scratch leaf.
function serveTreeRoutes() {
  const lane = captured<unknown>('walkReal.lane.captured.json');
  const file = captured<unknown>('walkReal.file.captured.json');
  const source = captured<unknown>('walkReal.source.captured.json');
  world.tree = (url) => {
    if (url.pathname.endsWith('/source-content')) return source;
    if (url.searchParams.get('lane')) return lane;
    return url.searchParams.get('file') === MERGE_FILE ? file : undefined;
  };
}

it('starts afresh at a followed intent marker, and its return does not bring the kept family back', async () => {
  const view = await keptState();
  serveTreeRoutes();
  fireEvent.click(
    view
      .getAllByTestId('review-inventory-open')
      .find((one) => one.textContent?.includes('familyWalkMerge'))!,
  );
  const mark = await waitFor(() => {
    const found = view.getAllByTestId('review-hunk-mark');
    expect(found).toHaveLength(1);
    return found[0];
  }, WAIT);
  fireEvent.click(mark);
  const occurrences = within(view.getByTestId('review-hunk-mark-panel')).getAllByTestId(
    'review-hunk-mark-occurrence',
  );
  // INV-ZS9ZS878 as a member of the first family: the kept family itself, followed from a file.
  fireEvent.click(occurrences.find((one) => one.textContent?.includes('(after,'))!);
  await waitFor(() => expect(selectedNode(view) && selectedName(view)).toBe('INV-ZS9ZS878'), WAIT);
  await waitFor(() => expect(view.queryByTestId('review-reading-pending')).toBeNull(), WAIT);
  // The tree is the marker's subject's own: the second family is not kept beside it.
  expect(families(view)).toEqual([R6R_ID]);
  noKept(view);
  // The way back restores the reading position; it brings no kept family back either.
  fireEvent.click(view.getByTestId('review-marker-return'));
  await waitFor(() => expect(view.queryByTestId('review-marker-return')).toBeNull(), WAIT);
  await waitFor(() => expect(families(view)).toEqual([HBJ_ID]), WAIT);
  noKept(view);
}, 30000);

it('opens on the subject asked for with its own families only, and a late catalogue selects the first family afresh', async () => {
  const catalogue = [R6R, HBJ].map((body) => ({
    selector_kind: 'family',
    selector_id: body.payload!.knowledge.revision_selection!.record_id,
    label: body.payload!.family_context!.entries[0].display_label,
    presence: 'both',
  }));
  let release: (() => void) | undefined;
  const held = new Promise<void>((resolve) => (release = resolve));
  // The catalogue answers after the bounded wait: the task context is read first, then the first
  // family is selected from the late answer.
  world.entries = {
    state: 'entries',
    entries: catalogue,
    total_subjects: 2,
    family_total: 2,
    invariant_total: 0,
  };
  const stubbed = globalThis.fetch;
  vi.stubGlobal('fetch', async (address: string, init?: RequestInit) => {
    if (String(address).includes('/entries')) await held;
    return stubbed(address, init);
  });
  const candidate = R6R.payload!.candidate;
  const view = render(
    <ReviewSurface
      repo={candidate.repository_id}
      master={candidate.master}
      leaf={candidate.leaf_id}
      onBack={() => undefined}
    />,
  );
  await view.findByTestId('review-center-unselected', undefined, WAIT);
  release?.();
  await waitFor(() => expect(families(view)).toEqual([R6R_ID]), WAIT);
  noKept(view);
  expect(view.getByTestId('review-center-family').dataset.family).toBe(R6R_ID);
});

it('keeps the families and states the subject when the answer composes no family context', async () => {
  const view = await keptState();
  const bare = structuredClone(ZS9);
  const context = bare.payload!.family_context!;
  bare.payload!.family_context = {
    ...context,
    state: 'no_family_recorded',
    entries: [],
    families_returned: 0,
  };
  const previous = world.answer;
  world.answer = (url, asked) => (asked === subjectOf(ZS9) ? bare : previous(url, asked));
  const row = memberRow(view, R6R_ID, 'INV-ZS9ZS878');
  fireEvent.click(row);
  await waitFor(() => expect(view.getByTestId('review-family-subject-state')).toBeTruthy(), WAIT);
  expect(view.getByTestId('review-family-subject-state').textContent).toBe(
    'For the selected subject: The selected invariant has no recorded family membership.',
  );
  // No row is removed, and the requested row is still the one marked.
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(keptTags(view)).toHaveLength(2);
  expect(selectedNode(view)).toBe(row);
});

it('keeps the walked tree at the actions rule 9 says change nothing', async () => {
  serveTreeRoutes();
  const view = await keptState();
  const shown = () => [families(view), keptTags(view)];
  const initial = shown();
  const reads = reviewCount();
  // The order control and the filter.
  fireEvent.click(view.getByTestId('review-tree-order'));
  expect(shown()).toEqual(initial);
  fireEvent.click(view.getByTestId('review-tree-order'));
  // A refresh of the subject catalogue.
  const catalogueReads = () =>
    world.requests.all.filter((url) => url.pathname.endsWith('/entries')).length;
  const listed = catalogueReads();
  fireEvent.click(
    within(view.getByTestId('review-catalogue-navigation')).getByText('Refresh subjects'),
  );
  await waitFor(() => expect(catalogueReads()).toBe(listed + 1), WAIT);
  expect(shown()).toEqual(initial);
  // Opening a file of the source explorer.
  fireEvent.click(
    view
      .getAllByTestId('review-inventory-open')
      .find((one) => one.textContent?.includes('familyWalkMerge'))!,
  );
  await view.findByTestId('review-opened-file', undefined, WAIT);
  expect(shown()).toEqual(initial);
  // A lane destination of the unexplained-changes lane, and the way back to a family.
  const destinations = await view.findByTestId('review-lane-destinations', undefined, WAIT);
  await waitFor(() => expect(destinations.dataset.laneState).toBe('measured'), WAIT);
  fireEvent.click(within(destinations).getAllByTestId('review-lane-destination')[0]);
  await view.findByTestId('review-lane', undefined, WAIT);
  expect(shown()).toEqual(initial);
  expect(reviewCount()).toBe(reads);
}, 30000);

// Every element asked to come into view while `run` is in progress (jsdom has no `scrollIntoView`).
async function recordingScrolls(run: (scrolled: Element[]) => Promise<void>) {
  const scrolled: Element[] = [];
  Element.prototype.scrollIntoView = function (this: Element) {
    scrolled.push(this);
  };
  try {
    await run(scrolled);
  } finally {
    delete (Element.prototype as Partial<Element>).scrollIntoView;
  }
}

it.each([true, false])(
  'reveals an in-tree read only in the stacked layout (%s)',
  async (stacked) => {
    const media = window.matchMedia;
    window.matchMedia = (query) => ({
      ...media(query),
      matches: stacked && query === '(max-width: 60rem)',
    });
    try {
      const view = open(SHARED);
      await waitFor(() => expect(families(view)).toEqual([R6R_ID, HBJ_ID]), WAIT);
      const workspace = view.getByTestId('review-workspace');
      await recordingScrolls(async (scrolled) => {
        const reads = reviewCount();
        await clickedSecondFamily(view);
        const center = view.getByTestId('review-center-column');
        expect(scrolled).toEqual(stacked ? [center] : []);
        expect(document.activeElement).toBe(selectedNode(view));
        expect(reviewCount() - reads).toBe(1);
        expect(view.getByTestId('review-workspace')).toBe(workspace);
        // A key activation reads the cached shared member and follows the same reveal rule.
        scrolled.length = 0;
        await step(view, K, 0);
        expect(scrolled).toEqual(stacked ? [center] : []);
        expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
        await clickedSecondFamily(view);
        scrolled.length = 0;
        // The same subject chosen outside the tree keeps its original scrolling behavior.
        fireEvent.click(catalogue(view, HBJ_ID));
        await settled(view);
        expect(scrolled).toEqual([]);
        expect(families(view)).toEqual([HBJ_ID]);
      });
    } finally {
      window.matchMedia = media;
    }
  },
);

it('does not reveal a stacked in-tree answer after the reader moved focus while it was pending', async () => {
  const view = await keptState();
  const media = window.matchMedia;
  window.matchMedia = (query) => ({ ...media(query), matches: query === '(max-width: 60rem)' });
  let release: (() => void) | undefined;
  const held = new Promise<void>((resolve) => (release = resolve));
  const served = globalThis.fetch;
  vi.stubGlobal('fetch', async (address: string, init?: RequestInit) => {
    if (String(address).includes(`selectorId=${ZS9_SUBJECT}`)) await held;
    return served(address, init);
  });
  try {
    await recordingScrolls(async (scrolled) => {
      fireEvent.click(memberRow(view, R6R_ID, 'INV-ZS9ZS878'));
      await view.findByTestId('review-reading-pending', undefined, WAIT);
      const elsewhere = view.getByTestId('review-family-filter');
      elsewhere.focus();
      release?.();
      await waitFor(
        () =>
          expect(view.getByTestId('review-center-member').textContent).toContain('INV-ZS9ZS878'),
        WAIT,
      );
      await act(async () => {});
      expect(document.activeElement).toBe(elsewhere);
      expect(scrolled).toEqual([]);
      expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
    });
  } finally {
    release?.();
    window.matchMedia = media;
  }
});

it('brings the selected row into view once the answer of a refresh is shown, without moving focus', async () => {
  const view = await keptState();
  await recordingScrolls(async (scrolled) => {
    const button = view.getByTestId('review-refresh');
    button.focus();
    fireEvent.click(button);
    // Not at the click: the rail keeps its place until the refreshed answer is shown.
    expect(scrolled).toEqual([]);
    await waitFor(() => expect(scrolled).toContain(selectedNode(view)), WAIT);
    expect(document.activeElement).toBe(button);
  });
});

it('waits for the answer of a refresh asked before the effects of the previous answer have run', async () => {
  const view = await keptState();
  await recordingScrolls(async (scrolled) => {
    const button = view.getByTestId('review-refresh');
    const shown = () =>
      view.getByTestId('review-surface').dataset.reviewPending === undefined &&
      selectedNode(view) !== null &&
      selectedName(view) === 'INV-ZS9ZS878';
    // A member row is selected, and the refresh is asked by a mutation observer in the first
    // microtask in which the member's answer is shown: after its commit and before React has run
    // that commit's passive effects. That late effect sees an answer that is new to it; it is not
    // the answer of the refresh.
    let atClick: Element[] | undefined;
    const observer = new MutationObserver(() => {
      if (atClick !== undefined || !shown()) return;
      fireEvent.click(button);
      atClick = [...scrolled];
    });
    observer.observe(view.container, {
      subtree: true,
      childList: true,
      attributes: true,
      characterData: true,
    });
    try {
      fireEvent.click(memberRow(view, R6R_ID, 'INV-ZS9ZS878'));
      await waitFor(() => expect(atClick, 'the refresh is asked').toBeDefined(), WAIT);
    } finally {
      observer.disconnect();
    }
    expect(atClick).toEqual([]);
    await waitFor(() => expect(scrolled).toContain(selectedNode(view)), WAIT);
  });
});

it('forgets the scroll a refresh asked for when its read fails, so a later page of the subject leaves the rail alone', async () => {
  const view = await keptState();
  await recordingScrolls(async (scrolled) => {
    const previous = world.answer;
    world.answer = () => new TypeError('fetch failed');
    fireEvent.click(view.getByTestId('review-refresh'));
    // The comparison last read stays on screen, with the failure stated beside it.
    await view.findByTestId('review-failure', undefined, WAIT);
    expect(view.getByTestId('review-center-family').dataset.family).toBe(HBJ_ID);
    world.answer = previous;
    // A page of the subject on screen is then read: an answer that is no selection and no refresh.
    const reads = reviewCount();
    fireEvent.change(view.getByTestId('review-page-collection'), { target: { value: 'records' } });
    await waitFor(() => {
      expect(reviewCount()).toBe(reads + 1);
      expect(view.getByTestId('review-page-controls').dataset.pageRequested).toBe('records');
    }, WAIT);
    // Every effect of that answer has run: nothing was brought into view.
    await act(async () => {});
    expect(scrolled).toEqual([]);
  });
});

it('brings the selected row into view when the retry of a failed refresh is answered', async () => {
  const view = await keptState();
  await recordingScrolls(async (scrolled) => {
    const previous = world.answer;
    world.answer = () => new TypeError('fetch failed');
    fireEvent.click(view.getByTestId('review-refresh'));
    const retry = await view.findByTestId('review-retry', undefined, WAIT);
    // Every effect of the failure has run: the request of the refresh is dropped.
    await act(async () => {});
    world.answer = previous;
    const reads = reviewCount();
    fireEvent.click(retry);
    // The retry is the refresh again. Not at the click: the rail keeps its place until the answer
    // is shown.
    expect(scrolled).toEqual([]);
    await waitFor(() => expect(scrolled).toContain(selectedNode(view)), WAIT);
    expect(reviewCount() - reads).toBe(1);
    expect(view.queryByTestId('review-retry')).toBeNull();
    expect(families(view)).toEqual([HBJ_ID]);
  });
});

// A refresh of the kept state fails, and `next` is run by a mutation observer in the first microtask
// in which that failure is shown with its retry control: after its commit and before React has run
// that commit's passive effects. The reviewer answers again from then on.
async function inTheTurnOfAFailedRefresh(view: View, next: () => void) {
  const previous = world.answer;
  world.answer = () => new TypeError('fetch failed');
  let run = false;
  const observer = new MutationObserver(() => {
    if (run || view.queryByTestId('review-retry') === null) return;
    run = true;
    world.answer = previous;
    next();
  });
  observer.observe(view.container, {
    subtree: true,
    childList: true,
    attributes: true,
    characterData: true,
  });
  try {
    fireEvent.click(view.getByTestId('review-refresh'));
    await waitFor(() => expect(run, 'the failure is shown').toBe(true), WAIT);
  } finally {
    observer.disconnect();
  }
}

it('brings the selected row into view after a refresh asked in the turn that shows a failed read', async () => {
  const view = await keptState();
  await recordingScrolls(async (scrolled) => {
    // The failure which the late effect of that turn sees was on screen when the second refresh was
    // asked. It is not the failure of that refresh, whose request stays.
    await inTheTurnOfAFailedRefresh(view, () =>
      fireEvent.click(view.getByTestId('review-refresh')),
    );
    await waitFor(() => expect(scrolled).toContain(selectedNode(view)), WAIT);
    expect(view.queryByTestId('review-retry')).toBeNull();
  });
});

it('brings the selected row into view when the retry is made in the turn that shows the failed refresh', async () => {
  const view = await keptState();
  await recordingScrolls(async (scrolled) => {
    // The retry comes before the effect that drops the request of the failed refresh, and is the
    // refresh again all the same.
    await inTheTurnOfAFailedRefresh(view, () => fireEvent.click(view.getByTestId('review-retry')));
    await waitFor(() => expect(scrolled).toContain(selectedNode(view)), WAIT);
    expect(view.queryByTestId('review-retry')).toBeNull();
  });
});
