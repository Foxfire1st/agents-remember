import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { dashboardStore } from '../../data/store';
import { ChangeSetViewer } from './ChangeSetViewer';

beforeEach(() => dashboardStore.getState().reset());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('keeps the parsed list and real editor on unchanged polls, then renders edits and current refusals', async () => {
  const polls: Array<() => Promise<void>> = [];
  const timeout = window.setTimeout.bind(window);
  vi.stubGlobal(
    'setTimeout',
    (callback: (...args: unknown[]) => void, delay?: number, ...args: unknown[]) => {
      if (delay === 2500) {
        polls.push(callback as () => Promise<void>);
        return timeout(() => {}, 0);
      }
      return timeout(callback, delay, ...args);
    },
  );
  let revision = 1;
  let refuse = false;
  const decoded: string[] = [];
  const fetch = vi.fn(async (address: string, init?: RequestInit) => {
    if (refuse)
      return new Response(JSON.stringify({ status: 'not-found', path: 'recorded object lost' }), {
        status: 404,
      });
    const etag = `W/"${revision}"`;
    const unchanged = new Headers(init?.headers).get('If-None-Match') === etag;
    const isFile = address.includes('/file-diff?');
    const counters = {
      code: { files: 1, insertions: revision, deletions: 0 },
      memory: { files: 0, insertions: 0, deletions: 0 },
    };
    const body = isFile
      ? {
          scope: 'leaf',
          kind: 'code',
          path: 'file.txt',
          language: 'text',
          before: { content: 'old\n' },
          after: { content: `new ${revision}\n` },
        }
      : {
          scope: 'leaf',
          code: [{ path: 'file.txt', insertions: revision, deletions: 0, status: 'M' }],
          memory: [],
          counters,
        };
    const response = unchanged
      ? new Response(null, { status: 304, headers: { ETag: etag } })
      : new Response(JSON.stringify(body), { headers: { ETag: etag } });
    const json = response.json.bind(response);
    vi.spyOn(response, 'json').mockImplementation(async () => {
      decoded.push(address);
      return json();
    });
    return response;
  });
  vi.stubGlobal('fetch', fetch);
  const view = render(
    <ChangeSetViewer repo="repo" master="master" leaf="LEAF" mode="working" onBack={vi.fn()} />,
  );
  fireEvent.click(await view.findByText('file.txt'));
  await waitFor(() => expect(view.container.querySelector('.cm-mergeView')).not.toBeNull());
  const editor = view.container.querySelector('.cm-mergeView');
  const listAddress = fetch.mock.calls.find(([address]) => !address.includes('/file-diff?'))![0];
  const original = dashboardStore.getState().changeSets.peek(listAddress);
  expect(decoded).toHaveLength(2);
  await act(async () => {
    await polls.shift()!();
  });
  expect(dashboardStore.getState().changeSets.peek(listAddress)).toBe(original);
  expect(decoded).toHaveLength(2);
  expect(view.container.querySelector('.cm-mergeView')).toBe(editor);
  revision = 2;
  await act(async () => {
    await polls.shift()!();
  });
  await waitFor(() =>
    expect(view.container.querySelector('.cm-mergeView')?.textContent).toContain('new 2'),
  );
  expect(view.container.querySelector('.cm-mergeView')).not.toBe(editor);
  expect(decoded).toHaveLength(4);
  refuse = true;
  await act(async () => {
    await polls.shift()!();
  });
  expect(view.getByTestId('pane-placeholder').textContent).toContain('recorded object lost');
  expect(dashboardStore.getState().changeSets.peek(listAddress)).toBeUndefined();
  refuse = false;
  await act(async () => {
    await polls.shift()!();
  });
  expect(view.getByTestId('changeset-counters').textContent).toContain('+2');
  expect(view.queryByTestId('changeset-refusal')).toBeNull();
});
