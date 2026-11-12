import { fireEvent, render, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DocHead } from './docHead';
import { seedTaskEntries, taskEntry, taskSummary, taskReference } from './test-utils';

describe('dependency facts in the task head (MIK-R128 rule 4 / L168 amendment 6)', () => {
  it('uses the same status and qualified selection as each task entry; retains gone refs disabled', () => {
    const onJump = vi.fn();
    const before = taskEntry({
      reference: taskReference('repo-a/m/01_prepare.json'),
      id: 'L1',
      title: 'Prepare the contract',
      stage: 'completed',
    });
    const after = taskEntry({
      reference: taskReference('repo-a/m/04_use.json'),
      id: 'L4',
      title: 'Use the contract',
      stage: 'started',
      activity: 'paused',
    });
    seedTaskEntries([before, after]);
    const doc = taskSummary({
      waitsForTasks: [
        { id: before.id, title: before.title, reference: before.reference, gone: false },
        { id: 'L2', title: 'Missing prerequisite', reference: undefined, gone: true },
      ],
      neededByTasks: [
        { id: after.id, title: after.title, reference: after.reference, gone: false },
      ],
    });
    const view = render(
      <DocHead entry={taskEntry()} doc={doc} chain={[]} onJump={onJump} openView={vi.fn()} />,
    );
    const waits = view.getByTestId('doc-waits-for');
    const needed = view.getByTestId('doc-needed-by');
    expect(waits.textContent).toContain('Waits for');
    expect(waits.textContent).toContain('completed');
    expect(waits.textContent).toContain('gone');
    expect(needed.textContent).toContain('Needed by');
    expect(needed.textContent).toContain('paused');
    fireEvent.click(within(waits).getByRole('button', { name: 'L1 · Prepare the contract' }));
    fireEvent.click(within(needed).getByRole('button', { name: 'L4 · Use the contract' }));
    expect(onJump.mock.calls).toEqual([
      ['taskdoc:repo-a/m/01_prepare.json'],
      ['taskdoc:repo-a/m/04_use.json'],
    ]);
    const missing = within(waits).getByRole('button', { name: 'L2 · Missing prerequisite' });
    expect(missing.hasAttribute('disabled')).toBe(true);
    fireEvent.click(missing);
    expect(onJump).toHaveBeenCalledTimes(2);
  });
  it('omits absent and empty directions', () => {
    const view = render(
      <DocHead
        entry={taskEntry()}
        doc={taskSummary({ neededByTasks: [] })}
        chain={[]}
        onJump={vi.fn()}
        openView={vi.fn()}
      />,
    );
    expect(view.queryByTestId('doc-waits-for')).toBeNull();
    expect(view.queryByTestId('doc-needed-by')).toBeNull();
  });
});
