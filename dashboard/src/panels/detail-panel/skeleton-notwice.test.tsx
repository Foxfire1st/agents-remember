// MIK-R127 evidence 3: no block is drawn twice, on a master that holds a
// `subTasks` section, a `sharedDecisions` section and a free-form objective.
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { seedTaskEntries, taskEntry, taskSummary, taskReference } from './test-utils';

const MASTER = 'repo-a/m/task.json';
const OBJECTIVE = 'The one objective text.';

describe('nothing twice (rule 8)', () => {
  function seedMaster() {
    const leaf = taskEntry({
      stepsDone: 0,
      stepsTotal: 0,
      reference: taskReference('repo-a/m/01_leaf.json'),
      id: 'L1',
      title: 'Leaf one',
      stage: 'planned',
    });
    seedTaskEntries([
      leaf,
      taskEntry({
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(MASTER),
        children: [leaf.key],
        counts: {
          planned: 1,
          started: 0,
          completed: 0,
          aborted: 0,
          works: 0,
          paused: 0,
          unknown: 0,
        },
        id: 'M',
        title: 'Master',
        summary: taskSummary({
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'master',
          kind: 'master',
          objective: OBJECTIVE,
          decisions: [
            {
              at: '2026-10-01',
              decision: 'Decided.',
              rationale: 'Because.',
              product_record: false,
            },
          ],
          sections: [
            { kind: 'subTasks', heading: 'Sub-tasks', body: 'Tasks intro prose.' },
            {
              kind: 'sharedDecisions',
              heading: 'Shared decisions',
              body: 'Decisions intro prose.',
            },
            { kind: 'freeform', heading: 'Echo', body: OBJECTIVE },
            { kind: 'freeform', heading: 'Other', body: 'Other body.' },
          ],
        }),
      }),
    ]);
  }

  it('draws one index, one objective, and no second list for structured sections', () => {
    seedMaster();
    const { container, getByTestId, getByText, queryByTestId, queryByText } = render(
      <DetailPanel selectedId={`taskdoc:${MASTER}`} />,
    );

    // one index: the waiting row stands once; the old in-section copy is gone
    expect(container.querySelectorAll('[data-testid="subtask-open-1"]')).toHaveLength(1);
    expect(queryByTestId('subtask-mid-1')).toBeNull();
    // the introducing texts stand with their blocks
    expect(getByTestId('tasks-intro').textContent).toContain('Tasks intro prose.');
    expect(getByTestId('decisions-intro').textContent).toContain('Decisions intro prose.');
    // one objective: the free-form byte-for-byte repeat is not listed again
    expect(container.querySelectorAll('[data-block="objective"]')).toHaveLength(1);
    expect(getByText(OBJECTIVE)).toBeTruthy();
    expect(queryByText('Echo')).toBeNull();
    // the other free-form section is one Further-notes line
    expect(getByText('Other')).toBeTruthy();
  });

  it('lists structured sections nowhere in the further notes', () => {
    seedMaster();
    const { queryByTestId } = render(<DetailPanel selectedId={`taskdoc:${MASTER}`} />);

    expect(queryByTestId('further-open-0')?.textContent).toContain('Other');
    expect(queryByTestId('further-open-1')).toBeNull();
  });
});
