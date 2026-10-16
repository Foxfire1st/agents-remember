// L127-C-F06 (page half): the page aligns with the projection's preview/
// count/truncation boundaries. Totals always come from the whole count, never
// the shown length; previews say they are previews, and a whole value names none.
import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { seedTaskEntries, taskEntry, taskSummary, taskReference } from './test-utils';

const DOC = 'repo-a/m/01_bounds.json';

function wholeDoc() {
  // Whole values: nothing is cut, and the totals equal what is shown.
  return taskEntry({
    id: '1',
    title: 'doc',
    stage: 'started',
    stepsDone: 0,
    stepsTotal: 0,
    activity: 'works',
    reference: taskReference(DOC),
    summary: taskSummary({
      objective: '',
      type: '',
      createdAt: '2026-06-20T09:00:00+00:00',
      form: 'task',
      kind: 'subTask',
      statusNote: `Whole status note ${'with enough words to pass the clip bound. '.repeat(6)}`,
      headerNotes: [
        { label: 'Gate', value: 'green' },
        { label: 'Owner', value: 'team' },
      ],
      steps: [
        {
          id: 'S1',
          title: 'Step',
          status: 'pending',
          note: 'Whole step note',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
      ],
      reviewState: {
        round: 1,
        pending: false,
        additionalRounds: 0,
        totalFindings: 1,
        openFindings: 1,
        findings: [
          {
            findingId: 'F1',
            description: 'Whole finding',
            open: true,
            descriptionTruncated: false,
          },
        ],
      },
    }),
  });
}

describe('projection bounds on the page (L127-C-F06)', () => {
  it('reads whole values with shown-length totals and names no preview', () => {
    seedTaskEntries([wholeDoc()]);
    const { getByTestId, queryByTestId, queryByText } = render(
      <DetailPanel selectedId={`taskdoc:${DOC}`} />,
    );

    expect(getByTestId('status-note').textContent).toContain('Whole status note');
    // No total named: no counted link beyond the shown notes.
    expect(queryByTestId('header-notes-all-link')).toBeNull();
    expect(getByTestId('review-line').textContent).toContain('Round 1');
    fireEvent.click(getByTestId('status-note-toggle'));
    // A whole value names no preview.
    expect(queryByText('(preview)', { exact: false })).toBeNull();
  });

  it('counts header notes from the whole total, not the shown length', () => {
    const doc = taskEntry({
      id: '1',
      title: 'doc',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference(DOC),
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        headerNotes: [
          { label: 'Gate', value: 'green' },
          { label: 'Owner', value: 'team' },
        ],
        headerNotesTotal: 10,
      }),
    });
    seedTaskEntries([doc]);
    const { getByTestId } = render(<DetailPanel selectedId={`taskdoc:${DOC}`} />);

    expect(getByTestId('header-notes-all-link').textContent).toContain('all 10 header notes');
  });

  it('marks a truncated status note as a preview when opened', () => {
    const doc = taskEntry({
      id: '1',
      title: 'doc',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference(DOC),
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        statusNote: 'x'.repeat(400),
        statusNoteTruncated: true,
      }),
    });
    seedTaskEntries([doc]);
    const { getByTestId, getByText } = render(<DetailPanel selectedId={`taskdoc:${DOC}`} />);

    fireEvent.click(getByTestId('status-note-toggle'));
    expect(getByText('(preview)', { exact: false })).toBeTruthy();
  });

  it('bounds the inline open findings with the whole round one link away', () => {
    const doc = taskEntry({
      id: '1',
      title: 'doc',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference(DOC),
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        reviewState: {
          round: 3,
          pending: false,
          additionalRounds: 0,
          totalFindings: 10,
          openFindings: 10,
          findings: Array.from({ length: 10 }, (_, i) => ({
            findingId: `F${i + 1}`,
            description: `Finding ${i + 1}`,
            open: true,
            descriptionTruncated: false,
          })),
        },
      }),
    });
    seedTaskEntries([doc]);
    const { container, getByTestId } = render(<DetailPanel selectedId={`taskdoc:${DOC}`} />);

    const open = container.querySelector('[data-testid="review-open-findings"]');
    expect(open?.children).toHaveLength(8);
    expect(getByTestId('findings-all-link').textContent).toContain('all 10 findings');
  });
});
