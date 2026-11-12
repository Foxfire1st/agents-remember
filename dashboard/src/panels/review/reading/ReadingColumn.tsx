// The review page's reading column (MIK-R89 v2 rules 1, 2, 4–7; mockup `open`): the page scroller.
// The toolbar holds the controls and selection crumb. This scroller holds the page-level notes,
// the summary line and the selected area: a family overview, one invariant's code, or the lane.
// The comparison overview carries no code editors. Sidebar inventory and central selection stay
// separate so visiting a subject never mounts every previously read subject's code.
import { useEffect, useState } from 'react';

import { css } from '../../../../styled-system/css';
import type { ChangeSetModel, SubjectNode } from '../changeSetOrder';
import type { ReviewSubjectRef } from '../blockIdentity';
import type { ReviewSelection } from '../reviewSelection';
import { ReadGate, ReadGateContext, useEngageOnGesture } from './readGate';
import { ReadingContext, type ReadingContextValue } from './readingContext';
import { muted } from './readingStyles';
import { ComparisonOverview, FamilyOverview, SubjectSection } from './SubjectSection';

const readingColumn = css({
  minWidth: 0,
  minHeight: 0,
  overflowY: 'auto',
  overflowX: 'hidden',
  // Content that changes height above the reader (a subject's slot filling, a lazily read file) keeps
  // what the reader sees in place (rule 9) by the column's own anchor (readerAnchor.ts), not the
  // browser's, which anchors the pending block that is then replaced.
  overflowAnchor: 'none',
  scrollbarGutter: 'stable',
  paddingRight: '10px',
  position: 'relative',
  // Stacked: the page scrolls (reviewPage.tsx, the review's main), with the browser's own anchoring
  // over the column's content. The column clips sideways without being a scroller (`clip`, where
  // `hidden` would make one), so the page remains the one scroller (rules 1 and 11).
  '@media (max-width: 40rem)': {
    overflowX: 'clip',
    overflowY: 'visible',
    minHeight: 'auto',
    paddingRight: 0,
    overflowAnchor: 'auto',
    scrollPaddingTop: 'auto',
  },
});

export interface Crumb {
  base: string;
  subject?: string;
  detail?: string;
}


const endpad = css({ height: '40vh', '@media (max-width: 40rem)': { height: '2rem' } });

export interface ReadingColumnProps {
  scroller: React.RefObject<HTMLDivElement | null>;
  view?: 'review' | 'explanation';
  context: ReadingContextValue;
  model: ChangeSetModel;
  focusedSubject: ReviewSubjectRef | undefined;
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

function focusedNode(model: ChangeSetModel, focused: ReviewSubjectRef, selection: ReviewSelection | null): SubjectNode | undefined {
  const family = selection?.kind === 'subject' ? selection.family : undefined;
  const occurrences = model.groups.flatMap((group) => group.subjects.filter((node) => node.row.selectorId === focused.id));
  const subject = occurrences.find((node) => node.row.familyId === family) ?? occurrences[0];
  if (!subject) return undefined;
  // Repeated family occurrences deliberately hold no locations in the sidebar model. The center
  // uses the loaded invariant's locations at the occurrence the reader actually selected.
  const locations = occurrences.find((node) => node.first) ?? subject;
  return {
    ...subject, first: true, cards: locations.cards,
    changed: locations.changed, unchanged: locations.unchanged, notComparable: locations.notComparable,
    ungradedChanged: locations.ungradedChanged,
  };
}

function SelectedArea({ model, focusedSubject, context, lane }: Pick<ReadingColumnProps, 'model' | 'focusedSubject' | 'context' | 'lane'>) {
  const selection = context.selection;
  if (isLaneSelection(selection)) return lane;
  if (!focusedSubject) return (
    <>
      {model.noChange ? <p className={muted} data-testid="review-no-change">This leaf changed no recorded meaning.</p> : null}
      <ComparisonOverview groups={model.groups} />
    </>
  );
  if (focusedSubject.kind === 'family') {
    const family = model.groups.find((group) => group.family?.row.selectorId === focusedSubject.id);
    return family ? <FamilyOverview group={family} subjects={model.subjects} /> : null;
  }
  const node = focusedNode(model, focusedSubject, selection);
  return node ? <SubjectSection key={node.key} node={node} family={node.row.familyId} /> : null;
}

function isLaneSelection(selection: ReviewSelection | null): boolean {
  return selection?.kind === 'lane' || (selection?.kind === 'block' && selection.block.kind === 'hunk');
}

export function ReadingColumn(props: ReadingColumnProps) {
  const [gate] = useState(() => new ReadGate());
  const { model } = props;
  // In-view reads start after the reader's first scroll, wheel, key move or selection (rule 2).
  useEngageOnGesture(gate, props.scroller);
  const selected = props.context.selection !== null;
  useEffect(() => {
    if (selected) gate.engage();
  }, [selected, gate]);
  // The selected area's in-view reads may start once its model is ready. Selecting an area also
  // engages its read gate, while the comparison overview mounts no source blocks.
  const opened = model.cards !== 'reading';
  useEffect(() => {
    if (opened) gate.open();
  }, [opened, gate]);
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
          <div data-testid="review-page-notes">{props.notes}</div>
          {props.view === 'explanation' ? null : props.orphan}
          {props.replacement ?? (
            <>
              {props.summary}
              <div data-testid="review-subjects">
                <SelectedArea model={model} focusedSubject={props.focusedSubject} context={props.context} lane={props.lane} />
              </div>
            </>
          )}
          {props.view === 'explanation' ? null : <>
            <div data-testid="review-canvas-slot" className={css({ minHeight: '2.5rem' })}>
              {props.canvas}
            </div>
            {props.details}
            <div className={endpad} aria-hidden="true" />
            {/* The review's reader-line reservation belongs to its source blocks. Adding it below
                the separately scrolling explanation creates a second, mostly empty scroll area. */}
            <div data-reading-spacer aria-hidden="true" />
          </>}
        </div>
      </ReadingContext.Provider>
    </ReadGateContext.Provider>
  );
}
