import { readFileSync } from 'node:fs';
import path from 'node:path';

import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewPayload, ReviewOutline } from '../../data/review';
import { FamilyReviewCenter } from './FamilyReviewCenter';
import { useReviewReadCycle, type ReviewPageRequest } from './ReviewReadCycle';
import { ReviewReadCache } from './ReviewReadCache';
import { shownPayload } from './ReviewOutcome';
import { ReviewSurface } from './ReviewSurface';
import { ChangeSetViewer } from '../changeset/ChangeSetViewer';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import { dashboardStore } from '../../data/store';

// Raw current tree-route bodies following the producer's published page-size-4 cursors.
// See familyReview.capture-provenance.json. No successful review body is rewritten.
// Corruptions below are explicit negative transport/admission probes, not capture replacements.
interface Body {
  payload: ReviewPayload;
}
const capture = JSON.parse(
  readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), 'familyPaging.captured.json'),
    'utf8',
  ),
) as {
  family_id: string;
  subject_id: string;
  before: Body[];
  after: Body[];
};
const first = capture.before[0];
const cards = JSON.parse(
  readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), 'l86.member-cards.captured.json'),
    'utf8',
  ),
);
// Historical full-tree response is retained only to isolate worklist chrome here;
// the current full-tree producer refusal is tracked separately, not qualified by these tests.
const trees = JSON.parse(
  readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), 'l86.trees.captured.json'),
    'utf8',
  ),
);
const lane = JSON.parse(
  readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), 'l86.lane.captured.json'),
    'utf8',
  ),
);
function treeReply(url: URL) {
  if (!url.pathname.endsWith('/trees')) return undefined;
  return url.searchParams.has('invariants') ? cards : url.searchParams.has('lane') ? lane : trees;
}
const target = {
  repo: first.payload.candidate.repository_id,
  master: first.payload.candidate.master,
  leaf: first.payload.candidate.leaf_id,
  selectorKind: 'invariant' as const,
  selectorId: capture.subject_id,
};
const family = (payload: ReviewPayload) =>
  payload.family_context!.entries.find((entry) => entry.family_id === capture.family_id)!;
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const cursorOf = (body: Body) => body.payload.page!.continued_from!;

// Test-only catalogue projection of the retained capture's identities: the replaced sidebar
// no longer discovers members from roster disclosures. Review payloads/cursors remain exact bytes.
function capturedOutline(): ReviewOutline {
  const families = first.payload.family_context!.entries;
  const members = [
    ...new Map(
      families.flatMap((entry) =>
        [...entry.before.members, ...entry.after.members].map(
          (member) => [member.invariant_id, member] as const,
        ),
      ),
    ).values(),
  ];
  return {
    state: 'outlined',
    detail: 'Captured family/member identities for the mounted legacy test.',
    families: families.map((entry) => ({
      selector_id: entry.family_id,
      id: entry.family_id,
      title: entry.display_label ?? entry.family_id,
      change: 'unchanged',
      members: [
        ...new Set([...entry.before.members, ...entry.after.members].map((m) => m.invariant_id)),
      ].map((id) => ({ selector_id: id!, id: id!, membership: 'both' })),
    })),
    invariants: members.map((member) => ({
      selector_id: member.invariant_id!,
      id: member.invariant_id!,
      summary: member.statement ?? member.display_label ?? member.invariant_id!,
      change: 'changed',
    })),
  };
}
vi.mock('../../data/useReviewCatalogue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../data/useReviewCatalogue')>();
  return {
    ...actual,
    useReviewCatalogue: () => ({
      loading: false,
      entries: [],
      empty: true,
      stale: false,
      facts: 'test',
      outline: capturedOutline(),
      refresh: () => undefined,
    }),
  };
});

afterEach(() => {
  resetCardCachesForTests();
  cleanup();
  dashboardStore.getState().reset();
  vi.unstubAllGlobals();
});

// The removed source-list assertions now inspect the viewer's exact population. This test-only
// list projects the same six captured inventory entries; it makes no HTTP/recorded-range claim.
async function expectCompleteInventory(view: ReturnType<typeof render>) {
  const inventory = first.payload.source.inventory;
  expect(view.getByTestId('review-scope-record').textContent).toContain(
    `${inventory.listed_total} changed files`,
  );
  const priorFetch = globalThis.fetch;
  const files = inventory.entries.map((entry) => ({
    path: entry.path,
    status: entry.status,
    insertions: 0,
    deletions: 0,
  }));
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://review.test');
      if (url.pathname === '/api/changeset/task')
        return response({
          scope: 'captured-family-inventory',
          code: files,
          memory: [],
          counters: {
            code: { files: files.length, insertions: 0, deletions: 0 },
            memory: { files: 0, insertions: 0, deletions: 0 },
          },
        });
      if (url.pathname === '/api/changeset/file-diff')
        return response({
          scope: 'captured-family-inventory',
          kind: 'code',
          path: url.searchParams.get('path'),
          language: 'text',
          before: null,
          after: null,
        });
      return await priorFetch(input);
    }),
  );
  const page = render(
    <ChangeSetViewer repo={target.repo} scope="captured-family-inventory" onBack={vi.fn()} />,
  );
  try {
    await waitFor(() =>
      expect(page.container.querySelectorAll('[data-active]')).toHaveLength(inventory.listed_total),
    );
    expect(
      [...page.container.querySelectorAll<HTMLElement>('[data-active]')].map(
        (row) => row.querySelector('button')!.children[1].textContent,
      ),
    ).toEqual(inventory.entries.map((entry) => entry.path));
  } finally {
    page.unmount();
    vi.stubGlobal('fetch', priorFetch);
  }
}

function mountCycle() {
  let body: unknown = first;
  const fetch = vi.fn<(input: RequestInfo | URL) => Promise<Response>>(async () => response(body));
  vi.stubGlobal('fetch', fetch);
  const cache = new ReviewReadCache();
  const view = renderHook(
    ({
      selection,
      selectorId,
      history,
    }: {
      selection?: ReviewPageRequest;
      selectorId: string;
      history?: 'recorded';
    }) => useReviewReadCycle({ ...target, selectorId, history, instead: null, selection, cache }),
    {
      initialProps: { selectorId: target.selectorId } as {
        selection?: ReviewPageRequest;
        selectorId: string;
        history?: 'recorded';
      },
    },
  );
  return {
    ...view,
    fetch,
    answer: (next: unknown) => {
      body = next;
    },
  };
}

it('retains exact content and claim identities while before and after walks advance independently', async () => {
  const view = mountCycle();
  await waitFor(() => expect(view.result.current.read.phase).toBe('reviewed'));
  const initialMembers = family(first.payload).before.members;
  const steps = [...capture.before.slice(1), ...capture.after.slice(1)];
  for (const body of [...steps, steps[steps.length - 1]]) {
    view.answer(body);
    await act(async () =>
      view.rerender({
        selectorId: target.selectorId,
        selection: { of: 'family_members', continuation: cursorOf(body), size: 4 },
      }),
    );
    const payload = view.result.current.retained!.payload;
    expect(family(payload).before.members.map((member) => member.statement)).toEqual(
      initialMembers.map((member) => member.statement),
    );
    expect(payload.knowledge).toEqual(body.payload.knowledge);
    expect(payload.judgment).toEqual(body.payload.judgment);
    expect(payload.source.inventory).toEqual(first.payload.source.inventory);
    expect(payload.knowledge.revision_selection?.record_id).toBe(target.selectorId);
  }
  const loaded = family(view.result.current.retained!.payload);
  for (const side of ['before', 'after'] as const) {
    const all = capture[side].flatMap((body) => family(body.payload)[side].members);
    const claims = new Set(
      all.flatMap((member) => member.sources.map((source) => source.claim_id)),
    );
    const members = loaded[side].members;
    expect(loaded[side].page?.complete).toBe(true);
    expect(members).toHaveLength(loaded[side].members_total);
    expect(new Set(members.map((member) => member.member_id)).size).toBe(members.length);
    expect(members.every((member) => member.state === 'recorded')).toBe(true);
    expect(members.flatMap((member) => member.sources)).toHaveLength(claims.size);
    expect(
      new Set(members.flatMap((member) => member.sources.map((source) => source.claim_id))),
    ).toEqual(claims);
  }
  expect(loaded.state).toBe('recorded');
});

it('retains the coherent display and fails a rejected continuation, including a structured page refusal', async () => {
  const corruptions: Array<(payload: ReviewPayload) => void> = [
    (payload) => {
      payload.page!.continued_from = 'another-walk';
    },
    (payload) => {
      payload.comparison!.after_code_tree_id = 'f'.repeat(40);
    },
    (payload) => {
      payload.comparison!.after_snapshot_digest = 'f'.repeat(64);
    },
    (payload) => {
      payload.knowledge.revision_selection!.record_id = 'other-subject';
    },
    (payload) => {
      payload.candidate.repository_id = 'other-repository';
    },
    (payload) => {
      family(payload).before.family_revision_id = 'other-family-revision';
    },
    (payload) => {
      payload.staleness.state = 'stale';
    },
    (payload) => {
      const member = family(payload).before.members[0];
      member.state = 'recorded';
      member.payload_digest = 'different-content';
      member.statement = 'conflict';
    },
    (payload) => {
      payload.page = null;
      payload.page_refusal = {
        code: 'comparison_page_reset',
        detail: 'The requested page could not be read.',
        next_action: 'Retry the published continuation.',
      };
    },
  ];
  for (const corrupt of corruptions) {
    const view = mountCycle();
    await waitFor(() => expect(view.result.current.read.phase).toBe('reviewed'));
    view.answer(capture.before[1]);
    await act(async () =>
      view.rerender({
        selectorId: target.selectorId,
        selection: { of: 'family_members', continuation: cursorOf(capture.before[1]), size: 4 },
      }),
    );
    const coherent = view.result.current.retained!.payload;
    const next = structuredClone(capture.before[2]);
    corrupt(next.payload);
    view.answer(next);
    await act(async () =>
      view.rerender({
        selectorId: target.selectorId,
        selection: { of: 'family_members', continuation: cursorOf(capture.before[2]), size: 4 },
      }),
    );
    expect(view.result.current.retained!.payload).toEqual(coherent);
    expect(shownPayload(view.result.current.read, view.result.current.retained!.payload)).toEqual(
      coherent,
    );
    expect(view.result.current.read).toMatchObject({
      phase: 'failed',
      problem: { code: next.payload.page_refusal?.code ?? 'comparison_page_unreadable' },
    });
    view.unmount();
  }
});

it('fails an unadvertised request cursor while preserving whole-review refusal semantics', async () => {
  const view = mountCycle();
  await waitFor(() => expect(view.result.current.read.phase).toBe('reviewed'));
  const next = structuredClone(capture.before[1]);
  next.payload.page!.continued_from = 'unadvertised-cursor';
  family(next.payload).before.page!.continued_from = 'unadvertised-cursor';
  view.answer(next);
  await act(async () =>
    view.rerender({
      selectorId: target.selectorId,
      selection: { of: 'family_members', continuation: 'unadvertised-cursor', size: 4 },
    }),
  );
  expect(view.result.current.read.phase).toBe('failed');
  expect(view.result.current.retained!.payload).toEqual(first.payload);
  view.answer({
    state: 'refused',
    operation: 'read_knowledge_review',
    repository_id: target.repo,
    refusal: {
      code: 'comparison_refused',
      detail: 'This review is unavailable.',
      next_action: 'Open a new review.',
    },
  });
  await act(async () =>
    view.rerender({
      selectorId: target.selectorId,
      selection: { of: 'family_members', continuation: cursorOf(capture.before[1]), size: 4 },
    }),
  );
  expect(view.result.current.read.phase).toBe('refused');
  expect(shownPayload(view.result.current.read, view.result.current.retained!.payload)).toBeNull();
});

it.each(['wrong subject', 'conflicting content', 'page refusal'])(
  'shows an explicit failure and the coherent accumulated review for %s',
  async (fault) => {
    const rejected = structuredClone(capture.before[2]);
    if (fault === 'wrong subject')
      rejected.payload.knowledge.revision_selection!.record_id = 'rejected-subject';
    if (fault === 'conflicting content') {
      const member = family(rejected.payload).before.members[0];
      member.state = 'recorded';
      member.payload_digest = 'conflicting-digest';
      member.statement = 'REJECTED IMMUTABLE CONTENT';
    }
    if (fault === 'page refusal') {
      rejected.payload.page = null;
      rejected.payload.page_refusal = {
        code: 'comparison_page_reset',
        detail: 'The family page could not be read.',
        next_action: 'Retry the published continuation.',
      };
    }
    const bodies = [first, capture.before[1], rejected];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL(String(input), 'http://review.test');
        const tree = treeReply(url);
        if (tree) return response(tree);
        if (url.pathname.endsWith('/source-content'))
          return response({
            state: 'refused',
            operation: 'read_knowledge_review_source_content',
            repository_id: target.repo,
            refusal: {
              code: 'not-found',
              detail: 'This check measures retained review state.',
              next_action: 'Read the served file.',
            },
          });
        if (url.pathname !== '/api/review/intent') throw new Error(`Unexpected route: ${url}`);
        return response(bodies.shift());
      }),
    );
    const view = render(<ReviewSurface {...target} onBack={vi.fn()} />);
    await view.findByTestId('review-center-member');
    const next = (body: Body) =>
      view
        .getAllByTestId('review-center-roster-next')
        .find(
          (button) =>
            button.dataset.family === capture.family_id &&
            button.dataset.continuation === cursorOf(body),
        )!;
    fireEvent.click(next(capture.before[1]));
    await waitFor(() =>
      expect(view.getByTestId('review-page-bounds').textContent).toContain('returned 8'),
    );
    // The tree's rows take their locations from the cards read, which settles on its own; the
    // retained review is compared once every member row holds them, whatever the machine's load.
    await waitFor(() =>
      view.getAllByTestId('review-family-member').forEach((row) =>
        expect(
          within(row).queryAllByTestId(/^review-tree-(location|group)$/).length,
        ).toBeGreaterThan(0),
      ),
    );
    const selected = view.getByTestId('review-center-member-changed').textContent;
    const evidence = view.getByTestId('review-center-judgment').textContent;
    const members = view.getAllByTestId('review-family-member').map((row) => row.textContent);
    fireEvent.click(next(capture.before[2]));
    // The page's other reads may state their own failures; this one is the walk's.
    const code = fault === 'page refusal' ? 'comparison_page_reset' : 'comparison_page_unreadable';
    const walkFailure = () =>
      view.queryAllByTestId('review-failure').find((node) => node.dataset.reviewCode === code);
    await waitFor(() => expect(walkFailure()).toBeTruthy());
    const failure = walkFailure()!;
    expect(view.getByTestId('review-retained-generation')).toBeTruthy();
    expect(view.getByTestId('review-center-member-changed').textContent).toBe(selected);
    expect(view.getByTestId('review-center-judgment').textContent).toBe(evidence);
    expect(view.getAllByTestId('review-family-member').map((row) => row.textContent)).toEqual(
      members,
    );
    expect(view.container.textContent).not.toContain('REJECTED IMMUTABLE CONTENT');
    expect(view.container.textContent).not.toContain('rejected-subject');
    await expectCompleteInventory(view);
    expect(next(capture.before[2])).toBeTruthy();
    if (fault === 'page refusal')
      expect(failure.textContent).toContain('Retry the published continuation.');
  },
);

function mountFamilyCenter(payload: ReviewPayload) {
  const onRosterNext = vi.fn();
  const view = render(
    <FamilyReviewCenter
      payload={payload}
      subject={{ kind: 'family', id: capture.family_id }}
      selection={{ familyId: capture.family_id }}
      layout="split"
      onOpenFromCenter={vi.fn()}
      onOpenMember={vi.fn()}
      onRosterNext={onRosterNext}
    />,
  );
  return { ...view, onRosterNext };
}

it('states loaded claim scope in family details for a content-only page, completed walk and unavailable side', async () => {
  const view = mountCycle();
  await waitFor(() => expect(view.result.current.read.phase).toBe('reviewed'));
  const partial = mountFamilyCenter(first.payload);
  expect(partial.getByTestId('review-center-family')).toBeTruthy();
  expect(partial.getByTestId('review-center-family-expressions-scope').textContent).toContain(
    'not yet fully loaded',
  );
  expect(partial.getByTestId('review-center-family-expressions-verdict').textContent).toContain(
    '0 loaded realization claim(s)',
  );
  expect(partial.getByTestId('review-center-family-expressions-none').textContent).toContain(
    'among the loaded claims',
  );
  expect(partial.container.textContent).not.toContain('Nothing is missing');
  expect(partial.container.textContent).not.toContain('shows its unchanged expressions');
  fireEvent.click(partial.getAllByTestId('review-center-roster-next')[0]);
  expect(partial.onRosterNext).toHaveBeenCalledWith(
    capture.family_id,
    'before',
    family(first.payload).before.page!.continuation,
  );
  partial.unmount();

  for (const body of [...capture.before.slice(1), ...capture.after.slice(1)]) {
    view.answer(body);
    await act(async () =>
      view.rerender({
        selectorId: target.selectorId,
        selection: { of: 'family_members', continuation: cursorOf(body), size: 4 },
      }),
    );
  }
  const loaded = view.result.current.retained!.payload;
  const complete = mountFamilyCenter(loaded);
  expect(complete.getByTestId('review-center-family-expressions-scope').textContent).toContain(
    'roster is fully loaded',
  );
  expect(complete.getAllByTestId('review-center-family-expression').length).toBeGreaterThan(0);
  expect(complete.queryByTestId('review-center-roster-next')).toBeNull();
  expect(complete.container.textContent).not.toContain('Nothing is missing');
  complete.unmount();

  const unavailable = structuredClone(loaded);
  const side = family(unavailable).before;
  side.state = 'unreadable';
  side.members = [];
  side.members_total = 0;
  delete side.page;
  delete side.guarantee;
  delete side.family_revision_id;
  const unread = mountFamilyCenter(unavailable);
  expect(unread.getByTestId('review-center-family-expressions-scope').textContent).toContain(
    'scope is unavailable',
  );
  expect(unread.container.textContent).not.toContain('roster is fully loaded');
  expect(unread.container.textContent).not.toContain('Nothing is missing');
  expect(unread.container.textContent).not.toContain('shows its unchanged expressions');
});

it('resets accumulated context on refresh, subject and history changes and drops late continuations', async () => {
  const view = mountCycle();
  await waitFor(() => expect(view.result.current.read.phase).toBe('reviewed'));
  const next = capture.before[1];
  const selection = { of: 'family_members' as const, continuation: cursorOf(next), size: 4 };
  view.answer(next);
  await act(async () => view.rerender({ selectorId: target.selectorId, selection }));
  expect(
    family(view.result.current.retained!.payload).before.members.every(
      (member) => member.state === 'recorded',
    ),
  ).toBe(true);
  await act(async () => view.result.current.refresh());
  expect(view.result.current.retained!.payload).toEqual(next.payload);
  expect(String(view.fetch.mock.calls.at(-1))).toContain('previousBindingDigest=');

  for (const changed of [
    { selectorId: 'other-subject' },
    { selectorId: target.selectorId, history: 'recorded' as const },
  ]) {
    await act(async () => view.rerender({ ...changed, selection }));
    expect(view.result.current.retained!.payload).toEqual(next.payload);
  }
  view.unmount();

  let resolveLate!: (value: Response) => void;
  const late = new Promise<Response>((resolve) => {
    resolveLate = resolve;
  });
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValueOnce(response(first))
      .mockReturnValueOnce(late)
      .mockResolvedValue(response(first)),
  );
  const cache = new ReviewReadCache();
  const fresh = renderHook(
    ({ selectorId, selection }: { selectorId: string; selection?: ReviewPageRequest }) =>
      useReviewReadCycle({ ...target, selectorId, selection, instead: null, cache }),
    {
      initialProps: { selectorId: target.selectorId } as {
        selectorId: string;
        selection?: ReviewPageRequest;
      },
    },
  );
  await waitFor(() => expect(fresh.result.current.read.phase).toBe('reviewed'));
  await act(async () => fresh.rerender({ selectorId: target.selectorId, selection }));
  await act(async () => fresh.rerender({ selectorId: 'new-subject' }));
  await act(async () => resolveLate(response(next)));
  expect(fresh.result.current.retained!.payload).toEqual(first.payload);
});

// MIK-R89 v2 rule 4 removed this case's layout select and Full file: the diff layout follows the
// reading column's width, and "Changes only | Full context" is the reader's one code choice.
it('keeps selected intent, judgment, siblings and focus while later roster claims become reachable', async () => {
  const pages = new Map(
    [...capture.before.slice(1), ...capture.after.slice(1)].map((body) => [cursorOf(body), body]),
  );
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://review.test');
    const tree = treeReply(url);
    if (tree) return response(tree);
    if (url.pathname.endsWith('/source-content'))
      return response({
        state: 'refused',
        operation: 'read_knowledge_review_source_content',
        repository_id: target.repo,
        refusal: {
          code: 'not-found',
          detail: 'This mounted check measures file selection.',
          next_action: 'Read the served file.',
        },
      });
    return response(pages.get(url.searchParams.get('continuation') ?? '') ?? first);
  });
  vi.stubGlobal('fetch', fetch);
  const view = render(<ReviewSurface {...target} onBack={vi.fn()} />);
  await view.findByTestId('review-center-member');
  // Open the actual captured subject within its recorded family context.
  const walked = view
    .getAllByTestId('review-family')
    .find((node) => node.dataset.family === capture.family_id)!;
  fireEvent.click(
    within(walked)
      .getAllByTestId('review-family-member-open')
      .find((node) => node.dataset.subjectId === capture.subject_id)!,
  );
  await waitFor(() =>
    expect(view.getByTestId('review-center-member').dataset.family).toBe(capture.family_id),
  );
  expect(view.getAllByTestId('review-center-roster-next')).toHaveLength(2);
  const evidence = view.getByTestId('review-center-judgment').textContent;
  const siblings = view.getAllByTestId('review-family-member').length;
  // The word-diff is mounted; the authoritative revision pair is Technical details' (MIK-R89 v2
  // rule 6, amended 9dde2b09).
  const selected = first.payload.knowledge.revision_selection!;
  expect(view.getByTestId('review-center-member-revisions').textContent).toContain(
    selected.before_revision_id,
  );
  expect(view.getByTestId('review-center-member-revisions').textContent).toContain(
    selected.after_revision_id,
  );
  const statement = view.getByTestId('review-center-member-changed').textContent;
  // A continuation keeps the reader's focus where the reader left it.
  const filter = view.getByTestId('review-family-filter');
  filter.focus();
  for (const body of [...capture.before.slice(1), ...capture.after.slice(1)]) {
    const control = view
      .getAllByTestId('review-center-roster-next')
      .find(
        (button) =>
          button.dataset.family === capture.family_id &&
          button.dataset.continuation === cursorOf(body),
      );
    expect(control).toBeDefined();
    fireEvent.click(control!);
    await waitFor(() =>
      expect(view.getByTestId('review-page-bounds').textContent).toContain(
        `returned ${body.payload.page!.returned}`,
      ),
    );
    expect(document.activeElement).toBe(filter);
  }
  const selectedStatement = view.getByTestId('review-center-member-changed');
  expect(selectedStatement.textContent).toBe(statement);
  expect(view.getByTestId('review-center-member-revisions').textContent).toContain(selected.before_revision_id);
  expect(view.getByTestId('review-center-member-revisions').textContent).toContain(selected.after_revision_id);
  expect(view.getByTestId('review-center-judgment').textContent).toBe(evidence);
  expect(view.getAllByTestId('review-family-member')).toHaveLength(siblings);
  expect(view.queryByTestId('review-center-roster-next')).toBeNull();
  const changedPaths = new Set(first.payload.source.inventory.entries.map((entry) => entry.path));
  const selectedRevision = first.payload.knowledge.revision_selection!.before_revision_id;
  const latePaths = new Set(
    capture.before.flatMap((body) =>
      family(body.payload)
        .before.members.filter((member) => member.invariant_revision_id === selectedRevision)
        .flatMap((member) =>
          member.sources.flatMap((source) =>
            source.path && changedPaths.has(source.path) ? [source.path] : [],
          ),
        ),
    ),
  );
  expect(latePaths.size).toBeGreaterThan(0);
  // The cards are the opening's read (MIK-R89 v2 W4); the later claims are reached in the
  // selected member's slot, as the recorded addresses of its accumulated realization claims.
  const claims = within(view.getByTestId('review-center-member-details')).getAllByTestId('review-center-expressions');
  const addresses = claims.flatMap((list) => Array.from(list.querySelectorAll('code'), (node) => node.textContent));
  for (const path of latePaths) expect(addresses).toContain(path);
  await expectCompleteInventory(view);
});
