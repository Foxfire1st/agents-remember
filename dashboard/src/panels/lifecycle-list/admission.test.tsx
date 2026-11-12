import { fireEvent,render } from '@testing-library/react';
import { expect,it,vi } from 'vitest';
import { taskEntrySelectionKey } from '../../data/taskReference';
import { LifecycleList } from './LifecycleList';
import { hierarchy,installListCleanup,rowOf,seed,task } from './test-utils';

installListCleanup();
it('shows every served task without a worktree and selects its qualified reference', () => {
  const { master, leaf } = hierarchy();
  master.parent = undefined;
  const missing = task('M-L2', {
    stage: 'planned',
    activity: undefined,
    summary: undefined,
    parent: master.key,
  });
  master.children.push(missing.key);
  seed([master, leaf, missing]);
  const onSelect = vi.fn();
  const ui = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
  expect(ui.getByText('Tasks · 3')).toBeTruthy();
  expect(rowOf(ui.getByText(leaf.title)).dataset.depth).toBe('1');
  expect(ui.getByText(missing.title)).toBeTruthy();
  fireEvent.click(ui.getByText(missing.title));
  expect(onSelect).toHaveBeenCalledWith(taskEntrySelectionKey(missing.key));
});
