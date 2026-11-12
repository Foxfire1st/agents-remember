// MIK-R176 rule 1: a task switch ends the reads of the task left behind.
//
// The change-set bar, the review summary and the document body each own their
// request. When the selection moves, each aborts it -- the browser's free
// connections belong to the task shown now -- and the abandoned answer leaves
// neither an error on the page nor an entry in the store.
import { render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { dashboardStore } from '../../data/store';
import { useIntentReviewSummary } from '../../data/reviewIntentSummary';
import { useTaskDocumentBody } from '../../data/useTaskDocumentBody';
import type { TaskEntry } from '../../types/projection';
import { taskEntry, taskReference, taskSummary } from './test-utils';
import { ChangeSetButton } from './changeSetBar';

afterEach(() => {
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
});

function entryFor(leaf: string): TaskEntry {
  return taskEntry({
    id: leaf,
    title: leaf,
    stage: 'started',
    stepsDone: 0,
    stepsTotal: 0,
    activity: 'works',
    reference: taskReference(`agents-remember/260928_maintained-invariant-knowledge/${leaf}.json`),
    summary: taskSummary({ bodyRevision: `revision-${leaf}` }),
  });
}

function SummaryReader({ leaf }: { leaf: string }) {
  useIntentReviewSummary('agents-remember', '260928_maintained-invariant-knowledge', leaf, 'facts');
  return null;
}

function BodyReader({ entry }: { entry: TaskEntry }) {
  useTaskDocumentBody(entry);
  return null;
}

function Page({ leaf }: { leaf: string }) {
  return (
    <>
      <ChangeSetButton
        target={{
          repo: 'agents-remember',
          master: '260928_maintained-invariant-knowledge',
          leaf,
          mode: 'working',
        }}
        label="working"
        onOpen={() => {}}
      />
      <SummaryReader leaf={leaf} />
      <BodyReader entry={entryFor(leaf)} />
    </>
  );
}

describe('a task switch ends the left page’s reads (MIK-R176)', () => {
  it('aborts the change set, the review summary and the body, and shows no error', async () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            const signal = init?.signal;
            if (!signal) return;
            signals.push(signal);
            signal.addEventListener(
              'abort',
              () => reject(new DOMException('The operation was aborted.', 'AbortError')),
              { once: true },
            );
          }),
      ),
    );

    const view = render(<Page leaf="260928-MIK-L174-left" />);
    await waitFor(() => expect(signals).toHaveLength(3));
    view.rerender(<Page leaf="260928-MIK-L175-shown" />);

    // Every read the left page started ends with its request.
    await waitFor(() => expect(signals.slice(0, 3).every((signal) => signal.aborted)).toBe(true));
    // The task now shown reports its own reads in flight, never the left task's abort.
    expect(view.getByTestId('changeset-state').getAttribute('data-review-state')).toBe('loading');
    expect(view.queryByText(/could not be read/)).toBeNull();
    view.unmount();
  });
});
