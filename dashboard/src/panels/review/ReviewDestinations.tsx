// Destinations share the held comparison read; attribution counts are not gate-answer counts.
import { useContext } from 'react';
import type { ReviewPayload } from '../../data/review';
import type { LaneRead, ReviewUnexplainedLane } from '../../data/reviewLane';
import { treeComparisonNumber, type ReviewTreesRead } from '../../data/reviewTrees';
import { ReviewChangeSetNavigation } from './changeSetNavigation';
import { matchingTreeRead } from './codeFacts';
import { worklistAnswer } from './worklistState';
import type { WorkspaceState } from './workspaceState';

export function laneCounts(read: LaneRead<ReviewUnexplainedLane> | null): {
  unexplained: number | '…' | 'unavailable'; unknown: number | '…' | 'unavailable';
} {
  if (read?.phase === 'loading') return { unexplained: '…', unknown: '…' };
  if (read?.phase !== 'ready' || read.value.state === 'unavailable')
    return { unexplained: 'unavailable', unknown: 'unavailable' };
  return { unexplained: read.value.unexplained_changes?.files.length ?? 'unavailable',
    unknown: read.value.unknown_attribution?.files.length ?? 'unavailable' };
}

export function ReviewDestinations({ payload, laneRead, state, leafTrees }: {
  payload: ReviewPayload; laneRead: LaneRead<ReviewUnexplainedLane> | null;
  state: WorkspaceState; leafTrees: ReviewTreesRead | null;
}) {
  const open = useContext(ReviewChangeSetNavigation);
  const counts = laneCounts(laneRead);
  const matching = matchingTreeRead(leafTrees, treeComparisonNumber(payload.limitations));
  const worklist = leafTrees?.phase === 'loading' ? '…' : worklistAnswer(matching?.worklist) ?? 'not available';
  const files = payload.source.inventory;
  const all = files.state === 'measured' ? files.listed_total + files.unrepresentable_paths.length : 'the change set could not be measured';
  return <div style={{ display: 'grid', gap: '0.25rem' }}>
    <button type="button" onClick={() => open?.('unexplained')}>
      Changes without an invariant <span>{counts.unexplained}</span>
    </button>
    <button type="button" onClick={() => open?.('unknown')}>
      Changes of unknown attribution <span>{counts.unknown}</span>
    </button>
    <button type="button" onClick={() => open?.('all')}>All changes <span>{all}</span></button>
    <button type="button" aria-pressed={state.worklist} onClick={() => state.setWorklist(true)}>
      Worklist <span>{worklist}</span>
    </button>
  </div>;
}
