// One block of the reading column (MIK-R89 v2 rules 4, 13; MIK-R31): one card-read entry (W1), its
// authored rationale directly above its excerpt, a changed range as its real diff and an unchanged
// range once. "Changes only | Full context" (the page's one control) passes the collapse of the diff
// (DiffPane, three lines of context around each change); the diff is side by side only when the
// reading column's content box is wide enough (reading/useReadingLayout.ts). No card has a scroll
// box. The card's intent marks are read only once it is in view (W5). A card has no Full file of its
// own: "Expand full file" goes on the review page and "Changes only | Full context" is the one control
// (rule 4); a whole file is read through the tree's one "All changes" link and the path links, never
// through a per-card copy (MIK-R86 v2 rule 11). The entries of one region share one card, which lists
// each with its rationale and its own ⊕ (changeSetOrder.ts `draws`; MIK-R86 v2 normative bullet 1).
import { Fragment, useContext, useLayoutEffect, useRef, useState } from 'react';

import { css } from '../../../styled-system/css';
import type { ReviewTreeComparison, ReviewTreeEntry, ReviewTreeEntrySide } from '../../data/reviewTrees';
import { AddToTray, addToTrayHost } from '../../tray/AddToTray';
import { reviewBlockPick } from '../../tray/pickBuilders';
import { DiffPane, type DiffMode } from '../changeset/DiffPane';
import { FilePane } from '../file-viewer/FilePane';
import type { PaneMarks } from '../file-viewer/markGutter';
import { blockIdentityOf, blockKey } from './blockIdentity';
import type { LocationNode } from './changeSetOrder';
import {
  cardVoice,
  drawnText,
  firstLines,
  languageOfPath,
  notCurrent,
  rangeLabel,
  type ExpressionCard,
} from './focusedCards';
import { sideMarks, useExcerptMarking } from './IntentMarkers';
import { blockDomKey } from './reading/blockDom';
import { useInView } from './reading/readGate';
import { block, blockHead, BLOCK_STOP, heldContent, heldSkeleton } from './reading/readingStyles';
import { ReadingContext, type ReviewPlace } from './reading/readingContext';

type CardEntry = ExpressionCard['entries'][number];

const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.2rem 0' });
const symbolText = css({ color: 'cyan', fontWeight: 700 });
const pathText = css({ color: 'muted', overflowWrap: 'anywhere', '& span': { opacity: 0.7 } });
const headButton = css({
  display: 'inline-flex',
  gap: '0.6rem',
  alignItems: 'baseline',
  minWidth: 0,
  background: 'transparent',
  border: 'none',
  padding: 0,
  font: 'inherit',
  color: 'inherit',
  cursor: 'pointer',
  textAlign: 'left',
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber', outlineOffset: '2px' },
});
const badge = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'amber',
  color: 'amber',
  borderRadius: '2px',
  paddingInline: '0.4rem',
  fontSize: '0.7rem',
  whiteSpace: 'nowrap',
});
const quietBadge = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  color: 'muted',
  borderRadius: '2px',
  paddingInline: '0.4rem',
  fontSize: '0.7rem',
  whiteSpace: 'nowrap',
});
// The block's introduction as the mockup's `.bnote` draws it (styles.css:210-211): one line per entry.
const voices = css({
  borderBottomWidth: '1px',
  borderBottomStyle: 'solid',
  borderBottomColor: 'grid',
  paddingBlock: '0.35rem',
  paddingInline: '0.7rem',
  display: 'grid',
  margin: 0,
  listStyle: 'none',
  fontSize: '0.74rem',
  lineHeight: '1.55',
  color: 'muted',
});
const voiceRow = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'baseline',
  columnGap: '0.4rem',
  minWidth: 0,
});
// A line of the introduction is plain text, selectable as any text of the page (MIK-R188 takes
// highlighted text from it): no box, no background, no border.
const voiceLine = css({
  flex: '1 1 0',
  minWidth: 0,
  cursor: 'pointer',
  userSelect: 'text',
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber' },
  '& b': { color: 'ink', fontWeight: 500 },
});
const voiceLineCut = css({ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' });
const voiceMetaText = css({ color: 'muted', fontSize: '0.7rem' });
// A revised entry's earlier rationale: its own plain, dimmer line under the entry's.
const beforeRow = css({
  display: 'flex',
  flexBasis: '100%',
  minWidth: 0,
  paddingLeft: '1rem',
  color: 'color-mix(in oklab, var(--muted) 80%, var(--bg))',
});
const readerOnly = css({ srOnly: true });
const stateBadge = css({
  flex: 'none',
  fontSize: '0.66rem',
  letterSpacing: '0.04em',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  paddingInline: '0.32rem',
  lineHeight: '1.45',
  whiteSpace: 'nowrap',
});
const gap = css({ color: 'amber' });
// The side lines divide by a 1px gap over the grid colour, so the divider follows the layout.
const sides = css({
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(12rem, 1fr))',
  gap: '1px',
  background: 'grid',
  '& > p': { margin: 0, paddingBlock: '0.3rem', paddingInline: '0.7rem', fontSize: '0.72rem', color: 'muted', background: 'bg' },
});

export interface BlockCardProps {
  node: LocationNode;
  place: ReviewPlace;
  comparison: ReviewTreeComparison;
  planning: Map<string, string>;
  layout: DiffMode;
  collapse: boolean;
  selected: boolean;
  onSelect: () => void;
}

// A changed range carries no badge: the mockup draws none (app.js:278-280), and its head's counts say
// it. An unchanged or not comparable range, which the mockup keeps in its groups, is named.
function changeBadge(card: ExpressionCard) {
  if (card.change === 'changed') return null;
  if (card.change === 'unchanged') return <span className={quietBadge}>Unchanged</span>;
  return <span className={badge}>Not comparable</span>;
}

// The head's "+N −N" as the mockup draws it (`.bhead .pm`, app.js:279; styles.css:95-96, 207; ruling
// 719ade09): the added count in mint, the removed count in alarm red, each sign in its own text so
// colour is never the only signal. A range whose counts are not known shows its span as before.
const COUNTS = /^\+(\d+) −(\d+)$/;

function LineCounts({ lines }: { lines: string }) {
  const counts = COUNTS.exec(lines);
  return (
    <span className={css({ fontSize: '0.72rem' })} data-testid="review-block-lines">
      {counts ? (
        <>
          <span className={css({ color: 'mint' })}>{`+${counts[1]}`}</span>{' '}
          <span className={css({ color: 'alarm' })}>{`−${counts[2]}`}</span>
        </>
      ) : (
        lines
      )}
    </span>
  );
}

function sideLine(name: 'Before' | 'After', side: ReviewTreeEntrySide) {
  const where = side.state === 'resolved' ? rangeLabel(side) : sideStateText(side);
  return (
    <p data-testid={`review-card-${name.toLowerCase()}`} data-side-state={side.state}>
      {name} · {where}
      {side.recorded === false ? ' · entry not recorded on this side' : ''}
    </p>
  );
}

// An unchanged card whose two ranges are the same lines: one line says so instead of two that repeat.
function sameRegion(card: ExpressionCard): boolean {
  const { before, after } = card;
  return (
    card.change === 'unchanged' &&
    before.path === after.path &&
    before.start_line === after.start_line &&
    before.end_line === after.end_line &&
    before.recorded === after.recorded
  );
}

// Where both sides are regions, the excerpt's own line numbers show both ranges, and the side lines
// are for a screen reader only, so the diff starts just below the introduction (ruling d9a1ec8f item
// 5). A side that is no region keeps its reason on screen.
function SideLines({ card }: { card: ExpressionCard }) {
  const regions = card.before.state === 'resolved' && card.after.state === 'resolved';
  return (
    <div className={regions ? `${sides} ${readerOnly}` : sides}>
      {sameRegion(card) ? (
        <p data-testid="review-card-both" data-side-state="resolved">
          Before and after · {rangeLabel(card.after)} · the same text on both sides
        </p>
      ) : (
        <>
          {sideLine('Before', card.before)}
          {sideLine('After', card.after)}
        </>
      )}
    </div>
  );
}

export function sideStateText(side: ReviewTreeEntrySide): string {
  if (side.state === 'absent') return 'no file on this side';
  if (side.state === 'unresolved') return `unresolved: ${side.reason ?? 'no reason given'}`;
  return `unavailable: ${side.reason ?? 'no reason given'}`;
}

function CardCurrentness({ card }: { card: ExpressionCard }) {
  // The mark names the entry, its MIK-R03 state and its side, exactly (F4). A recorded reason
  // reads beside it, outside any disclosure (F06) — never inside the mark's own text.
  const marks = notCurrent(card);
  if (!marks.length) return null;
  return (
    <p className={css({ margin: 0, paddingBlock: '0.2rem', paddingInline: '0.7rem' })}>
      {marks.map((mark) => (
        <Fragment key={`${mark.entry}:${mark.side}`}>
          <span
            className={css({ color: 'amber', fontSize: '0.72rem', marginRight: '0.8rem' })}
            data-testid="review-card-currentness"
            data-currentness={mark.state}
            data-entry={mark.entry}
            data-side={mark.side}
            data-review-content="currentness"
          >
            {mark.entry} {mark.state} on the {mark.side} side
          </span>
          {mark.reason ? (
            <span
              className={css({ color: 'amber', fontSize: '0.72rem', marginRight: '0.8rem' })}
              data-testid="review-card-currentness-reason"
              data-entry={mark.entry}
              data-side={mark.side}
              data-review-content="currentness"
            >
              : {mark.reason}
            </span>
          ) : null}
        </Fragment>
      ))}
    </p>
  );
}

// What an entry's line ends with: its invariant, its role (a proof entry's facet) and its planning mark.
function voiceMeta(entry: CardEntry, role: string | undefined, mark?: string): string {
  const parts = [entry.invariant, entry.kind === 'proof' ? 'proof facet' : (role ?? 'no role')];
  if (mark) parts.push(mark);
  return parts.join(' · ');
}

// The entry's state when it is not the normal one: a badge on its line, never behind a disclosure.
function voiceState(entry: CardEntry): string | null {
  if (!entry.after.recorded && entry.before.recorded) return 'retired in this leaf';
  if (entry.after.recorded && entry.before.recorded === false) return 'added in this leaf';
  return null;
}

// One line of a block's introduction (MIK-R86 v2 rule 19's prescription for long content, as ruled for
// the conditions in d9a1ec8f item 7): its own text, cut only by its overflow with an ellipsis and whole
// in its title; a click, or Enter or Space while it is focused, opens that one line in full, in place,
// and closes it again, as a condition's line does (IntentWordDiff.tsx `ItemLine`). It is no `<button>`,
// so its text stays selectable and it takes no control look; a click that ends a selection in it does
// not toggle it.
function OneLine({ testid, title, children }: { testid: string; title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen((was) => !was);
  return (
    <div
      role="button"
      tabIndex={0}
      className={open ? voiceLine : `${voiceLine} ${voiceLineCut}`}
      data-testid={testid}
      aria-expanded={open}
      title={title}
      onClick={(event) => {
        if (!selectedIn(event.currentTarget)) toggle();
      }}
      onKeyDown={(event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        event.stopPropagation();
        toggle();
      }}
    >
      {children}
    </div>
  );
}

// Whether the click ended with text selected in the line: the reader selected, and the line stays as
// it is.
function selectedIn(line: HTMLElement): boolean {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.toString() === '') return false;
  return line.contains(selection.anchorNode) || line.contains(selection.focusNode);
}

// An entry's line: "<entry id> <rationale> · <invariant> · <role>[ · <planning mark>]", or the named gap
// where no rationale (a proof entry's facet) is recorded.
function VoiceLine({ entry, said, meta }: { entry: CardEntry; said: string | undefined; meta: string }) {
  const missing = `No authored ${entry.kind === 'proof' ? 'facet' : 'rationale'} is recorded for ${entry.id}.`;
  return (
    <OneLine testid="review-card-voice-line" title={`${entry.id} ${said ?? missing} · ${meta}`}>
      <b>{entry.id}</b>{' '}
      {said ? (
        <span data-review-content data-testid="review-card-rationale">
          {said}
        </span>
      ) : (
        <span className={gap} data-testid="review-card-rationale-missing">
          {missing}
        </span>
      )}
      <span className={voiceMetaText}> · {meta}</span>
    </OneLine>
  );
}

// One entry of a block's introduction, as the mockup draws it (data-authored.js:17; ruling d9a1ec8f
// item 5) within MIK-R86 v2 rule 19: its one line; a state other than the normal one ("added in this
// leaf", "retired in this leaf", "rationale revised") as a visible badge beside it; and a revised
// entry's earlier rationale as a line of its own under it, "Before: …". Everything the entry says is
// on screen, cut only by a line's overflow. On a card several entries share, the line carries the
// entry's own ⊕ and, for an entry other than the card's own, the stop a selection of that entry
// scrolls to.
function Voice({
  entry,
  planning,
  tray,
  stop,
}: {
  entry: CardEntry;
  planning: Map<string, string>;
  tray?: React.ReactNode;
  stop?: string;
}) {
  const voice = cardVoice(entry);
  const state = voiceState(entry);
  const revised = voice.revised;
  return (
    <li
      data-testid="review-card-voice"
      data-entry={entry.id}
      data-invariant={entry.invariant}
      className={tray ? `${voiceRow} ${addToTrayHost}` : voiceRow}
      {...(stop ? { [BLOCK_STOP]: stop } : {})}
    >
      <VoiceLine
        entry={entry}
        said={entry.kind === 'proof' ? voice.facet : voice.rationale}
        meta={voiceMeta(entry, voice.role, planning.get(entry.invariant))}
      />
      {state ? (
        <span className={stateBadge} data-testid="review-card-state">
          {state}
        </span>
      ) : null}
      {revised !== undefined ? (
        <span className={stateBadge} data-testid="review-card-revised">
          rationale revised
        </span>
      ) : null}
      {tray}
      {revised !== undefined ? (
        <div className={beforeRow}>
          <OneLine testid="review-card-voice-before" title={`Before: ${revised}`}>
            <span data-review-content>Before: {revised}</span>
          </OneLine>
        </div>
      ) : null}
    </li>
  );
}

// What a pane keeps on screen while its editor is built again (D382: keeping the old content until
// the new content is drawn is best). A pane builds its editor again, once the language pack has
// resolved, when its content or its view changes, and a FilePane when its marks move (a review page
// DiffPane draws its marks in place). The destroyed editor's drawing, whole and inert, goes back into
// the emptied pane at once (a MutationObserver, before the browser paints) and leaves when the next
// editor is in the pane. Whole (rule 9, ruling fb5d04f6: a shown diff pane keeps its content and
// height until the new content is drawn and never passes through 0): CodeMirror takes an editor's
// gutters and their line numbers out before it removes the editor, and their marks' classes, and a
// side-by-side diff removes its two editors before its own root (WP8 on A4-F4: a split pane's kept
// drawing was an empty `div.cm-mergeView`, 0 px for up to 1.5 s; a unified pane's had lost its line
// numbers, its code 31 px further left). So what the same batch changed inside the removed root is
// undone, the last change first, before the root goes back.
const SNAPSHOT = 'data-held-snapshot';
// A mark's host, the container of a React portal (markGutter.tsx): what is inside it is React's.
const MARK_HOST = '.cm-paneMarkHost';

function keepLastDrawing(content: HTMLElement): () => void {
  const keeper = new MutationObserver((records) => {
    records.forEach((record, index) => {
      const pane = record.target;
      if (record.type !== 'childList' || !(pane instanceof HTMLElement) || pane.parentElement !== content) return;
      const fresh = [...record.addedNodes].some((node) => node instanceof Element && !node.hasAttribute(SNAPSHOT));
      if (fresh) for (const old of pane.querySelectorAll(`:scope > [${SNAPSHOT}]`)) old.remove();
      else if (pane.childElementCount === 0) holdDrawing(pane, record, records.slice(0, index));
    });
  });
  keeper.observe(content, { childList: true, subtree: true, attributes: true, attributeOldValue: true });
  return () => keeper.disconnect();
}

function holdDrawing(pane: HTMLElement, removal: MutationRecord, earlier: readonly MutationRecord[]): void {
  for (const node of removal.removedNodes) {
    if (!(node instanceof HTMLElement)) continue;
    for (const record of [...earlier].reverse()) undoInside(node, record);
    node.setAttribute(SNAPSHOT, '');
    node.setAttribute('aria-hidden', 'true');
    node.inert = true;
    pane.append(node);
  }
}

// Undoes one change inside `root`: an attribute gets its old value back; of a child list, what the
// change added goes, and what it removed, still out of the document, goes back where it was.
function undoInside(root: HTMLElement, record: MutationRecord): void {
  const target = record.target;
  if (!root.contains(target) || (target instanceof Element && target.closest(MARK_HOST))) return;
  if (record.type === 'attributes') undoAttribute(target, record);
  if (record.type === 'childList') undoChildren(root, target, record);
}

function undoAttribute(target: Node, record: MutationRecord): void {
  const name = record.attributeName;
  if (!(target instanceof Element) || !name) return;
  if (record.oldValue === null) target.removeAttribute(name);
  else target.setAttribute(name, record.oldValue);
}

function undoChildren(root: HTMLElement, target: Node, record: MutationRecord): void {
  for (const node of record.addedNodes) if (node.parentNode === target) target.removeChild(node);
  for (const node of [...record.removedNodes].reverse()) if (!node.isConnected && !root.contains(node)) putBack(target, node, record);
}

function putBack(target: Node, node: Node, record: MutationRecord): void {
  const { nextSibling: next, previousSibling: previous } = record;
  if (next?.parentNode === target) target.insertBefore(node, next);
  else if (previous?.parentNode === target) target.insertBefore(node, previous.nextSibling);
  else target.appendChild(node);
}

// A shown pane keeps its content and height while its editor is built again (rule 9, ruling
// fb5d04f6: a block that shows settled content at the column's top stays the reader's line while it
// is rebuilt; dev7 probe …081755: the `recordLabelOf` excerpt's pane went 127 → 0 → 196.8 px). The
// last editor stays on screen (`keepLastDrawing`), and the pane's last drawn height is its box's
// min-height from render on, with skeleton lines where nothing is drawn; each new drawing, at any
// height above 0, replaces it, so the box never goes through 0 and new content stands as drawn. A
// pane never drawn holds nothing and stays pending as before.
function HeldPane({ children }: { children: React.ReactNode }) {
  const content = useRef<HTMLDivElement>(null);
  const [drawn, setDrawn] = useState(0);
  useLayoutEffect(() => {
    const node = content.current;
    if (!node) return undefined;
    const measure = () => {
      const height = node.getBoundingClientRect().height;
      if (height > 0) setDrawn(height);
    };
    measure();
    const keep = keepLastDrawing(node);
    if (typeof ResizeObserver === 'undefined') return keep;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => {
      keep();
      observer.disconnect();
    };
  }, []);
  return (
    <div className={drawn > 0 ? heldSkeleton : undefined} style={drawn > 0 ? { minHeight: drawn } : undefined}>
      <div ref={content} className={heldContent}>
        {children}
      </div>
    </div>
  );
}

const PANE = '[data-testid="diff-pane"], [data-testid="file-pane"]';

// A pane drawing its marks: an element added to it that is not its kept drawing (its next editor), or
// its marks drawn in place.
function drawsMarks(record: MutationRecord): boolean {
  if (!(record.target instanceof Element) || !record.target.matches(PANE)) return false;
  if (record.type === 'attributes') return true;
  return [...record.addedNodes].some((added) => added instanceof Element && !added.hasAttribute(SNAPSHOT));
}

// Where an excerpt's marks sit in its panes. A change of it draws the panes again: a FilePane builds
// its editor again when a mark's place changes (`placement`, markGutter.tsx), a DiffPane draws its
// marks in place.
function marksKey(marks: PaneMarks | undefined): string {
  return (marks?.marks ?? []).map((mark) => `${mark.id}@${mark.side}:${mark.line}`).join('|');
}

// The whole excerpt (its panes, the lines under them, its marks' state line) keeps its last height
// from the render in which its marks land until a pane draws them (its next editor, or DiffPane's
// `data-marks` for marks drawn in place); from then on the new content stands as drawn (rule 9, ruling
// fb5d04f6, D382). The marks' landing takes the "intent marks loading…" line away at once, a frame
// before the pane draws them (final2 …100732: the `recordLabelOf` excerpt went 148.6 → 127 px before
// its pane was held). Marks that place nothing draw no pane again and hold nothing.
function HeldExcerpt({ marks, children }: { marks: string; children: React.ReactNode }) {
  const content = useRef<HTMLDivElement>(null);
  const [last, setLast] = useState(0);
  const [drawnWith, setDrawnWith] = useState(marks);
  const holding = marks !== drawnWith;
  useLayoutEffect(() => {
    const node = content.current;
    if (!node || holding) return undefined;
    const measure = () => {
      const height = node.getBoundingClientRect().height;
      if (height > 0) setLast(height);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [holding]);
  useLayoutEffect(() => {
    const node = content.current;
    if (!node || !holding) return undefined;
    const drawn = new MutationObserver((records) => {
      if (records.some(drawsMarks)) setDrawnWith(marks);
    });
    drawn.observe(node, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-marks'] });
    return () => drawn.disconnect();
  }, [holding, marks]);
  const hold = holding && last > 0;
  return (
    <div className={hold ? heldSkeleton : undefined} style={hold ? { minHeight: last } : undefined}>
      <div ref={content} className={heldContent}>
        {children}
      </div>
    </div>
  );
}

interface ExcerptProps {
  card: ExpressionCard;
  language: string;
  layout: DiffMode;
  collapse: boolean;
  visible: boolean;
}

function Excerpt(props: ExcerptProps) {
  if (props.card.change === 'unchanged') return <UnchangedExcerpt {...props} />;
  if (props.card.change === 'changed') return <ChangedExcerpt {...props} />;
  return <SeparateSides {...props} />;
}

// An unchanged range: its text once (the two sides share one content identity).
function UnchangedExcerpt({ card, language, visible }: ExcerptProps) {
  const { after } = card;
  // A range with the same text on both sides can still hold changed lines (code moved as a whole).
  const marking = useExcerptMarking(card, 'split', { before: false, after: true }, visible);
  if (after.excerpt === undefined)
    return <p className={muted}>{after.reason ?? 'No excerpt was read for this range.'}</p>;
  return (
    <div data-review-content data-testid="review-card-excerpt" data-excerpt="unchanged">
      <HeldExcerpt marks={marksKey(marking.marks)}>
        <HeldPane>
          <FilePane content={drawnText(after.excerpt)} language={language} firstLine={after.start_line} fit marks={marking.marks} />
        </HeldPane>
        <Truncated side={after} />
        {marking.note}
        {marking.panel}
      </HeldExcerpt>
    </div>
  );
}

// A changed range: the real diff of the two sides' excerpts; a side with no file is the empty
// operand of an added or deleted file, which the side line above names. Changes only folds every
// unchanged stretch to "N unchanged lines" with three lines of context (DiffPane's collapse).
function ChangedExcerpt({ card, language, layout, collapse, visible }: ExcerptProps) {
  const { before, after } = card;
  const marking = useExcerptMarking(card, layout, { before: true, after: true }, visible);
  const beforeText = before.state === 'resolved' ? before.excerpt : '';
  const afterText = after.state === 'resolved' ? after.excerpt : '';
  if (beforeText === undefined || afterText === undefined)
    return <p className={muted}>{before.reason ?? after.reason ?? 'No excerpt was read.'}</p>;
  return (
    <div data-review-content data-testid="review-card-excerpt" data-excerpt="diff" data-collapse={String(collapse)}>
      <HeldExcerpt marks={marksKey(marking.marks)}>
        <HeldPane>
          <DiffPane
            before={drawnText(beforeText)}
            after={drawnText(afterText)}
            language={language}
            mode={layout}
            collapse={collapse}
            firstLine={firstLines(card)}
            fit
            marks={marking.marks}
          />
        </HeldPane>
        <Truncated side={before} />
        <Truncated side={after} />
        {marking.note}
        {marking.panel}
      </HeldExcerpt>
    </div>
  );
}

// A card whose two sides are not both regions: each readable side's text on its own, never a diff,
// because a diff would claim the other side is a known operand.
function SeparateSides({ card, language, visible }: ExcerptProps) {
  const marking = useExcerptMarking(card, 'split', { before: true, after: true }, visible);
  return (
    <div data-review-content data-testid="review-card-excerpt" data-excerpt="separate">
      <HeldExcerpt marks={marksKey(marking.marks)}>
        {(['before', 'after'] as const).map((name) => {
          const side = card[name];
          return side.state === 'resolved' && side.excerpt !== undefined ? (
            <div key={name}>
              <p className={muted} style={{ padding: '0.3rem 0.7rem' }}>
                {name} side text · no comparison is drawn
              </p>
              <HeldPane>
                <FilePane content={drawnText(side.excerpt)} language={language} firstLine={side.start_line} fit marks={sideMarks(marking.marks, name)} />
              </HeldPane>
            </div>
          ) : null;
        })}
        {marking.note}
        {marking.panel}
      </HeldExcerpt>
    </div>
  );
}

function Truncated({ side }: { side: ReviewTreeEntrySide }) {
  return side.excerpt_truncated ? (
    <p className={muted} style={{ padding: '0 0.7rem' }}>
      The excerpt shows the start of {rangeLabel(side)}; the tree's All changes opens the whole file.
    </p>
  ) : null;
}

const dirOf = (path: string) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/') + 1) : '');
const nameOf = (path: string) => path.slice(path.lastIndexOf('/') + 1);

// The block's ⊕ (W29): its identity, with the symbol of the side it names in the chip's title. The
// server reads its invariant's revision at the memory tree of that side (MIK-R188 rule 2); the page row's
// revision does not say which side it is of.
function blockTrayPick(place: ReviewPlace, entry: ReviewTreeEntry, comparison: ReviewTreeComparison) {
  const side = blockIdentityOf(entry, comparison).side;
  return reviewBlockPick({ repository: place.repository, comparison, entry, symbol: entry[side].symbol });
}

function BlockHead({ node, place, comparison, onSelect }: Pick<BlockCardProps, 'node' | 'place' | 'comparison' | 'onSelect'>) {
  const { card } = node;
  return (
    <div className={blockHead}>
      <button type="button" className={headButton} data-testid="review-block-select" onClick={onSelect}>
        {node.symbol ? <span className={symbolText}>{node.symbol}</span> : null}
        <span className={pathText}>
          <span>{dirOf(node.path)}</span>
          {nameOf(node.path)}
        </span>
        <span className={css({ color: 'muted' })}>{node.range}</span>
      </button>
      <span className={quietBadge} data-testid="review-block-role">
        {card.kind === 'proof' ? 'proof' : node.role}
      </span>
      <LineCounts lines={node.lines} />
      {changeBadge(card)}
      <span className={css({ flex: 1 })} />
      {card.entries.length === 1 ? (
        <AddToTray pick={blockTrayPick(place, node.entry, comparison)} label={`block ${node.entry.id}`} />
      ) : null}
    </div>
  );
}

// An entry that its region's first location draws has no card of its own (changeSetOrder.ts `draws`).
export function ReviewBlockCard(props: BlockCardProps) {
  return props.node.draws.length ? <BlockCard {...props} /> : null;
}

function BlockCard(props: BlockCardProps) {
  const { node, planning, layout, collapse, place, comparison } = props;
  const { card } = node;
  const ref = useRef<HTMLElement>(null);
  const visible = useInView(ref);
  // The card is selected for a selection of any entry it draws.
  const selectedBlock = useContext(ReadingContext)?.selectedBlock;
  const selected = props.selected || (selectedBlock != null && node.draws.includes(selectedBlock));
  const shared = card.entries.length > 1;
  return (
    <article
      ref={ref}
      className={`${block} ${addToTrayHost}`}
      tabIndex={-1}
      {...{ [BLOCK_STOP]: blockDomKey(node.key) }}
      data-selected={selected ? 'true' : undefined}
      data-testid="review-expression-card"
      data-card-kind={card.kind}
      data-card-change={card.change}
      data-path={card.path}
      data-before-range={rangeLabel(card.before)}
      data-after-range={rangeLabel(card.after)}
      data-entries={card.entries.map((entry) => entry.id).join(',')}
      data-in-view={visible ? 'true' : 'false'}
    >
      <BlockHead node={node} place={place} comparison={comparison} onSelect={props.onSelect} />
      <ul className={voices} data-testid="review-card-rationales">
        {card.entries.map((entry) => {
          const key = blockKey(blockIdentityOf(entry, comparison));
          return (
            <Voice
              key={entry.id}
              entry={entry}
              planning={planning}
              tray={shared ? <AddToTray pick={blockTrayPick(place, entry, comparison)} label={`block ${entry.id}`} /> : undefined}
              stop={shared && key !== node.key ? blockDomKey(key) : undefined}
            />
          );
        })}
      </ul>
      <SideLines card={card} />
      <Excerpt card={card} language={languageOfPath(card.path)} layout={layout} collapse={collapse} visible={visible} />
      <CardCurrentness card={card} />
    </article>
  );
}
