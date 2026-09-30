// The unexplained-changes lane (MIK-R32): two destinations of the reviewer's tree, after the families.
//
//   * `Unexplained changes` -- the changed files no recorded entry explains (neither memory tree
//     records one for them), then the attributed files that carry unexplained hunks or a non-text
//     change the gate holds unexplained;
//   * `Unknown attribution` -- the files whose attribution could not be established (a knowledge side
//     unread, or entries that supply no range at the edited blob), with the reason, then the
//     attributed files carrying attribution-unknown hunks.
//
// Each destination reports its file and hunk totals separately; hunk counts never change a file
// total, and no allowlist or file-type rule hides a file. Selecting a file opens its actual diff
// focused on those hunks (LaneFileFocus), with the full file available. The lane is read only for a
// tree comparison; a dataset review renders none of it and asks nothing.
import { css, cx } from '../../../styled-system/css';
import type { ReviewSourceInventory } from '../../data/review';
import {
  type LaneDestinationName,
  type LaneRead,
  type ReviewLaneDestination,
  type ReviewLaneFile,
  type ReviewUnexplainedLane,
} from '../../data/reviewLane';
import {
  CLASS_LABELS,
  DESTINATION_TITLES,
  GROUP_NOTES,
  GROUP_TITLES,
  destinationGroups,
  destinationOf,
  destinationTotals,
} from './laneFocus';
import { type GateRead, LaneFileFocus, type LaneTask } from './LaneFileFocus';
import { ReviewProblemBlock } from './ReviewOutcome';
import type { DiffLayout } from './SourceExplorer';

export interface LaneSelection {
  destination: LaneDestinationName;
  path: string | null;
}

const AMBER_WASH = (percent: number) =>
  `color-mix(in oklab, var(--amber) ${percent}%, transparent)`;

const destinations = css({
  display: 'grid',
  gap: '0.1rem',
  marginTop: '0.6rem',
  paddingTop: '0.5rem',
  borderTopWidth: '1px',
  borderTopStyle: 'solid',
  borderTopColor: 'grid',
});
const node = css({
  display: 'grid',
  width: '100%',
  background: 'transparent',
  border: 'none',
  borderLeftWidth: '2px',
  borderLeftStyle: 'solid',
  borderLeftColor: 'transparent',
  color: 'ink',
  cursor: 'pointer',
  font: 'inherit',
  padding: '0.55rem 0.5rem',
  textAlign: 'left',
  borderRadius: '2px',
  _focusVisible: { outline: '1px solid var(--amber)', outlineOffset: '1px' },
  _hover: { background: AMBER_WASH(8) },
});
const current = css({ background: AMBER_WASH(16), borderLeftColor: 'amber' });
const nodeTitle = css({ fontSize: '0.84rem' });
const groupTitle = css({ color: 'ink', fontSize: '0.78rem', margin: '0.3rem 0 0' });
const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.15rem 0' });
const shell = css({
  background: 'bgPanel',
  border: '1px solid var(--grid)',
  borderRadius: '3px',
  padding: '1rem',
  minWidth: 0,
  display: 'grid',
  gap: '0.6rem',
});
const title = css({
  color: 'cyan',
  fontSize: '0.75rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  margin: 0,
});
const rows = css({
  margin: 0,
  padding: 0,
  listStyle: 'none',
  '& > li': { padding: '0.5rem 0', borderBottom: '1px solid var(--grid)', minWidth: 0 },
});
const rowButton = css({
  background: 'transparent',
  border: 'none',
  color: 'ink',
  cursor: 'pointer',
  font: 'inherit',
  fontSize: '0.8rem',
  padding: 0,
  textAlign: 'left',
  maxWidth: '100%',
  overflowWrap: 'anywhere',
  _focusVisible: { outline: '1px solid var(--amber)', outlineOffset: '2px' },
});

// The destination's line in the tree: pending shows an ellipsis and an unread lane says so -- never a
// zero it did not measure.
function destinationLine(read: LaneRead<ReviewUnexplainedLane>, name: LaneDestinationName) {
  if (read.phase === 'loading') return '…';
  if (read.phase === 'unavailable') return 'unavailable';
  const destination = destinationOf(read.value, name);
  if (read.value.state === 'unavailable' || !destination) return 'not measured';
  const totals = destinationTotals(destination);
  return read.value.state === 'partial' ? `${totals} · partial` : totals;
}

export function LaneDestinations({
  read,
  selection,
  onSelect,
}: {
  read: LaneRead<ReviewUnexplainedLane>;
  selection: LaneSelection | null;
  onSelect: (destination: LaneDestinationName) => void;
}) {
  return (
    <nav
      className={destinations}
      aria-label="Changes no recorded intent explains"
      data-testid="review-lane-destinations"
      data-lane-state={read.phase === 'ready' ? read.value.state : read.phase}
    >
      {(['unexplained', 'unknown'] as const).map((name) => {
        const chosen = selection?.destination === name;
        return (
          <button
            key={name}
            type="button"
            className={cx(node, chosen && current)}
            aria-current={chosen ? 'true' : undefined}
            data-testid="review-lane-destination"
            data-destination={name}
            onClick={() => onSelect(name)}
          >
            <span className={nodeTitle}>{DESTINATION_TITLES[name]}</span>
            <span className={muted} data-testid="review-lane-destination-totals">
              {destinationLine(read, name)}
            </span>
          </button>
        );
      })}
    </nav>
  );
}

export interface LaneCenterProps {
  read: LaneRead<ReviewUnexplainedLane>;
  selection: LaneSelection;
  onOpenFile: (path: string | null) => void;
  task: LaneTask;
  inventory: ReviewSourceInventory;
  layout: DiffLayout;
  gate: GateRead;
}

export function UnexplainedLaneCenter(props: LaneCenterProps) {
  const { read, selection } = props;
  return (
    <section
      className={shell}
      data-testid="review-lane"
      data-destination={selection.destination}
      data-lane-state={read.phase === 'ready' ? read.value.state : read.phase}
      aria-labelledby="review-lane-title"
    >
      <h2 className={title} id="review-lane-title">
        {DESTINATION_TITLES[selection.destination]}
      </h2>
      <LaneBody {...props} />
    </section>
  );
}

function LaneBody(props: LaneCenterProps) {
  const { read, selection } = props;
  if (read.phase === 'loading')
    return <p className={muted}>Classifying the comparison&apos;s changed files…</p>;
  if (read.phase === 'unavailable')
    return (
      <ReviewProblemBlock
        origin="failure"
        subject="the unexplained-changes lane"
        problem={read.problem}
      />
    );
  const lane = read.value;
  const destination = destinationOf(lane, selection.destination);
  if (lane.state === 'unavailable' || !destination)
    return (
      <p className={muted} data-testid="review-lane-unmeasured">
        The change set could not be measured, so no file is listed: {lane.detail}
      </p>
    );
  return (
    <>
      <p className={muted} data-testid="review-lane-totals">
        {destinationTotals(destination)} · {lane.detail}
      </p>
      {lane.state === 'partial' ? (
        <p className={muted} data-testid="review-lane-partial">
          Partially measured; not listed: {lane.unmeasured.join(', ')}
        </p>
      ) : null}
      <DestinationFiles {...props} destination={destination} />
    </>
  );
}

function DestinationFiles(props: LaneCenterProps & { destination: ReviewLaneDestination }) {
  const { destination, selection } = props;
  const [own, attributed] = destinationGroups(destination);
  if (!own.length && !attributed.length)
    return (
      <p className={muted} data-testid="review-lane-empty">
        {selection.destination === 'unexplained'
          ? 'No changed file or hunk of this comparison is unexplained.'
          : 'No changed file or hunk of this comparison is of unknown attribution.'}
      </p>
    );
  const [ownTitle, attributedTitle] = GROUP_TITLES[selection.destination];
  const [ownNote, attributedNote] = GROUP_NOTES[selection.destination];
  return (
    <>
      <FileGroup {...props} title={ownTitle} note={ownNote} files={own} group="bucket" />
      <FileGroup
        {...props}
        title={attributedTitle}
        note={attributedNote}
        files={attributed}
        group="attributed"
      />
    </>
  );
}

function FileGroup(
  props: LaneCenterProps & { title: string; note: string; files: ReviewLaneFile[]; group: string },
) {
  const { files } = props;
  if (!files.length) return null;
  return (
    <div data-testid="review-lane-group" data-group={props.group}>
      <p className={groupTitle}>
        {props.title} · {files.length}
      </p>
      <p className={muted}>{props.note}</p>
      <ul className={rows}>
        {files.map((file) => (
          <FileRow key={file.path} {...props} file={file} />
        ))}
      </ul>
    </div>
  );
}

function rowCounts(file: ReviewLaneFile, destination: LaneDestinationName): string {
  const counted =
    destination === 'unexplained' ? file.counts.unexplained : file.counts.attribution_unknown;
  const label = CLASS_LABELS[destination === 'unexplained' ? 'unexplained' : 'attribution_unknown'];
  const parts = file.counts.hunks ? [`${counted} ${label} of ${file.counts.hunks} hunk(s)`] : [];
  if (file.non_text)
    parts.push(
      `non-text (${file.non_text.content}${file.non_text.mode_change ? ', mode' : ''}) · gate ${file.non_text.gate}`,
    );
  return parts.join(' · ');
}

function FileRow(props: LaneCenterProps & { file: ReviewLaneFile }) {
  const { file, selection, onOpenFile } = props;
  const open = selection.path === file.path;
  return (
    <li
      data-testid="review-lane-file"
      data-path={file.path}
      data-bucket={file.bucket}
      data-non-text={file.non_text ? file.non_text.content : undefined}
    >
      <button
        type="button"
        className={rowButton}
        aria-expanded={open}
        data-testid="review-lane-file-open"
        onClick={() => onOpenFile(open ? null : file.path)}
      >
        {open ? '▾ ' : '▸ '}
        {file.path}
      </button>{' '}
      <span className={muted}>
        · {file.status} · {rowCounts(file, selection.destination)}
      </span>
      <RowReason file={file} destination={selection.destination} />
      {open ? (
        <LaneFileFocus
          task={props.task}
          path={file.path}
          destination={selection.destination}
          inventory={props.inventory}
          layout={props.layout}
          gate={props.gate}
        />
      ) : null}
    </li>
  );
}

// The owner's reason for a row, one click away: the group note already says what put it there.
function RowReason({
  file,
  destination,
}: {
  file: ReviewLaneFile;
  destination: LaneDestinationName;
}) {
  const reasons =
    file.bucket === 'attributed'
      ? destination === 'unknown'
        ? file.unknown_reasons
        : []
      : [file.reason];
  if (!reasons.length) return null;
  return (
    <details data-testid="review-lane-file-reason">
      <summary className={muted}>Why</summary>
      {reasons.map((reason) => (
        <p key={reason} className={muted}>
          {reason}
        </p>
      ))}
    </details>
  );
}
