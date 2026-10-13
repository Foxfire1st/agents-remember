// MIK-R127, "Failure And Recovery Behavior": "A document whose projection lacks a
// new field (an older cache) is drawn without that block; no error."
//
// Between a landing and the restart of the dashboard a server of the older build
// can answer a page of the newer one. What such a server sends is recorded here,
// not made up: `taskDocument.recorded.base-3c2285d1.json` holds what the
// task-document projection of commit 3c2285d1, the base of this task, sends for
// one made-up master and its task with a review round. The file names its build,
// its producer and the documents it was read from.
// `taskDocument.recorded.mik-r127.json` holds what this task's projection sends
// for the same two documents.
//
// Both reach the page the way a server's answer does: the summaries as the text
// of a `snapshot` frame through `connectState`, a body as the answer of
// `/api/task-document`. Nothing is cast and no fixture builder stands between.
// The frame around the documents is this build's empty projection: this task
// changes the shape of a task document and of nothing else in the frame.
//
// The base build sends no form, no type, no status note, no header notes, no
// review state and no note of a step. The page draws what it is sent.
import { act, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { connectState } from '../../data/stream';
import { analytics, projection } from '../../test/fixtures/wire';
import { DetailPanel } from './DetailPanel';
import baseBuild from './taskDocument.recorded.base-3c2285d1.json';
import thisBuild from './taskDocument.recorded.mik-r127.json';
import { stubNotes } from './test-utils';

const ROOT = '/recorded-coordination/tasks/sandbox-app/recorded-master';
const TASK = `taskdoc:${ROOT}/01_slugify.json`;
const MASTER = `taskdoc:${ROOT}/task.json`;

// What a recording holds of the server's answers, as far as this file reads it.
interface Recording {
  summaries: unknown[];
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

// The text of a snapshot frame: this build's empty projection with the recorded
// task documents in the place where the server puts them.
function frameText(recording: Recording): string {
  const frame: { analytics: { taskDocuments: unknown[] } } = JSON.parse(
    JSON.stringify(projection({ analytics: analytics({ taskDocuments: [] }) })),
  );
  frame.analytics.taskDocuments = recording.summaries;
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

describe('a document from a server of the base build (MIK-R127, failure and recovery)', () => {
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
    serve(baseBuild);
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
    serve(baseBuild);
    const view = await open(MASTER, 'skeleton-master');

    expect(view.getByTestId('doc-title').textContent).toBe('Recorded master: text helpers');
    expect(view.getByTestId('now-row-1').textContent).toContain('Add slugify() and its test');
    expect(view.getByTestId('now-steps-1').textContent).toContain('1/3');
    expect(view.getByTestId('now-step-1').textContent).toContain('S2');
    expect(view.queryByTestId('now-review-1')).toBeNull();
    expect(view.getByTestId('now-row-1').textContent).not.toMatch(/round/);
    expect(view.container.textContent).not.toMatch(/findings? open/);
    expect(view.container.textContent).not.toContain('undefined');
    expect(errorsOf(errors)).toEqual([]);
  });
});

describe('the same documents from this build', () => {
  it('draws the Review block of the task with the count of the round', async () => {
    serve(thisBuild);
    const view = await open(TASK, 'skeleton-task');

    expect(blocksOf(view.container)).toEqual(BLOCKS);
    expect(view.getByTestId('review-open-count').textContent).toBe('1 of 3 findings open');
    expect(view.getByTestId('status-note').textContent).toContain('Review round 2 is recorded');
  });

  it('draws the review of the task in its master’s row', async () => {
    serve(thisBuild);
    const view = await open(MASTER, 'skeleton-master');

    expect(view.getByTestId('now-review-1').textContent).toBe('round 2 · 1 of 3 findings open');
  });
});
