import { act, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { taskEntrySelectionKey } from '../../data/taskReference';
import { LifecycleList } from './LifecycleList';
import {
  hierarchy,
  installListCleanup,
  rowOf,
  seed,
  task,
  taskSummary,
  taskMap,
} from './test-utils';
import { TaskNowBlock } from '../detail-panel/task-groups';
import { ChildProgress } from '../detail-panel/indexRows';

installListCleanup();

function needEntries() {
  const master = task('NEED', {
    title: 'Need Master',
    summary: taskSummary({ kind: 'master', form: 'master' }),
    reference: { repository: 'repo', path: 'master/task.json' },
    activity: 'works',
    counts: { planned: 1, started: 2, completed: 1, aborted: 1, works: 1, paused: 1, unknown: 0 },
  });
  const children = [
    task('NEED-L1', { title: 'First waits', stage: 'planned', activity: undefined }),
    task('NEED-L2', { title: 'Second is done', stage: 'completed', activity: undefined }),
    task('NEED-L3', { title: 'Third works', activity: 'works' }),
    task('NEED-L4', { title: 'Fourth was dropped', stage: 'aborted', activity: undefined }),
    task('NEED-L5', { title: 'Fifth is paused', activity: 'paused' }),
  ];
  master.children = children.map((child) => child.key);
  for (const child of children) child.parent = master.key;
  return [master, ...children];
}

it('orders the tasks of a master by need and keeps the completed behind one line', () => {
  seed(needEntries());
  const onSelect = vi.fn();
  const ui = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
  const rows = () => [...ui.container.querySelectorAll('[role="option"]')]
    .map((row) => row.textContent);
  expect(rows()).toEqual([
    '▾Need Masterworks1 works · 1 paused · 1/4', 'L3Third worksworks',
    'L5Fifth is pausedpaused', 'L1First waitsplanned',
    '▸1 completed, 1 aborted',
  ]);
  expect(ui.getByText('Tasks · 6')).toBeTruthy();
  fireEvent.click(ui.getByText('1 completed, 1 aborted'));
  expect(rows().slice(4)).toEqual([
    '▾1 completed, 1 aborted', 'L2Second is donecompleted', 'L4Fourth was droppedaborted',
  ]);
  expect(onSelect).not.toHaveBeenCalled();
});

it('groups BY PHASE in the order of the work, with plain words and whole ids', () => {
  const entries = needEntries();
  const paused = task('PAUSED', { activity: 'paused' });
  const unknown = task('UNKNOWN', { activity: 'unknown' });
  seed([...entries.reverse(), unknown, paused]);
  const ui = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
  fireEvent.click(ui.getByText('BY PHASE'));
  expect([...ui.container.querySelectorAll('header')].map((node) => node.textContent)).toEqual([
    'works2', 'paused2', 'started · host state unknown1', 'planned1', 'completed1', 'aborted1',
  ]);
  const rows = [...ui.container.querySelectorAll('[role="option"]')].map((node) => node.textContent);
  expect(rows).toEqual([
    'NEED-L3Third worksrepoworks', 'Need Masterrepoworks1 works · 1 paused · 1/4',
    'NEED-L5Fifth is pausedrepopaused', 'PAUSEDPAUSEDrepopaused',
    'UNKNOWNUNKNOWNrepostarted · host state unknown', 'NEED-L1First waitsrepoplanned',
    'NEED-L2Second is donerepocompleted', 'NEED-L4Fourth was droppedrepoaborted',
  ]);
});

it('orders BY REPO groups by name independently of arrival and activity', () => {
  seed([
    task('Z', { activity: 'works', reference: { repository: 'zeta', path: 'm/z.json' } }),
    task('A', { activity: 'paused', reference: { repository: 'alpha', path: 'm/a.json' } }),
  ]);
  const ui = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
  expect([...ui.container.querySelectorAll('header')].map((node) => node.textContent))
    .toEqual(['alpha1', 'zeta1']);
});
it('uses served hierarchy and remembers closed parents; a chosen descendant holds its ancestors open', async () => {
  const { sprint, master, leaf, entries } = hierarchy();
  const onSelect = vi.fn();
  seed(entries);
  const ui = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
  const sprintRow = rowOf(ui.getByText(sprint.title));
  const masterRow = rowOf(ui.getByText(master.title));
  const leafRow = rowOf(ui.getByText(leaf.title));
  expect(sprintRow.dataset.tier).toBe('orchestration');
  expect(masterRow.dataset.tier).toBe('management');
  expect(leafRow.dataset.tier).toBeUndefined();
  expect(ui.getByText(sprint.title).className).toContain('fw_600');
  expect(ui.getByText(master.title).className).toContain('fw_600');
  expect(ui.getByText(leaf.title).className).not.toContain('fw_600');
  const indent = (row: HTMLElement) =>
    Number.parseFloat((row.firstElementChild as HTMLElement).style.paddingLeft);
  const step = indent(masterRow) - indent(sprintRow);
  expect(step).toBeGreaterThan(0);
  expect(indent(leafRow) - indent(masterRow)).toBeCloseTo(step, 3);
  expect(leafRow.querySelector('button')).toBeNull();
  const caret = ui.getByRole('button', { name: `Collapse ${sprint.title} tasks` });
  expect(caret.getAttribute('aria-expanded')).toBe('true');
  expect(caret.tabIndex).toBe(0);
  fireEvent.keyDown(caret, { key: 'Enter' });
  expect(onSelect).not.toHaveBeenCalled();
  expect(rowOf(ui.getByText(leaf.title)).dataset.parentKey).toBe(taskEntrySelectionKey(master.key));
  expect(rowOf(ui.getByText(leaf.title)).dataset.depth).toBe('2');
  fireEvent.click(ui.getByRole('button', { name: `Collapse ${sprint.title} tasks` }));
  expect(ui.queryByText(leaf.title)).toBeNull();
  expect(onSelect).not.toHaveBeenCalled();
  ui.rerender(<LifecycleList selectedId={taskEntrySelectionKey(leaf.key)} onSelect={vi.fn()} />);
  await waitFor(() => expect(ui.getByText(leaf.title)).toBeTruthy());
  expect(rowOf(ui.getByText(sprint.title)).dataset.held).toBe('');
});
it('puts a completed master behind its fold regardless of a working descendant and opens the fold in place', () => {
  const { sprint, master, entries } = hierarchy();
  master.stage = 'completed';
  master.activity = undefined;
  sprint.counts.started = 0;
  sprint.counts.completed = 1;
  seed(entries);
  const ui = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
  expect(ui.queryByText(master.title)).toBeNull();
  fireEvent.click(ui.getByText('1 completed'));
  expect(ui.getByText(master.title)).toBeTruthy();
  fireEvent.click(ui.getByText('BY PHASE'));
  expect(ui.getByText(master.title)).toBeTruthy();
});

it('opens and closes the focused parent with plain arrows and leaves modified arrows alone', () => {
  const { sprint, leaf, entries } = hierarchy();
  seed(entries);
  const ui = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
  const row = rowOf(ui.getByText(sprint.title));
  act(() => row.focus());
  fireEvent.keyDown(row, { key: 'ArrowLeft' });
  expect(ui.queryByText(leaf.title)).toBeNull();
  for (const modifier of ['altKey', 'ctrlKey', 'metaKey', 'shiftKey']) {
    expect(fireEvent.keyDown(row, { key: 'ArrowRight', [modifier]: true })).toBe(true);
    expect(ui.queryByText(leaf.title)).toBeNull();
  }
  fireEvent.keyDown(row, { key: 'ArrowRight' });
  expect(ui.getByText(leaf.title)).toBeTruthy();
  ui.rerender(<LifecycleList selectedId={taskEntrySelectionKey(leaf.key)} onSelect={vi.fn()} />);
  fireEvent.keyDown(row, { key: 'ArrowLeft' });
  expect(ui.getByText(leaf.title)).toBeTruthy();
});

it('remembers independent parent disclosures across a remount and leaves phase grouping flat', () => {
  const { sprint, master, leaf, entries } = hierarchy();
  seed(entries);
  const onSelect = vi.fn();
  const first = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
  fireEvent.click(first.getByRole('button', { name: `Collapse ${master.title} tasks` }));
  fireEvent.click(first.getByRole('button', { name: `Collapse ${sprint.title} tasks` }));
  expect(JSON.parse(localStorage.getItem('operations.tasks.collapsed.v1') ?? '[]')).toEqual([
    taskEntrySelectionKey(master.key),
    taskEntrySelectionKey(sprint.key),
  ]);
  first.unmount();
  const second = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
  fireEvent.click(second.getByRole('button', { name: `Expand ${sprint.title} tasks` }));
  expect(
    second
      .getByRole('button', { name: `Expand ${master.title} tasks` })
      .getAttribute('aria-expanded'),
  ).toBe('false');
  expect(second.queryByText(leaf.title)).toBeNull();
  fireEvent.click(second.getByText('BY PHASE'));
  expect(second.getByText(leaf.title, { selector: '[data-part="title"]' })).toBeTruthy();
  expect(second.queryByRole('button', { name: /^(Collapse|Expand) .* tasks$/ })).toBeNull();
  expect(second.getByText('Tasks · 3')).toBeTruthy();
  expect(onSelect).not.toHaveBeenCalled();
});

it('finds nested ids and standalone titles by typing what the row shows', async () => {
  const { sprint, entries } = hierarchy();
  const standalone = task('R', {
    title: 'Standalone planning task',
    stage: 'planned',
    activity: undefined,
  });
  seed([...entries, standalone]);
  for (const [typed, title] of [
    ['l1', 'M-L1'],
    ['stand', standalone.title],
  ]) {
    const ui = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    act(() => rowOf(ui.getByText(sprint.title)).focus());
    await userEvent.keyboard(typed);
    expect(document.activeElement?.querySelector('[data-part="title"]')?.textContent).toBe(title);
    ui.unmount();
  }
});

it('keeps flat master runs without orchestration tiers, and long titles shrink with full hover context', () => {
  const { master, leaf } = hierarchy();
  master.parent = undefined;
  leaf.title =
    'Operations task reader row title that is intentionally long enough to require ellipsis in the left rail';
  leaf.stepsDone = 1;
  leaf.stepsTotal = 4;
  leaf.summary = taskSummary({
    kind: 'subTask',
    form: 'task',
    currentStep: 'Constrain the row title',
  });
  seed([master, leaf]);
  const ui = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
  expect(ui.container.querySelector('[data-tier]')).toBeNull();
  expect(rowOf(ui.getByText(master.title)).dataset.depth).toBe('0');
  const title = ui.getByText(leaf.title);
  const row = rowOf(title);
  expect(row.dataset.depth).toBe('1');
  expect(row.className).toContain('min-w_0');
  expect(row.firstElementChild?.className).toContain('min-w_0');
  expect(title.className).toContain('flex_1_1_0');
  expect(title.className).toContain('tov_ellipsis');
  const word = row.querySelector('[data-testid="task-word"]');
  expect(word?.parentElement?.className).toContain('tov_ellipsis');
  expect(word?.parentElement?.className).toContain('max-w_15ch');
  const hover = row.firstElementChild?.getAttribute('title') ?? '';
  for (const text of [
    leaf.title,
    'State: paused',
    'Steps done: 1 of 4',
    'Current step: Constrain the row title',
    'Repo: repo',
  ])
    expect(hover).toContain(text);
});

it('names terminal disclosure state accessibly, excludes aborted tasks from progress and retains authored ties', () => {
  const { master, leaf } = hierarchy();
  master.parent = undefined;
  const done = task('M-L2', { stage: 'completed', activity: undefined, parent: master.key });
  const aborted = task('M-L3', { stage: 'aborted', activity: undefined, parent: master.key });
  const work = task('M-L4', { activity: 'works', parent: master.key });
  master.children = [done.key, leaf.key, aborted.key, work.key];
  master.counts = {
    planned: 0,
    started: 2,
    completed: 1,
    aborted: 1,
    works: 1,
    paused: 1,
    unknown: 0,
  };
  seed([master, done, leaf, aborted, work]);
  const onSelect = vi.fn();
  const ui = render(
    <>
      <LifecycleList selectedId={null} onSelect={onSelect} />
      <div data-testid="page-counts">
        <TaskNowBlock
          entry={master}
          tasks={taskMap([master, done, leaf, aborted, work])}
          onJump={vi.fn()}
        />
        <ChildProgress entry={master} />
      </div>
    </>,
  );
  const titles = () =>
    [...ui.container.querySelectorAll('[data-part="title"]')].map((item) => item.textContent);
  expect(titles()).toEqual([master.title, work.title, leaf.title]);
  expect(rowOf(ui.getByText(master.title)).textContent).toContain('1/3');
  expect(ui.getByTestId('page-counts').textContent).toContain('1/3');
  expect(ui.getByTestId('now-split').textContent).toContain('1 completed · 1 aborted');
  expect(rowOf(ui.getByText(master.title)).firstElementChild?.getAttribute('title')).toContain(
    'Tasks completed: 1 of 3, 1 abandoned',
  );
  const line = rowOf(ui.getByText('1 completed, 1 aborted'));
  expect(line.getAttribute('aria-label')).toBe('1 completed, 1 aborted, hidden');
  fireEvent.click(ui.getByText('1 completed, 1 aborted'));
  expect(line.getAttribute('aria-label')).toBe('1 completed, 1 aborted, shown');
  expect(titles()).toEqual([master.title, work.title, leaf.title, done.title, aborted.title]);
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.click(ui.getByText('BY PHASE'));
  expect([...ui.getAllByTestId('task-word')].map((word) => word.textContent)).toEqual([
    'works',
    'paused',
    'paused',
    'completed',
    'aborted',
  ]);
});

it('shows the served child activity counts in a master row', () => {
  const { master, entries } = hierarchy();
  // Deliberately unlike the single child in this small tree: the row reads the
  // published counts rather than deriving another answer from visible rows.
  master.counts = {
    planned: 4,
    started: 9,
    completed: 2,
    aborted: 1,
    works: 6,
    paused: 2,
    unknown: 1,
  };
  seed(entries);
  const ui = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
  expect(
    rowOf(ui.getByText(master.title)).querySelector('[data-testid="child-activity"]')?.textContent,
  ).toBe('6 works · 2 paused · 1 unknown · ');
});
