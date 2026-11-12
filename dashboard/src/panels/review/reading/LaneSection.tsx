// "Code without invariant" (MIK-R89 v2 rule 7; MIK-R86 v2 rule 10; W14): the inline section at the
// end of the reading column. It lists the lane read's files whose bucket is unexplained or of unknown
// attribution, each with its hunks, drawn as the rest of the page's code. A file's two reads (its
// classification and its text, W5) wait until the opening's six reads are done and the file is in
// view, with no gesture (f43ae286), and pass the reading column's gate of at most READ_BATCH files at
// a time. An added file longer than 30 lines shows its first 30 and a way to the rest in the diff
// viewer, in the filter whose destination lists it. "Full page" opens the change-set page over the
// mounted review, one link per destination that has files.
import { useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { css } from '../../../../styled-system/css';
import { comparisonFileDiff, type FileDiff } from '../../../data/changeset';
import { reviewProblemFromCause, type ReviewFailure } from '../../../data/review';
import type {
  LaneRead,
  ReviewFileClassification,
  ReviewLaneFile,
  ReviewLaneHunk,
  ReviewUnexplainedLane,
} from '../../../data/reviewLane';
import type { ReviewTreeComparison } from '../../../data/reviewTrees';
import { AddToTray, addToTrayHost } from '../../../tray/AddToTray';
import { DiffPane, type DiffMode } from '../../changeset/DiffPane';
import type { ReviewChangeFilter } from '../../changeset/reviewChangeSet';
import { blockKey, hunkIdentityOf, type HunkIdentity } from '../blockIdentity';
import { ReviewChangeSetNavigation } from '../changeSetNavigation';
import { IntentMarkerScope } from '../intentMarkerScope';
import { ReviewProblemBlock } from '../ReviewOutcome';
import type { ReviewSelection } from '../reviewSelection';
import { blockDomKey, laneFileDomId } from './blockDom';
import { useInView, useReadGate } from './readGate';
import { laneCodePick } from '../../../tray/pickBuilders';
import type { ReviewPlace } from './readingContext';
import {
  badge,
  block,
  blockHead,
  heldContent,
  heldSkeleton,
  laneRowAttrs,
  muted,
  sectionHead,
  subjectBox,
  subjectId,
  sublabel,
} from './readingStyles';

// The longest added file the section draws in full (mockup app.js:316 shows 30 of 143).
const ADDED_FILE_CAP = 30;

// The lane read's files without an invariant: its two buckets, each file once.
export function laneFiles(lane: ReviewUnexplainedLane): ReviewLaneFile[] {
  const files = [
    ...(lane.unexplained_changes?.files ?? []).filter((file) => file.bucket === 'unexplained'),
    ...(lane.unknown_attribution?.files ?? []).filter((file) => file.bucket === 'attribution_unknown'),
  ];
  const seen = new Set<string>();
  return files.filter((file) => (seen.has(file.path) ? false : (seen.add(file.path), true)));
}

// What the page learns of a lane file once its classification is read: its hunks (the `j`/`k` stops
// after the changed blocks, W21) and its line counts (the tree row's reason).
export interface LaneFileFacts {
  hunks: HunkIdentity[];
  lines: string;
}

// The lane part of the page's selection the section marks, and how it selects.
export interface LaneSelect {
  selection: ReviewSelection | null;
  onSelect: (selection: ReviewSelection) => void;
  onFile?: (path: string, facts: LaneFileFacts) => void;
}

// A hunk's row is quiet text (D401: the mockup draws no chips), still the button that selects the hunk
// (MIK-R89 v2 rule 13); `data-own-look` keeps the review's shared control look off it (ReviewLayout.tsx).
const hunkButton = css({
  background: 'transparent',
  border: 'none',
  padding: 0,
  font: 'inherit',
  color: 'inherit',
  cursor: 'pointer',
  _hover: { color: 'amber' },
});

// The list of a file's hunk rows: the rows its read draws, or the rows it reserves while it waits.
const hunkList = css({ listStyle: 'none', margin: 0, padding: '0.3rem 0.7rem', display: 'flex', flexWrap: 'wrap', gap: '0.3rem 1rem' });

// A reserved row's ⊕: the real control, hidden (it takes its room and nothing else).
const hiddenMark = css({ visibility: 'hidden' });

// The part below a file's header: one block formatting context, so that its children's margins are
// inside its height.
const heldBox = css({ display: 'flow-root' });

const dirOf = (path: string) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/') + 1) : '');
const nameOf = (path: string) => path.slice(path.lastIndexOf('/') + 1);

type FileRead =
  | { phase: 'waiting' }
  | { phase: 'ready'; classification: LaneRead<ReviewFileClassification> | null; diff: FileDiff }
  | { phase: 'unavailable'; problem: ReviewFailure };

// The file's two reads, once it is in view, through the gate.
function useLaneFileRead(place: ReviewPlace, number: number, file: ReviewLaneFile, visible: boolean): FileRead {
  const gate = useReadGate();
  const classify = useContext(IntentMarkerScope)?.classify;
  const [read, setRead] = useState<FileRead>({ phase: 'waiting' });
  const textual = file.content === 'text';
  useEffect(() => {
    if (!visible || !textual) return undefined;
    let live = true;
    const both = () =>
      Promise.all([
        classify ? (classify(file.path) ?? Promise.resolve(null)) : Promise.resolve(null),
        comparisonFileDiff(place.repo, place.master, place.leaf, number, 'code', file.path),
      ]);
    void (gate ? gate.run(both) : both()).then(
      ([classification, diff]) => live && setRead({ phase: 'ready', classification, diff }),
      (cause: unknown) => live && setRead({ phase: 'unavailable', problem: reviewProblemFromCause(cause) }),
    );
    return () => {
      live = false;
    };
  }, [visible, textual, gate, classify, place.repo, place.master, place.leaf, number, file.path]);
  return read;
}

function hunksOf(read: FileRead): ReviewLaneHunk[] {
  if (read.phase !== 'ready' || read.classification?.phase !== 'ready') return [];
  return read.classification.value.hunks;
}

function lineCounts(hunks: ReviewLaneHunk[]): { plus: number; minus: number } {
  return hunks.reduce(
    (sum, hunk) => ({ plus: sum.plus + hunk.after.count, minus: sum.minus + hunk.before.count }),
    { plus: 0, minus: 0 },
  );
}

const spanLabel = (start: number, count: number) => (count <= 1 ? `L${start}` : `L${start}–${start + count - 1}`);

// How many hunk rows a file reserves: while it waits for its read, one per hunk the lane read counts,
// when its read draws hunk rows (a text file, with the classification read on the page).
function reservedRowsOf(file: ReviewLaneFile, read: FileRead, classifies: boolean): number {
  return read.phase === 'waiting' && file.content === 'text' && classifies ? file.counts.hunks : 0;
}

// The rows a waiting file reserves: each drawn with a read row's elements and classes (its label's
// button and its ⊕, hidden), so that it is as tall as a read row; the label is neutral and no wider
// than a read row's ("L1 · linked" is the shortest), so the reserved rows never wrap into more lines
// than the read's rows.
function reservedRows(count: number, file: ReviewLaneFile, place: ReviewPlace, comparison: ReviewTreeComparison) {
  return Array.from({ length: count }, (_, index) => (
    <li key={`reserved-${index}`} className={`${addToTrayHost} ${muted}`} data-testid="review-lane-hunk-reserved">
      <button type="button" className={hunkButton} data-own-look disabled>
        hunk {index + 1}
      </button>{' '}
      <span className={hiddenMark}>
        <AddToTray pick={laneCodePick({ repository: place.repository, comparison, path: file.path })} label={`file ${file.path}`} />
      </span>
    </li>
  ));
}

// Calls `report` with the element's height now and again whenever it is resized.
function watchHeight(node: HTMLElement, report: (height: number) => void): (() => void) | undefined {
  report(node.getBoundingClientRect().height);
  if (typeof ResizeObserver === 'undefined') return undefined;
  const observer = new ResizeObserver(() => report(node.getBoundingClientRect().height));
  observer.observe(node);
  return () => observer.disconnect();
}

// The read's diff body is final: its editor has drawn, or the read draws no diff (its failure).
const diffFinal = (node: HTMLElement) =>
  node.querySelector('.cm-editor') !== null || node.querySelector('[data-testid="review-lane-diff"]') === null;

// While the file waits: the height of what is below its header (the reserved rows, the skeleton at
// its default height), measured and kept. After its read: that height is the box's min-height from
// the read's own commit, written at render, until what replaces it is as tall or its diff has drawn,
// so that no layout of a shorter file exists before the diff's own (rule 9; dev6 E7 at 1440: a file
// 14.8 px shorter on its read forced a clamp; dev7: a min-height set in a layout effect came after a
// layout of the shorter file inside the commit, and the browser clamped there). A diff shorter than
// the default then takes its own height, once. Its diff pane draws from 0 px once the language pack
// has loaded; a file whose classification does not come loses its reserved rows. The read's rows
// come at once in the reserved rows' place; what the skeleton held stays as skeleton lines at the
// held height until the diff has drawn (D382, D383: the file is not drawn yet).
function HeldBody({ waiting, children }: { waiting: boolean; children: React.ReactNode }) {
  const content = useRef<HTMLDivElement>(null);
  const [waited, setWaited] = useState(0);
  const [grown, setGrown] = useState(false);
  useLayoutEffect(() => {
    const node = content.current;
    if (!waiting || !node) return undefined;
    setGrown(false);
    return watchHeight(node, setWaited);
  }, [waiting]);
  useLayoutEffect(() => {
    const node = content.current;
    if (waiting || grown || waited <= 0 || !node) return undefined;
    return watchHeight(node, (height) => {
      if (height >= waited - 0.5 || diffFinal(node)) setGrown(true);
    });
  }, [waiting, grown, waited]);
  const hold = !waiting && !grown && waited > 0 ? { minHeight: waited } : undefined;
  return (
    <div className={hold ? `${heldBox} ${heldSkeleton}` : heldBox} style={hold}>
      <div ref={content} className={heldContent}>
        {children}
      </div>
    </div>
  );
}

function HunkRows({
  hunks,
  reserved,
  file,
  place,
  comparison,
  select,
}: {
  hunks: ReviewLaneHunk[];
  reserved: number;
  file: ReviewLaneFile;
  place: ReviewPlace;
  comparison: ReviewTreeComparison;
  select: LaneSelect;
}) {
  if (hunks.length === 0 && reserved === 0) return null;
  const selected = select.selection?.kind === 'block' ? blockKey(select.selection.block) : null;
  // One list in both states, so that the read's rows replace the reserved ones inside the same block.
  return (
    <ul className={hunkList} data-testid="review-lane-hunks" aria-hidden={hunks.length === 0 || undefined}>
      {hunks.length === 0 ? reservedRows(reserved, file, place, comparison) : hunks.map((hunk) => {
        const identity = hunkIdentityOf(file.path, hunk, comparison);
        const key = blockKey(identity);
        const at = hunk.after.count > 0 ? hunk.after : hunk.before;
        const label = `${spanLabel(at.start, at.count)} · ${hunk.classification.replace('_', ' ')}`;
        return (
          <li
            key={key}
            className={`${addToTrayHost} ${muted}`}
            {...laneRowAttrs(blockDomKey(key))}
            tabIndex={-1}
            data-testid="review-lane-hunk"
            data-selected={selected === key ? 'true' : undefined}
          >
            <button
              type="button"
              className={hunkButton}
              data-own-look
              aria-current={selected === key || undefined}
              onClick={() => select.onSelect({ kind: 'block', block: identity })}
            >
              {label}
            </button>{' '}
            <AddToTray
              pick={laneCodePick({ repository: place.repository, comparison, path: file.path, hunk })}
              label={`hunk ${file.path} ${spanLabel(at.start, at.count)}`}
            />
          </li>
        );
      })}
    </ul>
  );
}

// Hands the page a file's hunks and line counts once its classification is read.
function useReportFile(path: string, read: FileRead, comparison: ReviewTreeComparison, onFile: LaneSelect['onFile']): void {
  useEffect(() => {
    const hunks = hunksOf(read);
    if (!onFile || hunks.length === 0) return;
    const counts = lineCounts(hunks);
    onFile(path, { hunks: hunks.map((hunk) => hunkIdentityOf(path, hunk, comparison)), lines: `+${counts.plus} −${counts.minus}` });
  }, [path, read, comparison, onFile]);
}

// D383 (c3794ebb): a file's diff body is drawn for the first time with no size from the opening's
// reads (the lane read gives its hunk count, whose rows are reserved, and no line count). Until its
// read arrives, in view or not, it shows skeleton lines at a default height, about four diff lines;
// a file in view never shows "Read when in view…" (f43ae286).
const firstDraw = css({ height: '5rem' });

function FileBody({
  file,
  read,
  layout,
  collapse,
}: {
  file: ReviewLaneFile;
  read: FileRead;
  layout: DiffMode;
  collapse: boolean;
}) {
  if (file.content !== 'text')
    return <p className={muted}>{file.content} file · no text diff is drawn · {file.reason}</p>;
  if (read.phase === 'waiting')
    return (
      <div className={`${heldSkeleton} ${firstDraw}`} data-testid="review-lane-file-waiting">
        <span className={css({ srOnly: true })}>Reading the diff…</span>
      </div>
    );
  if (read.phase === 'unavailable')
    return <ReviewProblemBlock origin="failure" subject={`the diff of ${file.path}`} problem={read.problem} />;
  return <FileDiffBody file={file} diff={read.diff} layout={layout} collapse={collapse} />;
}

// A text's lines: the newline that ends the last line starts no line of its own.
function linesOf(text: string): string[] {
  const lines = text.split('\n');
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

// The file's text diff; an added file longer than ADDED_FILE_CAP lines shows its first lines only.
function cappedSides(diff: FileDiff): { before: string; after: string; more: number } {
  const lines = linesOf(diff.after?.content ?? '');
  const capped = diff.before === null && lines.length > ADDED_FILE_CAP;
  return {
    before: diff.before?.content ?? '',
    after: capped ? lines.slice(0, ADDED_FILE_CAP).join('\n') : (diff.after?.content ?? ''),
    more: capped ? lines.length - ADDED_FILE_CAP : 0,
  };
}

// The change-set page's filter that lists the file: the lane read's destination it came from
// (`laneFiles`). The page opens the asked path in that filter's list, and its first file when the
// list lacks it (MIK-R86 v2 rule 14).
const filterOf = (file: ReviewLaneFile): ReviewChangeFilter => (file.bucket === 'attribution_unknown' ? 'unknown' : 'unexplained');

function FileDiffBody({ file, diff, layout, collapse }: { file: ReviewLaneFile; diff: FileDiff; layout: DiffMode; collapse: boolean }) {
  const open = useContext(ReviewChangeSetNavigation);
  const { before, after, more } = cappedSides(diff);
  return (
    <>
      <div data-review-content data-testid="review-lane-diff">
        <DiffPane before={before} after={after} language={diff.language} mode={layout} collapse={collapse} fit />
      </div>
      {more > 0 ? (
        <button
          type="button"
          data-testid="review-lane-more"
          className={css({ display: 'block', width: '100%', textAlign: 'left', fontSize: '0.72rem' })}
          onClick={() => open?.(filterOf(file), file.path)}
        >
          {more} more added lines · open the file in the diff viewer
        </button>
      ) : null}
    </>
  );
}

// Ruling 719ade09 (D401): a file's "+N −N" drawn as the mockup's head draws it (app.js:314,
// styles.css:94-95 and 207): the added count in mint, the removed in alarm red, at the head's 0.72rem.
// Each keeps its sign in its text, so colour is never the only signal.
const countsBox = css({ fontSize: '0.72rem' });

function LineCounts({ plus, minus }: { plus: number; minus: number }) {
  return (
    <span className={countsBox} data-testid="review-lane-file-lines">
      <span className={css({ color: 'mint' })}>+{plus}</span> <span className={css({ color: 'alarm' })}>−{minus}</span>
    </span>
  );
}

function LaneFileBlock(props: {
  file: ReviewLaneFile;
  place: ReviewPlace;
  comparison: ReviewTreeComparison;
  layout: DiffMode;
  collapse: boolean;
  select: LaneSelect;
}) {
  const { file, place, comparison, select } = props;
  const ref = useRef<HTMLElement>(null);
  const visible = useInView(ref, true);
  const read = useLaneFileRead(place, comparison.number, file, visible);
  const classifies = useContext(IntentMarkerScope)?.classify !== undefined;
  useReportFile(file.path, read, comparison, select.onFile);
  const hunks = hunksOf(read);
  const counts = lineCounts(hunks);
  const fileSelected = select.selection?.kind === 'lane' && select.selection.path === file.path;
  return (
    <section
      ref={ref}
      className={`${block} ${addToTrayHost}`}
      data-testid="review-lane-file"
      data-path={file.path}
      data-lane-read={read.phase}
      data-selected={fileSelected ? 'true' : undefined}
      id={laneFileDomId(file.path)}
    >
      <div className={blockHead}>
        <span className={css({ color: 'muted' })}>
          <span className={css({ opacity: 0.7 })}>{dirOf(file.path)}</span>
          {nameOf(file.path)}
        </span>
        <span className={badge}>{file.status}</span>
        {hunks.length ? (
          <LineCounts plus={counts.plus} minus={counts.minus} />
        ) : (
          <span className={muted}>{file.counts.hunks} hunks</span>
        )}
        <span className={css({ flex: 1 })} />
        <AddToTray pick={laneCodePick({ repository: place.repository, comparison, path: file.path })} label={`file ${file.path}`} />
      </div>
      <HeldBody waiting={read.phase === 'waiting'}>
        <HunkRows
          hunks={hunks}
          reserved={reservedRowsOf(file, read, classifies)}
          file={file}
          place={place}
          comparison={comparison}
          select={select}
        />
        <FileBody file={file} read={read} layout={props.layout} collapse={props.collapse} />
      </HeldBody>
    </section>
  );
}

// The mockup's lane heading (D401; app.js `laneSection`, styles.css `.attach`, `.smeta`): its links are
// quiet bordered controls, and its counts line is the dim meta line. The links keep their own words
// (MIK-R86 v2 rule 10). The mockup's dim is the theme's muted text toward the page ground.
const dim = 'color-mix(in oklab, token(colors.muted) 82%, token(colors.bg))';
const attach = css({
  background: 'transparent',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  color: 'muted',
  font: 'inherit',
  fontSize: '0.72rem',
  paddingBlock: '0.05rem',
  paddingInline: '0.4rem',
  whiteSpace: 'nowrap',
  cursor: 'pointer',
  _hover: { color: 'cyan', borderColor: 'cyan' },
});
const meta = css({ margin: '0.3rem 0 0', fontSize: '0.72rem', color: dim });

// The label before the lane's files (mockup: "Changed files · no recorded intent claims these hunks"),
// in words true of the files listed: a file of unknown attribution may be claimed without a range.
function filesNote(lane: ReviewUnexplainedLane): string {
  const unknown = lane.unknown_attribution?.files.length ?? 0;
  if (unknown === 0) return 'no recorded intent claims these hunks';
  if ((lane.unexplained_changes?.files.length ?? 0) === 0) return 'the attribution of these hunks is unknown';
  return 'no recorded intent claims these hunks, or their attribution is unknown';
}

// Drawn when the section draws its files.
function LaneFilesLabel({ read, drawn }: { read: LaneRead<ReviewUnexplainedLane> | null; drawn: boolean }) {
  if (!drawn || read?.phase !== 'ready') return null;
  return (
    <div className={sublabel} data-testid="review-lane-files-label">
      Changed files <span>{filesNote(read.value)}</span>
    </div>
  );
}

function FullPageLinks({ lane }: { lane: ReviewUnexplainedLane }) {
  const open = useContext(ReviewChangeSetNavigation);
  const unexplained = lane.unexplained_changes?.files.length ?? 0;
  const unknown = lane.unknown_attribution?.files.length ?? 0;
  return (
    <>
      {unexplained ? (
        <button type="button" className={attach} data-own-look data-testid="review-lane-full-page" data-filter="unexplained" onClick={() => open?.('unexplained')}>
          Full page · Unexplained ↗
        </button>
      ) : null}
      {unknown ? (
        <button type="button" className={attach} data-own-look data-testid="review-lane-full-page" data-filter="unknown" onClick={() => open?.('unknown')}>
          Full page · Unknown attribution ↗
        </button>
      ) : null}
    </>
  );
}

function laneMeta(lane: ReviewUnexplainedLane): string {
  const total = lane.changed_total ?? '?';
  return `${total} changed files: ${lane.attributed ?? '?'} attributed, ${lane.unexplained ?? '?'} that no recorded entry claims, ${lane.attribution_unknown ?? '?'} of unknown attribution.`;
}

// The section's heading and the lane read's own state: reading, its owner's failure, or its counts.
function LaneHead({ read, count }: { read: LaneRead<ReviewUnexplainedLane> | null; count: number }) {
  const ready = read?.phase === 'ready' ? read.value : null;
  return (
    <>
      <div className={sectionHead}>
        <span className={subjectId}>lane</span>
        <h3>Code without invariant</h3>
        <span className={badge} data-testid="review-lane-count">
          {ready ? `${count} files` : '…'}
        </span>
        {ready ? <FullPageLinks lane={ready} /> : null}
      </div>
      {read === null || read.phase === 'loading' ? <p className={muted} role="status">Reading the change set&apos;s lane…</p> : null}
      {read?.phase === 'unavailable' ? (
        <ReviewProblemBlock origin="failure" subject="code without invariant" problem={read.problem} />
      ) : null}
      {ready ? (
        <p className={meta} data-testid="review-lane-meta">
          {laneMeta(ready)}
        </p>
      ) : null}
    </>
  );
}

export function LaneSection({
  read,
  place,
  comparison,
  layout,
  collapse,
  select,
}: {
  read: LaneRead<ReviewUnexplainedLane> | null;
  place: ReviewPlace;
  comparison: ReviewTreeComparison | null;
  layout: DiffMode;
  collapse: boolean;
  select: LaneSelect;
}) {
  const files = read?.phase === 'ready' ? laneFiles(read.value) : [];
  const whole = select.selection?.kind === 'lane' && select.selection.path === undefined;
  return (
    <article
      className={subjectBox}
      id="review-lane"
      data-testid="review-lane-section"
      data-lane-state={read?.phase ?? 'waiting'}
      data-selected={whole ? 'true' : undefined}
    >
      <LaneHead read={read} count={files.length} />
      <LaneFilesLabel read={read} drawn={comparison !== null && files.length > 0} />
      {comparison
        ? files.map((file) => (
            <LaneFileBlock
              key={file.path}
              file={file}
              place={place}
              comparison={comparison}
              layout={layout}
              collapse={collapse}
              select={select}
            />
          ))
        : null}
    </article>
  );
}
