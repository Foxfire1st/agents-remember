import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { dashboardStore } from '../data/store';
import { TaskRequirementLinksProvider } from './TaskRequirementLinks';
import { Markdown } from './Markdown';

const prose = `# Same heading\n\n[1]\n\n[Read requirement](requirements/rule.md)\n\n${'Whole document prose. '.repeat(100)}`;

beforeEach(() => dashboardStore.getState().reset());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('retained Markdown syntax', () => {
  it('parses once across navigation and isolates later marker and heading transforms', () => {
    const resources = dashboardStore.getState().taskDocuments;
    const prepare = resources.prepare.bind(resources);
    const parse = vi.fn();
    vi.spyOn(resources, 'prepare').mockImplementation((key, make) =>
      prepare(key, () => {
        parse();
        return make();
      }),
    );
    const first = render(
      <Markdown referenceMarkers headingIds>
        {prose}
      </Markdown>,
    );
    expect(first.container.querySelector('h1')?.id).toBe('same-heading');
    expect(first.container.querySelector('a[href="#reference-1"]')).toBeTruthy();
    first.unmount();
    const plain = render(<Markdown>{prose}</Markdown>);
    expect(plain.container.querySelector('h1')?.id).toBe('');
    expect(plain.container.querySelector('a[href="#reference-1"]')).toBeNull();
    expect(plain.container.textContent).toContain('[1]');
    expect(parse).toHaveBeenCalledOnce();
    expect(resources.retainedViews.entries).toBe(1);
  });

  it('uses the returned page callback after cached navigation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        async () =>
          new Response(
            JSON.stringify({
              repo: 'repo',
              master: 'master',
              document: 'master/task.json',
              registered: true,
              requirements: [
                {
                  name: 'rule.md',
                  path: 'rule.md',
                  address: 'requirements/rule.md',
                  size: 1,
                  sha256: 'a'.repeat(64),
                },
              ],
            }),
          ),
      ),
    );
    const oldOpen = vi.fn();
    const newOpen = vi.fn();
    const page = (open: typeof oldOpen) => (
      <TaskRequirementLinksProvider
        repo="repo"
        master="master"
        document="master/task.json"
        onOpenArtifact={open}
      >
        <Markdown>{prose}</Markdown>
      </TaskRequirementLinksProvider>
    );
    const first = render(page(oldOpen));
    await waitFor(() => expect(first.queryByTestId('requirement-link')).toBeTruthy());
    fireEvent.click(first.getByTestId('requirement-link'));
    expect(oldOpen).toHaveBeenCalledOnce();
    first.unmount();
    const returned = render(page(newOpen));
    await waitFor(() => expect(returned.queryByTestId('requirement-link')).toBeTruthy());
    fireEvent.click(returned.getByTestId('requirement-link'));
    expect(newOpen).toHaveBeenCalledOnce();
    expect(oldOpen).toHaveBeenCalledOnce();
    expect(dashboardStore.getState().taskDocuments.retainedViews.entries).toBe(1);
  });
});
