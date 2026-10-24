/**
 * The moved notice and the Update offer (MIK-R106 v2 rule 6; MIK-R186 rules 6 and 7; MIK-R187 rule 9).
 *
 * The watcher sees the leaf move past the binding and says so; it wakes nothing. Update is the
 * developer's press: it captures the new state, binds it and marks what rests on it stale, and it
 * never starts a model or sends the explainer a message. Its named steps and percentages are the
 * chat column's to show (rule 4.1); the notice names its counts.
 *
 * Two shapes reach this component: the `/state` page model (from/to/blocks) and the watcher's own
 * notice (kind/from/to/blocksChanged/updateOffered plus the text it published on the stream). Both
 * render one notice; the published line is shown as it was published when the notice carries it.
 *
 * `moved-no-explainer`: no page and no grade exists for this leaf, so there is nothing to update --
 * the deterministic view renews itself, and no control is offered (MIK-R187 rule 9).
 */

import { css } from '../../../../styled-system/css';
import type { MovedNoticeData } from './explainerApi';
import { movedLine } from './explainerModel';

const pending = css({
  display: 'flex',
  gap: '0.5rem',
  alignItems: 'baseline',
  flexWrap: 'wrap',
  padding: '0.35rem 0.55rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'amber',
  borderRadius: '3px',
  background: 'bgPanel',
  fontFamily: 'mono',
  fontSize: '0.72rem',
});

const line = css({ color: 'amber' });
const note = css({ color: 'muted', fontFamily: 'mono', fontSize: '0.7rem', margin: 0 });
const control = css({
  fontFamily: 'mono',
  fontSize: '0.72rem',
  padding: '0.15rem 0.45rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'amber',
  borderRadius: '3px',
  background: 'transparent',
  color: 'inherit',
  cursor: 'pointer',
  _disabled: { opacity: 0.45, cursor: 'default' },
});

export interface UpdateRun {
  state: 'idle' | 'updating' | 'done' | 'failed';
  detail?: string;
  counts?: { blocks?: number; parts?: number; grades?: number };
}

/**
 * The watcher's published notice (MIK-R186 rule 7), as `/state`'s `moved` field or the stream
 * event carries it. `text` is the exact line published on `/api/stream`.
 */
export interface WatcherNotice {
  kind?: 'comparison-moved' | 'master-moved';
  from: number | null;
  to: number | null;
  blocksChanged?: number;
  /** The same count under the page model's name, for a reader that predates this record. */
  blocks?: number;
  updateOffered?: boolean;
  commitFrom?: string | null;
  commitTo?: string | null;
  text?: string;
}

export interface MovedNoticeProps {
  moved: MovedNoticeData | WatcherNotice;
  /** Whether an explainer has worked here: a page or a grade exists to update. */
  hasWork: boolean;
  /** The press in flight, or its answer. */
  update?: UpdateRun | null;
  onUpdate?: () => void;
}

interface NoticeView {
  kind: 'comparison-moved' | 'master-moved';
  text: string;
  updateOffered: boolean;
}

export function MovedNotice({ moved, hasWork, update, onUpdate }: MovedNoticeProps) {
  const running = update?.state === 'updating';
  const notice = noticeView(moved, hasWork);
  return (
    <section className={pending} data-testid="moved-notice" data-moved={hasWork ? 'moved' : 'moved-no-explainer'}>
      <span className={line} data-testid="moved-line">
        {notice.text}
      </span>
      <NoticeActions offered={notice.updateOffered} running={running} onUpdate={onUpdate} />
      {update?.counts ? (
        <span className={note} data-testid="update-counts">
          {countsLine(update.counts)}
        </span>
      ) : null}
      {update?.state === 'failed' && update.detail ? (
        <span className={note} role="alert" data-testid="update-failed">
          {update.detail}
        </span>
      ) : null}
    </section>
  );
}

/** One notice out of either shape: the published line, its kind, and whether Update is offered. */
function noticeView(moved: MovedNoticeData | WatcherNotice, hasWork: boolean): NoticeView {
  return {
    kind: isMasterMoved(moved) ? 'master-moved' : 'comparison-moved',
    text: publishedLine(moved),
    updateOffered: updateOffered(moved, hasWork),
  };
}

function isMasterMoved(moved: MovedNoticeData | WatcherNotice): boolean {
  if ('kind' in moved && moved.kind === 'master-moved') return true;
  return Boolean(moved.commitFrom && moved.commitTo);
}

/** The text the stream published, or the page model's own line when no text came with it. */
function publishedLine(moved: MovedNoticeData | WatcherNotice): string {
  if ('text' in moved && moved.text) return moved.text;
  return movedLine({
    from: moved.from,
    to: moved.to,
    blocks: blockCount(moved),
    commitFrom: moved.commitFrom,
    commitTo: moved.commitTo,
  });
}

function blockCount(moved: MovedNoticeData | WatcherNotice): number {
  if ('blocksChanged' in moved && moved.blocksChanged !== undefined) return moved.blocksChanged;
  return (moved as MovedNoticeData).blocks;
}

/**
 * A planned binding's Update is offered even before a page exists: it rebinds at the tips
 * (rule 6). A recorded comparison's offer still waits for work to update (MIK-R187 rule 9).
 */
function updateOffered(moved: MovedNoticeData | WatcherNotice, hasWork: boolean): boolean {
  const allowed = !('updateOffered' in moved) || moved.updateOffered !== false;
  return allowed && (isMasterMoved(moved) || hasWork);
}

/** The offer, or the sentence that says there is nothing to update. */
function NoticeActions({
  offered,
  running,
  onUpdate,
}: {
  offered: boolean;
  running: boolean;
  onUpdate?: () => void;
}) {
  if (!offered) {
    return (
      <span className={note} data-testid="moved-no-explainer-note">
        No explainer has worked here, so the view renewed itself.
      </span>
    );
  }
  return (
    <>
      <button
        type="button"
        className={control}
        data-testid="update"
        disabled={running || !onUpdate}
        onClick={onUpdate}
      >
        {running ? 'Updating…' : 'Update'}
      </button>
      <span className={note} data-testid="moved-note">
        Seen by the watcher; the explainer was not woken.
      </span>
    </>
  );
}

function countsLine(counts: NonNullable<UpdateRun['counts']>): string {
  const parts: string[] = [];
  if (counts.blocks !== undefined) parts.push(`${counts.blocks} blocks changed`);
  if (counts.parts !== undefined) parts.push(`${counts.parts} parts marked stale`);
  if (counts.grades !== undefined) parts.push(`${counts.grades} grades marked stale`);
  return parts.length ? `Updated: ${parts.join(' · ')}.` : 'Updated.';
}
