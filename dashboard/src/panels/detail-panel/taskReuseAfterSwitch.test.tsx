// MIK-R176 rule 1, the retained half: leaving a task and coming back must show the body the
// page already read, without a second request. The page's own reader is mounted here; the
// store it reads through keeps the answer under the body's revision.
import { render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { dashboardStore } from '../../data/store';
import { useTaskDocumentBody } from '../../data/useTaskDocumentBody';
import type { TaskEntry } from '../../types/projection';
import { taskEntry, taskReference, taskSummary } from './test-utils';

afterEach(() => {
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
});

const LEFT = '260928-MIK-L174-left';
const SHOWN = '260928-MIK-L175-shown';

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

function BodyReader({ leaf, sink }: { leaf: string; sink: { shown: string[] } }) {
  const body = useTaskDocumentBody(entryFor(leaf));
  if (body.state === 'available' && body.document) sink.shown.push(leaf);
  return null;
}

describe('a task switch never waits, and a retained body is shown again (MIK-R176)', () => {
  it('answers a revisited task from what the first visit kept, with no second request for it', async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        urls.push(String(url));
        const leaf = String(url).includes(LEFT) ? LEFT : SHOWN;
        return new Response(JSON.stringify({ bodyRevision: `revision-${leaf}`, title: leaf }), {
          headers: { 'content-type': 'application/json' },
        });
      }),
    );

    const sink = { shown: [] as string[] };
    const view = render(<BodyReader leaf={LEFT} sink={sink} />);
    await waitFor(() => expect(sink.shown).toContain(LEFT));
    const leftReads = () => urls.filter((url) => url.includes(LEFT)).length;
    const firstVisit = leftReads();
    expect(firstVisit).toBe(1);

    view.rerender(<BodyReader leaf={SHOWN} sink={sink} />);
    await waitFor(() => expect(sink.shown).toContain(SHOWN));

    view.rerender(<BodyReader leaf={LEFT} sink={sink} />);
    await waitFor(() => expect(sink.shown.filter((title) => title === LEFT).length).toBeGreaterThan(1));

    // The revisit read nothing: the first answer was kept under its revision.
    expect(leftReads()).toBe(firstVisit);
    view.unmount();
  });
});
