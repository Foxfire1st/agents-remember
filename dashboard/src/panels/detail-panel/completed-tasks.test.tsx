// MIK-R128 evidence 13: time-first master completed list and latest-five preview.
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { GroupViewBody, MasterTasksBlock, fullOrderPositions } from './task-groups';
import { taskDoc } from './test-utils';
import type { TaskDocNode, SubTaskRow } from '../../types/projection';
import { completionTime, completedEntries } from './completedTasksModel';

const MASTER = '/tasks/repo-a/completed/task.json';
function dataset(numbers = ['L153', 'L46', 'L2', 'L9', 'L12', 'L80', 'L7']) {
  const refs: SubTaskRow[] = numbers.map((number) => ({
    number,
    name: `Task ${number}`,
    file: `${number}.md`,
    status: 'Completed',
    scope: '',
  }));
  const docs = numbers.map((number, index) =>
    taskDoc({
      kind: 'subTask',
      docPath: `/tasks/repo-a/completed/${number}.json`,
      id: number,
      title: `Task ${number}`,
      status: 'Completed',
      completedAt:
        index === 2 ? undefined : `2026-10-${String(index + 1).padStart(2, '0')}T12:00:00Z`,
      decisions:
        index === 2
          ? [
              {
                at: '2026-10-09T15:12:00Z',
                decision: 'Finalize task lifecycle.',
                rationale: '',
                product_record: true,
              },
              {
                at: 'invalid',
                decision: 'Finalize task lifecycle.',
                rationale: '',
                product_record: true,
              },
              {
                at: '2026-10-01T00:00:00Z',
                decision: 'Finalize task lifecycle.',
                rationale: '',
                product_record: true,
              },
            ]
          : [],
    }),
  );
  // Unknown completion time remains at the end, not date of creation.
  if (docs[3]) {
    docs[3].completedAt = undefined;
  }
  return { refs, docs };
}
function viewBody(refs: SubTaskRow[], docs: TaskDocNode[], parentId: string | null = 'M') {
  return (
    <GroupViewBody
      refs={refs}
      discarded={undefined}
      positions={fullOrderPositions(refs)}
      handlers={{ sliceDocs: docs, onOpen: vi.fn(), onJump: vi.fn() }}
      parentId={parentId ?? undefined}
    />
  );
}
function rowNumbers(root: HTMLElement): string[] {
  return [...root.querySelectorAll('[data-testid^="group-row-"]')].map(
    (row) => row.querySelector('[data-completed-number]')?.textContent ?? '',
  );
}

afterEach(cleanup);

describe('legacy completion time controls', () => {
  const finalizer = (at: string, decision = 'Finalize task lifecycle.') => ({
    at,
    decision,
    rationale: '',
    product_record: true,
  });
  it.each([
    [finalizer('invalid'), finalizer('')],
    [finalizer('1969-12-31T23:59:59Z'), finalizer('invalid')],
    [finalizer('2026-10-09T15:12:00Z'), finalizer('2026-10-01T00:00:00Z')],
  ])('uses the latest valid exact record independently of storage order: %j', (...decisions) => {
    const doc = taskDoc({ kind: 'subTask', docPath: '/t/old.json', decisions });
    const valid = decisions.map((item) => item.at).filter((at) => Number.isFinite(Date.parse(at)));
    const expected = valid.sort((a, b) => Date.parse(b) - Date.parse(a))[0];
    const before = JSON.stringify(doc);
    expect(completionTime(doc)).toBe(expected);
    expect(completionTime({ ...doc, decisions: [...decisions].reverse() })).toBe(expected);
    expect(completionTime({ ...doc, completedAt: '2026-10-02T00:00:00Z' })).toBe(
      '2026-10-02T00:00:00Z',
    );
    expect(JSON.stringify(doc)).toBe(before);
  });
  it('does not infer a time from similar prose, creation, or absent documents', () => {
    const doc = taskDoc({
      kind: 'subTask',
      docPath: '/t/old.json',
      decisions: [finalizer('2026-10-09T00:00:00Z', 'Finalize task lifecycle')],
    });
    expect(completionTime(doc)).toBeUndefined();
    expect(completionTime(undefined)).toBeUndefined();
  });
});

describe('master completed tasks', () => {
  it('opens newest first, reads a legacy landing record and keeps unknown last in both time directions; toggles numeric order', () => {
    const { refs, docs } = dataset();
    const view = render(viewBody(refs, docs));
    const table = view.getByTestId('group-table');
    expect(rowNumbers(table)).toEqual(['L2', 'L7', 'L80', 'L12', 'L46', 'L153', 'L9']);
    expect(within(view.getByTestId('group-row-3')).getByText('2026-10-09 15:12')).toBeTruthy();
    const number = view.getByRole('button', { name: /number/i });
    fireEvent.click(number);
    expect(rowNumbers(table)).toEqual(['L2', 'L7', 'L9', 'L12', 'L46', 'L80', 'L153']);
    expect(number.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
    fireEvent.click(number);
    expect(rowNumbers(table)).toEqual(['L153', 'L80', 'L46', 'L12', 'L9', 'L7', 'L2']);
    expect(number.closest('th')?.getAttribute('aria-sort')).toBe('descending');
    const time = view.getByRole('button', { name: /completed/i });
    fireEvent.click(time);
    expect(rowNumbers(table)[0]).toBe('L2');
    expect(time.closest('th')?.getAttribute('aria-sort')).toBe('descending');
    fireEvent.click(time);
    expect(rowNumbers(table)).toEqual(['L153', 'L46', 'L12', 'L80', 'L7', 'L2', 'L9']);
    expect(time.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
    // Sorting is ephemeral: opening another instance restores newest first.
    view.unmount();
    const reopened = render(viewBody(refs, docs));
    expect(rowNumbers(reopened.getByTestId('group-table'))[0]).toBe('L2');
  });

  it.each([7, 3, 0])('previews newest five above the link, with %i completed tasks', (count) => {
    const { refs, docs } = dataset(['L153', 'L46', 'L2', 'L9', 'L12', 'L80', 'L7'].slice(0, count));
    const openView = vi.fn();
    const doc = taskDoc({ kind: 'master', id: 'M', docPath: MASTER, subTasks: refs });
    const view = render(
      <MasterTasksBlock
        doc={doc}
        sliceDocs={docs}
        onOpen={vi.fn()}
        onJump={vi.fn()}
        openView={openView}
      />,
    );
    if (count === 0) {
      expect(view.queryByTestId('completed-preview')).toBeNull();
      expect(view.queryByTestId('group-done-link')).toBeNull();
      return;
    }
    const preview = view.getByTestId('completed-preview');
    expect(rowNumbers(preview)).toEqual(
      count === 7 ? ['L2', 'L7', 'L80', 'L12', 'L46'] : ['L2', 'L46', 'L153'],
    );
    expect(preview.querySelectorAll('time')).toHaveLength(Math.min(count, 5));
    expect(
      preview.compareDocumentPosition(view.getByTestId('group-done-link')) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
    fireEvent.click(view.getByTestId('group-done-link'));
    expect(openView).toHaveBeenCalledWith({ kind: 'group', status: 'done' });
  });

  it('leaves abandoned master rows and sprint rows in authored order without sort controls', () => {
    const { refs, docs } = dataset(['L153', 'L46']);
    const gone = refs.map((ref) => ({ ...ref, status: 'abandoned' }));
    const view = render(viewBody(gone, docs));
    expect(view.queryByRole('button', { name: /completed/i })).toBeNull();
    expect(
      [...view.getByTestId('group-table').children].map((row) => row.getAttribute('data-testid')),
    ).toEqual(['group-row-1', 'group-row-2']);
    view.unmount();
    const sprint = render(viewBody(refs, docs, null));
    expect(sprint.queryByRole('button', { name: /completed/i })).toBeNull();
    expect(
      [...sprint.getByTestId('group-table').children].map((row) => row.getAttribute('data-testid')),
    ).toEqual(['group-row-1', 'group-row-2']);
  });
});

describe('composed completed lookup operation counts', () => {
  it.each([100, 300])('indexes preparation once for %i rows', (size) => {
    const { refs, docs } = dataset(Array.from({ length: size }, (_, i) => `L${i}`));
    let reads = 0;
    const counted = docs.map((doc) => ({
      ...doc,
      get docPath() {
        reads++;
        return doc.docPath;
      },
    }));
    const positions = fullOrderPositions(refs);
    const control = completedEntries(refs, docs, 'M', positions);
    expect(completedEntries(refs, counted, 'M', positions)).toEqual(control);
    console.info(
      JSON.stringify({ path: 'completedEntries', size, docPathReads: reads, output: control }),
    );
    expect(reads).toBeLessThanOrEqual(size * 2);
  });

  it.each([100, 300].flatMap((size) => [false, true].map((preview) => ({ size, preview }))))(
    'keeps complete output and navigation linear at $size rows, preview=$preview',
    ({ size, preview }) => {
      const { refs, docs } = dataset(Array.from({ length: size }, (_, i) => `L${i}`));
      docs.push({
        ...docs[0],
        docPath: '/different/L0.json',
        title: 'Must not win duplicate slug',
      });
      refs.push({
        number: 'missing',
        name: 'Missing row',
        file: 'folder/L0.md',
        status: 'Completed',
        scope: '',
      });
      refs.push({ number: 'empty', name: 'No file row', file: '', status: 'Completed', scope: '' });
      let reads = 0;
      const counted = docs.map((doc) => ({
        ...doc,
        get docPath() {
          reads++;
          return doc.docPath;
        },
      }));
      const onOpen = vi.fn();
      const draw = (pool: TaskDocNode[]) =>
        preview ? (
          <MasterTasksBlock
            doc={taskDoc({ kind: 'master', docPath: MASTER, id: 'M', subTasks: refs })}
            sliceDocs={pool}
            onOpen={onOpen}
            onJump={vi.fn()}
            openView={vi.fn()}
          />
        ) : (
          <GroupViewBody
            refs={refs}
            discarded={undefined}
            positions={fullOrderPositions(refs)}
            handlers={{ sliceDocs: pool, onOpen, onJump: vi.fn() }}
            parentId="M"
          />
        );
      const control = render(draw(docs));
      const html = control.container.innerHTML;
      control.unmount();
      const measured = render(draw(counted));
      expect(measured.container.innerHTML).toBe(html);
      const table = measured.getByTestId(preview ? 'completed-preview' : 'group-table');
      expect(rowNumbers(table)).toHaveLength(preview ? 5 : size + 2);
      for (const button of table.querySelectorAll<HTMLButtonElement>(
        'button[data-testid^="subtask-open-"]',
      )) {
        const position = Number(button.dataset.testid?.split('-').pop());
        fireEvent.click(button);
        expect(onOpen).toHaveBeenLastCalledWith(`L${position - 1}`);
      }
      if (!preview) {
        expect(measured.getByTestId('group-row-1').textContent).toContain('Task L0');
        expect(measured.getByTestId(`group-row-${size + 1}`).querySelector('button')).toBeNull();
        expect(measured.getByTestId(`group-row-${size + 2}`).querySelector('button')).toBeNull();
        fireEvent.click(measured.getByRole('button', { name: /number/i }));
        fireEvent.click(measured.getByRole('button', { name: /completed/i }));
      }
      console.info(
        JSON.stringify({
          path: preview ? 'preview' : 'whole-list',
          size,
          docPathReads: reads,
          output: html,
        }),
      );
      expect(reads).toBeLessThanOrEqual(size * 4 + 30);
    },
  );
});
