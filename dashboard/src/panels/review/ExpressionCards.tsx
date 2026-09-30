// The central reading path's code and test expressions for a tree comparison (MIK-R31): one focused
// card per (path, range), its authored rationale directly above the excerpt, a changed range as its
// real diff and an unchanged range once, labelled unchanged. Full-file inspection and the complete
// changed-file inventory stay one step away from every card.
//
// The cards read the selection's entries from the tree view (data/reviewTrees.ts); an unconverted
// review never mounts this component and keeps the landed file view (ReviewExpressions.tsx).
import { useContext, useEffect, useState } from 'react';

import { css } from '../../../styled-system/css';
import type { ReviewChangedFile, ReviewPayload } from '../../data/review';
import type { ReviewTreeEntry, ReviewTreesRead, ReviewTreeEntrySide } from '../../data/reviewTrees';
import { DiffPane } from '../changeset/DiffPane';
import { FilePane } from '../file-viewer/FilePane';
import { sideMarks, useExcerptMarking } from './IntentMarkers';
import { IntentMarkerScope } from './intentMarkerScope';
import { ReviewProblemBlock } from './ReviewOutcome';
import { ExpressionControls } from './ReviewExpressions';
import { SourceContent } from './SourceContent';
import type { DiffLayout } from './SourceExplorer';
import {
  cardCounts,
  cardVoice,
  expressionCards,
  firstLines,
  languageOfPath,
  notCurrent,
  rangeLabel,
  type CardScope,
  type ExpressionCard,
} from './focusedCards';

type CardEntry = ExpressionCard['entries'][number];

const section = css({ display: 'grid', gap: '0.9rem', minWidth: 0 });
const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.2rem 0' });
const cardShell = css({
  border: '1px solid var(--grid)',
  borderRadius: '3px',
  background: 'bg',
  minWidth: 0,
  display: 'grid',
});
const cardHead = css({
  display: 'flex',
  gap: '0.6rem',
  alignItems: 'flex-start',
  justifyContent: 'space-between',
  flexWrap: 'wrap',
  padding: '0.75rem 0.8rem 0.55rem',
});
const kindLabel = css({
  color: 'cyan',
  fontSize: '0.68rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  margin: '0 0 0.3rem',
});
const pathText = css({ fontSize: '0.8rem', overflowWrap: 'anywhere', margin: 0 });
const badge = css({
  border: '1px solid var(--amber)',
  color: 'amber',
  borderRadius: '2px',
  padding: '0.1rem 0.45rem',
  fontSize: '0.7rem',
  whiteSpace: 'nowrap',
});
const quietBadge = css({
  border: '1px solid var(--grid)',
  color: 'muted',
  borderRadius: '2px',
  padding: '0.1rem 0.45rem',
  fontSize: '0.7rem',
  whiteSpace: 'nowrap',
});
const voices = css({
  borderTop: '1px solid var(--grid)',
  padding: '0.6rem 0.8rem',
  display: 'grid',
  gap: '0.55rem',
  margin: 0,
  listStyle: 'none',
});
const voiceText = css({ fontSize: '0.82rem', lineHeight: '1.7', margin: '0.15rem 0 0' });
const gap = css({ color: 'amber', fontSize: '0.8rem', margin: '0.15rem 0 0' });
// The side lines divide by a 1px gap over the grid colour, so the divider follows the layout: a
// column rule side by side, a row rule when the two lines stack on a narrow screen.
const sides = css({
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(12rem, 1fr))',
  gap: '1px',
  background: 'grid',
  borderTop: '1px solid var(--grid)',
  '& > p': {
    margin: 0,
    padding: '0.45rem 0.8rem',
    fontSize: '0.75rem',
    color: 'muted',
    background: 'bg',
  },
});
const excerptBox = css({ borderTop: '1px solid var(--grid)', minWidth: 0 });
const actions = css({
  display: 'flex',
  gap: '0.9rem',
  flexWrap: 'wrap',
  alignItems: 'center',
  borderTop: '1px solid var(--grid)',
  padding: '0.45rem 0.8rem',
});
const linkButton = css({
  background: 'transparent',
  border: 'none',
  color: 'amber',
  cursor: 'pointer',
  font: 'inherit',
  fontSize: '0.75rem',
  padding: 0,
  _focusVisible: { outline: '1px solid var(--amber)', outlineOffset: '2px' },
});
const fullFileBox = css({ borderTop: '1px solid var(--grid)', padding: '0.7rem', minWidth: 0 });

export interface CardsProps {
  payload: ReviewPayload;
  read: ReviewTreesRead;
  seed?: string;
  planning: Map<string, string>;
  layout: DiffLayout;
  onLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
  openPath: string | null | undefined;
  onOpenPath: (path: string | null) => void;
  // Set when the family's roster is bounded: the cards cover only the members loaded so far.
  scope?: CardScope;
}

export function ExpressionCards(props: CardsProps) {
  const { read, layout, onLayout, fullFile, onFullFile } = props;
  const controls = (
    <ExpressionControls
      layout={layout}
      onLayout={onLayout}
      fullFile={fullFile}
      onFullFile={onFullFile}
    />
  );
  if (read.phase === 'loading')
    return (
      <section className={section} data-testid="review-expression-cards" data-cards-state="loading">
        {controls}
        <p className={muted} role="status">
          Reading the selection&apos;s code and test locations…
        </p>
      </section>
    );
  if (read.phase !== 'trees')
    return (
      <section
        className={section}
        data-testid="review-expression-cards"
        data-cards-state="unavailable"
      >
        {controls}
        {read.phase === 'unavailable' ? (
          <ReviewProblemBlock
            origin="failure"
            subject="the expression cards"
            problem={read.problem}
          />
        ) : (
          <p className={muted}>This review is not a tree comparison; no cards apply.</p>
        )}
        <OpenedFile {...props} cards={[]} />
      </section>
    );
  return <ReadyCards {...props} entries={read.trees.entries ?? []} />;
}

// One card's full file is open at a time, in the card that asked for it (review F1): the open state
// is the card's key. A path opened from the source explorer opens in the first card of that path.
function ReadyCards(props: CardsProps & { entries: ReviewTreeEntry[] }) {
  const { openPath, onOpenPath, scope } = props;
  // A return to an intent marker followed from a card's full file (MIK-R34) reopens that card.
  const returningCard = returnedCard(useContext(IntentMarkerScope)?.returning?.pane);
  const [openCard, setOpenCard] = useState<string | null>(returningCard);
  useEffect(() => {
    if (returningCard !== null) setOpenCard(returningCard);
  }, [returningCard]);
  const cards = expressionCards(props.entries, props.seed);
  const counts = cardCounts(cards);
  const chosen = cards.find((card) => card.key === openCard && card.path === openPath);
  const shown = chosen?.key ?? cards.find((card) => card.path === openPath)?.key;
  const toggle = (card: ExpressionCard) => {
    const closing = card.key === shown;
    setOpenCard(closing ? null : card.key);
    onOpenPath(closing ? null : card.path);
  };
  return (
    <section
      className={section}
      data-testid="review-expression-cards"
      data-cards-state="ready"
      data-card-count={counts.cards}
      data-changed-count={counts.changed}
      data-unchanged-count={counts.unchanged}
    >
      <ExpressionControls
        layout={props.layout}
        onLayout={props.onLayout}
        fullFile={props.fullFile}
        onFullFile={props.onFullFile}
      />
      <p className={muted} data-testid="review-card-counts">
        {counts.cards} location{counts.cards === 1 ? '' : 's'} · {counts.changed} changed ·{' '}
        {counts.unchanged} unchanged
        {counts.undetermined ? ` · ${counts.undetermined} not comparable` : ''}
      </p>
      {scope ? <ScopeLine scope={scope} /> : null}
      {cards.length === 0 ? (
        <p className={muted} data-testid="review-cards-none">
          No realization or proof entry is recorded for this selection on either side.
        </p>
      ) : null}
      <OpenedFile {...props} cards={cards} />
      {cards.map((card) => (
        <FocusedCard
          key={card.key}
          card={card}
          {...props}
          open={card.key === shown}
          onToggle={() => toggle(card)}
        />
      ))}
    </section>
  );
}

const CARD_PANE = 'card:';

function returnedCard(pane: string | undefined): string | null {
  return pane?.startsWith(CARD_PANE) ? pane.slice(CARD_PANE.length) : null;
}

// A bounded roster: the counts above are of the loaded members' locations, never the family's.
function ScopeLine({ scope }: { scope: CardScope }) {
  return (
    <p className={gap} data-testid="review-cards-scope">
      These cards cover the loaded {scope.loaded} of {scope.total} members. Continue the
      family&apos;s roster walk (its next-page control) to load the rest; their locations are not
      counted here.
    </p>
  );
}

function changeBadge(card: ExpressionCard) {
  if (card.change === 'changed') return <span className={badge}>Changed range</span>;
  if (card.change === 'unchanged') return <span className={quietBadge}>Unchanged</span>;
  return <span className={badge}>Not comparable</span>;
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

function SideLines({ card }: { card: ExpressionCard }) {
  return (
    <div className={sides}>
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

function sideStateText(side: ReviewTreeEntrySide): string {
  if (side.state === 'absent') return 'no file on this side';
  if (side.state === 'unresolved') return `unresolved: ${side.reason ?? 'no reason given'}`;
  return `unavailable: ${side.reason ?? 'no reason given'}`;
}

function FocusedCard({
  card,
  payload,
  planning,
  layout,
  fullFile,
  open,
  onToggle,
}: CardsProps & { card: ExpressionCard; open: boolean; onToggle: () => void }) {
  const marks = notCurrent(card);
  return (
    <article
      className={cardShell}
      data-testid="review-expression-card"
      data-card-kind={card.kind}
      data-card-change={card.change}
      data-path={card.path}
      data-before-range={rangeLabel(card.before)}
      data-after-range={rangeLabel(card.after)}
      data-entries={card.entries.map((entry) => entry.id).join(',')}
    >
      <header className={cardHead}>
        <div className={css({ minWidth: 0 })}>
          <p className={kindLabel}>
            {card.kind === 'proof' ? 'Test proof' : 'Code expression'} ·{' '}
            {cardRoles(card).join(' · ')}
          </p>
          <p className={pathText}>{card.path}</p>
        </div>
        {changeBadge(card)}
      </header>
      <ul className={voices} data-testid="review-card-rationales">
        {card.entries.map((entry) => (
          <Voice key={entry.id} entry={entry} planning={planning} />
        ))}
      </ul>
      <SideLines card={card} />
      <div className={excerptBox}>
        <Excerpt card={card} layout={layout} />
      </div>
      <p className={css({ margin: 0, padding: '0 0.8rem' })}>
        {marks.map((mark) => (
          <span
            key={`${mark.entry}:${mark.side}`}
            className={css({ color: 'amber', fontSize: '0.72rem', marginRight: '0.8rem' })}
            data-testid="review-card-currentness"
            data-currentness={mark.state}
          >
            {mark.entry} {mark.state} on the {mark.side} side
          </span>
        ))}
      </p>
      <div className={actions}>
        <button
          type="button"
          className={linkButton}
          data-testid="review-card-full-file"
          aria-expanded={open}
          onClick={onToggle}
        >
          {open ? 'Hide full file' : 'Full file'}
        </button>
        <InventoryLink payload={payload} />
        <CardDetails card={card} />
      </div>
      {open ? (
        <div className={fullFileBox}>
          <FullFile
            payload={payload}
            path={card.path}
            layout={layout}
            fullFile={fullFile}
            pane={`${CARD_PANE}${card.key}`}
          />
        </div>
      ) : null}
    </article>
  );
}

function cardRoles(card: ExpressionCard): string[] {
  const named = card.entries.map((entry) => {
    const voice = cardVoice(entry);
    return entry.kind === 'proof' ? 'facet' : (voice.role ?? 'no role recorded');
  });
  return [...new Set(named)];
}

function voiceHeading(entry: CardEntry, role: string | undefined, mark?: string): string {
  const parts = [entry.invariant, entry.kind === 'proof' ? 'proof facet' : (role ?? 'no role')];
  if (mark) parts.push(mark);
  if (!entry.after.recorded && entry.before.recorded) parts.push('retired in this leaf');
  if (entry.after.recorded && entry.before.recorded === false) parts.push('added in this leaf');
  return parts.join(' · ');
}

function Voice({ entry, planning }: { entry: CardEntry; planning: Map<string, string> }) {
  const voice = cardVoice(entry);
  const said = entry.kind === 'proof' ? voice.facet : voice.rationale;
  return (
    <li data-testid="review-card-voice" data-entry={entry.id} data-invariant={entry.invariant}>
      <span className={css({ color: 'muted', fontSize: '0.72rem' })}>
        {voiceHeading(entry, voice.role, planning.get(entry.invariant))}
      </span>
      {said ? (
        <p className={voiceText} data-testid="review-card-rationale">
          {said}
        </p>
      ) : (
        <p className={gap} data-testid="review-card-rationale-missing">
          No authored {entry.kind === 'proof' ? 'facet' : 'rationale'} is recorded for {entry.id}.
        </p>
      )}
      {voice.revised !== undefined ? (
        <details className={muted}>
          <summary>Revised in this leaf</summary>
          Before: {voice.revised}
        </details>
      ) : null}
    </li>
  );
}

function Excerpt({ card, layout }: { card: ExpressionCard; layout: DiffLayout }) {
  const language = languageOfPath(card.path);
  if (card.change === 'unchanged') return <UnchangedExcerpt card={card} language={language} />;
  if (card.change === 'changed')
    return <ChangedExcerpt card={card} language={language} layout={layout} />;
  return <SeparateSides card={card} language={language} />;
}

// An unchanged range: its text once (the two sides share one content identity).
function UnchangedExcerpt({ card, language }: { card: ExpressionCard; language: string }) {
  const { after } = card;
  // A range with the same text on both sides can still hold changed lines (code moved as a whole).
  const marking = useExcerptMarking(card, 'split', { before: false, after: true });
  if (after.excerpt === undefined)
    return <p className={muted}>{after.reason ?? 'No excerpt was read for this range.'}</p>;
  return (
    <div data-testid="review-card-excerpt" data-excerpt="unchanged">
      <FilePane
        content={after.excerpt}
        language={language}
        firstLine={after.start_line}
        fit
        marks={marking.marks}
      />
      <Truncated side={after} />
      {marking.note}
      {marking.panel}
    </div>
  );
}

// A changed range: the real diff of the two sides' excerpts; a side with no file is the empty
// operand of an added or deleted file, which the side line above names.
function ChangedExcerpt({
  card,
  language,
  layout,
}: {
  card: ExpressionCard;
  language: string;
  layout: DiffLayout;
}) {
  const { before, after } = card;
  const marking = useExcerptMarking(card, layout, { before: true, after: true });
  const beforeText = before.state === 'resolved' ? before.excerpt : '';
  const afterText = after.state === 'resolved' ? after.excerpt : '';
  if (beforeText === undefined || afterText === undefined)
    return <p className={muted}>{before.reason ?? after.reason ?? 'No excerpt was read.'}</p>;
  return (
    <div data-testid="review-card-excerpt" data-excerpt="diff">
      <DiffPane
        before={beforeText}
        after={afterText}
        language={language}
        mode={layout}
        collapse={false}
        firstLine={firstLines(card)}
        fit
        marks={marking.marks}
      />
      <Truncated side={before} />
      <Truncated side={after} />
      {marking.note}
      {marking.panel}
    </div>
  );
}

// A card whose two sides are not both regions: each readable side's text on its own, never a diff,
// because a diff would claim the other side is a known operand.
function SeparateSides({ card, language }: { card: ExpressionCard; language: string }) {
  const marking = useExcerptMarking(card, 'split', { before: true, after: true });
  return (
    <div data-testid="review-card-excerpt" data-excerpt="separate">
      {(['before', 'after'] as const).map((name) => {
        const side = card[name];
        return side.state === 'resolved' && side.excerpt !== undefined ? (
          <div key={name}>
            <p className={muted} style={{ padding: '0.3rem 0.8rem' }}>
              {name} side text · no comparison is drawn
            </p>
            <FilePane
              content={side.excerpt}
              language={language}
              firstLine={side.start_line}
              fit
              marks={sideMarks(marking.marks, name)}
            />
          </div>
        ) : null;
      })}
      {marking.note}
      {marking.panel}
    </div>
  );
}

function Truncated({ side }: { side: ReviewTreeEntrySide }) {
  return side.excerpt_truncated ? (
    <p className={muted} style={{ padding: '0 0.8rem' }}>
      The excerpt shows the start of {rangeLabel(side)}; open the full file for the rest.
    </p>
  ) : null;
}

function CardDetails({ card }: { card: ExpressionCard }) {
  return (
    <details className={muted}>
      <summary>Entry details</summary>
      {card.entries.map((entry) => (
        <p key={entry.id} data-testid="review-card-entry-detail">
          {entry.id} · {entry.kind} · {entry.invariant} · before {sideFacts(entry.before)} · after{' '}
          {sideFacts(entry.after)}
        </p>
      ))}
    </details>
  );
}

function sideFacts(side: ReviewTreeEntrySide): string {
  return [
    side.path,
    side.state,
    side.blob ? `blob ${side.blob}` : null,
    side.content ?? null,
    side.recorded === undefined
      ? 'memory side unread'
      : side.recorded
        ? 'recorded'
        : 'not recorded',
    side.currentness ? `R03 ${side.currentness}` : null,
    side.currentness_reason ?? null,
    side.reason ?? null,
  ]
    .filter((fact): fact is string => fact !== null)
    .join(' · ');
}

function InventoryLink({ payload }: { payload: ReviewPayload }) {
  const inventory = payload.source.inventory;
  const total = inventory.state === 'measured' ? `${inventory.listed_total}` : 'unmeasured';
  return (
    <button
      type="button"
      className={linkButton}
      data-testid="review-card-inventory"
      onClick={(event) => {
        const explorer = event.currentTarget
          .closest('[data-testid="review-workspace"]')
          ?.querySelector<HTMLElement>('[data-testid="review-source-explorer"]');
        explorer?.scrollIntoView({ block: 'start' });
        explorer?.querySelector<HTMLElement>('button')?.focus({ preventScroll: true });
      }}
    >
      All changed files ({total})
    </button>
  );
}

// The full file of one path, at the review's own generation, through the landed content read.
function FullFile({
  payload,
  path,
  layout,
  fullFile,
  pane,
}: {
  payload: ReviewPayload;
  path: string;
  layout: DiffLayout;
  fullFile: boolean;
  // The pane's name for the intent markers' return (MIK-R34).
  pane: string;
}) {
  const inventory = payload.source.inventory;
  if (!inventory.before_code_tree_id || !inventory.after_code_tree_id)
    return <p className={muted}>This review names no code generation to open the file at.</p>;
  const entry: ReviewChangedFile = inventory.entries.find((row) => row.path === path) ?? {
    path,
    status: 'unknown',
    content: 'unknown',
    mode_change: false,
    detail: 'an unchanged file a recorded entry names',
  };
  return (
    <div data-testid="review-card-full-file-content" data-path={path}>
      <SourceContent
        repo={payload.candidate.repository_id}
        master={payload.candidate.master}
        leaf={payload.candidate.leaf_id}
        entry={entry}
        beforeCodeTreeId={inventory.before_code_tree_id}
        afterCodeTreeId={inventory.after_code_tree_id}
        mode={layout}
        collapse={!fullFile}
        markers={{ pane }}
      />
    </div>
  );
}

// A file opened from the source explorer (or a card) that no card shows: it opens here, above the
// cards, so an unattributed change is inspected in the same reading path.
function OpenedFile(props: CardsProps & { cards: ExpressionCard[] }) {
  const { openPath, cards, payload, layout, fullFile, onOpenPath } = props;
  if (!openPath || cards.some((card) => card.path === openPath)) return null;
  return (
    <article className={cardShell} data-testid="review-opened-file" data-path={openPath}>
      <header className={cardHead}>
        <div className={css({ minWidth: 0 })}>
          <p className={kindLabel}>Opened from the source changes</p>
          <p className={pathText}>{openPath}</p>
        </div>
        <button type="button" className={linkButton} onClick={() => onOpenPath(null)}>
          Close
        </button>
      </header>
      <div className={fullFileBox}>
        <FullFile
          payload={payload}
          path={openPath}
          layout={layout}
          fullFile={fullFile}
          pane="opened"
        />
      </div>
    </article>
  );
}
