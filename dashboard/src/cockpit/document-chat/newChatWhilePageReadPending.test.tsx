import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { useIntentReviewSummary } from '../../data/reviewIntentSummary';
import { dashboardStore } from '../../data/store';
import { useTaskDocumentBody } from '../../data/useTaskDocumentBody';
import { ChangeSetButton } from '../../panels/detail-panel/changeSetBar';
import { chatLeaf, chatMaster, chatSprint } from '../../test/fixtures/documentChat';
import type { TaskEntry } from '../../types/projection';
import { DocumentChat } from './DocumentChat';

const LEFT = {
  ...chatLeaf,
  id: 'left-task',
  reference: { repository: 'repo', path: 'master/left-task.json' },
  summary: { ...chatLeaf.summary!, bodyRevision: 'left-v1' },
};
const RIGHT = { ...chatLeaf, summary: { ...chatLeaf.summary!, bodyRevision: 'right-v1' } };
const TASKS = [chatSprint, chatMaster, LEFT, RIGHT];

function Page({ entry }: { entry: TaskEntry }) {
  useTaskDocumentBody(entry);
  useIntentReviewSummary('repo', 'master', entry.id, 'facts');
  return (
    <>
      {(['working', 'committed'] as const).map((mode) => (
        <ChangeSetButton
          key={mode}
          target={{ repo: 'repo', master: 'master', leaf: entry.id, mode }}
          label={mode}
          onOpen={() => {}}
        />
      ))}
      <DocumentChat active taskDocumentRef={entry.reference} tasks={TASKS} />
    </>
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
  sessionStorage.clear();
});

it('dispatches and binds the selected leaf chat while four abandoned page reads remain slow', async () => {
  sessionStorage.clear();
  const calls: { url: string; body: Record<string, unknown> }[] = [];
  const oldReads: AbortSignal[] = [];
  let slowResponsesCompleted = 0;
  let requestId: string | undefined;
  const execution = {
    kind: 'paseo-agent',
    agentId: 'selected-worker',
    workspaceId: 'leaf-workspace',
  };
  const receipt = () => ({
    status: 'running',
    requestId,
    canStart: true,
    canRevive: false,
    execution,
  });
  const agent = {
    agentId: execution.agentId,
    workspaceId: execution.workspaceId,
    archivedAt: null,
    createdAt: '2026-01-01',
    labels: { 'ar.role': 'worker', 'ar.task-ref': 'repo/master/leaf.json' },
  };
  // Four free same-origin connections, after the dashboard's two long-lived streams.
  // Old producers never finish; aborting their fetches is what releases these slots.
  let occupied = 0;
  const waiting: (() => void)[] = [];
  const pump = () => {
    while (occupied < 4 && waiting.length) waiting.shift()!();
  };
  const finishSlow: (() => void)[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (input: string, init?: RequestInit) =>
        new Promise<Response>((resolve, reject) => {
          const url = String(input);
          const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
          const signal = init?.signal;
          let started = false;
          let settled = false;
          const finish = (response?: Response) => {
            if (settled) return;
            settled = true;
            if (started) occupied--;
            if (response) resolve(response);
            else reject(new DOMException('Aborted', 'AbortError'));
            pump();
          };
          signal?.addEventListener('abort', () => finish(), { once: true });
          waiting.push(() => {
            if (settled) return;
            if (signal?.aborted) {
              finish();
              return;
            }
            started = true;
            occupied++;
            calls.push({ url, body });
            if (decodeURIComponent(url).includes('left-task') && !url.includes('role-launch')) {
              expect(signal).toBeInstanceOf(AbortSignal);
              oldReads.push(signal!);
              finishSlow.push(() => {
                slowResponsesCompleted++;
                finish(new Response('{}'));
              });
              return;
            }
            let value: unknown;
            if (url.endsWith('/frame'))
              value = {
                available: true,
                frameBaseUrl: 'https://host.example',
                serverId: 'server',
                projectsWorkspaceId: 'projects',
              };
            else if (url.endsWith('/dispatch')) {
              expect(init?.method).toBe('POST');
              requestId = String(body.requestId);
              value = receipt();
            } else if (url.endsWith('/document-chats'))
              value = { agents: requestId && body.requestId === requestId ? [agent] : [] };
            else if (url.endsWith('/document-chats/tree')) value = { chats: [] };
            else if (url.endsWith('/result')) {
              if (!requestId) {
                finish(new Response('{}', { status: 404 }));
                return;
              }
              value = receipt();
            } else if (url.startsWith('/api/task-document'))
              value = { bodyRevision: 'right-v1', title: 'Selected task' };
            else if (url.startsWith('/api/changeset'))
              value = {
                scope: RIGHT.id,
                code: [],
                memory: [],
                counters: {
                  code: { files: 0, insertions: 0, deletions: 0 },
                  memory: { files: 0, insertions: 0, deletions: 0 },
                },
              };
            else
              value = {
                roleDefaults: { agent: 'codex', available: true },
                agents: [{ id: 'codex', label: 'Codex', models: [] }],
                executions: [],
              };
            finish(new Response(JSON.stringify(value)));
          });
          pump();
        }),
    ),
  );

  const ui = render(<Page entry={LEFT} />);
  await waitFor(() => expect(oldReads).toHaveLength(4));
  expect(occupied).toBe(4);
  ui.rerender(<Page entry={RIGHT} />);
  await waitFor(() => expect(oldReads.every((signal) => signal.aborted)).toBe(true));
  await ui.findByTestId('role-launcher');
  const frame = ui.container.querySelector('iframe')!;
  expect(frame).not.toBeNull();
  const posts = vi.spyOn(frame.contentWindow!, 'postMessage');
  act(() =>
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: 'https://host.example',
        source: frame.contentWindow,
        data: { source: 'ar-plugin', type: 'ready' },
      }),
    ),
  );
  await waitFor(() =>
    expect((ui.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
      false,
    ),
  );
  fireEvent.click(ui.getByRole('button', { name: 'Start role' }));
  await waitFor(() =>
    expect(posts).toHaveBeenCalledWith(
      { type: 'ar.open', agentId: 'selected-worker', workspaceId: 'leaf-workspace' },
      'https://host.example',
    ),
  );
  const dispatch = calls.find(({ url }) => url.endsWith('/dispatch'))!;
  expect(dispatch.body).toMatchObject({
    role: 'worker',
    action: 'start',
    taskDocumentRef: RIGHT.reference,
  });
  expect(
    calls.some(
      ({ url, body }) =>
        url.endsWith('/document-chats') &&
        body.requestId === requestId &&
        body.taskDocumentRef &&
        JSON.stringify(body.taskDocumentRef) === JSON.stringify(RIGHT.reference),
    ),
  ).toBe(true);
  act(() =>
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: 'https://host.example',
        source: frame.contentWindow,
        data: { source: 'ar-plugin', type: 'shown', agentId: 'selected-worker' },
      }),
    ),
  );
  await waitFor(() => expect(frame.hasAttribute('inert')).toBe(false));
  expect(slowResponsesCompleted).toBe(0);
  expect(oldReads.every((signal) => signal.aborted)).toBe(true);
  // The old server responses can arrive late without changing the selected chat.
  act(() => finishSlow.forEach((finish) => finish()));
  expect(dispatch.body.taskDocumentRef).toEqual(RIGHT.reference);
});
