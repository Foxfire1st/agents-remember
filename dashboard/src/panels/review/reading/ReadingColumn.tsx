// The review page's reading column (MIK-R89 v2 rules 1, 2, 4–7; mockup `open`): the page scroller.
// It opens with nothing selected and holds, top to bottom: a sticky head (the tree's toggle, the
// crumb, "Changes only | Full context", the chat's toggle), the page-level notes, the summary line,
// the subjects of the active view in the active sort (the one model the tree draws too), "Code
// without invariant", the landed canvas panel in its reserved place (ruling Q-B) and Technical
// details. Every page-level block is mounted from the first paint and stays mounted while reads are
// pending (rule 9); only the reading area of the selected subject changes with its read.
import { useEffect, useState } from 'react';

import { css } from '../../../../styled-system/css';
import { ToggleButton, ToggleButtonGroup } from 'react-aria-components';
import type { ChangeSetModel } from '../changeSetOrder';
import { codeContextStore, useCodeContext, type CodeContext } from './codeContextPreference';
import { ReadGate, ReadGateContext, useEngageOnGesture } from './readGate';
import { ReadingContext, type ReadingContextValue } from './readingContext';
import { muted } from './readingStyles';
import { SubjectGroupSection } from './SubjectSection';

// The sticky head's height, measured, on the column (rule 9; dev5 R188: a ⊕ brought into view with
// `block: 'start'` sat under the head).
const HEAD_HEIGHT = '--reading-head-height';

// Sets HEAD_HEIGHT from the head's border box, and again whenever its content resizes it (its height
// is its content's: a min-height with padding and a border), so the padding never drifts from it.
function useHeadPadding(scroller: React.RefObject<HTMLDivElement | null>): void {
  useEffect(() => {
    const node = scroller.current;
    const head = node?.querySelector<HTMLElement>('[data-reading-head]');
    if (!node || !head) return undefined;
    const measure = () => {
      const height = `${head.getBoundingClientRect().height}px`;
      if (node.style.getPropertyValue(HEAD_HEIGHT) !== height) node.style.setProperty(HEAD_HEIGHT, height);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(head, { box: 'border-box' });
    return () => observer.disconnect();
  }, [scroller]);
}

const readingColumn = css({
  minWidth: 0,
  minHeight: 0,
  overflowY: 'auto',
  overflowX: 'hidden',
  // Content that changes height above the reader (a subject's slot filling, a lazily read file) keeps
  // what the reader sees in place (rule 9) by the column's own anchor (readerAnchor.ts), not the
  // browser's, which anchors the pending block that is then replaced.
  overflowAnchor: 'none',
  // Whatever brings a block into view in the column (a scrollIntoView, a focus scroll, find-in-page)
  // brings it below the sticky head: the scroll padding is the head's measured height (useHeadPadding).
  scrollPaddingTop: 'var(--reading-head-height, 0px)',
  scrollbarGutter: 'stable',
  paddingRight: '10px',
  position: 'relative',
  // Stacked: the page scrolls (reviewPage.tsx, the review's main), with the browser's own anchoring
  // over the column's content. The column clips sideways without being a scroller (`clip`, where
  // `hidden` would make one), so the head sticks to the top of the screen (rules 1 and 11).
  '@media (max-width: 40rem)': {
    overflowX: 'clip',
    overflowY: 'visible',
    minHeight: 'auto',
    paddingRight: 0,
    overflowAnchor: 'auto',
    scrollPaddingTop: 'auto',
  },
});

const head = css({
  position: 'sticky',
  top: 0,
  zIndex: 10,
  background: 'bg',
  display: 'flex',
  alignItems: 'center',
  gap: '0.6rem',
  paddingBottom: '0.5rem',
  borderBottomWidth: '1px',
  borderBottomStyle: 'solid',
  borderBottomColor: 'grid',
  minHeight: '2.2rem',
  overflowAnchor: 'none',
  // The head is always in view, yet Chrome scrolls a sticky element into view at its place in the
  // flow, the column's top: a keyboard or script focus on the tree's toggle, or a scroll into view,
  // took the column to 0 and the reader's line with it (ruling 8450399b; MIK-R89 v2 rule 9). The head
  // and its parts take a scroll area that runs past the column's end, which a scroll into view finds
  // already covering the view, so it moves nothing.
  scrollMarginBottom: '1000000px',
  '& *': { scrollMarginBottom: '1000000px' },
});

const crumb = css({
  flex: 1,
  minWidth: 0,
  fontSize: '0.75rem',
  color: 'muted',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
});

const crumbSubject = css({ color: 'ink', fontWeight: 600 });

// The crumb's parts (pageParts.tsx `crumbOf`; mockup app.js `updateCrumb`, styles.css
// `.rhead .crumb b`): the change set, the selected subject's id or the lane in bold, and a block's
// file and range or a lane file's name.
export interface Crumb {
  base: string;
  subject?: string;
  detail?: string;
}

function CrumbLine({ crumb: parts }: { crumb: Crumb }) {
  return (
    <div className={crumb} data-testid="review-crumb">
      {parts.base}
      {parts.subject === undefined ? null : (
        <>
          {' › '}
          <b className={crumbSubject}>{parts.subject}</b>
        </>
      )}
      {parts.detail ? ` › ${parts.detail}` : null}
    </div>
  );
}

// The mockup's segmented switch (styles.css `.seg`; ruling d9a1ec8f item 2): one bordered group, its
// buttons divided by a rule, the selected one amber on a faint amber ground. The review's shared
// control look leaves a `data-segment` group alone (ReviewLayout.tsx `reviewRoot`).
const segment = css({
  display: 'inline-flex',
  flex: 'none',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  overflow: 'hidden',
  '& button': {
    font: 'inherit',
    fontSize: '0.72rem',
    letterSpacing: '0.04em',
    paddingBlock: '0.28rem',
    paddingInline: '0.7rem',
    background: 'bg',
    color: 'muted',
    border: 'none',
    borderRightWidth: '1px',
    borderRightStyle: 'solid',
    borderRightColor: 'grid',
    cursor: 'pointer',
  },
  '& button:last-child': { borderRightWidth: '0' },
  '& button[data-selected]': { background: 'color-mix(in oklab, token(colors.amber) 16%, transparent)', color: 'amber' },
  '& button[data-focus-visible]': { outlineWidth: '2px', outlineStyle: 'solid', outlineColor: 'amber', outlineOffset: '-2px' },
});

const endpad = css({ height: '40vh', '@media (max-width: 40rem)': { height: '2rem' } });

// The one "Changes only | Full context" control (rule 4, D230): remembered (`review.code-context.v1`).
function CodeContextSwitch() {
  const context = useCodeContext();
  return (
    <ToggleButtonGroup
      className={segment}
      selectionMode="single"
      disallowEmptySelection
      selectedKeys={[context]}
      onSelectionChange={(keys) => {
        const next = [...keys][0];
        if (next === 'changes' || next === 'full') codeContextStore.getState().setContext(next as CodeContext);
      }}
      aria-label="Code context"
      data-testid="review-code-context"
      data-segment
    >
      <ToggleButton id="changes">Changes only</ToggleButton>
      <ToggleButton id="full">Full context</ToggleButton>
    </ToggleButtonGroup>
  );
}

export interface ReadingColumnProps {
  scroller: React.RefObject<HTMLDivElement | null>;
  context: ReadingContextValue;
  model: ChangeSetModel;
  // The head's two ends (the tree's and the chat's toggles) and its crumb.
  treeToggle: React.ReactNode;
  chatToggle: React.ReactNode;
  crumbText: Crumb;
  markerReturn: React.ReactNode;
  // Page-level notes in their reserved place at the top: the task context's own outcome, a link
  // selection outside the comparison, an older comparison's read-only line.
  notes: React.ReactNode;
  // The selected subject's reading when the page draws no section for it (pageParts.tsx OrphanSlot).
  orphan?: React.ReactNode;
  summary: React.ReactNode;
  // The reader's other view (the leaf worklist) in place of the change set, when chosen.
  replacement?: React.ReactNode;
  lane: React.ReactNode;
  canvas: React.ReactNode;
  details: React.ReactNode;
}

export function ReadingColumn(props: ReadingColumnProps) {
  const [gate] = useState(() => new ReadGate());
  const { model } = props;
  // In-view reads start after the reader's first scroll, wheel, key move or selection (rule 2).
  useEngageOnGesture(gate, props.scroller);
  useHeadPadding(props.scroller);
  const selected = props.context.selection !== null;
  useEffect(() => {
    if (selected) gate.engage();
  }, [selected, gate]);
  // In-view lane file reads start, with no gesture, once the opening's six reads are done (rule 2,
  // f43ae286): the cards' read is the last of them to start (it waits for the leaf-wide trees and the
  // outline), and the lane's files are drawn once the lane read has answered.
  const opened = model.cards !== 'reading';
  useEffect(() => {
    if (opened) gate.open();
  }, [opened, gate]);
  const families = model.groups.some((group) => group.family !== undefined);
  // The first subject section the column draws: a rule stands above every later one, and above the
  // lane when one is drawn (ruling 9498d6e4; mockup styles.css `.subject + .subject`).
  const firstSubject = model.subjects[0]?.key;
  return (
    <ReadGateContext.Provider value={gate}>
      <ReadingContext.Provider value={props.context}>
        <div
          ref={props.scroller}
          className={readingColumn}
          data-review-scroller
          data-testid="review-center-column"
          tabIndex={-1}
        >
          <div className={head} data-reading-head>
            {props.treeToggle}
            <CrumbLine crumb={props.crumbText} />
            {props.markerReturn}
            <CodeContextSwitch />
            {props.chatToggle}
          </div>
          <div data-testid="review-page-notes">{props.notes}</div>
          {props.orphan}
          {props.replacement ?? (
            <>
              {props.summary}
              {model.noChange ? (
                <p className={muted} data-testid="review-no-change">
                  This leaf changed no recorded meaning.
                </p>
              ) : null}
              <div data-testid="review-subjects" data-subjects-drawn={firstSubject === undefined ? undefined : ''}>
                {model.groups.map((group) => (
                  <SubjectGroupSection key={group.key} group={group} labelUnlisted={families} firstSubject={firstSubject} />
                ))}
              </div>
              {props.lane}
            </>
          )}
          <div data-testid="review-canvas-slot" className={css({ minHeight: '2.5rem' })}>
            {props.canvas}
          </div>
          {props.details}
          <div className={endpad} aria-hidden="true" />
          {/* The blank the reader's line holds at the column's end while a shrink would clamp the
              column (readerAnchor.ts); its height is set there, and it is empty otherwise. */}
          <div data-reading-spacer aria-hidden="true" />
        </div>
      </ReadingContext.Provider>
    </ReadGateContext.Provider>
  );
}
