import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { dashboardStore } from '../../data/store';
import { taskEntrySelectionKey } from '../../data/taskReference';
import { projection, taskEntry, taskSummary } from '../../test/fixtures/wire';
import { DetailPanel } from './DetailPanel';

const generation = { codeBase: 'cb', codeTip: 'ct', memoryBase: 'mb', memoryTip: 'mt', digest: 'g' };
const counters = { code: { files: 2, insertions: 17, deletions: 3 }, memory: { files: 1, insertions: 5, deletions: 1 } };

afterEach(() => { cleanup(); dashboardStore.getState().reset(); vi.unstubAllGlobals(); });

it('reads and renders the master counter from its summary while the body is still pending', async () => {
  const summary = taskSummary({ kind: 'master', form: 'master', objective: 'Summary objective' });
  const entry = taskEntry({ reference: { repository: 'repo', path: 'master/task.json' }, summary });
  dashboardStore.getState().applySnapshot(projection({ tasks: [entry] }));
  let releaseBody!: (body: Response) => void;
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    urls.push(url);
    if (url.startsWith('/api/task-document')) return new Promise<Response>((resolve) => { releaseBody = resolve; });
    if (url.startsWith('/api/changeset/master/counters'))
      return Promise.resolve(new Response(JSON.stringify({ master: 'master', generation, counters, leaves: [], currentness: 'current', scope: 'integrated' })));
    return Promise.resolve(new Response('{}', { status: 503 }));
  }));
  const onOpen = vi.fn();
  const view = render(<DetailPanel selectedId={taskEntrySelectionKey(entry.key)} onOpenChangeSet={onOpen} />);
  await waitFor(() => expect(view.getByTestId('open-changeset').textContent).toContain('+22 −4'));
  expect(view.getByText('Summary objective')).toBeTruthy();
  expect(view.getByText(/Loading complete task document/)).toBeTruthy();
  expect(urls.filter((url) => url.includes('/master/counters?') && url.includes('includeLeaves=false'))).toHaveLength(1);
  fireEvent.click(view.getByTestId('open-changeset'));
  expect(onOpen).toHaveBeenCalledWith({ repo: 'repo', master: 'master', generation });
  await act(async () => releaseBody(new Response(JSON.stringify({ ...summary, objective: 'Full objective' }))));
  await waitFor(() => expect(view.getByText('Full objective')).toBeTruthy());
  expect(view.queryByText(/Loading complete task document/)).toBeNull();
  expect(view.getAllByTestId('open-changeset')).toHaveLength(1);
  expect(urls.filter((url) => url.includes('/master/counters?') && url.includes('includeLeaves=false'))).toHaveLength(1);
});

it('keeps the leaf body boundary and does not start any change-set read while it is pending', async () => {
  const summary = taskSummary({ kind: 'subTask', form: 'task' });
  const entry = taskEntry({ reference: { repository: 'repo', path: 'master/leaf.json' }, summary });
  dashboardStore.getState().applySnapshot(projection({ tasks: [entry] }));
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    urls.push(url);
    return new Promise<Response>(() => undefined);
  }));
  const view = render(<DetailPanel selectedId={taskEntrySelectionKey(entry.key)} onOpenChangeSet={vi.fn()} />);
  await waitFor(() => expect(urls.some((url) => url.startsWith('/api/task-document'))).toBe(true));
  expect(view.queryByTestId('open-changeset')).toBeNull();
  expect(urls.some((url) => url.startsWith('/api/changeset'))).toBe(false);
});
