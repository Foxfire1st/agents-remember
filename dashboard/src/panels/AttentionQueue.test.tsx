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
  }),
  attentionItem({
    id: 'actionable-drift:repo:main',
    kind: 'actionable-drift',
    severity: 'warn',
    lane: 'repo',
    title: '1 actionable drift in repo',
    repoId: 'repo',
  }),
  attentionItem({
    id: 'failed-setup:group',
    kind: 'failed-setup',
    severity: 'alarm',
    lane: 'worktree',
    title: 'Provider setup needs attention',
  }),
  attentionItem({
    id: 'blocked-start:/worktrees/group',
    kind: 'blocked-start',
    severity: 'warn',
    lane: 'worktree',
    title: 'Worktree start blocked',
    detail: 'no exact ledger mapping for selected code base commit',
  }),
];

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
