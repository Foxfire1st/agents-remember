// Every task selection dispatches on the served summary form.
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DetailPanel } from './DetailPanel';
import { enclosure, seedTaskEntries, taskEntry, taskSummary, taskReference } from './test-utils';

it.each(['sprint', 'master', 'task'] as const)('dispatches the served %s entry', (form) => {
  const reference = taskReference(`repo-a/${form}/task.json`);
  seedTaskEntries([
    taskEntry({
      reference,
      id: form,
      title: `Dispatch ${form}`,
      summary: taskSummary({ form, kind: form === 'task' ? 'subTask' : 'master' }),
    }),
  ]);
  const view = render(<DetailPanel selectedId={`taskdoc:repo-a/${form}/task.json`} />);
  expect(view.getByTestId(`skeleton-${form}`)).toBeTruthy();
});

it.each(['sprint', 'master', 'task'] as const)(
  'keeps %s dispatch with its bound worktree',
  (form) => {
    const reference = taskReference(`repo-a/${form}/task.json`);
    seedTaskEntries([
      taskEntry({
        reference,
        id: form,
        title: `Bound ${form}`,
        worktrees: [enclosure({ enclosure: '/contracts/root' })],
        summary: taskSummary({ form, kind: form === 'task' ? 'subTask' : 'master' }),
      }),
    ]);
    const view = render(<DetailPanel selectedId={`taskdoc:repo-a/${form}/task.json`} />);
    expect(view.getByTestId(`skeleton-${form}`)).toBeTruthy();
  },
);

describe('canonical task selection', () => {
  it('does not resolve an unrelated operational key as a task', () => {
    seedTaskEntries([
      taskEntry({
        reference: taskReference('repo-a/m/task.json'),
        summary: taskSummary({ form: 'master' }),
      }),
    ]);
    const view = render(<DetailPanel selectedId="unrelated-operation" />);
    expect(view.queryByTestId('skeleton-master')).toBeNull();
    expect(
      view.getByText('Select a task to inspect its steps, review, requirements and reports.'),
    ).toBeTruthy();
  });
});
