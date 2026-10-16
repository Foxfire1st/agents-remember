import type { EnclosureNode, ProviderNode } from '../../types/projection';
import type { useDetailPanelState } from './state';
import { lanes, spine, spineHead } from './styles';
import { lane, laneTitle, laneRepo, laneMeta } from './styles';

type DetailState = ReturnType<typeof useDetailPanelState>;

export function WorktreeSpine({
  state,
  enclosure,
  groupName,
}: {
  state: DetailState;
  enclosure: EnclosureNode | undefined;
  groupName: string;
}) {
  const { providers } = state;
  const engines = groupName
    ? Object.values(providers).filter(
        (p) => p.scope === 'worktree' && p.worktreeGroup === groupName,
      )
    : [];
  if (!enclosure) return null;
  return (
    <div className={spine}>
      <div className={spineHead}>worktree · {groupName || enclosure.repoName}</div>
      <div className={lanes}>
        <SpineLane
          kind="code"
          title="code → CGC"
          repo={enclosure.repoName}
          engines={engines.filter((engine) => engine.role === 'code')}
        />
        <SpineLane
          kind="memory"
          title="memory → GrepAI"
          repo={`ar-${enclosure.repoName}`}
          engines={engines.filter((engine) => engine.role === 'memory')}
        />
      </div>
    </div>
  );
}

export function SpineLane({
  kind,
  title,
  repo,
  engines,
}: {
  kind: 'code' | 'memory';
  title: string;
  repo: string;
  engines: ProviderNode[];
}) {
  return (
    <div className={lane({ kind })}>
      <div className={laneTitle}>{title}</div>
      <div className={laneRepo}>{repo}</div>
      {engines.length > 0 ? (
        <div className={laneMeta}>
          {engines.map((engine) => (
            <span key={engine.id}>{engine.state}</span>
          ))}
        </div>
      ) : (
        <span className="muted">no isolated engine recorded</span>
      )}
    </div>
  );
}
