import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { postAttentionDismiss } from '../data/actions';
import { dashboardStore } from '../data/store';
import { analytics, attentionItem, projection } from '../test/fixtures/wire';
import type { AttentionItem } from '../types/projection';
import { AttentionQueue } from './AttentionQueue';

vi.mock('../data/actions', () => ({ postAttentionDismiss: vi.fn() }));

const items: AttentionItem[] = [
  attentionItem({
    id: 'provider-down:cgc',
    kind: 'provider-down',
    severity: 'alarm',
    lane: 'repo',
    title: 'Provider cgc down',
    detail: 'stopped',
    itemClass: 'operational',
    dismissible: true,
  }),
  attentionItem({
    id: 'actionable-drift:repo:main',
    kind: 'actionable-drift',
    severity: 'warn',
    lane: 'repo',
    title: '1 actionable drift in repo',
    repoId: 'repo',
    itemClass: 'operational',
    dismissible: true,
  }),
  attentionItem({
    id: 'failed-setup:group',
    kind: 'failed-setup',
    severity: 'alarm',
    lane: 'worktree',
    title: 'Provider setup needs attention',
    itemClass: 'operational',
    dismissible: true,
  }),
  attentionItem({
    id: 'blocked-start:/worktrees/group',
    kind: 'blocked-start',
    severity: 'warn',
    lane: 'worktree',
    title: 'Worktree start blocked',
    detail: 'no exact ledger mapping for selected code base commit',
    itemClass: 'operational',
    dismissible: true,
  }),
];

// An action that names an agent and no task, served with the flag the v1 build published for a
// live cause: the page must not gate on it. One acknowledgement per occurrence (MIK-R200 rule 7)
// means its row carries the × and "Clear all" takes it whatever the flag says.
const permissionWait = attentionItem({
  id: 'permission-wait:agent-7',
  kind: 'permission-wait',
  severity: 'warn',
  lane: 'agent',
  title: 'Agent waits for permission',
  agentId: 'agent-7',
  agentRole: 'worker',
  itemClass: 'action',
  dismissible: false,
});

// An action that names a task: the row opens the task, and acknowledges once per occurrence.
const taskStalled = attentionItem({
  id: 'task-stalled:L191',
  kind: 'task-stalled',
  severity: 'alarm',
  lane: 'task',
  title: 'Leaf L191 is stalled',
  taskKey: 'L191',
  itemClass: 'action',
  dismissible: false,
});

// A notice: one acknowledgement per occurrence.
const taskLanded = attentionItem({
  id: 'task-landed:L190:occ-1',
  kind: 'task-landed',
  severity: 'info',
  lane: 'task',
  title: 'Leaf L190 landed',
  taskKey: 'L190',
  occurrenceId: 'occ-1',
  itemClass: 'notice',
  dismissible: true,
});

function seed(queue: AttentionItem[]) {
  dashboardStore
    .getState()
    .applySnapshot(projection({ analytics: analytics({ attentionQueue: queue }) }));
}

function renderOpen() {
  const view = render(<AttentionQueue />);
  fireEvent.click(view.getByTestId('attention-bell'));
  return view;
}

afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
  vi.clearAllMocks();
});

describe('the four remaining attention kinds', () => {
  it('keeps the blocked-start cause and severity visible without a session or task join', () => {
    seed([items[3]]);
    const view = renderOpen();
    expect(view.getByTestId('attn-item').textContent).toContain(
      'Worktree start blocked · no exact ledger mapping for selected code base commit',
    );
    expect(view.getByTestId('attn-item').textContent).toContain('worktree');
    expect(view.getByRole('img', { name: 'Severity: warn' })).toBeTruthy();
    expect(view.queryByTestId('attn-open')).toBeNull();
  });

  it.each(items)('dismisses $kind by its own control and hides it immediately', async (item) => {
    vi.mocked(postAttentionDismiss).mockResolvedValue('dismissed');
    seed([item]);
    const view = renderOpen();
    fireEvent.click(view.getByTestId('attn-dismiss'));
    expect(view.queryByText(item.title)).toBeNull();
    expect(view.getByTestId('attention-queue').textContent).toContain('nothing waiting');
    expect(view.getByTestId('attention-bell').getAttribute('aria-label')).toBe(
      'Attention: nothing waiting',
    );
    expect(view.getByTestId('attention-bell').textContent).toBe('');
    await waitFor(() =>
      expect(postAttentionDismiss).toHaveBeenCalledExactlyOnceWith({
        itemId: item.id,
        kind: item.kind,
      }),
    );
  });

  it('Clear all sends every listed item, including provider, setup and start failures', async () => {
    vi.mocked(postAttentionDismiss).mockResolvedValue('dismissed');
    seed(items);
    const view = renderOpen();
    fireEvent.click(view.getByTestId('attn-clear'));
    expect(view.queryAllByTestId('attn-item')).toHaveLength(0);
    await waitFor(() => expect(postAttentionDismiss).toHaveBeenCalledTimes(4));
    for (const item of items)
      expect(postAttentionDismiss).toHaveBeenCalledWith({ itemId: item.id, kind: item.kind });
  });

  it('restores an item and reports that its click did not hold when the write fails', async () => {
    vi.mocked(postAttentionDismiss).mockResolvedValue('error');
    seed([items[1]]);
    const view = renderOpen();
    fireEvent.click(view.getByTestId('attn-dismiss'));
    await waitFor(() => expect(view.getByText(items[1].title)).toBeTruthy());
    expect(view.getByRole('alert').textContent).toContain('Dismissal did not hold');
    vi.mocked(postAttentionDismiss).mockResolvedValue('dismissed');
    fireEvent.click(view.getByTestId('attn-dismiss'));
    await waitFor(() => expect(view.queryByRole('alert')).toBeNull());
    expect(view.queryByText(items[1].title)).toBeNull();
  });

  it('Clear all restores only failed writes and reports the failure', async () => {
    vi.mocked(postAttentionDismiss).mockImplementation(async (item) =>
      item.kind === 'provider-down' ? 'error' : 'dismissed',
    );
    seed(items);
    const view = renderOpen();
    fireEvent.click(view.getByTestId('attn-clear'));
    await waitFor(() => expect(view.getAllByTestId('attn-item')).toHaveLength(1));
    expect(view.getByText(items[0].title + ' · stopped')).toBeTruthy();
    expect(view.getByRole('alert').textContent).toContain('Dismissal did not hold');
  });
});

describe('AttentionQueue bell (MIK-R144 rule 3a)', () => {
  it('is a bell without a number while nothing waits, and its layer says so', () => {
    seed([]);
    const view = render(<AttentionQueue />);
    const bell = view.getByTestId('attention-bell');
    expect(bell.getAttribute('aria-label')).toBe('Attention: nothing waiting');
    expect(bell.textContent).toBe('');
    expect(view.queryByTestId('attention-queue')).toBeNull();
    fireEvent.click(bell);
    expect(view.getByTestId('attention-queue').textContent).toBe('Attention · nothing waiting');
    expect(view.queryByTestId('attn-item')).toBeNull();
  });

  it('carries the number and severity, opens the layer and keeps server order', () => {
    const queue = [items[1], items[0], items[3], items[2]];
    seed(queue);
    const view = render(<AttentionQueue />);
    const bell = view.getByTestId('attention-bell');
    expect(bell.getAttribute('aria-label')).toBe('Attention: 4 waiting');
    expect(bell.textContent).toBe('4');
    expect(bell.querySelector('[title]')?.getAttribute('title')).toBe(
      'Attention: 4 waiting (2 alarm, 2 warning)',
    );
    expect(bell.getAttribute('data-tone')).toBe('alarm');
    expect(bell.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(bell);
    expect(bell.getAttribute('aria-expanded')).toBe('true');
    expect(view.getByRole('dialog', { name: 'Attention' }).textContent).toContain(
      'Attention · 4 waiting',
    );
    expect(
      view.getAllByTestId('attn-severity').map((mark) => mark.getAttribute('aria-label')),
    ).toEqual(queue.map((item) => `Severity: ${item.severity}`));
  });

  it('closes by Escape without dismissing the item', () => {
    seed([items[1]]);
    const view = renderOpen();
    fireEvent.keyDown(view.getByRole('dialog', { name: 'Attention' }), { key: 'Escape' });
    expect(view.queryByTestId('attention-queue')).toBeNull();
    expect(postAttentionDismiss).not.toHaveBeenCalled();
    expect(view.getByTestId('attention-bell').textContent).toBe('1');
  });

  it('closes by a press outside the layer and keeps what waits', async () => {
    seed([items[1]]);
    const view = renderOpen();
    const sheet = view.baseElement.querySelector("[data-testid='underlay']");
    if (!sheet) throw new Error('an open layer has no sheet under it');
    await userEvent.click(sheet);
    expect(view.queryByTestId('attention-queue')).toBeNull();
    expect(view.getByTestId('attention-bell').getAttribute('aria-expanded')).toBe('false');
    expect(view.getByTestId('attention-bell').textContent).toBe('1');
    expect(postAttentionDismiss).not.toHaveBeenCalled();
  });
});

describe('an entry of any kind is acknowledged once per occurrence (MIK-R200 rule 7)', () => {
  it('offers a × on every row, the action row included', () => {
    seed([permissionWait, taskLanded]);
    const view = renderOpen();
    expect(view.getAllByTestId('attn-item')).toHaveLength(2);
    expect(view.getAllByTestId('attn-dismiss')).toHaveLength(2);
    const actionRow = view
      .getAllByTestId('attn-item')
      .find((row) => row.textContent?.includes(permissionWait.title));
    if (!actionRow) throw new Error('the action row did not render');
    expect(actionRow.querySelector("[data-testid='attn-dismiss']")).toBeTruthy();
  });

  it('dismisses the action row alone, posting its own id and kind', async () => {
    vi.mocked(postAttentionDismiss).mockResolvedValue('dismissed');
    seed([permissionWait, taskLanded]);
    const view = renderOpen();
    const actionRow = view
      .getAllByTestId('attn-item')
      .find((row) => row.textContent?.includes(permissionWait.title));
    if (!actionRow) throw new Error('the action row did not render');
    const control = actionRow.querySelector("[data-testid='attn-dismiss']");
    if (!(control instanceof HTMLElement)) throw new Error('the action row has no ×');

    fireEvent.click(control);
    // The row goes at once, and the notice beside it stays.
    expect(view.queryByText(permissionWait.title)).toBeNull();
    expect(view.getByText(taskLanded.title)).toBeTruthy();
    expect(view.getByTestId('attention-bell').textContent).toBe('1');
    await waitFor(() =>
      expect(postAttentionDismiss).toHaveBeenCalledExactlyOnceWith({
        itemId: permissionWait.id,
        kind: 'permission-wait',
      }),
    );
  });

  it('Clear all over an all-action queue is offered and posts one dismissal per row', async () => {
    vi.mocked(postAttentionDismiss).mockResolvedValue('dismissed');
    seed([permissionWait, taskStalled]);
    const view = renderOpen();
    fireEvent.click(view.getByTestId('attn-clear'));
    expect(view.queryAllByTestId('attn-item')).toHaveLength(0);
    await waitFor(() => expect(postAttentionDismiss).toHaveBeenCalledTimes(2));
    expect(postAttentionDismiss).toHaveBeenCalledWith({
      itemId: permissionWait.id,
      kind: 'permission-wait',
    });
    expect(postAttentionDismiss).toHaveBeenCalledWith({
      itemId: taskStalled.id,
      kind: 'task-stalled',
    });
  });

  it('hides a locally suppressed action, from the layer and from the bell', () => {
    seed([permissionWait, taskLanded]);
    dashboardStore.getState().suppressAttention([permissionWait.id]);
    const view = renderOpen();
    expect(view.queryByText(permissionWait.title)).toBeNull();
    expect(view.getAllByTestId('attn-item')).toHaveLength(1);
    expect(view.getByText(taskLanded.title)).toBeTruthy();
    expect(view.getByTestId('attention-bell').textContent).toBe('1');
  });

  it('clears a mixed queue in full and says nothing waiting', async () => {
    vi.mocked(postAttentionDismiss).mockResolvedValue('dismissed');
    seed([permissionWait, taskStalled, taskLanded]);
    const view = renderOpen();
    fireEvent.click(view.getByTestId('attn-clear'));
    expect(view.queryAllByTestId('attn-item')).toHaveLength(0);
    expect(view.getByTestId('attention-queue').textContent).toContain('nothing waiting');
    await waitFor(() => expect(postAttentionDismiss).toHaveBeenCalledTimes(3));
    for (const item of [permissionWait, taskStalled, taskLanded])
      expect(postAttentionDismiss).toHaveBeenCalledWith({ itemId: item.id, kind: item.kind });
  });

  it('still offers the × on an entry served with the old non-dismissible flag', async () => {
    vi.mocked(postAttentionDismiss).mockResolvedValue('dismissed');
    const legacy = attentionItem({
      ...permissionWait,
      id: 'permission-wait:agent-9',
      dismissible: false,
    });
    seed([legacy]);
    const view = renderOpen();
    fireEvent.click(view.getByTestId('attn-dismiss'));
    expect(view.queryByText(legacy.title)).toBeNull();
    await waitFor(() =>
      expect(postAttentionDismiss).toHaveBeenCalledExactlyOnceWith({
        itemId: legacy.id,
        kind: legacy.kind,
      }),
    );
  });
});

describe('a notice is acknowledged once per occurrence', () => {
  it('dismisses once and hides', async () => {
    vi.mocked(postAttentionDismiss).mockResolvedValue('dismissed');
    seed([taskLanded]);
    const view = renderOpen();
    fireEvent.click(view.getByTestId('attn-dismiss'));
    expect(view.queryByText(taskLanded.title)).toBeNull();
    expect(view.getByTestId('attention-queue').textContent).toContain('nothing waiting');
    await waitFor(() =>
      expect(postAttentionDismiss).toHaveBeenCalledExactlyOnceWith({
        itemId: taskLanded.id,
        kind: taskLanded.kind,
      }),
    );
  });

  it('shows the next distinct occurrence after the previous was acknowledged', async () => {
    vi.mocked(postAttentionDismiss).mockResolvedValue('dismissed');
    seed([taskLanded]);
    const view = renderOpen();
    fireEvent.click(view.getByTestId('attn-dismiss'));
    await waitFor(() => expect(postAttentionDismiss).toHaveBeenCalledTimes(1));

    const next = attentionItem({
      ...taskLanded,
      id: 'task-landed:L190:occ-2',
      occurrenceId: 'occ-2',
      title: 'Leaf L190 landed again',
    });
    seed([next]);
    await waitFor(() => expect(view.getByText(next.title)).toBeTruthy());
    expect(view.getByTestId('attention-bell').textContent).toBe('1');
    expect(view.getByTestId('attn-dismiss')).toBeTruthy();
  });
});

describe('a row opens its actual subject', () => {
  it('opens the exact task key of a task row', () => {
    const onOpenTask = vi.fn();
    seed([taskStalled, permissionWait]);
    const view = render(<AttentionQueue onOpenTask={onOpenTask} />);
    fireEvent.click(view.getByTestId('attention-bell'));
    const taskLink = view.getByTestId('attn-open');
    expect(taskLink.getAttribute('aria-label')).toContain(taskStalled.taskKey);
    fireEvent.click(taskLink);
    expect(onOpenTask).toHaveBeenCalledExactlyOnceWith('L191');
  });

  it('opens the exact agent of an agent row, and a taskless agent row has no task link', () => {
    const onOpenTask = vi.fn();
    const onOpenAgent = vi.fn();
    seed([permissionWait, taskLanded]);
    const view = render(<AttentionQueue onOpenTask={onOpenTask} onOpenAgent={onOpenAgent} />);
    fireEvent.click(view.getByTestId('attention-bell'));

    const agentRow = view
      .getAllByTestId('attn-item')
      .find((row) => row.textContent?.includes(permissionWait.title));
    if (!agentRow) throw new Error('the agent row did not render');
    expect(agentRow.querySelector("[data-testid='attn-open']")).toBeNull();
    const agentLink = agentRow.querySelector("[data-testid='attn-open-agent']");
    if (!(agentLink instanceof HTMLElement)) throw new Error('the agent row has no way in');
    expect(agentLink.getAttribute('aria-label')).toContain('agent-7');
    fireEvent.click(agentLink);
    expect(onOpenAgent).toHaveBeenCalledExactlyOnceWith({ agentId: 'agent-7', role: 'worker' });
    expect(onOpenTask).not.toHaveBeenCalled();

    // The notice names a task only: it has the task link and no agent link.
    const noticeRow = view
      .getAllByTestId('attn-item')
      .find((row) => row.textContent?.includes(taskLanded.title));
    if (!noticeRow) throw new Error('the notice row did not render');
    expect(noticeRow.querySelector("[data-testid='attn-open-agent']")).toBeNull();
    expect(noticeRow.querySelector("[data-testid='attn-open']")).toBeTruthy();
  });

  it('renders no way in when no handler is supplied', () => {
    seed([taskStalled, permissionWait]);
    const view = renderOpen();
    expect(view.queryByTestId('attn-open')).toBeNull();
    expect(view.queryByTestId('attn-open-agent')).toBeNull();
    // The place text stays for every row.
    expect(view.getAllByTestId('attn-place').map((place) => place.textContent)).toEqual([
      'task',
      'agent',
    ]);
  });
});
