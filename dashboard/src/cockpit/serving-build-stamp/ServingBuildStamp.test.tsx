import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { dashboardStore } from '../../data/store';
import type { BuildCurrentness } from '../../types/projection';
import { ServingBuildStamp } from './ServingBuildStamp';

const build = {
  version: '9.9.9',
  commit: 'abc1234',
  bootedAt: '2026-11-05T00:00:00Z',
  dirty: true,
  dashboardBuild: 'different-client',
};

beforeEach(() => dashboardStore.getState().reset());
afterEach(cleanup);

describe('serving build currentness', () => {
  it('keeps an absent build empty and retains the legacy immutable stamp', () => {
    const view = render(<ServingBuildStamp />);
    expect(view.queryByTestId('serving-build')).toBeNull();
    act(() => dashboardStore.setState({ servingBuild: build }));
    const stamp = view.getByTestId('serving-build');
    expect(stamp.textContent).toContain('abc1234* · up');
    expect(stamp.title).toContain('dirty — serving uncommitted code');
    expect(stamp.title).toContain(build.bootedAt);
    expect(stamp.dataset.clientBuildCurrent).toBe('false');
    expect(stamp.title).toContain('client bundle differs');
    expect(stamp.dataset.buildCurrentness).toBeUndefined();
  });

  it('updates current, stale and unknown state without replacing boot identity', () => {
    dashboardStore.setState({ servingBuild: build, servingProcessId: 123 });
    const view = render(<ServingBuildStamp />);
    for (const state of ['current', 'stale', 'unknown'] as const) {
      const currentness: BuildCurrentness = {
        mode: 'installed',
        state,
        loadedDigest: 'loaded-sha',
        installedDigest: 'installed-sha',
        firstSeenAt: '2026-11-05T01:00:00Z',
        reason: 'Python source differs.',
        remedy: 'Restart the dashboard.',
      };
      act(() => dashboardStore.getState().applyDelta('buildCurrentness', currentness));
      const stamp = view.getByTestId('serving-build');
      expect(stamp.textContent).toContain(state);
      expect(stamp.textContent).toContain('abc1234*');
      expect(stamp.title).toContain('loaded Python digest loaded-sha');
      expect(stamp.title).toContain('installed Python digest installed-sha');
      expect(stamp.title).toContain(currentness.firstSeenAt);
      expect(stamp.title).toContain('Restart the dashboard.');
      expect(stamp.title).toContain('process 123');
      expect(dashboardStore.getState().servingBuild).toBe(build);
    }
  });

  it('names checkout mode and explains an unknown digest without inventing identities', () => {
    dashboardStore.setState({
      servingBuild: build,
      buildCurrentness: { mode: 'checkout', state: 'unknown', reason: 'Python digest unreadable.' },
    });
    const { getByTestId } = render(<ServingBuildStamp />);
    const stamp = getByTestId('serving-build');
    expect(stamp.textContent).toContain('checkout · unknown');
    expect(stamp.title).toContain('Python digest unreadable.');
    expect(stamp.title).not.toContain('loaded Python digest');
    expect(stamp.title).not.toContain('installed Python digest');
  });
});
