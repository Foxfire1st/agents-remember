// MIK-R33 in the mounted reviewer: j/k selection moves over the served bodies of the store-authored
// comparison (triage.capture-provenance.json). `ReviewSurface` is the real component and only `fetch`
// is stubbed. A move selects the next change's subject exactly as a click does, and keeps the family
// context, the focus and the mounted workspace (ICR-R32 rule 6, L48).
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import { ReviewSurface } from './ReviewSurface';
import { treeOrderStore } from './triageOrderPreference';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const family = captured<ReviewResult>('triage.family.captured.json');
const memberA = captured<ReviewResult>('triage.memberA.captured.json');
const memberH = captured<ReviewResult>('triage.memberH.captured.json');
const entries = captured<unknown>('triage.entries.captured.json');
const J = { key: 'j', code: 'KeyJ' };
// A cold surface render under a loaded suite can exceed the library's 1 s default.
const WAIT = { timeout: 5000 };

const subjectOf = (result: ReviewResult) => result.payload!.knowledge.revision_selection!.record_id;
const refused = {
  state: 'refused',
  refusal: {
    code: 'not-found',
    detail: 'The tree view is not part of this check.',
    next_action: 'Open the mounted tree route.',
  },
};

beforeEach(() => {
  localStorage.clear();
  treeOrderStore.getState().setOrder('triage');
  const bodies = new Map([family, memberA, memberH].map((body) => [subjectOf(body), body]));
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      const body = url.pathname.endsWith('/entries')
        ? entries
        : url.pathname.endsWith('/trees') || url.pathname.endsWith('/source-content')
          ? refused
          : bodies.get(url.searchParams.get('selectorId') ?? '');
      if (body === undefined) throw new Error(`Unexpected review request: ${address}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const node = (container: HTMLElement, record: string) =>
  Array.from(container.querySelectorAll<HTMLElement>('[data-tree-node]')).find((one) =>
    one.textContent?.includes(record),
  )!;

it('moves the selection to the next change and keeps family context, focus and workspace', async () => {
  const payload = family.payload!;
  const view = render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      selectorKind="family"
      selectorId={subjectOf(family)}
      onBack={() => undefined}
    />,
  );
  await view.findAllByTestId('review-change-badge', undefined, WAIT);
  const workspace = view.getByTestId('review-workspace');
  // The centre's member list follows the tree's displayed order (triage, then authored).
  const treeOrder = () => [
    ...new Set(
      view.getAllByTestId('review-family-member-open').map((node) => node.dataset.revision),
    ),
  ];
  const centreOrder = () =>
    view.getAllByTestId('review-center-open-member').map((node) => node.dataset.revision);
  expect(centreOrder()).toEqual(treeOrder());
  fireEvent.click(view.getByTestId('review-tree-order'));
  expect(centreOrder()).toEqual(treeOrder());
  fireEvent.click(view.getByTestId('review-tree-order'));
  expect(view.getByTestId('review-surface').dataset.kbzone).toBe('review');
  node(view.container, 'FAM-F00001').focus();

  fireEvent.keyDown(document.activeElement!, J);
  // The revised member's subject is read, as a click would read it.
  await view.findByTestId('review-center-member', undefined, WAIT);
  await waitFor(
    () =>
      expect(view.getByTestId('review-center-column').textContent).toContain(
        'INV-AAAAAA holds (r2).',
      ),
    WAIT,
  );
  const first = node(view.container, 'INV-AAAAAA');
  expect(first.getAttribute('aria-current')).toBe('true');
  expect(document.activeElement).toBe(first);
  expect(view.getByTestId('review-workspace')).toBe(workspace);
  // The family context stays on screen, still badged.
  expect(view.getByTestId('review-family-breakdown').textContent).toContain('of 9');

  fireEvent.keyDown(document.activeElement!, J);
  await waitFor(
    () =>
      expect(view.getByTestId('review-center-column').textContent).toContain(
        'INV-HHHHHH holds (r1).',
      ),
    WAIT,
  );
  const second = node(view.container, 'INV-HHHHHH');
  expect(second.getAttribute('aria-current')).toBe('true');
  expect(document.activeElement).toBe(second);
  expect(view.getByTestId('review-workspace')).toBe(workspace);
});
