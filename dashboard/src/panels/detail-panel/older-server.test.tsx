// MIK-R127 failure/recovery on the native L168 entry contract: optional reader facts
// may be absent. Historical server recordings stay unchanged as provenance; no retired
// collection or compatibility parser is installed to consume them. The new recording was
// produced by native task-source, entry and body projectors from their recorded input documents.
import { act, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { connectState } from '../../data/stream';
import { projection } from '../../test/fixtures/wire';
import { DetailPanel } from './DetailPanel';
import baseBuild from './taskDocument.recorded.base-3c2285d1.json';
import thisBuild from './taskDocument.recorded.mik-r127.json';
import nativeBuild from './taskEntries.recorded.l168.json';
import { stubNotes } from './test-utils';

const ROOT = 'sandbox-app/recorded-master';
const TASK = `taskdoc:${ROOT}/01_slugify.json`;
const MASTER = `taskdoc:${ROOT}/task.json`;

// What a recording holds of the server's answers, as far as this file reads it.
interface Recording {
  tasks: unknown[];
  taskCounts: unknown;
  bodies: Record<string, unknown>;
}

// The stream as the browser gives it to the page: frames arrive as text.
class FrameSource {
  static last: FrameSource | undefined;
  private listeners: Record<string, Array<(event: unknown) => void>> = {};
  constructor(public url: string) {
    FrameSource.last = this;
  }
  addEventListener(type: string, fn: (event: unknown) => void): void {
    (this.listeners[type] ??= []).push(fn);
  }
  close(): void {}
  send(type: string, data: string): void {
    for (const fn of this.listeners[type] ?? []) fn({ data });
  }
}

// Recorded native entries arrive as a real SSE snapshot frame.
function frameText(recording: Recording): string {
  const frame: { tasks: unknown[]; taskCounts: unknown } = JSON.parse(JSON.stringify(projection()));
  frame.tasks = recording.tasks;
  frame.taskCounts = recording.taskCounts;
  return JSON.stringify(frame);
}

let stop: (() => void) | undefined;

// A server that answers with the recording: the stream's snapshot and the body
// of each document. Every other request is answered as the page's tests do.
function serve(recording: Recording): void {
  stubNotes([], '');
  const others = globalThis.fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (!url.startsWith('/api/task-document')) return others(url, init);
      const path = new URLSearchParams(url.split('?', 2)[1] ?? '').get('path') ?? '';
      const body = recording.bodies[path.slice(path.lastIndexOf('/') + 1)];
      return new Response(JSON.stringify(body ?? null), { status: body ? 200 : 404 });
    }),
  );
  vi.stubGlobal('EventSource', FrameSource);
  stop = connectState();
  act(() => FrameSource.last?.send('snapshot', frameText(recording)));
}

// What the page reported as an error. React's notice about an update outside
// `act` is a notice of the test environment and no error of the page.
function errorsOf(spy: { mock: { calls: unknown[][] } }): string[] {
  return spy.mock.calls
    .map((call) => String(call[0]))
    .filter((text) => !text.includes('not wrapped in act('));
}

async function open(selectedId: string, skeleton: string) {
  const view = render(<DetailPanel selectedId={selectedId} />);
  await waitFor(() => expect(view.getByTestId(skeleton)).toBeTruthy());
  // the body of the selected document has been asked for and answered
  await waitFor(() =>
    expect(
      vi
        .mocked(globalThis.fetch)
        .mock.calls.some(([url]) => String(url).startsWith('/api/task-document')),
    ).toBe(true),
  );
  await act(async () => {});
  return view;
}

const blocksOf = (container: HTMLElement): string[] =>
  Array.from(container.querySelectorAll('[data-block]')).map(
    (el) => el.getAttribute('data-block') ?? '',
  );

// The blocks of the recorded task as this build's server lets the page draw them.
const BLOCKS = ['head', 'objective', 'steps', 'review', 'references', 'notes'];

afterEach(() => {
  stop?.();
  stop = undefined;
  FrameSource.last = undefined;
});

describe('native entries with absent optional reader facts (MIK-R127, failure and recovery)', () => {
  it('names the build it was recorded from, and that build sends no review state', () => {
    expect(baseBuild.recordedFrom.build).toBe('commit 3c2285d12f3fd8797774d4519321071523e2e5f5');
    // the document the server read holds a review round with an open finding
    expect(baseBuild.documents['01_slugify.json'].reviewState.remainingFindingIds).toEqual(['F2']);
    // what it sent for that document names none
    expect(JSON.stringify(baseBuild.summaries)).not.toContain('reviewState');
    expect(JSON.stringify(baseBuild.bodies)).not.toContain('reviewState');
    // and both builds read the same two documents
    expect(thisBuild.documents).toEqual(baseBuild.documents);
  });

  it('draws the task without the blocks its server does not send, and without an error', async () => {
    const errors = vi.spyOn(console, 'error');
    serve(nativeBuild.optionalAbsent);
    const view = await open(TASK, 'skeleton-task');

    // what the server sends is drawn
    expect(view.getByTestId('doc-title').textContent).toBe('Add slugify() and its test');
    expect(view.getByTestId('objective-text').textContent).toContain('lowercases its text');
    expect(view.getByTestId('step-S1').textContent).toContain('Write the function');
    expect(view.getByTestId('step-state-S2').textContent).toBe('current');

    // what it does not send is not drawn: the same page without its Review block,
    // and no count
    expect(blocksOf(view.container)).toEqual(BLOCKS.filter((block) => block !== 'review'));
    expect(view.queryByTestId('review-open-count')).toBeNull();
    expect(view.queryByTestId('status-note')).toBeNull();
    expect(view.queryByTestId('header-notes')).toBeNull();
    expect(view.container.textContent).not.toMatch(/findings? open/);
    expect(view.container.textContent).not.toContain('undefined');
    expect(errorsOf(errors)).toEqual([]);
  });

  it('draws its master with the task in work and no word of a review', async () => {
    const errors = vi.spyOn(console, 'error');
    serve(nativeBuild.optionalAbsent);
    const view = await open(MASTER, 'skeleton-master');

    expect(view.getByTestId('doc-title').textContent).toBe('Recorded master: text helpers');
    expect(view.getByTestId('now-row-1').textContent).toContain('Add slugify() and its test');
    expect(view.getByTestId('now-steps-1').textContent).toContain('1/3');
    expect(view.getByTestId('now-step-1').textContent).toContain('S2');
    expect(view.getByTestId('now-review-1').textContent).not.toMatch(/round|finding/);
    expect(view.getByTestId('now-row-1').textContent).not.toMatch(/round/);
    expect(view.container.textContent).not.toMatch(/findings? open/);
    expect(view.container.textContent).not.toContain('undefined');
    expect(errorsOf(errors)).toEqual([]);
  });
});

describe('the same native entries with their recorded review facts', () => {
  it('draws the Review block of the task with the count of the round', async () => {
    serve(nativeBuild.reviewPresent);
    const view = await open(TASK, 'skeleton-task');

    expect(blocksOf(view.container)).toEqual(BLOCKS);
    expect(view.getByTestId('review-open-count').textContent).toBe('1 of 3 findings open');
    expect(view.getByTestId('status-note').textContent).toContain('Review round 2 is recorded');
  });

  it('draws the review of the task in its master’s row', async () => {
    serve(nativeBuild.reviewPresent);
    const view = await open(MASTER, 'skeleton-master');

    expect(view.getByTestId('now-review-1').textContent).toBe('round 2 · 1 of 3 findings open');
  });
});
