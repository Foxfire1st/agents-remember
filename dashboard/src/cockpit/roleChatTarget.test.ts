// The seam between an attention row's served seat and the Chats pane (L190-R1-F09): the exact
// agent a surface names, the launcher selection that seat is, and the frame target that results.
import { describe, expect, it } from 'vitest';

import type { TaskDocumentRef } from '../types/projection';
import {
  chatFrameScope,
  chatFrameTarget,
  launcherRoleOf,
  seatChatSelection,
  seatChatTarget,
  selectionEndsNamedChat,
} from './roleChatTarget';

const LEAF: TaskDocumentRef = { repository: 'repo', path: 'master/M-L1.json' };

describe('a bell row as the chat request', () => {
  it('carries the exact agent with its role and its own document, and invents no task', () => {
    // Task-bound: the row's document travels as the selection's document.
    expect(
      seatChatTarget({ agentId: 'agent-8', role: 'worker', taskKey: 'M-L1', taskReference: LEAF }),
    ).toEqual({ agentId: 'agent-8', role: 'worker', taskDocumentRef: LEAF });
    // Taskless: no document, and none made up for it.
    expect(seatChatTarget({ agentId: 'agent-7', role: 'worker' })).toEqual({
      agentId: 'agent-7',
      role: 'worker',
    });
    expect(seatChatTarget({ agentId: 'agent-9' })).toEqual({ agentId: 'agent-9' });
  });

  it('is the launcher selection of the seat, or none for a role the launcher does not have', () => {
    expect(
      seatChatSelection({ agentId: 'agent-8', role: 'worker', taskDocumentRef: LEAF }),
    ).toEqual({
      role: 'worker',
      taskDocumentRef: LEAF,
    });
    expect(seatChatSelection({ agentId: 'agent-7', role: 'worker' })).toEqual({ role: 'worker' });
    // A row without a launcher role still steers the frame; the bar keeps its own selection.
    expect(seatChatSelection({ agentId: 'agent-9' })).toBeNull();
    expect(seatChatSelection({ agentId: 'agent-9', role: 'not-a-role' })).toBeNull();
    // A seat served under the investigator's old name is the launcher's own investigator.
    expect(launcherRoleOf('system-specialist')).toBe('investigator');
    expect(launcherRoleOf('orchestrator')).toBe('orchestrator');
    expect(launcherRoleOf(undefined)).toBeNull();
  });

  it('asks for the named agent over the seat another receipt of the same scope names', () => {
    const previousSeat = {
      status: 'closed',
      execution: { kind: 'paseo-agent', agentId: 'agent-previous-seat' },
    };
    expect(chatFrameTarget({ agentId: 'agent-8', role: 'worker' }, previousSeat)).toEqual({
      agentId: 'agent-8',
    });
    // Without a named chat the pane keeps its own way in: the selection's execution.
    expect(chatFrameTarget(null, previousSeat)).toEqual({ agentId: 'agent-previous-seat' });
    expect(chatFrameTarget(undefined, previousSeat)).toEqual({ agentId: 'agent-previous-seat' });
    // A rejected launch names no host agent, and no request is invented for it.
    expect(
      chatFrameTarget(null, {
        status: 'rejected',
        execution: { kind: 'paseo-agent', agentId: 'agent-rejected' },
      }),
    ).toBeNull();
  });

  it('scopes the frame to the seat the row named, whose document the launcher cannot hold', () => {
    const selectionScope = JSON.stringify({ role: 'worker' });
    // Task-bound: the leaf is the chat's scope, even though the launcher pruned it.
    expect(
      chatFrameScope({ agentId: 'agent-8', role: 'worker', taskDocumentRef: LEAF }, selectionScope),
    ).toBe(JSON.stringify({ role: 'worker', taskDocumentRef: LEAF }));
    // Taskless: the role alone, and no document made up for it.
    expect(chatFrameScope({ agentId: 'agent-7', role: 'worker' }, selectionScope)).toBe(
      selectionScope,
    );
    // No named chat (and no launcher role to name): the selection's own scope.
    expect(chatFrameScope(null, '{"role":"architect"}')).toBe('{"role":"architect"}');
    expect(chatFrameScope({ agentId: 'agent-9' }, selectionScope)).toBe(selectionScope);
  });

  it('keeps the named chat while the launcher repairs the seat, and drops it on a real pick', () => {
    const named = { agentId: 'agent-8', role: 'worker', taskDocumentRef: LEAF };
    // The launcher's own repair prunes the leaf it cannot resolve: the chat stays.
    expect(selectionEndsNamedChat({ role: 'worker' }, named)).toBe(false);
    // The developer's pick changes the scope the chat belongs to.
    expect(
      selectionEndsNamedChat(
        { role: 'worker', taskDocumentRef: { repository: 'repo', path: 'x' } },
        named,
      ),
    ).toBe(true);
    expect(selectionEndsNamedChat({ role: 'reviewer' }, named)).toBe(true);
    expect(selectionEndsNamedChat({ role: 'reviewer' }, null)).toBe(false);
  });
});
