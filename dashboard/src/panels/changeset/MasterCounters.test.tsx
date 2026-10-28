import { act, cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { dashboardStore } from '../../data/store';
import { loadMasterCounters } from '../../data/changeset';
import { ChangeSetButton } from '../detail-panel/changeSetBar';
import { ChangeSetViewer } from './ChangeSetViewer';

const generation = { codeBase: 'cb', codeTip: 'ct', memoryBase: 'mb', memoryTip: 'mt', digest: 'g' };
const counters = { code: { files: 2, insertions: 17, deletions: 3 }, memory: { files: 1, insertions: 5, deletions: 1 } };
const net = { master: 'M', generation, currentness: 'current', counters, leaves: [] };
const leaves = [{ leafId: 'L', state: 'committed', counters }];
interface Pending { url: string; answer: (body: unknown, status?: number) => void }
function requests() {
  const pending: Pending[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string) => new Promise<Response>((resolve) => {
    pending.push({ url, answer: (body, status = 200) => resolve(new Response(JSON.stringify(body), { status })) });
  })));
  return pending;
}
afterEach(() => { cleanup(); dashboardStore.getState().reset(); vi.unstubAllGlobals(); });

it('shows button totals before leaf rows and preserves a pinned open target', async () => {
  const pending = requests();
  const opened = vi.fn();
  const view = render(<ChangeSetButton target={{ repo: 'r', master: 'M' }} label="series" onOpen={opened} />);
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () => pending[0].answer(net));
  await waitFor(() => expect(view.getByTestId('open-changeset').textContent).toContain('+22 −4'));
  expect(view.getByTestId('master-attribution-state').textContent).toContain('reading');
  view.getByTestId('open-changeset').click();
  expect(opened).toHaveBeenCalledWith({ repo: 'r', master: 'M', generation });
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => pending[1].answer({ ...net, leaves }));
  await waitFor(() => expect(view.getByTestId('changeset-leaf-attribution').textContent).toContain('1 committed'));
  expect(view.queryByTestId('master-attribution-state')).toBeNull();
});

it('shows viewer CounterRow while full inspection and attribution are pending', async () => {
  const pending = requests();
  const view = render(<ChangeSetViewer repo="r" master="M" onBack={vi.fn()} />);
  await waitFor(() => expect(pending).toHaveLength(1));
  expect(pending[0].url).toContain('/master/counters?');
  await act(async () => pending[0].answer(net));
  await waitFor(() => expect(view.getByTestId('changeset-counters').textContent).toContain('+17'));
  await waitFor(() => expect(pending).toHaveLength(3));
  const inspection = pending.find((item) => item.url.startsWith('/api/changeset/master?'))!;
  expect(inspection.url).toContain('includeLeaves=false');
  expect(inspection.url).toContain('codeTip=ct');
  expect(view.getByTestId('master-attribution-state').textContent).toContain('reading');
  const attribution = pending.find((item) => item.url.includes('/master/counters?') && item.url.includes('includeLeaves=true'))!;
  await act(async () => attribution.answer({ ...net, leaves }));
  expect(view.getByTestId('changeset-counters').textContent).toContain('+17');
  await act(async () => inspection.answer({ ...net, code: [], memory: [] }));
  await waitFor(() => expect(view.getByTestId('changeset-leaf-row').textContent).toContain('L'));
});

it('keeps unmeasured button honest without printing a zero total', async () => {
  const pending = requests();
  const view = render(<ChangeSetButton target={{ repo: 'r', master: 'M' }} label="series" onOpen={vi.fn()} />);
  await waitFor(() => expect(pending).toHaveLength(1));
  await act(async () => pending[0].answer({ ...net, generation: null, currentness: 'unmeasured' }));
  await waitFor(() => expect(view.getByTestId('changeset-state').textContent).toBe('unmeasured'));
  expect(view.getByTestId('open-changeset').textContent).not.toContain('+0');
  expect(view.queryByTestId('changeset-leaf-attribution')).toBeNull();
  expect(pending).toHaveLength(1);
});

it('shows the clicked exact pair while validating it, but never opens inspection on the snapshot', async () => {
  const pending = requests();
  const seeded = loadMasterCounters('r', 'M');
  await waitFor(() => expect(pending).toHaveLength(1));
  pending[0].answer(net);
  await seeded;
  const view = render(<ChangeSetViewer repo="r" master="M" generation={generation} onBack={vi.fn()} />);
  expect(view.getByTestId('changeset-counters').textContent).toContain('+17');
  await waitFor(() => expect(pending).toHaveLength(2));
  expect(pending[1].url).toContain('/master/counters?');
  expect(pending.some((item) => item.url.startsWith('/api/changeset/master?'))).toBe(false);
  await act(async () => pending[1].answer({ detail: 'recorded commit unavailable' }, 404));
  await waitFor(() => expect(view.queryByTestId('changeset-counters')).toBeNull());
  expect(pending.some((item) => item.url.startsWith('/api/changeset/master?'))).toBe(false);
});

it('never borrows displayed totals from a different pinned generation', async () => {
  const pending = requests();
  const seeded = loadMasterCounters('r', 'M');
  await waitFor(() => expect(pending).toHaveLength(1));
  pending[0].answer(net);
  await seeded;
  const view = render(<ChangeSetViewer repo="r" master="M"
    generation={{ ...generation, codeTip: 'other' }} onBack={vi.fn()} />);
  expect(view.queryByTestId('changeset-counters')).toBeNull();
  await waitFor(() => expect(pending).toHaveLength(2));
  expect(pending.some((item) => item.url.startsWith('/api/changeset/master?'))).toBe(false);
});
