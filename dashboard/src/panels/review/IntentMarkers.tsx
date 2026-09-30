// Per-hunk intent markers in the reviewer's diffs (MIK-R34): each hunk of a changed text file names the
// invariants whose recorded ranges meet its changed lines, and following one opens that invariant at
// its tree position; `Back to <file>` returns to the hunk, with focus on its marker.
//
// Every mark comes from the one per-file classification (MIK-R32) through hunkMarkers.ts, placed on the
// side line numbers it names. The marks sit in the diff's own gutter (file-viewer/markGutter.tsx); a
// mark's list opens below the pane. Nothing here edits, comments on or assesses a change.
import { useContext, useEffect, useId, useState, type ReactNode } from 'react';

import { css } from '../../../styled-system/css';
import type { ReviewSourceExpansion, ReviewSourceSide } from '../../data/review';
import type { ReviewFileClassification } from '../../data/reviewLane';
import type { ReviewTreeEntrySide } from '../../data/reviewTrees';
import type { PaneMark, PaneMarks } from '../file-viewer/markGutter';
import {
  type FileMarks,
  type HunkMark,
  type MarkEntry,
  type MarkTarget,
  type PaneLayout,
  type PaneWindow,
  fileMarks,
  markAnchor,
  occurrenceLabel,
  sidesLabel,
  wholeSide,
} from './hunkMarkers';
import {
  IntentMarkerScope,
  type MarkRead,
  type MarkerAt,
  useMarkClassification,
} from './intentMarkerScope';
import { sideLines, spanLabel } from './laneFocus';

const muted = css({ color: 'muted', fontSize: '0.72rem', margin: '0.2rem 0' });
const markButton = css({
  display: 'block',
  width: '100%',
  margin: '0.1rem 0',
  padding: '0 0.25rem',
  background: 'transparent',
  border: '1px solid currentColor',
  borderRadius: '2px',
  font: 'inherit',
  lineHeight: '1.3',
  textAlign: 'left',
  whiteSpace: 'normal',
  overflowWrap: 'anywhere',
  cursor: 'pointer',
  '&[data-mark=linked]': { color: 'cyan' },
  '&[data-mark=unexplained]': { color: 'amber' },
  '&[data-mark=attribution_unknown]': { color: 'muted', borderStyle: 'dashed' },
  '&[aria-expanded=true]': { background: 'color-mix(in oklab, var(--amber) 18%, transparent)' },
  _hover: { background: 'color-mix(in oklab, var(--amber) 10%, transparent)' },
  _focusVisible: { outline: '2px solid var(--amber)', outlineOffset: '1px' },
  // A phone-width gutter shows the compact text; the label stays the text and the accessible name.
  '@media (max-width: 40rem)': {
    padding: '0 0.1rem',
    textAlign: 'center',
    '& > span': { display: 'none' },
    _after: { content: 'attr(data-compact)' },
  },
});
const panel = css({
  border: '1px solid var(--grid)',
  borderLeft: '2px solid var(--amber)',
  borderRadius: '2px',
  background: 'bgPanel',
  padding: '0.45rem 0.7rem',
  margin: '0.45rem 0',
  fontSize: '0.75rem',
  minWidth: 0,
});
const panelHead = css({
  display: 'flex',
  gap: '0.6rem',
  flexWrap: 'wrap',
  alignItems: 'baseline',
  justifyContent: 'space-between',
});
const entries = css({
  listStyle: 'none',
  margin: '0.35rem 0',
  padding: 0,
  display: 'grid',
  gap: '0.45rem',
});
const occurrences = css({
  display: 'flex',
  flexWrap: 'wrap',
  gap: '0.35rem',
  marginTop: '0.25rem',
});
const fileMark = css({ fontSize: '0.75rem', margin: '0.3rem 0', color: 'muted' });
const toneBadge = css({
  border: '1px solid currentColor',
  borderRadius: '2px',
  padding: '0.05rem 0.4rem',
  marginRight: '0.4rem',
  fontSize: '0.7rem',
  whiteSpace: 'nowrap',
  '&[data-mark=unexplained]': { color: 'amber' },
  '&[data-mark=attribution_unknown]': { color: 'muted', borderStyle: 'dashed' },
});

// -- one pane's marks -------------------------------------------------------------------------------

export interface PaneMarkInput {
  path: string;
  // Which pane of the file this is ("file", "lane-full", "card:<key>", a lane window): a return
  // restores the list and the focus in the pane the marker was followed from.
  pane: string;
  file: ReviewFileClassification;
  layout: PaneLayout;
  window: PaneWindow;
  // The pane draws the whole text: a hunk it cannot place lies past the text the read returned.
  whole?: boolean;
}

export interface PaneMarking {
  marks?: PaneMarks;
  // The open marker's list, rendered below the pane.
  panel: ReactNode;
  // The file-level mark, or what keeps a hunk unmarked here; rendered above the pane.
  note: ReactNode;
}

const NO_MARKING: PaneMarking = { panel: null, note: null };

// The hunk of this pane a return is bringing the reader back to, if any.
function returningHunk(
  returning: MarkerAt | null | undefined,
  input: PaneMarkInput | null,
): string | null {
  if (!returning || !input) return null;
  return returning.path === input.path && returning.pane === input.pane ? returning.hunk : null;
}

export function usePaneMarking(input: PaneMarkInput | null): PaneMarking {
  const scope = useContext(IntentMarkerScope);
  const back = returningHunk(scope?.returning, input);
  const [open, setOpen] = useState<string | null>(back);
  const panelId = useId();
  // A return to this pane reopens the list the marker was followed from.
  useEffect(() => {
    if (back) setOpen(back);
  }, [back]);
  if (!scope || !input) return NO_MARKING;
  const marks = fileMarks(input.file);
  if (marks.kind === 'file') return { panel: null, note: <FileMarkNote marks={marks} /> };
  const placed = marks.hunks.flatMap((mark) => {
    const anchor = markAnchor(mark, input.layout, input.window);
    return anchor ? [{ mark, anchor }] : [];
  });
  const shown = placed.find((one) => one.mark.key === open)?.mark;
  const unplaced = input.whole ? marks.hunks.length - placed.length : 0;
  const button = (mark: HunkMark) => (
    <MarkButton
      mark={mark}
      path={input.path}
      open={open === mark.key}
      panelId={panelId}
      onToggle={() => setOpen(open === mark.key ? null : mark.key)}
    />
  );
  return {
    marks: {
      marks: placed.map(({ mark, anchor }): PaneMark => ({
        id: mark.key,
        side: anchor.side,
        line: anchor.line,
        label: mark.label,
        compact: mark.compact,
        node: button(mark),
      })),
      reveal: back,
      onRevealed: scope.settle,
    },
    panel: shown ? (
      <MarkPanel
        id={panelId}
        mark={shown}
        onClose={() => setOpen(null)}
        onFollow={(target) =>
          scope.follow({ path: input.path, pane: input.pane, hunk: shown.key }, target)
        }
      />
    ) : null,
    note: unplaced > 0 ? <PastTextNote count={unplaced} /> : null,
  };
}

function MarkButton({
  mark,
  path,
  open,
  panelId,
  onToggle,
}: {
  mark: HunkMark;
  path: string;
  open: boolean;
  panelId: string;
  onToggle: () => void;
}) {
  const where = `before ${spanLabel(mark.before)}, after ${spanLabel(mark.after)}`;
  return (
    <button
      type="button"
      className={markButton}
      // `data-path` also keeps the surface's generic button chrome off a gutter mark.
      data-path={path}
      data-testid="review-hunk-mark"
      data-mark={mark.tone}
      data-hunk={mark.key}
      data-before={spanLabel(mark.before)}
      data-after={spanLabel(mark.after)}
      data-compact={mark.compact}
      aria-expanded={open}
      aria-controls={open ? panelId : undefined}
      aria-label={`${mark.label} · hunk ${where}`}
      title={`${mark.label} · hunk ${where}`}
      onClick={onToggle}
    >
      <span>{mark.label}</span>
    </button>
  );
}

const TONE_NOTES: Record<HunkMark['tone'], string> = {
  linked:
    'Listed because a recorded range meets a changed line of this hunk; that asserts no coverage, correctness or preservation.',
  unexplained: "No recorded entry's range meets this hunk's changed lines on either side.",
  attribution_unknown:
    'Whether a recorded entry explains these changed lines is not established on the side(s) below.',
};

function MarkPanel({
  id,
  mark,
  onClose,
  onFollow,
}: {
  id: string;
  mark: HunkMark;
  onClose: () => void;
  onFollow: (target: MarkTarget) => void;
}) {
  const where = `before ${spanLabel(mark.before)} → after ${spanLabel(mark.after)}`;
  return (
    <section
      id={id}
      className={panel}
      data-testid="review-hunk-mark-panel"
      data-hunk={mark.key}
      data-mark={mark.tone}
      aria-label={`Intent marks of the hunk at ${where}`}
    >
      <div className={panelHead}>
        <strong>{mark.label}</strong>
        <span className={muted}>{where}</span>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      <EntryList title="Intents" entries={mark.realizations} onFollow={onFollow} />
      <EntryList title="Tests" entries={mark.proofs} onFollow={onFollow} />
      {mark.unknown.map((one) => (
        <p
          key={`${one.side}:${one.detail}`}
          className={muted}
          data-testid="review-hunk-mark-unknown"
          data-side={one.side}
        >
          {one.side}: attribution unknown — {one.detail}
        </p>
      ))}
      <p className={muted}>{TONE_NOTES[mark.tone]}</p>
    </section>
  );
}

function revisionsText(entry: MarkEntry): string {
  return entry.revisions.length ? ` · r${entry.revisions.join(' → r')}` : '';
}

// One kind's intersecting invariants: realizations under "Intents", proofs under "Tests" (each marked
// as a test with its facet). The two lists are never merged into one count.
function EntryList({
  title,
  entries: listed,
  onFollow,
}: {
  title: string;
  entries: MarkEntry[];
  onFollow: (target: MarkTarget) => void;
}) {
  if (!listed.length) return null;
  return (
    <div data-testid="review-hunk-mark-group" data-group={title.toLowerCase()}>
      <p className={muted}>
        {title} ({listed.length})
      </p>
      <ul className={entries}>
        {listed.map((entry) => (
          <li
            key={entry.key}
            data-testid="review-hunk-mark-entry"
            data-kind={entry.kind}
            data-invariant={entry.invariant}
          >
            <span>
              {entry.invariant}
              {revisionsText(entry)}
              {entry.kind === 'proof' ? ' · test' : ''}
            </span>
            <span className={muted}> · {entry.entryIds.join(', ')}</span>
            {entry.facets.map((facet) => (
              <p key={facet} className={muted} data-testid="review-hunk-mark-facet">
                facet: {facet}
              </p>
            ))}
            <div className={occurrences}>
              {entry.occurrences.map((occurrence) => (
                <button
                  type="button"
                  key={occurrence.key}
                  data-testid="review-hunk-mark-occurrence"
                  data-state={occurrence.state}
                  data-family={occurrence.family}
                  data-invariant={entry.invariant}
                  data-sides={occurrence.sides.join(',')}
                  onClick={() => onFollow(occurrence.target)}
                >
                  {occurrenceLabel(occurrence)} ({sidesLabel(occurrence.sides)}
                  {occurrence.revision === undefined ? '' : `, r${occurrence.revision}`})
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

const FILE_MARK_TEXT: Record<'unexplained' | 'attribution_unknown', string> = {
  unexplained:
    'one mark for the whole file: neither memory tree records a realization or proof entry for this path, so its hunks carry no marks of their own.',
  attribution_unknown: "neither side's knowledge could be read, so no hunk is marked",
};

// The one mark of a file whose hunks carry none, for a view that draws it once above several panes
// (the lane's windows); nothing outside a tree comparison's workspace, nothing for a marked file.
export function FileMark({ file }: { file: ReviewFileClassification }) {
  const scope = useContext(IntentMarkerScope);
  const marks = fileMarks(file);
  return scope && marks.kind === 'file' ? <FileMarkNote marks={marks} /> : null;
}

function FileMarkNote({ marks }: { marks: Extract<FileMarks, { kind: 'file' }> }) {
  return (
    <p className={fileMark} data-testid="review-file-mark" data-mark={marks.tone}>
      <span className={toneBadge} data-mark={marks.tone}>
        {marks.tone === 'unexplained' ? 'unexplained' : 'attribution unknown'}
      </span>
      {FILE_MARK_TEXT[marks.tone]}
      {marks.tone === 'attribution_unknown' ? ` (${marks.detail})` : ''}
    </p>
  );
}

function PastTextNote({ count }: { count: number }) {
  return (
    <p className={muted} data-testid="review-marks-past-text">
      {count} hunk(s) lie past the bounded text drawn here and carry no mark in it.
    </p>
  );
}

// -- the source content's diff -----------------------------------------------------------------------

function sideWindow(side: ReviewSourceSide) {
  return side.state === 'present' && side.text !== undefined
    ? wholeSide(sideLines(side.text).length)
    : null;
}

// The classification describes exactly the blobs this pane draws; otherwise its lines are another
// file's lines, and no mark is placed.
export function describesDrawn(
  file: ReviewFileClassification,
  drawn: { before?: string; after?: string },
): boolean {
  const [before, after] = file.sides;
  return (
    (before.blob ?? null) === (drawn.before ?? null) &&
    (after.blob ?? null) === (drawn.after ?? null)
  );
}

// The marks of the landed source-content view of one entry (the explorer's opened file, a card's or
// the lane's full file): the whole text of each side, in the layout on screen.
export function useSourceMarking(
  expansion: ReviewSourceExpansion,
  layout: PaneLayout,
  markers?: { pane?: string; classification?: ReviewFileClassification },
): PaneMarking {
  const read = useMarkClassification(
    expansion.path,
    expansion.status !== 'unchanged',
    markers?.classification,
  );
  const file = read.phase === 'ready' ? read.value : null;
  const drawn = file && drawnSides(file, expansion) ? file : null;
  const marking = usePaneMarking(
    drawn && sourceInput(expansion, drawn, layout, markers?.pane ?? 'file'),
  );
  return {
    ...marking,
    note: (
      <>
        <MarkReadNote read={read} mismatch={file !== null && drawn === null} />
        {marking.note}
      </>
    ),
  };
}

function drawnSides(file: ReviewFileClassification, expansion: ReviewSourceExpansion): boolean {
  return describesDrawn(file, {
    before: expansion.before.object_id,
    after: expansion.after.object_id,
  });
}

// The whole text of each side; one diff editor per side only when both are drawn as a diff.
function sourceInput(
  expansion: ReviewSourceExpansion,
  file: ReviewFileClassification,
  layout: PaneLayout,
  pane: string,
): PaneMarkInput {
  const bothDrawn = expansion.before.state === 'present' && expansion.after.state === 'present';
  return {
    path: expansion.path,
    pane,
    file,
    layout: bothDrawn ? layout : 'split',
    window: { before: sideWindow(expansion.before), after: sideWindow(expansion.after) },
    whole: true,
  };
}

// -- a card's excerpt --------------------------------------------------------------------------------

// The lines of one side a card's excerpt draws: its resolved range's text, from its start line.
function excerptWindow(side: ReviewTreeEntrySide, drawn: boolean) {
  if (!drawn || side.state !== 'resolved' || side.excerpt === undefined) return null;
  const first = side.start_line ?? 1;
  return { first, last: first + sideLines(side.excerpt).length - 1 };
}

export interface ExcerptCard {
  key: string;
  path: string;
  before: ReviewTreeEntrySide;
  after: ReviewTreeEntrySide;
}

// Whether the classification describes the sides the excerpt draws. Only those are compared: a side
// the excerpt does not draw -- an unreadable memory side serves no blob -- places nothing, so it can
// never take the drawn side's marks away (review R1 F1). `null` when the excerpt draws no side.
function drawnSidesMatch(
  file: ReviewFileClassification,
  card: ExcerptCard,
  window: PaneWindow,
): boolean | null {
  const drawn = (['before', 'after'] as const).filter((side) => window[side] !== null);
  if (!drawn.length) return null;
  return drawn.every(
    (side) =>
      (file.sides.find((one) => one.side === side)?.blob ?? null) === (card[side].blob ?? null),
  );
}

// The marks of a card's excerpt (MIK-R34, with the L32 F5 rule): every owner hunk whose changed lines
// the excerpt draws, on the sides it draws, from the file's one classification -- only for a file
// whose two blobs differ, and only where the drawn sides' blobs are the ones the classification names.
export function useExcerptMarking(
  card: ExcerptCard,
  layout: PaneLayout,
  draws: { before: boolean; after: boolean },
): PaneMarking {
  const read = useMarkClassification(card.path, card.before.blob !== card.after.blob);
  const file = read.phase === 'ready' ? read.value : null;
  const window = {
    before: excerptWindow(card.before, draws.before),
    after: excerptWindow(card.after, draws.after),
  };
  const matches = file ? drawnSidesMatch(file, card, window) : null;
  const marking = usePaneMarking(
    file && matches
      ? {
          path: card.path,
          pane: `card-excerpt:${card.key}`,
          file,
          layout: window.before && window.after ? layout : 'split',
          window,
        }
      : null,
  );
  return {
    ...marking,
    note: (
      <>
        <MarkReadNote read={read} mismatch={matches === false} quietWhileReading />
        {marking.note}
      </>
    ),
  };
}

// Why a changed file shows no marks yet, or none at all: never silence, never a zero. A card excerpt
// is quiet while the read is under way (its marks simply appear).
export function MarkReadNote({
  read,
  mismatch,
  quietWhileReading = false,
}: {
  read: MarkRead;
  mismatch: boolean;
  quietWhileReading?: boolean;
}) {
  if (read.phase === 'unlisted')
    return (
      <p className={muted} data-testid="review-marks-state" data-marks-state="unlisted">
        intent marks unavailable: the change inventory is partial and does not list this path, so
        its classification is not read.
      </p>
    );
  if (read.phase === 'loading' && quietWhileReading) return null;
  if (read.phase === 'loading')
    return (
      <p className={muted} data-testid="review-marks-state" data-marks-state="loading">
        reading this file&apos;s intent marks…
      </p>
    );
  if (read.phase === 'unavailable')
    return (
      <p className={muted} data-testid="review-marks-state" data-marks-state="unavailable">
        intent marks unavailable · {read.problem.code}: {read.problem.detail}
      </p>
    );
  if (mismatch)
    return (
      <p className={muted} data-testid="review-marks-state" data-marks-state="other-content">
        intent marks not placed: the classification describes other content of this path than the
        text drawn here.
      </p>
    );
  return null;
}

// The marks of one side of a pane that draws each side in its own editor.
export function sideMarks(marks: PaneMarks | undefined, side: 'before' | 'after') {
  return marks && { ...marks, marks: marks.marks.filter((mark) => mark.side === side) };
}

// -- the return ------------------------------------------------------------------------------------

// `Back to <file>`: shown while a followed marker has not been returned to. It restores the file, its
// disclosure, the diff layout and the reading position at the hunk, and puts focus on the marker.
export function MarkerReturn({ className }: { className?: string }) {
  const scope = useContext(IntentMarkerScope);
  const origin = scope?.origin;
  if (!scope || !origin) return null;
  const name = origin.path.split('/').pop() || origin.path;
  return (
    <button
      type="button"
      className={className}
      data-testid="review-marker-return"
      data-return-path={origin.path}
      data-return-pane={origin.pane}
      data-return-hunk={origin.hunk}
      title={origin.path}
      onClick={scope.back}
    >
      ← Back to {name}
    </button>
  );
}
