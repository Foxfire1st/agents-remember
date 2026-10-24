// The review page's parts of the shell (MIK-R89 v2 rules 1, 8, 11 and 12; build plan W22–W26):
// the review's slot in the railed grid, and how the shell's right rail lies as the review's chat
// column. The column state itself is reviewChat.ts's; the rail is Cockpit.tsx's `RightRail`.
import type { CSSProperties } from 'react';

import { css, cx } from '../../styled-system/css';
import { SidebarToggle } from '../grammar/SidebarToggle';
import type { ChangeSetTarget } from '../panels/changeset/ChangeSetViewer';
import { ReviewSurface } from '../panels/review/ReviewSurface';
import { rail, reviewChatClose, reviewChatColumn, RIGHT_RAIL_ID, takeoverBody, viewport } from './layout';
import type { ReviewChatColumn, ReviewChatControl } from './reviewChat';

// At 40rem and narrower the main lies over the whole screen and is the stacked page's one scroller
// (rules 1 and 11): the tree above the reading column, both moved by the reader's wheel and touch, and
// never sideways. Above it the main stays still and the reading column scrolls.
const stackedPage = css({
  '@media (max-width: 40rem)': { overflowX: 'hidden', overflowY: 'auto' },
});

// The review page inside the railed grid (rule 12, W23): a conditional slot just before the right
// rail, so the rail and its one chat frame keep their place in the document. At 40rem and narrower
// it lies over the whole screen, as the review takeover did.
export function ReviewMain({
  target,
  chat,
  onBack,
}: {
  target: ChangeSetTarget;
  chat: ReviewChatControl;
  onBack: () => void;
}) {
  // A review target may carry a subject's selector or none at all: the task-context entry opens the
  // review on the task, and the surface asks for exactly what it was handed. It may also name the
  // record it is addressed to (ICR-R12): a closed leaf's entry carries `historical`, and the
  // surface then asks for that leaf's recorded comparison rather than for a live candidate there
  // is none of.
  return (
    <main className={cx(viewport, takeoverBody, stackedPage, 'viewport')} data-view="intent-review">
      <ReviewSurface
        repo={target.repo}
        master={target.master ?? ''}
        leaf={target.leaf ?? ''}
        selectorKind={target.review?.selectorKind}
        selectorId={target.review?.selectorId}
        history={target.review?.historical ? 'recorded' : undefined}
        onBack={onBack}
        chat={chat}
      />
    </main>
  );
}

/**
 * The right rail as the review's chat column: the task's chat only, open or closed by the review's
 * toggle, preloaded while closed (rule 12), at the review's width.
 */
export function reviewRailView(fullBleed: boolean, column: ReviewChatColumn) {
  const shown = !fullBleed && column.open;
  return {
    shown,
    chat: true,
    river: false,
    style: shown ? columnPlacement(column) : ({ display: 'none' } as CSSProperties),
    className: cx(rail, reviewChatColumn, 'rail rail--right'),
    width: column.width,
    onResize: column.onResize,
    handle: { label: 'Resize the chat column', testId: 'review-chat-resize' },
    chatProps: {
      taskDocumentRef: column.documentRef,
      preload: true,
      unbound: column.unbound,
      explainer: column.explainer ?? undefined,
    },
  };
}

// The open chat column: a grid track from 1440 px; below it the same element lies over the reading
// column, and at 40rem and narrower over the screen, never wider than it (rule 11). Only `position`
// changes, so the element is never moved.
function columnPlacement(column: ReviewChatColumn): CSSProperties {
  if (column.placement === 'grid') return { display: 'flex' };
  const over: CSSProperties = { display: 'flex', top: 0, right: 0, bottom: 0, boxShadow: '-8px 0 24px rgb(0 0 0 / 0.45)' };
  if (column.placement === 'overlay') return { ...over, position: 'absolute', zIndex: 20, width: `${column.width}px` };
  return { ...over, position: 'fixed', zIndex: 60, width: `${column.width}px`, maxWidth: 'calc(100vw - 1.6rem)' };
}

// Over the reading column the chat column covers the review's own chat toggle: it brings its own,
// at its edge, so it can always be closed (rule 8).
export function ReviewChatClose({ column }: { column: ReviewChatColumn | null }) {
  if (!column || column.placement === 'grid') return null;
  return (
    <div className={reviewChatClose}>
      <SidebarToggle
        side="right"
        open={column.open}
        onToggle={column.toggle}
        name="Chat sidebar"
        controls={RIGHT_RAIL_ID}
        testId="review-chat-close"
      />
    </div>
  );
}
