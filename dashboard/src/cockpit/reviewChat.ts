// The review page's chat column (MIK-R89 v2 rules 1, 11 and 12; build plan W24–W26).
//
// The column is the shell's own right rail: the review renders inside the railed grid beside it,
// so the one chat frame is never moved and never loaded again. This module holds what the shell
// decides for that column while a review is shown:
//   - open or closed. It opens closed for every review it is shown for and keeps nothing in
//     storage, so a reload and every new review target open it closed (rule 8's one exception);
//   - its width, remembered per width class (above 1600 px, and at or below), and the gutter;
//   - where it lies: in the grid, over the reading column below 1440 px, or over the whole screen
//     at 40rem and narrower;
//   - which task's chat it shows. The review names repository, master folder and leaf; the task
//     list names the task document. Until the task list has arrived the column is held, and a
//     task the list does not hold gets a named line: it never falls back to the Projects
//     architect's chat (rule 12, "a cold #review load opens this task's chat").
import { useCallback, useEffect, useMemo, useState } from 'react';

import { useDashboard } from '../data/store';
import { usePersistedNumber } from '../panels/file-viewer/usePersistedFlag';
import { dirName } from '../panels/detail-panel/model';
import type { ChangeSetTarget } from '../panels/changeset/ChangeSetViewer';
import { isExpanded, trayStore } from '../tray/trayStore';
import type { TaskDocumentRef, TaskEntry } from '../types/projection';
import type { DocumentChatUnbound } from './document-chat/state';
import type { ExplainerColumnTarget } from '../explainer/ExplainerColumn';
import { onFirstRequest } from '../explainer/request';
import { sameExplainerAddress, type ExplainerAddress } from '../explainer/client';
import { useMediaQuery, useNarrowCockpit } from './layout';

export const REVIEW_CHAT_WIDTH_KEYS = {
  wide: 'cockpit.review.chat-w.wide',
  narrow: 'cockpit.review.chat-w.narrow',
} as const;
export const REVIEW_CHAT_WIDTH_DEFAULTS = { wide: 500, narrow: 420 } as const;
// The width classes of the approved mockup: its 1920 and 1440 layouts switch at 1600 px.
const NARROW_CLASS = '(max-width: 1600px)';
// Below 1440 px the open column lies over the reading column instead of taking its width.
const OVERLAY = '(max-width: 1439.98px)';

export type ReviewChatPlacement = 'grid' | 'overlay' | 'narrow';

/** Which task's chat the column shows, read from the task list (W25). */
export type ReviewChatTask =
  | { state: 'resolved'; ref: TaskDocumentRef }
  | { state: 'pending' }
  | { state: 'unknown' };

export function reviewChatTask(
  review: Pick<ChangeSetTarget, 'repo' | 'master' | 'leaf'>,
  tasks: Record<string, TaskEntry>,
  hydrated: boolean,
): ReviewChatTask {
  // The review entry names the master by its folder (`changeSetBar.tsx`, `dirName`), the leaf by
  // its id and the repository by name: the same three facts pick the one task document.
  const entry = Object.values(tasks).find(
    (task) =>
      task.id === review.leaf &&
      task.reference?.repository === review.repo &&
      dirName(task.reference.path) === review.master,
  );
  if (entry?.reference) return { state: 'resolved', ref: entry.reference };
  return hydrated ? { state: 'unknown' } : { state: 'pending' };
}

export const REVIEW_CHAT_PENDING_LINE = "Waiting for the task list to name this review's task…";

export function reviewChatUnknownLine(review: Pick<ChangeSetTarget, 'repo' | 'master' | 'leaf'>): string {
  return `The task list holds no task ${review.leaf ?? ''} in ${review.repo}/${review.master ?? ''}, so this review has no task chat to show.`;
}

/**
 * What the review page needs of its chat column: whether it is open, and its one toggle. The review
 * surface draws the chat side's shared toggle in its top line and binds `]` to `toggle`; the toggle
 * names the column by `RIGHT_RAIL_ID` (layout.tsx).
 */
export interface ReviewChatControl {
  open: boolean;
  toggle: () => void;
  /**
   * Open the column, and never close it.
   *
   * A press that asks for the first request (`✦ Explain`, a ⊕ with no seat) opens the column: the
   * ask's own listener does that, and the press then has to *open* it too, not toggle it. Toggling
   * there ran against the state the press started from (`open: false`), so the ask's open and the
   * press's toggle cancelled out in one batch and the column stayed closed: the explainer column
   * existed inside a `display: none` rail with a 0×0 box, and the press looked like it did nothing.
   */
  expand?: () => void;
}

export interface ReviewExplainerTarget extends ExplainerColumnTarget {
  repository: string;
  master: string;
  leaf: string;
  planned: boolean;
  comparisonNumber: number | null;
  history: 'live' | 'recorded';
}

export interface ReviewChatColumn extends ReviewChatControl {
  width: number;
  onResize: (width: number) => void;
  /** The review grid's gutter in px: 14 above 1600 px, 12 at or below (W26). */
  gutter: number;
  placement: ReviewChatPlacement;
  documentRef: TaskDocumentRef | undefined;
  unbound: DocumentChatUnbound | null;
  /**
   * The leaf this column's explainer belongs to (MIK-R106 v2 rule 1). Null for a review that names
   * no leaf: only a leaf has an explainer, and there the document's own chat stands.
   */
  explainer: ReviewExplainerTarget | null;
}

/**
 * The task list's entry for one review's leaf, or `undefined` while the list does not name it.
 *
 * The master is matched by its folder name (`dirName`), the same way the review entry names it.
 */
function findTaskEntry(
  review: Pick<ChangeSetTarget, 'repo' | 'master' | 'leaf'> | null,
  tasks: Record<string, TaskEntry>,
): TaskEntry | undefined {
  if (!review) return undefined;
  return Object.values(tasks).find(
    (task) =>
      task.id === review.leaf &&
      task.reference?.repository === review.repo &&
      dirName(task.reference.path) === review.master,
  );
}

/**
 * Whether the task list says this leaf is planning, or `null` while it does not say.
 *
 * The review payload carries no planning fact, and the explainer's `/planned` route refuses a leaf
 * that is not planning -- so a page that asked anyway earned a 4xx on every open of every leaf
 * (MIK-R211 v2 rule 7). The leaf's own stage in the task list is that fact: `planned` means the leaf
 * has not published yet. `null` is "not known yet", which asks nothing rather than asking wrongly.
 */
export function useLeafPlanned(
  review: Pick<ChangeSetTarget, 'repo' | 'master' | 'leaf'> | null,
): boolean | null {
  const tasks = useDashboard((state) => state.tasks);
  const hydrated = useDashboard((state) => state.generatedAt !== null);
  return useMemo(() => {
    if (!hydrated) return null;
    const entry = findTaskEntry(review, tasks);
    return entry ? entry.stage === 'planned' : null;
  }, [hydrated, review, tasks]);
}

function useReviewChatTask(review: ChangeSetTarget | null) {
  const tasks = useDashboard((state) => state.tasks);
  const hydrated = useDashboard((state) => state.generatedAt !== null);
  const entry = useMemo(() => findTaskEntry(review, tasks), [review, tasks]);
  const task = useMemo(
    () => (review ? reviewChatTask(review, tasks, hydrated) : null),
    [review, tasks, hydrated],
  );
  const unknownLine = review ? reviewChatUnknownLine(review) : '';
  const state = task?.state;
  const unbound = useMemo<DocumentChatUnbound | null>(() => {
    if (state === 'pending') return { line: REVIEW_CHAT_PENDING_LINE, pending: true };
    if (state === 'unknown') return { line: unknownLine, pending: false };
    return null;
  }, [state, unknownLine]);
  const planned = Boolean(entry?.stage === 'planned');
  return { documentRef: task?.state === 'resolved' ? task.ref : undefined, unbound, planned };
}

function useReviewChatWidth() {
  const narrowClass = useMediaQuery(NARROW_CLASS);
  const [wide, setWide] = usePersistedNumber(REVIEW_CHAT_WIDTH_KEYS.wide, REVIEW_CHAT_WIDTH_DEFAULTS.wide);
  const [narrow, setNarrow] = usePersistedNumber(
    REVIEW_CHAT_WIDTH_KEYS.narrow,
    REVIEW_CHAT_WIDTH_DEFAULTS.narrow,
  );
  return narrowClass
    ? { width: narrow, onResize: setNarrow, gutter: 12 }
    : { width: wide, onResize: setWide, gutter: 14 };
}

function useReviewChatPlacement(): ReviewChatPlacement {
  const narrow = useNarrowCockpit();
  const overlay = useMediaQuery(OVERLAY);
  if (narrow) return 'narrow';
  return overlay ? 'overlay' : 'grid';
}

/**
 * The tray and the closed column (MIK-R188 rule 5 on the review page). While a review is shown:
 * - his open of the tray's full view (the Tray button, the pill or a click on its minimised line) opens
 *   the column, so the tray docks above the task chat's message box. A pick shows the tray minimised
 *   and opens no column (MIK-R188 rule 5, 15f92a8f). Closing the column afterwards leaves the tray
 *   open at the bottom right.
 * - a first pick made while the column is closed takes as its default the chat the opening column
 *   then shows; leaving the review ends that wait.
 * Opening the column is what its toggle does: it starts no agent and reads nothing more.
 */
function useTrayOpensColumn(review: ChangeSetTarget | null, open: boolean, openColumn: () => void): void {
  useEffect(() => {
    if (!review) return;
    return trayStore.subscribe((state, previous) => {
      if (isExpanded(state) && !isExpanded(previous)) openColumn();
    });
  }, [review, openColumn]);
  useEffect(() => trayStore.getState().setDeferDefault(review !== null && !open), [review, open]);
  useEffect(() => {
    if (!review) return;
    return () => trayStore.getState().clearPendingDefault();
  }, [review]);
}

/**
 * The column's state while `review` is shown. Open is held as the review target it was opened for,
 * so every other target (a new review, the same address opened again after Back, a cold load) is
 * closed without an effect and without storage.
 */
export function useReviewChat(review: ChangeSetTarget | null): ReviewChatColumn {
  const [openFor, setOpenFor] = useState<ChangeSetTarget | null>(null);
  const toggle = useCallback(
    () => setOpenFor((current) => (review && current !== review ? review : null)),
    [review],
  );
  const openColumn = useCallback(() => setOpenFor(review), [review]);
  const open = review !== null && openFor === review;
  useTrayOpensColumn(review, open, openColumn);
  const width = useReviewChatWidth();
  const placement = useReviewChatPlacement();
  const task = useReviewChatTask(review);
  const explainer = useExplainerTargetOf(review, task.planned);
  useFirstRequestOpens(review, openColumn);
  return {
    open,
    toggle,
    expand: openColumn,
    ...width,
    placement,
    documentRef: task.documentRef,
    unbound: task.unbound,
    explainer,
  };
}

/**
 * A review that names a leaf has an explainer (MIK-R106 v2 rule 1). The column selects it, or its
 * placeholder, and never the task's Worker. A review without a leaf keeps the document's chat.
 */
export function explainerAddress(
  review: Pick<ChangeSetTarget, 'repo' | 'master' | 'leaf'> | null,
): ExplainerAddress | null {
  return addressOfLeaf(review?.repo ?? null, review?.master ?? null, review?.leaf ?? null);
}

/** The address three facts make; all three, or no explainer at all. */
function addressOfLeaf(
  repository: string | null,
  master: string | null,
  leaf: string | null,
): ExplainerAddress | null {
  if (!repository || !master || !leaf) return null;
  return { repository, master, leaf };
}

/**
 * The address, kept by the three facts it is: the column and the ask compare it across renders, so
 * a fresh object per render must not read as a change.
 */
function useExplainerAddressOf(review: ChangeSetTarget | null): ExplainerAddress | null {
  const repository = review?.repo ?? null;
  const master = review?.master ?? null;
  const leaf = review?.leaf ?? null;
  return useMemo(() => addressOfLeaf(repository, master, leaf), [repository, master, leaf]);
}

function useExplainerTargetOf(
  review: ChangeSetTarget | null,
  planned: boolean,
): ReviewExplainerTarget | null {
  const address = useExplainerAddressOf(review);
  const comparisonNumber = typeof review?.comparison === 'number' ? review.comparison : null;
  const history: 'live' | 'recorded' = review?.review?.historical ? 'recorded' : 'live';
  return useMemo(() => {
    if (!address) return null;
    return {
      address,
      task: address.leaf,
      preload: true,
      repository: address.repository,
      master: address.master,
      leaf: address.leaf,
      planned,
      comparisonNumber,
      history,
    };
  }, [address, planned, comparisonNumber, history]);
}

/**
 * ✦ Explain and a ⊕ with no seat ask for the first-request form (MIK-R106 v2 rule 3). The ask
 * opens the column, which then shows the form; it starts nothing (rule 10).
 */
function useFirstRequestOpens(review: ChangeSetTarget | null, openColumn: () => void): void {
  const address = useExplainerAddressOf(review);
  useEffect(() => {
    if (!address) return;
    return onFirstRequest((body) => {
      if (sameExplainerAddress(body.address, address)) openColumn();
    });
  }, [address, openColumn]);
}
