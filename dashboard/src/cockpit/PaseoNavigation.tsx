import { css } from '../../styled-system/css';
import type { SeatVisualState } from '../data/stateGrammar';
import { StateDot } from '../panels/session-cockpit/StateDot';
import type { PaseoChatGroup } from './paseoNavigationModel';
import type { PaseoAgentStatus, PaseoHierarchyAgent } from './paseoFrameModel';

const rail = css({
  flexShrink: '0',
  width: '17rem',
  minHeight: '0',
  overflowY: 'auto',
  borderRight: '1px solid var(--grid)',
  background: 'var(--bg-panel)',
  padding: '0.5rem',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.72rem',
});
const heading = css({ color: 'amber', paddingBlock: '0.4rem', overflowWrap: 'anywhere' });
const nested = css({
  paddingLeft: '0.7rem',
  borderLeft: '1px solid var(--grid)',
  marginLeft: '0.2rem',
});
const chatButton = css({
  display: 'block',
  width: '100%',
  textAlign: 'left',
  padding: '0.35rem 0.4rem',
  border: '1px solid transparent',
  borderRadius: '3px',
  color: 'ink',
  background: 'transparent',
  cursor: 'pointer',
  font: 'inherit',
  overflowWrap: 'anywhere',
  _hover: { background: 'var(--bg)', borderColor: 'grid' },
  _focusVisible: { outline: '1px solid token(colors.amber)' },
  _disabled: { cursor: 'default', opacity: '0.6' },
  '&[aria-current=true]': { borderColor: 'amber', background: 'var(--bg)', color: 'amber' },
});
const meta = css({ display: 'block', color: 'muted', fontSize: '0.65rem', marginTop: '0.15rem' });
const identity = css({ display: 'flex', alignItems: 'center', gap: '0.4rem', minWidth: '0' });
const name = css({ flex: '1', minWidth: '0', overflowWrap: 'anywhere' });
const providerMark = css({
  flex: 'none',
  minWidth: '1.5em',
  maxWidth: '5rem',
  textAlign: 'center',
  overflowWrap: 'anywhere',
  color: 'muted',
  fontWeight: 'bold',
});
const busyGlyph = css({
  display: 'inline-block',
  flex: 'none',
  color: 'cyan',
  animation: 'paseoAgentSpin 2.4s linear infinite',
  _motionReduce: { animation: 'none' },
});

const PROVIDERS = new Map(
  Object.entries({
    pi: { mark: 'π', name: 'Pi' },
    codex: { mark: '</>', name: 'Codex' },
    claude: { mark: '✳', name: 'Claude' },
    hermes: { mark: 'H', name: 'Hermes' },
    eve: { mark: 'E', name: 'Eve' },
  }),
);

const STATUS_VISUALS: Record<PaseoAgentStatus, SeatVisualState> = {
  closed: { key: 'exited', word: 'Closed', color: 'dormant', pulse: false },
  error: { key: 'failed', word: 'Error', color: 'alarm', pulse: false },
  running: { key: 'working', word: 'Busy', color: 'cyan', pulse: true },
  initializing: { key: 'starting', word: 'Starting', color: 'cyan', pulse: true },
  idle: { key: 'ready', word: 'Idle', color: 'mint', pulse: false },
};

function activity(agent: PaseoHierarchyAgent): SeatVisualState {
  const state = STATUS_VISUALS[agent.status];
  if (agent.status === 'closed') return state;
  if (agent.providerUnavailable)
    return { key: 'unclassified', word: 'Provider unavailable', color: 'dormant', pulse: false };
  if (agent.status === 'error' || agent.attentionReason === 'error') return STATUS_VISUALS.error;
  if (agent.pendingPermissionCount > 0 || agent.attentionReason === 'permission')
    return { key: 'awaiting-input', word: 'Needs input', color: 'amber', pulse: false };
  if (state.pulse) return state;
  return agent.requiresAttention
    ? {
        key: 'turn-ended',
        word: agent.attentionReason === 'finished' ? 'Reply ready' : 'Needs attention',
        color: 'amber',
        pulse: false,
      }
    : state;
}

function AgentIdentity({ agent }: { agent: PaseoHierarchyAgent }) {
  const provider = PROVIDERS.get(agent.provider) ?? { mark: agent.provider, name: agent.provider };
  const state = activity(agent);
  const busy = state.key === 'working' || state.key === 'starting';
  return (
    <span className={identity}>
      <span
        className={providerMark}
        role="img"
        aria-label={`Harness: ${provider.name}`}
        title={`Harness: ${provider.name}`}
      >
        <span aria-hidden="true">{provider.mark}</span>
      </span>
      <span className={name}>{agent.name || agent.agentId}</span>
      <span title={state.word} data-agent-activity={state.word}>
        {busy ? (
          <span
            className={busyGlyph}
            role="img"
            aria-label={`Status: ${state.word}`}
            data-testid="paseo-agent-busy"
          >
            <span aria-hidden="true">◐</span>
          </span>
        ) : (
          <StateDot state={state} ariaLabel={`Status: ${state.word}`} />
        )}
      </span>
    </span>
  );
}

export function PaseoNavigation({
  groups,
  selectedAgentIds,
  loading,
  unavailable,
  enabled,
  onSelect,
}: {
  groups: PaseoChatGroup[];
  selectedAgentIds: readonly string[];
  loading: boolean;
  unavailable: boolean;
  enabled: boolean;
  onSelect: (agent: PaseoHierarchyAgent) => void;
}) {
  const selected = new Set(selectedAgentIds);
  const renderGroup = (group: PaseoChatGroup) => (
    <section key={group.key} aria-label={group.title} data-scope={group.key}>
      <div className={heading} title={group.key}>
        {group.title}
        {group.repository ? (
          <span className={meta}>
            {group.kind} · {group.repository} · {group.documentId}
          </span>
        ) : null}
      </div>
      {group.chats.map((agent) => (
        <button
          key={agent.agentId}
          type="button"
          className={chatButton}
          aria-current={selected.has(agent.agentId) ? 'true' : undefined}
          title={agent.agentId}
          data-agent-id={agent.agentId}
          disabled={!enabled}
          onClick={() => onSelect(agent)}
        >
          <AgentIdentity agent={agent} />
          <span className={meta}>{agent.agentId.slice(0, 8)}</span>
          {agent.labels['ar.qualification-fixture'] === 'true' ? (
            <span className={meta}>Qualification fixture</span>
          ) : null}
        </button>
      ))}
      {group.children.length ? (
        <div className={nested}>{group.children.map(renderGroup)}</div>
      ) : null}
    </section>
  );
  return (
    <nav aria-label="AR chat navigation" className={rail}>
      {groups.map(renderGroup)}
      {loading && !unavailable ? (
        <div role="status" className={meta}>
          Waiting for the chat catalog…
        </div>
      ) : null}
      {!loading &&
      !unavailable &&
      !groups.some((group) => group.chats.length || group.children.length) ? (
        <div role="status" className={meta}>
          No chats
        </div>
      ) : null}
    </nav>
  );
}
