import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { cells, seedPromotedLeaf } from './test-utils';

describe('DetailPanel promoted lifecycle identity', () => {
  it('renders the leaf task document without falling back to the master task documents', () => {
    seedPromotedLeaf();
    const { getAllByText, getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId="taskdoc:agents-remember/260610_browser-dashboard/16_engine-room-stack-entry-height.json" />,
    );

    expect(getAllByText('16_engine-room-stack-entry-height').length).toBeGreaterThan(0);
    // the head names the form, not the stored kind: a leaf reads "task"
    expect(getByTestId('doc-form').textContent).toBe('task');
    expect(getByText('Engine Room Stack Entry Height')).toBeTruthy();
    expect(getByText('Keep a single Engine Room enclosure entry visually bounded.')).toBeTruthy();
    expect(getAllByText('Fix the stack entry height')).toHaveLength(1);
    expect(cells(getByTestId('step-S1')).slice(1, 3)).toEqual(['S1', 'Fix the stack entry height']);
    expect(getByText('Notes')).toBeTruthy();
    // the free-form section stands as one Further-notes line with its own view
    fireEvent.click(getByTestId('further-open-0'));
    expect(getByTestId('doc-view-title').textContent).toBe('Notes');
    expect(getByText('This is the authored leaf task document.')).toBeTruthy();
    fireEvent.click(getByTestId('doc-view-back'));
    expect(queryByText('Lifecycle Finalize Task')).toBeNull();
    expect(queryByText('Close out the lifecycle finalizer.')).toBeNull();
    expect(queryByText('Series Contract')).toBeNull();
    expect(queryByText('schema: ar-series-contract/v1')).toBeNull();
    expect(
      queryByText(
        'taskdoc:agents-remember/260610_browser-dashboard/16_engine-room-stack-entry-height.json',
      ),
    ).toBeNull();
    expect(queryByText('No task document bound to this task.')).toBeNull();
  });

  it('links an enclosure-opened leaf back to its parent task document', () => {
    seedPromotedLeaf();
    const onOpenLifecycle = vi.fn();
    const { getByTestId } = render(
      <DetailPanel
        selectedId="taskdoc:agents-remember/260610_browser-dashboard/16_engine-room-stack-entry-height.json"
        onOpenLifecycle={onOpenLifecycle}
      />,
    );

    expect(getByTestId('master-parent-link').textContent).toContain('Browser Dashboard Series');
    fireEvent.click(getByTestId('master-parent-link'));
    expect(onOpenLifecycle).toHaveBeenCalledWith(
      'taskdoc:agents-remember/260610_browser-dashboard/task.json',
    );
  });
});
