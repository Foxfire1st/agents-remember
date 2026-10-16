import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { dashboardStore } from '../../data/store';
import { IntentEntryRevalidation } from '../../data/intentEntryRevalidation';
import { SERVED, taskEntry } from '../../test/fixtures/wire';
import { ChangeSetButton, DocChangeSetBar } from './changeSetBar';
import { IntentReviewEntry } from './intentReviewEntry';

beforeEach(() => dashboardStore.getState().reset());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const target = { repo: 'repo', master: 'master', leaf: 'LEAF', mode: 'committed' as const };
const counters = {
  code: { files: 1, insertions: 9, deletions: 2 },
  memory: { files: 0, insertions: 0, deletions: 0 },
};
const counted = {
  state: 'counted',
  counts: {
    added: 4,
    removed: 1,
    invariants: { after_only: 4, before_only: 1 },
    guarantees: { after_only: 0, before_only: 0 },
    realization_only: 0,
    membership_only: 0,
    unresolved: 0,
  },
};
const answer = (body: unknown, etag: string) =>
  new Response(JSON.stringify(body), { headers: { ETag: etag } });

it('shows retained counters synchronously on return, validates by ETag, and uses the current click handler', async () => {
  let complete!: (response: Response) => void;
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(answer({ counters }, 'W/"c1"'))
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          complete = resolve;
        }),
    );
  vi.stubGlobal('fetch', fetch);
  const oldOpen = vi.fn();
  const first = render(<ChangeSetButton target={target} label="committed" onOpen={oldOpen} />);
  await waitFor(() => expect(first.getByTestId('open-changeset').textContent).toContain('+9 −2'));
  first.unmount();
  const currentOpen = vi.fn();
  const second = render(<ChangeSetButton target={target} label="committed" onOpen={currentOpen} />);
  expect(second.getByTestId('open-changeset').textContent).toContain('+9 −2');
  expect(fetch.mock.calls[1][1].headers).toEqual({ 'If-None-Match': 'W/"c1"' });
  fireEvent.click(second.getByTestId('open-changeset'));
  expect(currentOpen).toHaveBeenCalledWith(target);
  expect(oldOpen).not.toHaveBeenCalled();
  const unchanged = new Response(null, { status: 304 });
  const json = vi.spyOn(unchanged, 'json');
  await act(async () => complete(unchanged));
  expect(json).not.toHaveBeenCalled();
  expect(second.getByTestId('open-changeset').textContent).toContain('+9 −2');
});

it('shows retained intent counts on return while validating and then shows a new refusal', async () => {
  let complete!: (response: Response) => void;
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(answer(counted, 'W/"s1"'))
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          complete = resolve;
        }),
    );
  vi.stubGlobal('fetch', fetch);
  const props = {
    repo: 'repo',
    master: 'master',
    leaf: 'LEAF',
    live: true,
    facts: 'current',
    onOpen: vi.fn(),
  };
  const first = render(<IntentReviewEntry {...props} />);
  await waitFor(() => expect(first.getByTestId('intent-review-counts').textContent).toBe('+4 −1'));
  first.unmount();
  const second = render(<IntentReviewEntry {...props} />);
  expect(second.getByTestId('intent-review-counts').textContent).toBe('+4 −1');
  expect(fetch.mock.calls[1][1].headers).toEqual({ 'If-None-Match': 'W/"s1"' });
  await act(async () =>
    complete(
      answer(
        {
          state: 'unavailable',
          refusal: {
            code: 'candidate_not_live',
            detail: 'candidate is gone',
            next_action: 'read recorded comparison',
          },
        },
        'W/"s2"',
      ),
    ),
  );
  expect(second.getByTestId('open-intent-review').dataset.intentState).toBe('unavailable');
  expect(second.queryByTestId('intent-review-counts')).toBeNull();
});

it('revalidates counters when a mounted task is shown again', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(answer({ counters }, 'W/"c1"'))
    .mockResolvedValueOnce(
      answer({ counters: { ...counters, code: { ...counters.code, insertions: 12 } } }, 'W/"c2"'),
    );
  vi.stubGlobal('fetch', fetch);
  const page = (shown: boolean) => (
    <IntentEntryRevalidation shown={shown}>
      <ChangeSetButton target={target} label="committed" onOpen={vi.fn()} />
    </IntentEntryRevalidation>
  );
  const view = render(page(true));
  await waitFor(() => expect(view.getByTestId('open-changeset').textContent).toContain('+9 −2'));
  view.rerender(page(false));
  expect(fetch).toHaveBeenCalledTimes(1);
  view.rerender(page(true));
  await waitFor(() => expect(view.getByTestId('open-changeset').textContent).toContain('+12 −2'));
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch.mock.calls[1][1].headers).toEqual({ 'If-None-Match': 'W/"c1"' });
});

it('uses only this entry’s bound worktrees for its working control', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      answer(url.includes('/review/') ? counted : { counters }, 'W/"current"'),
    ),
  );
  const bound = SERVED.tasks.find((entry) => entry.worktrees.length > 0);
  expect(bound).toBeDefined();
  const entry = taskEntry({
    reference: { repository: 'repo', path: 'master/leaf.json' },
    id: 'LEAF',
    worktrees: bound!.worktrees,
  });
  expect(Object.keys(dashboardStore.getState().enclosures)).toHaveLength(0);
  const view = render(<DocChangeSetBar kind="leaf" entry={entry} onOpen={vi.fn()} />);
  expect(view.getAllByTestId('open-changeset').map((button) => button.textContent)).toEqual(
    expect.arrayContaining([expect.stringContaining('working')]),
  );
  await waitFor(() => expect(view.getByTestId('intent-review-counts').textContent).toBe('+4 −1'));
});
