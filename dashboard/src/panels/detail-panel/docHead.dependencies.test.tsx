import { fireEvent, render, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DocHead } from './docHead';
import { taskDoc } from './test-utils';

describe('dependency facts in the task head (MIK-R128 rule 4)', () => {
  it('names both directions with state, navigates by document selection key, and retains gone refs disabled', () => {
    const onJump = vi.fn();
    const doc = taskDoc({
      kind: 'subTask',
      docPath: '/tasks/repo-a/m/03_leaf.json',
      waitsForTasks: [
        {
          id: 'L1',
          title: 'Prepare the contract',
          status: 'Completed',
          docPath: '/tasks/repo-a/m/01_prepare.json',
        },
        { id: 'L2', title: 'Missing prerequisite', status: 'gone' },
      ],
      neededByTasks: [
        {
          id: 'L4',
          title: 'Use the contract',
          status: 'planning',
          docPath: '/tasks/repo-a/m/04_use.json',
        },
      ],
    });
    const view = render(<DocHead doc={doc} chain={[]} onJump={onJump} openView={vi.fn()} />);
    const waits = view.getByTestId('doc-waits-for');
    const needed = view.getByTestId('doc-needed-by');
    expect(waits.textContent).toContain('Waits for');
    expect(waits.textContent).toContain('Completed');
    expect(waits.textContent).toContain('gone');
    expect(needed.textContent).toContain('Needed by');
    expect(needed.textContent).toContain('planning');
    fireEvent.click(within(waits).getByRole('button', { name: 'L1 · Prepare the contract' }));
    fireEvent.click(within(needed).getByRole('button', { name: 'L4 · Use the contract' }));
    expect(onJump.mock.calls).toEqual([
      ['taskdoc:/tasks/repo-a/m/01_prepare.json'],
      ['taskdoc:/tasks/repo-a/m/04_use.json'],
    ]);
    const missing = within(waits).getByRole('button', { name: 'L2 · Missing prerequisite' });
    expect(missing.hasAttribute('disabled')).toBe(true);
    fireEvent.click(missing);
    expect(onJump).toHaveBeenCalledTimes(2);
  });

  it('omits both absent and empty directions on an older document', () => {
    const view = render(
      <DocHead
        doc={taskDoc({
          kind: 'subTask',
          docPath: '/tasks/repo-a/m/01_leaf.json',
          neededByTasks: [],
        })}
        chain={[]}
        onJump={vi.fn()}
        openView={vi.fn()}
      />,
    );
    expect(view.queryByTestId('doc-waits-for')).toBeNull();
    expect(view.queryByTestId('doc-needed-by')).toBeNull();
  });
});
