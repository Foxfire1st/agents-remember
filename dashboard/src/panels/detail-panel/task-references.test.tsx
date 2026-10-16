// L127-C-F05: task references render through the server-resolved affordance
// (note buttons, requirement links), bounded to eight lines with a counted
// own-view link, under the one "References and reports" heading before Further
// notes, with the Notes line last. Master and sprint reference views resolve
// the same way instead of inert text.
import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { seedTaskEntries, stubNotes, taskEntry, taskSummary, taskReference } from './test-utils';

const REPO = 'agents-remember';
const MASTER = '260831_closeout-certification-reform';
const DOC_PATH = `${REPO}/${MASTER}/23_task-references.json`;
const PACKET = 'L127-R05-packet.md';

const requirement = {
  name: PACKET,
  path: PACKET,
  address: `requirements/${PACKET}`,
  size: 100,
  sha256: 'abc',
};

function seedReferenceTask() {
  const references = [
    `requirements/${PACKET} (canonical packet)`,
    'See notes/plan.md for the rollout plan',
    ...Array.from({ length: 8 }, (_, i) => `Background note ${i + 1}`),
  ];
  seedTaskEntries([
    taskEntry({
      id: '1',
      stage: 'started',
      stepsDone: 0,
      stepsTotal: 0,
      activity: 'works',
      reference: taskReference(DOC_PATH),
      title: 'Reference task',
      summary: taskSummary({
        objective: '',
        type: '',
        createdAt: '2026-06-20T09:00:00+00:00',
        form: 'task',
        kind: 'subTask',
        references,
        sections: [{ kind: 'freeform', heading: 'Context', body: 'Why these references.' }],
      }),
    }),
  ]);
  stubNotes(
    [
      { name: 'plan.md', path: 'plan.md', size: 10, language: 'markdown' },
      { name: 'top.md', path: 'top.md', size: 10, language: 'markdown' },
    ],
    'note body',
    [requirement],
  );
  return references;
}

function headingOrder(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('h3')).map((el) => el.textContent ?? '');
}

describe('task references (L127-C-F05)', () => {
  it('bounds the preview to eight lines with a counted all-link', async () => {
    seedReferenceTask();
    const { container, findByTestId } = render(<DetailPanel selectedId={`taskdoc:${DOC_PATH}`} />);

    await findByTestId('references-all-link');
    const list = container.querySelector('[data-testid="reference-rows"]');
    expect(list?.children).toHaveLength(8);
    expect(container.querySelector('[data-testid="references-all-link"]')?.textContent).toContain(
      'all 10 references',
    );
  });

  it('keeps the resolved-note affordance and requirement links in the preview', async () => {
    seedReferenceTask();
    const onOpenNotes = vi.fn();
    const view = render(
      <DetailPanel selectedId={`taskdoc:${DOC_PATH}`} onOpenNotes={onOpenNotes} />,
    );

    const noteButton = await view.findByTestId('note-ref-2');
    expect(noteButton.tagName.toLowerCase()).toBe('button');
    expect(noteButton.getAttribute('title')).toContain('open notes/plan.md');
    fireEvent.click(noteButton);
    expect(onOpenNotes).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'notes', path: 'plan.md' }),
    );

    const requirementButton = await view.findByTestId('requirement-ref-1');
    expect(requirementButton.tagName.toLowerCase()).toBe('button');
  });

  it('places the one References heading before Further notes with the Notes line last', async () => {
    seedReferenceTask();
    const { container, findByTestId } = render(<DetailPanel selectedId={`taskdoc:${DOC_PATH}`} />);

    await findByTestId('references-all-link');
    await findByTestId('notes-root-link');
    const heads = headingOrder(container);
    // one heading holds the references and the reports: no second References block
    expect(heads.filter((head) => /references/i.test(head))).toEqual(['References and reports']);
    expect(heads.indexOf('References and reports')).toBeLessThan(heads.indexOf('Further notes'));
    expect(heads.indexOf('Further notes')).toBeLessThan(heads.indexOf('Notes'));
    expect(heads[heads.length - 1]).toBe('Notes');
  });

  it('opens the whole resolved list one link away', async () => {
    seedReferenceTask();
    const view = render(<DetailPanel selectedId={`taskdoc:${DOC_PATH}`} />);

    fireEvent.click(await view.findByTestId('references-all-link'));
    expect(view.getByTestId('doc-view-title').textContent).toBe('References');
    expect(await view.findByTestId('note-ref-2')).toBeTruthy();
    expect(view.queryByTestId('references-all-link')).toBeNull();
  });
});

describe('master reference views (L127-C-F05)', () => {
  it('resolves note buttons in the master references view', async () => {
    const masterPath = `${REPO}/${MASTER}/task.json`;
    seedTaskEntries([
      taskEntry({
        id: '1',
        stage: 'started',
        stepsDone: 0,
        stepsTotal: 0,
        activity: 'works',
        reference: taskReference(masterPath),
        title: 'Reference master',
        summary: taskSummary({
          objective: '',
          type: '',
          createdAt: '2026-06-20T09:00:00+00:00',
          form: 'master',
          kind: 'master',
          references: ['See notes/plan.md for the rollout plan'],
        }),
      }),
    ]);
    stubNotes([{ name: 'plan.md', path: 'plan.md', size: 10, language: 'markdown' }], 'note body');
    const onOpenNotes = vi.fn();
    const view = render(
      <DetailPanel selectedId={`taskdoc:${masterPath}`} onOpenNotes={onOpenNotes} />,
    );

    fireEvent.click(await view.findByTestId('references-all-link'));
    expect(view.getByTestId('doc-view-title').textContent).toBe('References');
    const noteButton = await view.findByTestId('note-ref-1');
    expect(noteButton.tagName.toLowerCase()).toBe('button');
    fireEvent.click(noteButton);
    expect(onOpenNotes).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'notes', path: 'plan.md' }),
    );
  });
});
