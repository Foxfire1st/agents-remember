// MIK-R127 evidence 2: tests of each bound and its link, of the view and of
// the way back.
import { act, fireEvent, render, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { cells, seedTaskDocuments, taskDoc } from './test-utils';

const LEAF = '/tasks/repo-a/m/01_leaf.json';
const MASTER = '/tasks/repo-a/m/task.json';

function seedLeaf(over: Omit<Parameters<typeof taskDoc>[0], 'kind' | 'docPath'>) {
  const leaf = taskDoc({
    id: 'L1',
    kind: 'subTask',
    docPath: LEAF,
    title: 'Leaf one',
    status: 'inProgress',
    ...over,
  });
  seedTaskDocuments([taskDoc({ id: 'M', kind: 'master', docPath: MASTER, title: 'Master' }), leaf]);
  return leaf;
}

function renderLeaf() {
  return render(<DetailPanel selectedId={`taskdoc:${LEAF}`} />);
}

describe('requirement bound (rule 6)', () => {
  it('shows eight lines and a link naming all, opens the view, back returns', () => {
    const requirements = Array.from({ length: 10 }, (_, index) => `Requirement ${index + 1}.`);
    seedLeaf({ requirements });
    const { container, getByTestId, getByText, queryByTestId } = renderLeaf();

    // the head strip names the primary requirement too; the bound counts the block
    const block = within(container.querySelector('[data-block="requirements"]') as HTMLElement);
    for (let index = 1; index <= 8; index += 1) {
      expect(block.getByText(`Requirement ${index}.`)).toBeTruthy();
    }
    expect(block.queryByText('Requirement 9.')).toBeNull();
    expect(queryByTestId('doc-view')).toBeNull();
    expect(getByTestId('requirements-all-link').textContent).toContain('all 10 requirement lines');

    fireEvent.click(getByTestId('requirements-all-link'));
    expect(getByTestId('doc-view')).toBeTruthy();
    expect(getByTestId('doc-view-title').textContent).toBe('Requirements');
    expect(getByText('Requirement 10.')).toBeTruthy();

    fireEvent.click(getByTestId('doc-view-back'));
    expect(queryByTestId('doc-view')).toBeNull();
    expect(container.querySelector('[data-testid="skeleton-task"]')).toBeTruthy();
    expect(getByTestId('requirements-all-link')).toBeTruthy();
  });

  it('shows no link when eight or fewer lines fit', () => {
    seedLeaf({ requirements: ['One.', 'Two.'] });
    const { container, queryByTestId } = renderLeaf();

    const block = within(container.querySelector('[data-block="requirements"]') as HTMLElement);
    expect(block.getByText('One.')).toBeTruthy();
    expect(queryByTestId('requirements-all-link')).toBeNull();
  });
});

describe('register bound (rules 3 and 6)', () => {
  const SPRINT = '/tasks/repo-a/sprint/task.json';
  const body = [
    '| A | B | C | D | E |',
    '| --- | --- | --- | --- | --- |',
    ...Array.from({ length: 7 }, (_, index) => `| ${index + 1} | b | c | d | e |`),
  ].join('\n');

  function seedSprint() {
    seedTaskDocuments([
      taskDoc({
        id: 'S',
        kind: 'master',
        docPath: SPRINT,
        title: 'Sprint',
        form: 'sprint',
        orchestrates: ['master-a'],
        objective: 'Sprint objective.',
        sections: [{ kind: 'freeform', heading: 'Judgment Register', body }],
        subTasks: [],
      }),
    ]);
  }

  it('shows the five newest rows and a link naming all, opens the view, back returns', () => {
    seedSprint();
    const { container, getByTestId, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${SPRINT}`} />,
    );

    // newest first: rows 7..3 on the page, rows 2..1 only in the view
    const table = getByTestId('register-table');
    expect(table.textContent).toContain('7');
    expect(table.textContent).not.toContain('10 |');
    expect(getByTestId('register-all-link-0').textContent).toContain('all 7 rows');

    fireEvent.click(getByTestId('register-all-link-0'));
    expect(getByTestId('doc-view-title').textContent).toBe('Judgment Register');
    expect(getByTestId('doc-view').textContent).toContain('1');

    fireEvent.click(getByTestId('doc-view-back'));
    expect(queryByTestId('doc-view')).toBeNull();
    expect(container.querySelector('[data-testid="skeleton-sprint"]')).toBeTruthy();
  });
});

describe('header-note bound (rule 2)', () => {
  it('shows six lines and a link to all where there are more', () => {
    seedLeaf({
      headerNotes: Array.from({ length: 7 }, (_, index) => ({
        label: `Note ${index + 1}`,
        value: `Value ${index + 1}.`,
      })),
    });
    const { getByTestId, getByText, queryByTestId, queryByText } = renderLeaf();

    expect(getByText('Value 6.')).toBeTruthy();
    // six in the head, as facts or as lines; the seventh stands in the view only
    expect(queryByText('Value 7.')).toBeNull();
    expect(getByTestId('header-notes-all-link').textContent).toContain('all 7 header notes');

    fireEvent.click(getByTestId('header-notes-all-link'));
    expect(getByTestId('doc-view-title').textContent).toBe('Header notes');
    expect(getByText('Value 7.')).toBeTruthy();

    fireEvent.click(getByTestId('doc-view-back'));
    expect(queryByTestId('doc-view')).toBeNull();
  });
});

describe('proposed code titles (rule 5)', () => {
  it('lists titles only and opens each example in a view of its own', () => {
    seedLeaf({
      codeExamples: [
        {
          id: 'E1',
          title: 'First shape',
          distinctChange: 'The change.',
          why: 'The reason.',
          language: 'tsx',
          snippet: 'const first = 1;',
        },
        {
          id: 'E2',
          title: 'Second shape',
          distinctChange: 'Another change.',
          why: 'Another reason.',
          language: 'tsx',
          snippet: 'const second = 2;',
        },
      ],
    });
    const { getByTestId, getByText, queryByText, queryByTestId } = renderLeaf();

    expect(getByText('E1 — First shape')).toBeTruthy();
    expect(getByText('E2 — Second shape')).toBeTruthy();
    expect(queryByText('const first = 1;')).toBeNull();

    fireEvent.click(getByTestId('code-open-E1'));
    expect(getByTestId('doc-view-title').textContent).toBe('E1 — First shape');
    expect(getByText('const first = 1;')).toBeTruthy();

    fireEvent.click(getByTestId('doc-view-back'));
    expect(queryByTestId('doc-view')).toBeNull();
    expect(queryByText('const first = 1;')).toBeNull();
  });
});

describe('further-notes lines (rule 7)', () => {
  it('shows heading and size, opens the section, back returns', () => {
    seedLeaf({
      sections: [{ kind: 'freeform', heading: 'Extra', body: 'Extra body text.' }],
    });
    const { getByTestId, getByText, queryByTestId } = renderLeaf();

    expect(getByTestId('further-size-0').textContent).toContain('characters');
    fireEvent.click(getByTestId('further-open-0'));
    expect(getByTestId('doc-view-title').textContent).toBe('Extra');
    expect(getByText('Extra body text.')).toBeTruthy();

    fireEvent.click(getByTestId('doc-view-back'));
    expect(queryByTestId('doc-view')).toBeNull();
  });
});

describe('completed group view (rules 3-4)', () => {
  it('counts completed tasks behind a link and lists them in the view', () => {
    const master = taskDoc({
      id: 'M',
      kind: 'master',
      docPath: MASTER,
      title: 'Master',
      form: 'master',
      subTasks: [
        { number: 'L1', name: 'Done one', file: '01_one.md', status: 'Completed', scope: '' },
        { number: 'L2', name: 'Done two', file: '02_two.md', status: 'Completed', scope: '' },
        { number: 'L3', name: 'Waiting', file: '03_three.md', status: 'planning', scope: '' },
      ],
    });
    seedTaskDocuments([master]);
    const { getByTestId, getByText, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${MASTER}`} />,
    );

    expect(getByTestId('group-done-link').textContent).toContain('2 completed tasks');
    fireEvent.click(getByTestId('group-done-link'));
    expect(getByTestId('doc-view-title').textContent).toBe('Completed tasks');
    expect(cells(getByText('Done one').parentElement)).toEqual(['L1', 'Done one']);
    expect(cells(getByText('Done two').parentElement)).toEqual(['L2', 'Done two']);

    fireEvent.click(getByTestId('doc-view-back'));
    expect(queryByTestId('doc-view')).toBeNull();
  });
});

describe('review findings (rule 5)', () => {
  it('shows the round and the open findings with a link to the whole round', () => {
    seedLeaf({
      reviewState: {
        round: 4,
        pending: false,
        additionalRounds: 0,
        findings: [
          { findingId: 'F1', description: 'Still open.', open: true, descriptionTruncated: false },
          {
            findingId: 'F2',
            description: 'Already closed.',
            open: false,
            descriptionTruncated: false,
          },
        ],
        totalFindings: 2,
        openFindings: 1,
      },
    });
    const { getByTestId, getByText, queryByTestId } = renderLeaf();

    expect(getByTestId('review-line').textContent).toContain('Round 4');
    expect(getByTestId('review-open-count').textContent).toContain('1 of 2 findings open');
    expect(getByTestId('findings-all-link').textContent).toContain('all 2 findings');

    fireEvent.click(getByTestId('findings-all-link'));
    expect(getByTestId('doc-view-title').textContent).toBe('Review findings');
    expect(getByTestId('finding-F1').textContent).toContain('open');
    expect(getByTestId('finding-F2').textContent).toContain('closed');

    fireEvent.click(getByTestId('doc-view-back'));
    expect(queryByTestId('doc-view')).toBeNull();
    expect(getByText('Still open.')).toBeTruthy();
  });
});

describe('step notes and the next mark (rule 5)', () => {
  it('marks the first step that is not done and opens its note in place', () => {
    seedLeaf({
      steps: [
        {
          id: 'S1',
          title: 'Done step',
          status: 'done',
          outcome: 'The deliverable.',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
        {
          id: 'S2',
          title: 'Open step',
          status: 'pending',
          note: 'A long note about the open step that opens in place when asked by the reader.',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
      ],
      stepsDone: 1,
      stepsTotal: 2,
    });
    const { getByTestId, queryByTestId } = renderLeaf();

    expect(queryByTestId('step-next-S1')).toBeNull();
    expect(getByTestId('step-next-S2')).toBeTruthy();
    expect(getByTestId('step-note-S1').textContent).toContain('The deliverable.');
    expect(getByTestId('step-note-S2').textContent).toContain('A long note');
  });
});

describe('own-view scroll owner (L127-C-F03)', () => {
  const tenRequirements = Array.from({ length: 10 }, (_, index) => `Requirement ${index + 1}.`);

  it('saves and restores the detail-panel offset, not the window', async () => {
    seedLeaf({ requirements: tenRequirements });
    const { getByTestId, queryByTestId } = renderLeaf();
    const panel = getByTestId('detail-panel');
    Object.defineProperty(panel, 'scrollHeight', { value: 600, configurable: true });
    Object.defineProperty(panel, 'clientHeight', { value: 298, configurable: true });
    const scrollTo = vi.fn();
    (panel as unknown as { scrollTo: (...args: Array<number>) => void }).scrollTo = scrollTo;
    panel.scrollTop = 302;

    fireEvent.click(getByTestId('requirements-all-link'));
    expect(getByTestId('doc-view')).toBeTruthy();
    expect(scrollTo).toHaveBeenCalledWith(0, 0);

    fireEvent.click(getByTestId('doc-view-back'));
    expect(queryByTestId('doc-view')).toBeNull();
    await vi.waitFor(() => expect(scrollTo).toHaveBeenCalledWith(0, 302));
  });

  it('keeps window behavior when no panel scrolls', () => {
    seedLeaf({ requirements: tenRequirements });
    const { getByTestId } = renderLeaf();
    const realScrollTo = window.scrollTo;
    const scrollTo = vi.fn();
    window.scrollTo = scrollTo as typeof window.scrollTo;
    try {
      fireEvent.click(getByTestId('requirements-all-link'));
      expect(getByTestId('doc-view')).toBeTruthy();
      expect(scrollTo).toHaveBeenCalledWith(0, 0);
    } finally {
      window.scrollTo = realScrollTo;
    }
  });
});

describe('the way back by the Escape key', () => {
  const tenRequirements = Array.from({ length: 10 }, (_, index) => `Requirement ${index + 1}.`);

  it('closes the open view and returns to the page', () => {
    seedLeaf({ requirements: tenRequirements });
    const { container, getByTestId, queryByTestId } = renderLeaf();
    fireEvent.click(getByTestId('requirements-all-link'));
    expect(getByTestId('doc-view')).toBeTruthy();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(queryByTestId('doc-view')).toBeNull();
    expect(container.querySelector('[data-testid="skeleton-task"]')).toBeTruthy();
  });

  it('leaves the page alone when no view is open, and another key leaves the view open', () => {
    seedLeaf({ requirements: tenRequirements });
    const { getByTestId, queryByTestId } = renderLeaf();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(queryByTestId('doc-view')).toBeNull();

    fireEvent.click(getByTestId('requirements-all-link'));
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(getByTestId('doc-view')).toBeTruthy();
  });

  it('listens for the key while a view is open: once per opening, not per render', () => {
    seedLeaf({ requirements: tenRequirements });
    const added = vi.spyOn(window, 'addEventListener');
    const removed = vi.spyOn(window, 'removeEventListener');
    const keydowns = (spy: { mock: { calls: unknown[][] } }): number =>
      spy.mock.calls.filter(([type]) => type === 'keydown').length;
    const { getByTestId } = renderLeaf();
    const addedBefore = keydowns(added);
    const removedBefore = keydowns(removed);

    fireEvent.click(getByTestId('requirements-all-link'));
    expect(keydowns(added)).toBe(addedBefore + 1);

    // the document arrives anew and the page draws again while the view stays
    // open: the one listener stays
    act(() => void seedLeaf({ requirements: [...tenRequirements, 'Requirement 11.'] }));
    expect(getByTestId('doc-view').textContent).toContain('Requirement 11.');
    act(() => void seedLeaf({ requirements: [...tenRequirements, 'Requirement 12.'] }));
    expect(getByTestId('doc-view').textContent).toContain('Requirement 12.');
    expect(keydowns(added)).toBe(addedBefore + 1);
    expect(keydowns(removed)).toBe(removedBefore);

    // closing takes it away, and the next opening listens again
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(keydowns(removed)).toBe(removedBefore + 1);
    fireEvent.click(getByTestId('requirements-all-link'));
    expect(keydowns(added)).toBe(addedBefore + 2);
  });
});
