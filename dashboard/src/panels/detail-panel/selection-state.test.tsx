// MIK-R127 rules 2 and 6 with D219: what a reader opened on one document belongs
// to that document. The next document selected starts closed (its objective held
// to its first lines, its notes on one line, its page and not a view of its own),
// while the same document keeps what is open when its data arrive again.
// All documents here are made up.
import { fireEvent, render, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DetailPanel } from './DetailPanel';
import type { TaskDocNode } from '../../types/projection';
import { seedTaskDocuments, stubNotes, taskDoc } from './test-utils';

const REPO = 'agents-remember';
const LONG_NOTE =
  'The leaf owns one module and one requirement. A change that needs a second module is a second leaf, and the order of the two is written into the master before either of them starts.';

// An objective tall enough to be held to its first lines.
const objective = (name: string): string =>
  Array.from({ length: 6 }, (_, index) => `- ${name} point ${index + 1}.`).join('\n');

type Form = 'task' | 'master' | 'sprint';

const pathOf = (form: Form, name: string): string =>
  form === 'task'
    ? `/tasks/${REPO}/select-master/0${name === 'first' ? 1 : 2}_${name}.json`
    : `/tasks/${REPO}/select-${form}-${name}/task.json`;

function docOf(form: Form, name: string, over: Partial<TaskDocNode> = {}): TaskDocNode {
  return taskDoc({
    id: `SEL-${form}-${name}`,
    lifecycleId: undefined,
    kind: form === 'task' ? 'subTask' : 'master',
    form,
    repository: REPO,
    docPath: pathOf(form, name),
    title: `The ${name} document`,
    objective: objective(name),
    orchestrates: form === 'sprint' ? ['select-master'] : [],
    ...over,
  });
}

function seedPair(form: Form, over: Partial<TaskDocNode> = {}): void {
  const parent =
    form === 'task'
      ? [
          taskDoc({
            id: 'SEL-M',
            lifecycleId: undefined,
            kind: 'master',
            repository: REPO,
            docPath: `/tasks/${REPO}/select-master/task.json`,
          }),
        ]
      : [];
  seedTaskDocuments([...parent, docOf(form, 'first', over), docOf(form, 'second', over)]);
  stubNotes([], '');
}

const select = (form: Form, name: string): string => `taskdoc:${pathOf(form, name)}`;

describe('what is open belongs to the selected document', () => {
  it.each(['task', 'master', 'sprint'] as const)(
    'starts the next %s with its objective held to its first lines',
    async (form) => {
      seedPair(form);
      const view = render(<DetailPanel selectedId={select(form, 'first')} />);
      expect(view.getByTestId('objective-text').getAttribute('data-open')).toBe('false');
      fireEvent.click(view.getByTestId('objective-toggle'));
      expect(view.getByTestId('objective-text').getAttribute('data-open')).toBe('true');

      view.rerender(<DetailPanel selectedId={select(form, 'second')} />);
      await waitFor(() =>
        expect(view.getByTestId('doc-title').textContent).toBe('The second document'),
      );
      expect(view.getByTestId('objective-text').getAttribute('data-open')).toBe('false');
      expect(view.getByTestId('objective-toggle').getAttribute('aria-expanded')).toBe('false');
    },
  );

  it('closes an opened note line and leaves a view of its own when another document is selected', async () => {
    seedPair('task', {
      statusNote: LONG_NOTE,
      sections: [{ kind: 'freeform', heading: 'Boundary cases', body: 'A made-up section.' }],
    });
    const view = render(<DetailPanel selectedId={select('task', 'first')} />);
    fireEvent.click(view.getByTestId('status-note-toggle'));
    expect(view.getByTestId('status-note-toggle').getAttribute('aria-expanded')).toBe('true');

    view.rerender(<DetailPanel selectedId={select('task', 'second')} />);
    await waitFor(() =>
      expect(view.getByTestId('doc-title').textContent).toBe('The second document'),
    );
    expect(view.getByTestId('status-note-toggle').getAttribute('aria-expanded')).toBe('false');

    // a view of its own is the first document's too: the next one shows its page
    fireEvent.click(view.getByTestId('further-open-0'));
    expect(view.getByTestId('doc-view-title').textContent).toBe('Boundary cases');
    view.rerender(<DetailPanel selectedId={select('task', 'first')} />);
    await waitFor(() =>
      expect(view.getByTestId('doc-title').textContent).toBe('The first document'),
    );
    expect(view.queryByTestId('doc-view')).toBeNull();
    expect(view.getByTestId('skeleton-task')).toBeTruthy();
  });

  it('keeps the objective open when the same document arrives again', () => {
    seedPair('task');
    const view = render(<DetailPanel selectedId={select('task', 'first')} />);
    fireEvent.click(view.getByTestId('objective-toggle'));

    // the same document, read anew: another object with another status
    seedPair('task', { status: 'inProgress' });
    view.rerender(<DetailPanel selectedId={select('task', 'first')} />);
    expect(view.getByTestId('doc-status').textContent).toContain('in work');
    expect(view.getByTestId('objective-text').getAttribute('data-open')).toBe('true');
  });
});
