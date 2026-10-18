import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { dashboardStore } from '../data/store';
import { readFileSync } from 'node:fs';
import path from 'node:path';
const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(
      path.join(path.dirname(new URL(import.meta.url).pathname), '../panels/review', name),
      'utf8',
    ),
  ) as T;
import type { ReviewResult } from '../data/review';
import { CockpitShell } from './Cockpit';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
  window.history.replaceState(null, '', '/');
});

function refuseUnknown() {
  const fetchMock = vi.fn(async (address: string) => {
    const url = new URL(address, window.location.origin);
    const body = url.pathname.startsWith('/api/review/intent')
      ? {
          state: 'refused',
          refusal: {
            code: 'unknown_leaf',
            detail: 'This leaf is not known.',
            next_action: 'Choose a known leaf.',
          },
        }
      : { repos: [] };
    const ok = url.pathname.startsWith('/api/review/intent') || url.pathname === '/api/files/repos';
    return { ok, status: ok ? 200 : 404, json: async () => body } as Response;
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

it('opens a recorded review link at startup and shows the existing refusal for unknown targets', async () => {
  window.history.replaceState(
    null,
    '',
    '/#review?repo=unknown-repo&master=unknown-master&leaf=unknown-leaf&history=recorded',
  );
  const fetchMock = refuseUnknown();
  const view = render(<CockpitShell />);
  const surface = await view.findByTestId('review-surface');
  expect(surface.dataset.reviewTarget).toBe('unknown-repo/unknown-master/unknown-leaf');
  expect(surface.dataset.reviewHistory).toBe('recorded');
  await view.findByText(/This leaf is not known/);
  expect(fetchMock.mock.calls.some(([url]) => url.includes('history=recorded'))).toBe(true);
  fireEvent.click(view.getByTestId('review-back'));
  expect(window.location.hash).toBe('');
  expect(view.queryByTestId('review-surface')).toBeNull();
  expect(view.getByRole('radio', { name: 'Operations' }).getAttribute('aria-checked')).toBe('true');
});

it('browser Back closes the review including its open change-set page, without putting selection in the hash', async () => {
  const hash =
    '#review?repo=agents-remember&master=260928_maintained-invariant-knowledge&leaf=260928-MIK-L37&history=recorded';
  window.history.replaceState(
    null,
    '',
    '/#knowledge?repo=agents-remember&commit=published&path=dashboard',
  );
  window.history.pushState(null, '', hash);
  const task = captured<ReviewResult>('l86.task.captured.json');
  const catalogue = captured('l86.entries.captured.json');
  const families = [
    'familyReview.identical',
    'l86.family',
    'subjectReview.successorFamily',
    'familyReview.oneSided',
  ].map((name) => captured<ReviewResult>(`${name}.captured.json`));
  const member = captured<ReviewResult>('l86.member.captured.json');
  const subjects = new Map(
    [...families, member].map((body) => [
      `${body.payload!.knowledge.revision_selection!.record_kind}:${body.payload!.knowledge.revision_selection!.record_id}`,
      body,
    ]),
  );
  const changes = captured('l86.changeset.captured.json');
  const diffs = captured<Record<string, unknown>>('l86.file-diffs.captured.json');
  const fetchMock = vi.fn(async (address: string) => {
    const url = new URL(address, window.location.origin);
    let body: unknown;
    if (url.pathname === '/api/review/intent/entries') body = catalogue;
    else if (url.pathname === '/api/review/intent') {
      const id = url.searchParams.get('selectorId');
      body = id ? subjects.get(`${url.searchParams.get('selectorKind')}:${id}`) : task;
      if (!body) throw new Error(`Uncaptured subject ${url}`);
    } else if (url.pathname === '/api/changeset/task') body = changes;
    else if (url.pathname === '/api/changeset/file-diff')
      body = diffs[`${url.searchParams.get('kind') ?? 'code'}:${url.searchParams.get('path')}`];
    else if (url.pathname === '/api/review/trees') {
      // This hash/back journey deliberately does not qualify focused excerpts. Never
      // answer an unrelated family's scope with the l86.cards seven-member capture.
      if (url.searchParams.has('invariants'))
        throw new Error('Focused excerpts are outside the hash/back check.');
      const name = url.searchParams.has('file')
        ? 'classification'
        : url.searchParams.has('lane')
          ? 'lane'
          : 'trees';
      body = captured(`l86.${name}.captured.json`);
    } else if (url.pathname === '/api/review/intent/source-content')
      body = captured('l86.full-file.captured.json');
    else if (url.pathname === '/api/files/repos') body = { repos: [] };
    else
      body = {
        state: 'refused',
        refusal: {
          code: 'not-found',
          detail: 'Not served in this check.',
          next_action: 'Return to the review.',
        },
      };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal('fetch', fetchMock);
  const view = render(<CockpitShell />);
  await view.findByTestId('review-catalogue-navigation');
  const root = view.getByTestId('review-surface');
  const reads = () =>
    fetchMock.mock.calls.filter(
      ([url]) => new URL(url, window.location.origin).pathname === '/api/review/intent',
    ).length;
  const before = reads();
  // A hash never owns the subject. An extra query key cannot reset the mounted reader.
  act(() => {
    window.history.replaceState(null, '', hash + '&selectorId=outside-subject');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  expect(view.getByTestId('review-surface')).toBe(root);
  expect(reads()).toBe(before);
  fireEvent.click(
    within(view.getByTestId('review-catalogue-navigation')).getByRole('button', {
      name: /^All changes/,
    }),
  );
  await view.findByTestId('changeset-viewer');
  expect(window.location.hash).toBe(hash + '&selectorId=outside-subject');
  await act(async () => {
    window.history.back();
  });
  await waitFor(() => expect(view.queryByTestId('review-surface')).toBeNull());
  expect(view.queryByTestId('changeset-viewer')).toBeNull();
  expect(window.location.hash).toBe(
    '#knowledge?repo=agents-remember&commit=published&path=dashboard',
  );
  expect(view.getByRole('radio', { name: 'Knowledge' }).getAttribute('aria-checked')).toBe('true');
});
