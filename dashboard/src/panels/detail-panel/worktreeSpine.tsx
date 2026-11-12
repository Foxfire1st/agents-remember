import type { EnclosureNode } from '../../types/projection';
import { lane, laneMeta, laneRepo, laneTitle, lanes, spine, spineHead } from './styles';

// The Tasks view's worktree spine: the two checkouts an enclosure binds — the code worktree and
// its memory twin — with the facts the server observes about each (whether the path exists, and
// the cleanup state that decides whether it still will).
//
// The isolated search engines that used to sit on these lanes (CGC over the code checkout,
// GrepAI over the memory one) are gone from the served projection, so nothing here reads engine
// or provider state. That is why an enclosure recorded before their removal and one recorded
// after it render identically: the lanes carry only what the enclosure itself says.
//
// `codeWorktreeExists` / `memoryWorktreeExists` are observations of the path *now*, not a
// creation history: a checkout that existed and was cleaned up answers false exactly like one
// that was never created. `EnclosureNode` carries no field that separates the two, so an absent
// lane may only be reported as absent — never as "not created".
export function WorktreeSpine({
  enclosure,
  groupName,
}: {
  enclosure: EnclosureNode;
  groupName: string;
}) {
  return (
    <div className={spine} data-testid="worktree-spine">
      <div className={spineHead}>worktree · {groupName || enclosure.repoName}</div>
      <div className={lanes}>
        <SpineLane
          kind="code"
          title="code checkout"
          repo={enclosure.repoName}
          exists={enclosure.codeWorktreeExists}
          cleanup={enclosure.cleanup}
        />
        <SpineLane
          kind="memory"
          title="memory checkout"
          repo={`ar-${enclosure.repoName}`}
          exists={enclosure.memoryWorktreeExists}
          cleanup={enclosure.cleanup}
        />
      </div>
    </div>
  );
}

export function SpineLane({
  kind,
  title,
  repo,
  exists,
  cleanup,
}: {
  kind: 'code' | 'memory';
  title: string;
  repo: string;
  exists: boolean;
  cleanup: string;
}) {
  return (
    <div className={lane({ kind })} data-testid={`spine-lane-${kind}`}>
      <div className={laneTitle}>{title}</div>
      <div className={laneRepo}>{repo}</div>
      <div className={laneMeta}>
        {exists ? 'worktree present' : 'worktree absent'} · {cleanup} cleanup
      </div>
    </div>
  );
}
