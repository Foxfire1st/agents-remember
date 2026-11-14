import { render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { resetFirstRequest } from '../../../explainer/request';
import { resetTray } from '../../../explainer/testSupport';
import { ExplanationView } from './ExplanationView';
import { BASE, channel, json, props, serve } from './ExplanationView.test-utils';

afterEach(() => {
  resetTray();
  resetFirstRequest();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

// When the last render ended unavailable for a missing browser, the review page's own "No
// explanation yet" block says what is missing and the command -- no new control -- and a page that
// published leaves the block silent.
const NOTHING_YET = {
  state: 'ok',
  seat: { state: 'missing' },
  retired: false,
  retiredAt: null,
  binding: { kind: 'recorded', comparison: 9, label: '#9' },
  explanation: null,
  building: null,
  stale: { parts: [], total: 0, unavailable: 'the leaf has no retained revision' },
  parts: [],
  claims: [],
  moved: null,
  limit: null,
};

const NOTE =
  'This installation fetches its browser once, for the first page: that page takes a minute or two while it does, and later pages are immediate.';

const SAVED_WITH_PAGE = {
  ...NOTHING_YET,
  explanation: { revision: 1, at: '2026-11-13T12:57:00Z', entrypoint: 'index.html', comparison: null },
};

const FAILED_FOR_BROWSER = {
  ...NOTHING_YET,
  lastRender: {
    state: 'unavailable',
    missing: ['Playwright 1.65.0 at /home/u/.local/share/agents-remember/explainer-browser'],
    detail: 'npm could not install playwright@1.65.0: npm error code ECONNREFUSED',
    command: 'agents-remember explainer-browser --fetch',
  },
};

function page(state: unknown) {
  return serve((call, _calls, counter) => {
    if (call.url.startsWith(`${BASE}/state`)) return json(state);
    if (call.url === `${BASE}/revisions`) return json({ state: 'ok', pinned: null, revisions: [] });
    if (call.url === '/api/canvas/sessions') {
      counter.sessions += 1;
      return json(channel(`s${counter.sessions}`, 1));
    }
    return json({ state: 'refused', reason: 'unmocked', detail: call.url }, 404);
  });
}

describe('the empty block says why the last explanation could not be published (rule 4)', () => {
  it('names the missing browser and the command, in that block, with no control added', async () => {
    page(FAILED_FOR_BROWSER);
    render(<ExplanationView {...props} />);

    const block = await screen.findByTestId('explanation-empty');
    const said = await within(block).findByTestId('explanation-empty-browser');

    expect(said.textContent).toContain('Playwright 1.65.0');
    expect(said.textContent).toContain('agents-remember explainer-browser --fetch');
    // The block keeps the one control it always had, and gains none.
    expect(within(block).getByTestId('explain-change-set')).toBeTruthy();
    expect(within(block).queryByTestId('explanation-empty-browser-fetch')).toBeNull();
  });

  it('says the sentence before the first fetch, in the same block (rule 1)', async () => {
    page({
      ...NOTHING_YET,
      browserNotice: {
        notice:
          'This installation fetches its browser once, for the first page: that page takes a minute or two while it does, and later pages are immediate.',
        missing: ['Playwright 1.65.0 at /home/u/.local/share/agents-remember/explainer-browser'],
      },
    });
    render(<ExplanationView {...props} />);

    const said = await screen.findByTestId('explanation-browser-notice');

    expect(said.textContent).toContain('fetches its browser once, for the first page');
    expect(said.textContent).toContain('later pages are immediate');
  });

  it('says it over an earlier page too, while the fetch runs (rule 1, "while")', async () => {
    page({ ...SAVED_WITH_PAGE, browserNotice: { notice: NOTE, missing: [] } });
    render(<ExplanationView {...props} />);

    const said = await screen.findByTestId('explanation-browser-notice');
    expect(said.textContent).toContain('fetches its browser once');
    // The page itself is still drawn under it.
    expect(await screen.findByTestId('explanation-frame')).toBeTruthy();
  });

  it('says a failed fetch over an earlier page too, and calls that page earlier (rule 4)', async () => {
    page({ ...SAVED_WITH_PAGE, lastRender: FAILED_FOR_BROWSER.lastRender, browserNotice: null });
    render(<ExplanationView {...props} />);

    const said = await screen.findByTestId('explanation-browser-notice');
    expect(said.textContent).toContain('could not be published');
    expect(said.textContent).toContain('explainer-browser --fetch');
    expect(said.textContent).toContain('earlier');
    // The earlier page is still drawn, under the line that says it is not the answer.
    expect(await screen.findByTestId('explanation-frame')).toBeTruthy();
  });

  it('says nothing about a browser when the last page was published', async () => {
    page({ ...NOTHING_YET, lastRender: null });
    render(<ExplanationView {...props} />);

    const block = await screen.findByTestId('explanation-empty');
    expect(within(block).getByTestId('explain-change-set')).toBeTruthy();
    expect(within(block).queryByTestId('explanation-empty-browser')).toBeNull();
  });
});
