import { readFileSync } from 'node:fs';
import path from 'node:path';

import { act, cleanup, fireEvent, render, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewPayload } from '../../data/review';
import { FamilyReviewCenter } from './FamilyReviewCenter';
import { useReviewReadCycle, type ReviewPageRequest } from './ReviewReadCycle';
import { shownPayload } from './ReviewOutcome';
import { ReviewSurface } from './ReviewSurface';

// These are complete HTTP bodies captured from the public authorship + real review route used by
// test_review_family_context_population.py::walk_responses, at page size 4. No display payload is constructed.
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
const target = {
  repo: first.payload.candidate.repository_id,
  master: first.payload.candidate.master,
  leaf: first.payload.candidate.leaf_id,
  selectorKind: 'invariant' as const,
  selectorId: capture.subject_id,
};
const family = (payload: ReviewPayload) =>
  payload.family_context!.entries.find((entry) => entry.family_id === capture.family_id)!;
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
const cursorOf = (body: Body) => body.payload.page!.continued_from!;

vi.mock('../../data/useReviewCatalogue', () => ({
  useReviewCatalogue: () => ({
    loading: false,
    entries: [],
    empty: true,
    stale: false,
    facts: 'test',
    refresh: () => undefined,
  }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mountCycle() {
  let body: unknown = first;
  const fetch = vi.fn<(input: RequestInfo | URL) => Promise<Response>>(async () => response(body));
  vi.stubGlobal('fetch', fetch);
  const view = renderHook(
    ({
      selection,
      selectorId,
      history,
    }: {
      selection?: ReviewPageRequest;
      selectorId: string;
      history?: 'recorded';
    }) => useReviewReadCycle({ ...target, selectorId, history, instead: null, selection }),
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
    expect(payload.evidence).toEqual(body.payload.evidence);
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
        if (String(input).includes('/source-content'))
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
        return response(bodies.shift());
      }),
    );
    const view = render(<ReviewSurface {...target} onBack={vi.fn()} />);
    await view.findByTestId('review-center-member');
    const next = (body: Body) =>
      view
        .getAllByTestId('review-family-roster-next')
        .find(
          (button) =>
            button.dataset.family === capture.family_id &&
            button.dataset.continuation === cursorOf(body),
        )!;
    fireEvent.click(next(capture.before[1]));
    await waitFor(() =>
      expect(view.getByTestId('review-page-bounds').textContent).toContain('returned 8'),
    );
    const selected = view
      .getByTestId('review-center-member-changed')
      .querySelector('pre')!.textContent;
    const evidence = view.getByTestId('review-center-evidence').textContent;
    const members = view.getAllByTestId('review-family-member').map((row) => row.textContent);
    fireEvent.click(next(capture.before[2]));
    const failure = await view.findByTestId('review-failure');
    expect(failure.dataset.reviewCode).toBe(
      fault === 'page refusal' ? 'comparison_page_reset' : 'comparison_page_unreadable',
    );
    expect(view.getByTestId('review-retained-generation')).toBeTruthy();
    expect(view.getByTestId('review-center-member-changed').querySelector('pre')!.textContent).toBe(
      selected,
    );
    expect(view.getByTestId('review-center-evidence').textContent).toBe(evidence);
    expect(view.getAllByTestId('review-family-member').map((row) => row.textContent)).toEqual(
      members,
    );
    expect(view.container.textContent).not.toContain('REJECTED IMMUTABLE CONTENT');
    expect(view.container.textContent).not.toContain('rejected-subject');
    expect(view.getAllByTestId('review-inventory-entry')).toHaveLength(
      first.payload.source.inventory.listed_total,
    );
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
      onLayout={vi.fn()}
      fullFile={false}
      onFullFile={vi.fn()}
      openPath={null}
      onOpenPath={vi.fn()}
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
  const fresh = renderHook(
    ({ selectorId, selection }: { selectorId: string; selection?: ReviewPageRequest }) =>
      useReviewReadCycle({ ...target, selectorId, selection, instead: null }),
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

it('keeps selected intent, evidence, siblings, focus and open layout while a late source expression becomes reachable', async () => {
  const pages = new Map(
    [...capture.before.slice(1), ...capture.after.slice(1)].map((body) => [cursorOf(body), body]),
  );
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://review.test');
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
  expect(view.getByTestId('review-display-state').textContent).toContain('not yet fully loaded');
  const evidence = view.getByTestId('review-center-evidence').textContent;
  const siblings = view.getAllByTestId('review-family-member').length;
  const layout = view.getByTestId('review-center-diff-layout') as HTMLSelectElement;
  fireEvent.change(layout, { target: { value: 'inline' } });
  const statement = view
    .getByTestId('review-center-member-changed')
    .querySelector('pre')!.textContent;
  fireEvent.click(view.getByTestId('review-center-full-file'));
  const open = view.getAllByTestId('review-inventory-open')[0];
  fireEvent.click(open);
  const expanded = view.getByTestId('review-display-state').textContent;
  layout.focus();
  for (const body of [...capture.before.slice(1), ...capture.after.slice(1)]) {
    const control = view
      .getAllByTestId('review-family-roster-next')
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
    expect(document.activeElement).toBe(layout);
  }
  const selectedStatement = view.getByTestId('review-center-member-changed');
  expect(selectedStatement.querySelector('pre')!.textContent).toBe(statement);
  expect(selectedStatement.textContent).toContain(first.payload.knowledge.before_statement.text);
  expect(selectedStatement.textContent).toContain(first.payload.knowledge.after_statement.text);
  expect(view.getByTestId('review-center-evidence').textContent).toBe(evidence);
  expect(view.getAllByTestId('review-family-member')).toHaveLength(siblings);
  expect(layout.value).toBe('inline');
  expect(view.getByTestId('review-center-full-file').getAttribute('aria-pressed')).toBe('true');
  expect(view.getByTestId('review-display-state').textContent).toContain('expanded:');
  expect(expanded).toContain('expanded:');
  expect(view.getByTestId('review-display-state').textContent).not.toContain(
    'not yet fully loaded',
  );
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
  const renderedPaths = view
    .getAllByTestId('review-expression-card')
    .map((card) => card.dataset.path);
  for (const path of latePaths) expect(renderedPaths).toContain(path);
  expect(view.getAllByTestId('review-inventory-entry')).toHaveLength(changedPaths.size);
});
