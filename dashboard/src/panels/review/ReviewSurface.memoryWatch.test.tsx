// Synthetic SSE consumer signals only; native filesystem producer proof is backend-owned.
import { act, cleanup, render, waitFor, fireEvent } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { captured, replyFor } from './l86.test-utils';
import type { ReviewResult } from '../../data/review';
import { IntentEntryRevalidation } from '../../data/intentEntryRevalidation';
import { connectState } from '../../data/stream';
class FakeSource {
  static latest: FakeSource;
  listeners = new Map<string, (event: MessageEvent) => void>();
  constructor(public url: string) { FakeSource.latest = this; }
  addEventListener(name: string, callback: (event: MessageEvent) => void) { this.listeners.set(name, callback); }
  close() {}
}
const publishReviewMemory = (data: unknown) => FakeSource.latest.listeners.get('review.memory')?.(new MessageEvent('review.memory', { data: JSON.stringify(data) }));
let dispose: (() => void) | undefined;
import { DocChangeSetBar } from '../detail-panel/changeSetBar';
import { enclosure, seedProjection, taskEntry } from '../detail-panel/test-utils';
import { ReviewSurface } from './ReviewSurface';

const target = { repo: 'agents-remember', master: 'watch', leaf: 'watched-leaf' };
const entry = taskEntry({
  id: target.leaf,
  reference: { repository: target.repo, path: `${target.master}/${target.leaf}.json` },
  worktrees: [enclosure({ repoName: target.repo, taskName: target.master, leafId: target.leaf })],
});
const bodies = [captured<ReviewResult>('subjectReview.successorFamily.captured.json'), captured<ReviewResult>('l86.family.captured.json')];
const families = bodies.map((body) => body.payload!.family_context!.entries[0]);
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
afterEach(() => { cleanup(); dispose?.(); vi.unstubAllGlobals(); });

it('reconciles an explicit subject and entry read before the first native readiness baseline', async () => {
  // Consumer schedule, not native timing proof: hold the first SSE baseline until both HTTP
  // snapshots finish, then change the input once before releasing readiness. No second edit.
  seedProjection({ enclosures: [enclosure({ enclosure: '/contracts/watch', lifecycleId: 'watch', repoName: target.repo, taskName: target.master, leafId: target.leaf, worktreeGroup: '/worktrees/watch-ar' })], activeWorktreeGroups: ['watch-ar'] });
  vi.stubGlobal('EventSource', FakeSource);
  dispose = connectState();
  const urls: URL[] = [];
  const selected = families[1];
  let edited = false;
  vi.stubGlobal('fetch', vi.fn(async (address: string) => {
    const url = new URL(address, 'http://localhost'); urls.push(url);
    if (url.pathname.endsWith('/summary')) return response({ state: 'counted', counts: { added: edited ? 2 : 1, removed: 0 } });
    if (url.pathname.endsWith('/entries')) return response({ state: 'entries', entries: families.map((family) => ({ selector_kind: 'family', selector_id: family.family_id, label: family.display_label, presence: 'both' })), total_subjects: 2, family_total: 2, invariant_total: 0 });
    if (url.pathname === '/api/review/intent') {
      const body = structuredClone(bodies[1]);
      if (edited) { body.payload!.comparison!.after_snapshot_digest = 'f'.repeat(64); body.payload!.family_context!.entries[0].display_label = 'Only pre-arm edit'; }
      return response(body);
    }
    return response(replyFor(url));
  }));
  const view = render(<IntentEntryRevalidation shown={true}><DocChangeSetBar kind="leaf" entry={entry} onOpen={() => undefined} /><ReviewSurface {...target} selectorKind="family" selectorId={selected.family_id} onBack={() => undefined} /></IntentEntryRevalidation>);
  const reviews = () => urls.filter((url) => url.pathname === '/api/review/intent');
  const summaries = () => urls.filter((url) => url.pathname.endsWith('/summary'));
  await waitFor(() => {
    expect(view.getByTestId('review-center-family').dataset.family).toBe(selected.family_id);
    expect(view.getByTestId('open-intent-review').textContent).toContain('+1 −0');
  });
  expect(reviews()).toHaveLength(1);
  expect(summaries()).toHaveLength(1);
  edited = true;
  act(() => publishReviewMemory({ ...target, revision: 'ready-baseline:0' }));
  await waitFor(() => {
    expect(view.getByTestId('review-center-family').textContent).toContain('Only pre-arm edit');
    expect(view.getByTestId('open-intent-review').textContent).toContain('+2 −0');
  });
  expect(reviews()).toHaveLength(2);
  expect(reviews()[1].searchParams.get('selectorId')).toBe(selected.family_id);
  expect(reviews()[1].searchParams.get('previousBindingDigest')).not.toBeNull();
  expect(summaries()).toHaveLength(2);
  act(() => {
    publishReviewMemory({ ...target, revision: 'ready-baseline:0' });
    publishReviewMemory({ ...target, master: 'other', revision: 'unrelated:0' });
  });
  await act(async () => {});
  expect(reviews()).toHaveLength(2);
  expect(summaries()).toHaveLength(2);
});

it('refreshes the actual selected review and mounted entry once, without resurrecting other subject answers', async () => {
  seedProjection({ enclosures: [enclosure({ enclosure: '/contracts/watch', lifecycleId: 'watch', repoName: target.repo, taskName: target.master, leafId: target.leaf, worktreeGroup: '/worktrees/watch-ar' })], activeWorktreeGroups: ['watch-ar'] });
  vi.stubGlobal('EventSource', FakeSource);
  dispose = connectState();
  const urls: URL[] = [];
  let changed = false;
  vi.stubGlobal('fetch', vi.fn(async (address: string) => {
    const url = new URL(address, 'http://localhost'); urls.push(url);
    if (url.pathname.endsWith('/summary')) return response({ state: 'counted', counts: { added: changed ? 2 : 1, removed: 0 } });
    if (url.pathname.endsWith('/entries')) return response({ state: 'entries', entries: families.map((family) => ({ selector_kind: 'family', selector_id: family.family_id, label: family.display_label, presence: 'both' })), total_subjects: 2, family_total: 2, invariant_total: 0 });
    if (url.pathname === '/api/review/intent') {
      const body = structuredClone(bodies.find((item) => item.payload!.knowledge.revision_selection!.record_id === url.searchParams.get('selectorId'))!);
      if (changed) { body.payload!.comparison!.after_snapshot_digest = 'f'.repeat(64); body.payload!.family_context!.entries[0].display_label = 'Updated watched family'; }
      return response(body);
    }
    return response(replyFor(url));
  }));
  const view = render(<IntentEntryRevalidation shown={true}><DocChangeSetBar kind="leaf" entry={entry} onOpen={() => undefined} /><ReviewSurface {...target} onBack={() => undefined} /></IntentEntryRevalidation>);
  const reviews = () => urls.filter((url) => url.pathname === '/api/review/intent');
  await waitFor(() => expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id));
  expect(view.getByTestId('open-intent-review').textContent).toContain('+1 −0');
  act(() => publishReviewMemory({ ...target, revision: 'baseline' }));
  await waitFor(() => expect(reviews()).toHaveLength(2));
  await waitFor(() => expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id));
  fireEvent.click(view.getAllByTestId('review-family-open').find((button) => button.dataset.subjectId === families[1].family_id)!);
  await waitFor(() => expect(view.getByTestId('review-center-family').dataset.family).toBe(families[1].family_id));
  changed = true;
  act(() => { publishReviewMemory({ ...target, master: 'other', revision: 'ignored' }); publishReviewMemory({ ...target, revision: 'next' }); });
  await waitFor(() => expect(view.getByTestId('open-intent-review').textContent).toContain('+2 −0'));
  await waitFor(() => expect(view.getByTestId('review-center-family').textContent).toContain('Updated watched family'));
  expect(reviews()).toHaveLength(4);
  expect(reviews()[3].searchParams.get('selectorId')).toBe(families[1].family_id);
  expect(reviews()[3].searchParams.get('previousBindingDigest')).not.toBeNull();
  await waitFor(() => expect(view.getAllByTestId('review-family-open').length).toBeGreaterThan(0));
  fireEvent.click(view.getAllByTestId('review-family-open').find((button) => button.dataset.subjectId === families[0].family_id)!);
  await waitFor(() => expect(reviews()).toHaveLength(5));
  expect(urls.filter((url) => url.pathname.endsWith('/summary'))).toHaveLength(3);
  // Readiness returned the same comparison; only the later changed comparison renews the catalogue.
  expect(urls.filter((url) => url.pathname.endsWith('/entries'))).toHaveLength(2);
});

it('rejects pre-readiness review and entry answers that arrive after the reconciled generation', async () => {
  seedProjection({ enclosures: [enclosure({ enclosure: '/contracts/watch', lifecycleId: 'watch', repoName: target.repo, taskName: target.master, leafId: target.leaf, worktreeGroup: '/worktrees/watch-ar' })], activeWorktreeGroups: ['watch-ar'] });
  vi.stubGlobal('EventSource', FakeSource);
  dispose = connectState();
  const initial: Array<() => void> = [];
  const urls: URL[] = [];
  let ready = false;
  vi.stubGlobal('fetch', vi.fn(async (address: string) => {
    const url = new URL(address, 'http://localhost'); urls.push(url);
    const summary = url.pathname.endsWith('/summary');
    if (summary || url.pathname === '/api/review/intent') {
      const body = summary
        ? response({ state: 'counted', counts: { added: ready ? 2 : 1, removed: 0 } })
        : changedBody(ready ? 'Reconciled generation' : 'Obsolete initial snapshot', ready ? 'f' : 'e');
      if (!ready) return new Promise<Response>((resolve) => initial.push(() => resolve(body)));
      return body;
    }
    if (url.pathname.endsWith('/entries')) return response({ state: 'entries', entries: [{ selector_kind: 'family', selector_id: families[0].family_id, label: families[0].display_label, presence: 'both' }], total_subjects: 1, family_total: 1, invariant_total: 0 });
    return response(replyFor(url));
  }));
  const view = render(<IntentEntryRevalidation shown={true}><DocChangeSetBar kind="leaf" entry={entry} onOpen={() => undefined} /><ReviewSurface {...target} selectorKind="family" selectorId={families[0].family_id} onBack={() => undefined} /></IntentEntryRevalidation>);
  await waitFor(() => expect(initial).toHaveLength(2));
  ready = true;
  act(() => publishReviewMemory({ ...target, revision: 'ready-baseline:0' }));
  await waitFor(() => {
    expect(view.getByTestId('review-center-family').textContent).toContain('Reconciled generation');
    expect(view.getByTestId('open-intent-review').textContent).toContain('+2 −0');
  });
  await act(async () => initial.forEach((resolve) => resolve()));
  expect(view.getByTestId('review-center-family').textContent).not.toContain('Obsolete initial snapshot');
  expect(view.getByTestId('review-center-family').textContent).toContain('Reconciled generation');
  expect(view.getByTestId('open-intent-review').textContent).toContain('+2 −0');
  expect(urls.filter((url) => url.pathname === '/api/review/intent')).toHaveLength(2);
  expect(urls.filter((url) => url.pathname.endsWith('/summary'))).toHaveLength(2);
});

function deferredSurface() {
  seedProjection({ enclosures: [enclosure({ enclosure: '/contracts/watch', lifecycleId: 'watch', repoName: target.repo, taskName: target.master, leafId: target.leaf, worktreeGroup: '/worktrees/watch-ar' })], activeWorktreeGroups: ['watch-ar'] });
  vi.stubGlobal('EventSource', FakeSource);
  dispose = connectState();
  const reviews: URL[] = [];
  const pending: Array<{ resolve: (body: Response) => void; reject: (error: Error) => void }> = [];
  let hold = false;
  vi.stubGlobal('fetch', vi.fn(async (address: string) => {
    const url = new URL(address, 'http://localhost');
    if (url.pathname.endsWith('/entries')) return response({ state: 'entries', entries: families.map((family) => ({ selector_kind: 'family', selector_id: family.family_id, label: family.display_label, presence: 'both' })), total_subjects: 2, family_total: 2, invariant_total: 0 });
    if (url.pathname === '/api/review/intent') {
      reviews.push(url);
      if (hold) return new Promise<Response>((resolve, reject) => pending.push({ resolve, reject }));
      return response(bodies.find((item) => item.payload!.knowledge.revision_selection!.record_id === url.searchParams.get('selectorId'))!);
    }
    return response(replyFor(url));
  }));
  const view = render(<ReviewSurface {...target} onBack={() => undefined} />);
  return { view, reviews, pending, hold: () => { hold = true; } };
}
function changedBody(label: string, digest: string) {
  // Explicit synthetic display/input-generation perturbation, not producer classification evidence.
  const body = structuredClone(bodies[0]);
  body.payload!.comparison!.after_snapshot_digest = digest.repeat(64);
  body.payload!.family_context!.entries[0].display_label = label;
  return response(body);
}

it('never lets an earlier memory revision replace a newer visible answer', async () => {
  const { view, reviews, pending, hold } = deferredSurface();
  await view.findByTestId('review-center-family');
  act(() => publishReviewMemory({ ...target, revision: 'baseline' }));
  await waitFor(() => expect(reviews).toHaveLength(2));
  await view.findByTestId('review-center-family');
  hold();
  act(() => publishReviewMemory({ ...target, revision: 'first-edit' }));
  await waitFor(() => expect(pending).toHaveLength(1));
  act(() => publishReviewMemory({ ...target, revision: 'second-edit' }));
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => pending[1].resolve(changedBody('Newest watched family', 'f')));
  await waitFor(() => expect(view.getByTestId('review-center-family').textContent).toContain('Newest watched family'));
  await act(async () => pending[0].resolve(changedBody('Obsolete watched family', 'e')));
  expect(view.getByTestId('review-center-family').textContent).toContain('Newest watched family');
  expect(view.getByTestId('review-center-family').textContent).not.toContain('Obsolete watched family');
  act(() => publishReviewMemory({ ...target, revision: 'second-edit' }));
  expect(reviews).toHaveLength(4);
});

it('retains the labeled coherent generation when a memory-triggered read fails and recovers on the next edit', async () => {
  const { view, reviews, pending, hold } = deferredSurface();
  await view.findByTestId('review-center-family');
  act(() => publishReviewMemory({ ...target, revision: 'baseline' }));
  await waitFor(() => expect(reviews).toHaveLength(2));
  await view.findByTestId('review-center-family');
  hold();
  act(() => publishReviewMemory({ ...target, revision: 'failed-edit' }));
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () => pending[0].reject(new Error('Current memory could not be read')));
  expect((await view.findByTestId('review-failure')).textContent).toContain('Current memory could not be read');
  expect(view.getByTestId('review-retained-generation')).toBeTruthy();
  act(() => publishReviewMemory({ ...target, revision: 'recovered-edit' }));
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => pending[1].resolve(changedBody('Recovered watched family', 'f')));
  await waitFor(() => expect(view.getByTestId('review-center-family').textContent).toContain('Recovered watched family'));
  expect(view.queryByTestId('review-failure')).toBeNull();
});
