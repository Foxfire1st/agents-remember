// MIK-R42: while the leaf-wide read computes, the knowledge panel says so; when the read gives no
// answer it says that instead of being absent, which would read as "this leaf changed no knowledge".

import { act, cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { readFileSync } from 'node:fs';
import path from 'node:path';
import type { ReviewResult } from '../../data/review';
import { ReviewSurface } from './ReviewSurface';
import { LeafKnowledgeNotice } from './LeafKnowledgeChanges';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const family = captured<ReviewResult>('gitTrees.family.captured.json');
const invariant = captured<ReviewResult>('gitTrees.invariant.captured.json');
const entries = captured<unknown>('gitTrees.entries.captured.json');
const cards = captured<unknown>('gitTrees.cards.captured.json');

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('the knowledge panel while the leaf-wide read has no tree answer', () => {
  it('says that it is computing', () => {
    render(<LeafKnowledgeNotice read={{ phase: 'loading' }} />);
    const note = screen.getByTestId('review-leaf-knowledge-computing');
    expect(note.textContent).toMatch(/Computing the knowledge changes/);
    expect(note.getAttribute('role')).toBe('status');
  });

  it('names the failure and its action when the read is unavailable', () => {
    render(
      <LeafKnowledgeNotice
        read={{
          phase: 'unavailable',
          problem: {
            token: 'unavailable-history',
            code: 'reviewer_busy',
            detail: 'the reviewer is computing other worklists',
            nextAction: 'the reviewer is computing other worklists; retry',
          },
        }}
      />,
    );
    const note = screen.getByTestId('review-leaf-knowledge-unavailable');
    expect(note.textContent).toContain(
      'are unavailable: the reviewer is computing other worklists',
    );
    expect(note.textContent).toContain('retry.');
  });

  it('draws nothing for a dataset review', () => {
    const { container } = render(<LeafKnowledgeNotice read={{ phase: 'not-converted' }} />);
    expect(container.textContent).toBe('');
  });
});

it.each(['family', 'invariant'] as const)(
  'shows the held read and final failure with %s selected through the real surface',
  async (kind) => {
    const subject = kind === 'family' ? family : invariant;
    const payload = subject.payload!;
    let finish!: (response: Response) => void;
    const held = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string) => {
        const url = new URL(address, 'http://localhost');
        if (
          url.pathname.endsWith('/trees') &&
          !url.searchParams.get('invariants') &&
          !url.searchParams.get('lane')
        )
          return held;
        const body = url.pathname.endsWith('/trees')
          ? cards
          : url.pathname.endsWith('/entries')
            ? entries
            : subject;
        return { ok: true, status: 200, json: async () => body } as Response;
      }),
    );
    const view = render(
      <ReviewSurface
        repo={payload.candidate.repository_id}
        master={payload.candidate.master}
        leaf={payload.candidate.leaf_id}
        selectorKind={kind}
        selectorId={payload.knowledge.revision_selection!.record_id}
        onBack={() => undefined}
      />,
    );
    const center = await view.findByTestId(
      kind === 'family' ? 'review-center-family' : 'review-center-member',
    );
    const status = within(center).getByTestId('review-leaf-knowledge-computing');
    expect(status.getAttribute('role')).toBe('status');
    expect(status.textContent).toContain('Computing the knowledge changes');
    expect(status.closest('details:not([open])')).toBeNull();
    await act(async () => {
      finish({
        ok: true,
        status: 200,
        json: async () => ({
          state: 'refused',
          knowledge_sides: [],
          refusal: {
            code: 'candidate_unresolved',
            detail: 'The installed build changed.',
            next_action: 'Restart the dashboard.',
          },
        }),
      } as Response);
    });
    const unavailable = within(center).getByTestId('review-leaf-knowledge-unavailable');
    const nextStatus = within(unavailable).getByRole('status');
    expect(nextStatus).toBe(status);
    expect(nextStatus.textContent).toContain('are unavailable');
    expect(nextStatus.closest('details:not([open])')).toBeNull();
    expect(unavailable.textContent).toContain('Restart the dashboard.');
  },
);
