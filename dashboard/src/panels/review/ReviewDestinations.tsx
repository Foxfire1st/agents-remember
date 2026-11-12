// The tree's pinned rows (MIK-R86 v2 rule 4; MIK-R89 v2 rules 3 and 7; W14): "Worklist" with its
// item count, then "Code without invariant" with its file count, pinned to the bottom of the tree
// (treeStyles.ts `pinned`), and the lane's file rows after them inside the same scroller. The lane lists the lane
// read's files without an invariant: its unexplained files and its files of unknown attribution (each
// destination's own bucket files, not the attributed files it also lists). Counts read "…" while the
// lane is read and "unavailable" when it cannot be; they are never invented as zero.
import type { LaneDestinationName, LaneRead, ReviewLaneFile, ReviewUnexplainedLane } from '../../data/reviewLane';
import type { ReviewSelection } from './reviewSelection';
import { laneFileMatches } from './changeSetOrder';
import * as styles from './tree/treeStyles';
import { treeArrow } from './tree/treeKeys';
import { CountsReason } from './tree/TreeRows';

export interface TreeLaneFile {
  path: string;
  destination: LaneDestinationName;
  // The row's one reason: the file's line counts once its diff is read, else its lane facts.
  reason: string;
}

export interface TreeLane {
  state: 'reading' | 'ready' | 'unavailable';
  reason?: string;
  files: TreeLaneFile[];
  counts: Record<LaneDestinationName, number>;
}

function laneReason(file: ReviewLaneFile): string {
  if (file.content !== 'text' || file.non_text) return 'non-text';
  const { hunks, unexplained, attribution_unknown: unknown } = file.counts;
  if (unexplained) return `${unexplained} of ${hunks} hunks unexplained`;
  if (unknown) return `${unknown} of ${hunks} hunks unknown`;
  return `${hunks} hunks`;
}

// The lane rows from the lane read (read 6). `lines` holds a file's "+a −r" once the reading column
// has read its diff (W5); until then the row gives the lane's hunk counts.
export function treeLane(
  read: LaneRead<ReviewUnexplainedLane> | null,
  lines: ReadonlyMap<string, string> = new Map(),
): TreeLane {
  const none = { unexplained: 0, unknown: 0 };
  if (read === null || read.phase === 'loading') return { state: 'reading', files: [], counts: none };
  if (read.phase === 'unavailable') return { state: 'unavailable', reason: read.problem.detail, files: [], counts: none };
  const lane = read.value;
  if (lane.state === 'unavailable') return { state: 'unavailable', reason: lane.detail, files: [], counts: none };
  const bucket = (destination: LaneDestinationName) => {
    const listed = destination === 'unexplained' ? lane.unexplained_changes : lane.unknown_attribution;
    return (listed?.files ?? []).slice(0, listed?.bucket_files ?? 0).map((file) => ({
      path: file.path,
      destination,
      reason: lines.get(file.path) ?? laneReason(file),
    }));
  };
  const unexplained = bucket('unexplained');
  const unknown = bucket('unknown');
  return {
    state: 'ready',
    files: [...unexplained, ...unknown],
    counts: { unexplained: unexplained.length, unknown: unknown.length },
  };
}

const fileName = (path: string) => path.split('/').pop() ?? path;

function laneCount(lane: TreeLane): string {
  if (lane.state === 'reading') return '…';
  if (lane.state === 'unavailable') return 'unavailable';
  return `${lane.files.length} ${lane.files.length === 1 ? 'file' : 'files'}`;
}

const DESTINATION_LABEL: Record<LaneDestinationName, string> = {
  unexplained: 'Unexplained',
  unknown: 'Unknown attribution',
};

export interface TreePinnedProps {
  lane: TreeLane;
  query: string;
  selection: ReviewSelection | null;
  onSelect: (selection: ReviewSelection) => void;
  // The change-set page drawn over the review, with that destination's filter (MIK-R86 v2 rule 10).
  onOpenLane: (destination: LaneDestinationName) => void;
  // The Worklist row's count text ("3 open", "all answered", "…", "not available").
  worklist: { count: string; open: boolean };
  onOpenWorklist: () => void;
}

function LaneFileRows({
  files,
  selection,
  onSelect,
}: {
  files: TreeLaneFile[];
  selection: ReviewSelection | null;
  onSelect: (selection: ReviewSelection) => void;
}) {
  return (
    <ul className={styles.list} data-testid="review-tree-lane-files">
      {files.map((file) => {
        const current =
          (selection?.kind === 'lane' && selection.path === file.path) ||
          (selection?.kind === 'block' && selection.block.kind === 'hunk' && selection.block.path === file.path);
        return (
          <li key={`${file.destination}:${file.path}`}>
            <button
              type="button"
              className={`${styles.row} ${styles.child}`}
              data-tree-node="lane-file"
              data-row-key={`lane:${file.path}`}
              data-path={file.path}
              aria-current={current || undefined}
              aria-label={`${file.path} ${file.reason}`}
              title={file.path}
              onClick={() => onSelect({ kind: 'lane', path: file.path })}
              onKeyDown={treeArrow}
            >
              <span className={styles.caret} aria-hidden="true" />
              <span className={styles.name}>{fileName(file.path)}</span>
              <CountsReason text={file.reason} />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function TreePinned({ lane, query, selection, onSelect, onOpenLane, worklist, onOpenWorklist }: TreePinnedProps) {
  const laneSelected = selection?.kind === 'lane' && selection.path === undefined;
  const files = lane.files.filter((file) => laneFileMatches(file.path, query));
  const destinations = (['unexplained', 'unknown'] as const).filter((name) => lane.counts[name] > 0);
  return (
    <>
      <div data-review-tree-pinned="" className={styles.pinned}>
        <button
          type="button"
          className={styles.row}
          data-tree-node="worklist"
          data-testid="review-tree-worklist"
          aria-pressed={worklist.open}
          aria-label={`Worklist ${worklist.count}`}
          onClick={onOpenWorklist}
          onKeyDown={treeArrow}
        >
          <span className={styles.caret} aria-hidden="true" />
          <span className={styles.name}>Worklist</span>
          <span className={styles.reason}>{worklist.count}</span>
        </button>
        <div className={styles.laneHead}>
          <button
            type="button"
            className={styles.row}
            data-tree-node="lane"
            data-testid="review-tree-lane"
            aria-current={laneSelected || undefined}
            aria-label={`Code without invariant ${laneCount(lane)}`}
            title={lane.state === 'unavailable' ? lane.reason : undefined}
            onClick={() => onSelect({ kind: 'lane' })}
            onKeyDown={treeArrow}
          >
            <span className={styles.caret} aria-hidden="true">▾</span>
            <span className={styles.name}>Code without invariant</span>
            <span className={styles.reason}>{laneCount(lane)}</span>
          </button>
          {destinations.map((name) => (
            <button
              key={name}
              type="button"
              className={styles.link}
              data-testid="review-tree-full-page"
              data-destination={name}
              aria-label={`Full page: ${DESTINATION_LABEL[name]}, ${lane.counts[name]} files`}
              title={`Open the ${DESTINATION_LABEL[name].toLowerCase()} files on the change-set page`}
              onClick={() => onOpenLane(name)}
            >
              {destinations.length === 1 ? 'Full page ↗' : `${DESTINATION_LABEL[name]} ↗`}
            </button>
          ))}
        </div>
      </div>
      <LaneFileRows files={files} selection={selection} onSelect={onSelect} />
    </>
  );
}
