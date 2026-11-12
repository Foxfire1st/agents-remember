// Retire on the task document (MIK-R106 v2 rule 7): the same control and the same two lists as the
// chat header's, and nothing at all while no seat is known.
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ExplainerTaskDocumentControl, publishExplainerSeat, resetExplainerSeats } from './ExplainerTaskDocument';
import { ADDRESS, RETIRE, TASK, backend, explainerState } from './testSupport';

beforeEach(() => resetExplainerSeats());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('Retire on the task document (rule 7)', () => {
  it('hydrates a cold task document from /state and shows nothing when no explainer exists', async () => {
    const server = backend({ [ '/api/explainer/repo/master/L181/state' ]: { body: explainerState() } });
    const panel = render(<ExplainerTaskDocumentControl address={ADDRESS} task={TASK} />);
    await waitFor(() => expect(server.count('GET', '/api/explainer/repo/master/L181/state')).toBe(1));
    expect(panel.queryByTestId('explainer-task-document')).toBeNull();
    expect(server.posts()).toEqual([]);
  });

  it('retires an existing explainer directly from a cold task document without visiting Review first', async () => {
    const statePath = '/api/explainer/repo/master/L181/state';
    const server = backend({
      [statePath]: [
        { body: explainerState({ seat: 'live', agentId: 'a-exp', workspaceId: 'wks', generation: 1 }) },
        { body: explainerState({ seat: null, retiredAt: '2026-10-19T15:12:00Z' }) },
      ],
      [RETIRE]: {
        body: {
          state: 'retired',
          receipt: receipt(),
        },
      },
    });
    const panel = render(<ExplainerTaskDocumentControl address={ADDRESS} task={TASK} />);
    expect((await panel.findByTestId('explainer-task-document-state')).textContent).toContain('live');
    fireEvent.click(panel.getByTestId('explainer-task-document-retire'));
    fireEvent.click(await panel.findByTestId('explainer-task-document-confirm-send'));
    await waitFor(() => expect(server.count('POST', RETIRE)).toBe(1));
    expect(typeof server.last('POST', RETIRE)?.body?.receiptId).toBe('string');
    expect((await panel.findByTestId('explainer-task-document-receipt')).textContent).toContain('No process remains');
    expect(panel.queryByTestId('explainer-task-document-retire')).toBeNull();
    act(() => publishExplainerSeat(ADDRESS, explainerState({
      seat: 'live', agentId: 'a-next', workspaceId: 'wks-next', generation: 2,
    })));
    expect(panel.queryByTestId('explainer-task-document-receipt')).toBeNull();
    act(() => publishExplainerSeat(ADDRESS, explainerState({
      seat: 'closed', agentId: 'a-next', workspaceId: 'wks-next', generation: 2,
    })));
    expect(panel.queryByTestId('explainer-task-document-receipt')).toBeNull();
  });

  it('offers Retire for a live seat, with the confirmation’s two lists', async () => {
    const server = backend({ [RETIRE]: { body: { receipt: receipt(), state: explainerState({ retiredAt: '2026-10-19T15:12:00Z' }) } } });
    publishExplainerSeat(ADDRESS, explainerState({ seat: 'live', agentId: 'a-exp', workspaceId: 'wks' }));
    const panel = render(<ExplainerTaskDocumentControl address={ADDRESS} task={TASK} />);
    expect((await panel.findByTestId('explainer-task-document-state')).textContent).toContain('live');
    fireEvent.click(panel.getByTestId('explainer-task-document-retire'));
    const confirm = await panel.findByTestId('explainer-task-document-confirm');
    expect(confirm.textContent).toContain('Remove the explainer of L181?');
    expect(panel.getByTestId('explainer-task-document-ends').textContent).toContain('AR_EXPLAINER_WORKSPACE');
    expect(panel.getByTestId('explainer-task-document-stays').textContent).toContain('published revision');

    fireEvent.click(panel.getByTestId('explainer-task-document-confirm-send'));
    await waitFor(() => expect(server.count('POST', RETIRE)).toBe(1));
    expect((await panel.findByTestId('explainer-task-document-receipt')).textContent).toContain('No process remains');
    expect(panel.queryByTestId('explainer-task-document-retire')).toBeNull();
  });

  it('names the processes a half-retired seat left behind and keeps recovery open until none remain', async () => {
    const server = backend({
      [RETIRE]: [
        { body: { state: 'pending', receipt: receipt([4321], 'pending') } },
        { body: { state: 'retired', receipt: receipt([], 'finished') } },
      ],
    });
    publishExplainerSeat(ADDRESS, explainerState({ seat: 'live', agentId: 'a-exp', workspaceId: 'wks' }));
    const panel = render(<ExplainerTaskDocumentControl address={ADDRESS} task={TASK} />);
    fireEvent.click(await panel.findByTestId('explainer-task-document-retire'));
    fireEvent.click(panel.getByTestId('explainer-task-document-confirm-send'));
    expect((await panel.findByTestId('explainer-task-document-receipt')).textContent).toContain('Processes remain: 4321');
    // Confirmation stays open for recovery so a retry finishes the retirement with the same receiptId.
    expect(panel.getByTestId('explainer-task-document-confirm')).toBeTruthy();
    const firstReceiptId = server.last('POST', RETIRE)?.body?.receiptId;
    fireEvent.click(panel.getByTestId('explainer-task-document-confirm-send'));
    await waitFor(() => expect(server.count('POST', RETIRE)).toBe(2));
    expect(server.last('POST', RETIRE)?.body?.receiptId).toBe(firstReceiptId);
    await waitFor(() =>
      expect(panel.getByTestId('explainer-task-document-receipt').textContent).toContain('No process remains'),
    );
    expect(panel.queryByTestId('explainer-task-document-confirm')).toBeNull();
  });
});

function receipt(remaining: number[] = [], state: 'pending' | 'finished' | 'failed' = 'finished') {
  return {
    receiptId: 'r1',
    agentId: 'a-exp',
    generation: 1,
    state,
    step: state === 'finished' ? 'finished' : 'processes-remain',
    endedByMarker: 3,
    endedByTree: 3,
    remaining,
    detail: '',
  };
}
