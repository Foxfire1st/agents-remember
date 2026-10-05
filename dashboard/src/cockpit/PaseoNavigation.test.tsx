import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PaseoNavigation } from './PaseoNavigation';
import type { PaseoHierarchyAgent } from './paseoFrameModel';
import type { PaseoChatGroup } from './paseoNavigationModel';

afterEach(cleanup);

const idle: PaseoHierarchyAgent = {
  agentId: 'native-worker-1',
  name: 'Worker with a long wrapping title',
  provider: 'pi',
  status: 'idle',
  pendingPermissionCount: 0,
  requiresAttention: false,
  attentionReason: null,
  providerUnavailable: false,
  workspaceId: 'workspace',
  parentAgentId: 'manager',
  archivedAt: null,
  labels: { 'ar.role': 'worker' },
};
const group = (chats: PaseoHierarchyAgent[]): PaseoChatGroup[] => [
  {
    key: 'projects',
    title: 'Projects',
    kind: 'projects',
    chats,
    children: [],
  },
];

describe('native sidebar row feedback', () => {
  it('keeps actual harness identities and the same selected clickable row through busy, attention and terminal updates', () => {
    const onSelect = vi.fn();
    const props = {
      selectedAgentIds: [idle.agentId],
      loading: false,
      unavailable: false,
      enabled: true,
      onSelect,
    };
    const providers = [
      ['pi', 'Pi', 'π'],
      ['codex', 'Codex', '</>'],
      ['claude', 'Claude', '✳'],
      ['hermes', 'Hermes', 'H'],
      ['eve', 'Eve', 'E'],
      ['constructor', 'constructor', 'constructor'],
    ];
    const chats = providers.map(([provider], index) => ({
      ...idle,
      provider,
      agentId: 'provider-' + index,
    }));
    const { getByRole, queryByTestId, rerender, container } = render(
      <PaseoNavigation {...props} groups={group(chats)} />,
    );
    for (const [, label, mark] of providers) {
      expect(getByRole('img', { name: 'Harness: ' + label }).textContent).toBe(mark);
    }
    rerender(<PaseoNavigation {...props} groups={group([idle])} />);
    const row = getByRole('button', { name: /Worker with a long wrapping title/ });
    const initialLamp = getByRole('img', { name: 'Status: Idle' });
    expect(initialLamp.getAttribute('data-state-color')).toBe('mint');
    expect(row.getAttribute('aria-current')).toBe('true');
    expect(row.textContent).toContain(idle.agentId.slice(0, 8));

    const cases: Array<[Partial<PaseoHierarchyAgent>, string, string, boolean]> = [
      [{ status: 'running' }, 'Busy', 'cyan', true],
      [{ status: 'initializing' }, 'Starting', 'cyan', true],
      [{ status: 'running', pendingPermissionCount: 1 }, 'Needs input', 'amber', false],
      [{ status: 'running', attentionReason: 'permission' }, 'Needs input', 'amber', false],
      [
        { status: 'error', pendingPermissionCount: 1, attentionReason: 'permission' },
        'Error',
        'alarm',
        false,
      ],
      [{ status: 'running', attentionReason: 'error' }, 'Error', 'alarm', false],
      [{ requiresAttention: true, attentionReason: 'finished' }, 'Reply ready', 'amber', false],
      [{ requiresAttention: true }, 'Needs attention', 'amber', false],
      [
        {
          status: 'running',
          providerUnavailable: true,
          pendingPermissionCount: 1,
          attentionReason: 'error',
        },
        'Provider unavailable',
        'dormant',
        false,
      ],
      [
        {
          status: 'closed',
          providerUnavailable: true,
          pendingPermissionCount: 1,
          attentionReason: 'error',
        },
        'Closed',
        'dormant',
        false,
      ],
      [{}, 'Idle', 'mint', false],
    ];
    for (const [patch, word, color, busy] of cases) {
      const agent = { ...idle, ...patch };
      rerender(<PaseoNavigation {...props} groups={group([agent])} />);
      expect(getByRole('button', { name: /Worker with a long wrapping title/ })).toBe(row);
      expect(row.getAttribute('aria-current')).toBe('true');
      const indicator = getByRole('img', { name: 'Status: ' + word });
      expect(indicator.parentElement?.getAttribute('title')).toBe(word);
      if (busy) {
        expect(queryByTestId('paseo-agent-busy')).toBe(indicator);
        expect(indicator.textContent).toBe('◐');
        expect(indicator.querySelector('[aria-hidden=true]')).not.toBeNull();
      } else {
        expect(queryByTestId('paseo-agent-busy')).toBeNull();
        expect(indicator.getAttribute('data-state-color')).toBe(color);
        expect(indicator.getAttribute('data-state-pulse')).toBe('false');
      }
      fireEvent.click(row);
      expect(onSelect).toHaveBeenLastCalledWith(agent);
    }
    expect(container.textContent).not.toMatch(/PASS|accepted|completed/i);
    rerender(<PaseoNavigation {...props} enabled={false} groups={group([idle])} />);
    fireEvent.click(row);
    expect(onSelect).toHaveBeenCalledTimes(cases.length);
  });
});
