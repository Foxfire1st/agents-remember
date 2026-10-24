// Retire on the task document (MIK-R106 v2 rule 7): the same control as the chat header's, on the
// task document, with the same confirmation naming what ends and what stays.
//
// WHERE ITS TRUTH COMES FROM. When a leaf task document is opened cold, the control reads the
// leaf's authoritative explainer state (`GET …/state`) once, and also follows any state the review
// page's column published.
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';

import { css } from '../../styled-system/css';
import { dirName } from '../panels/detail-panel/model';
import type { TaskDocumentRef, TaskEntry } from '../types/projection';
import {
  newExplainerId,
  readExplainerState,
  retireExplainer,
  type ExplainerAddress,
  type ExplainerRetireAnswer,
  type ExplainerState,
} from './client';
import { canRetire, retireConfirmation, type RetireConfirmation } from './model';
import { registerExplainerTarget } from './request';

const MAX_SEAT_CACHE = 64;

const keyOf = (address: ExplainerAddress): string =>
  `${address.repository}\u0000${address.master}\u0000${address.leaf}`;

// The state the column or task document read, kept by reference: `useSyncExternalStore` compares
// snapshots by identity, so this map holds the one object per leaf rather than building a new one.
const seats = new Map<string, ExplainerState>();
const listeners = new Set<() => void>();

/** Publish a leaf's explainer state so the task document and tray share the same truth. */
export function publishExplainerSeat(address: ExplainerAddress, state: ExplainerState): void {
  registerExplainerTarget(address, {
    agentId: state.agentId,
    activeSeat: state.seat === 'live' || state.seat === 'closed',
  });
  const key = keyOf(address);
  if (seats.get(key) === state) return;
  if (!seats.has(key) && seats.size >= MAX_SEAT_CACHE) {
    const oldest = seats.keys().next().value;
    if (oldest !== undefined) seats.delete(oldest);
  }
  seats.set(key, state);
  for (const listener of listeners) listener();
}

export function knownExplainerSeat(address: ExplainerAddress): ExplainerState | null {
  return seats.get(keyOf(address)) ?? null;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Tests only. */
export function resetExplainerSeats(): void {
  seats.clear();
  listeners.clear();
}

/** The seat's words on the task document: what the leaf's explainer is doing now. */
function seatWords(seat: ExplainerState): string {
  if (seat.seat === 'live') return 'live · its chat is on the intent review page';
  if (seat.seat === 'closed') return 'closed · resumes when its chat is shown';
  if (seat.retiredAt !== null) return 'no explainer · the last one was retired';
  return 'no explainer';
}

/** Whether the document shows the control at all: a seat to retire, or one that ended. */
function hasSeat(seat: ExplainerState | null): seat is ExplainerState {
  if (!seat) return false;
  return canRetire(seat) || seat.retiredAt !== null;
}

/** The confirmation, with the two lists Retire names (MIK-R106 rule 7). */
function RetirePanel({
  words,
  onCancel,
  onConfirm,
}: {
  words: RetireConfirmation;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className={panel} role="group" aria-label="Retire the explainer" data-testid="explainer-task-document-confirm">
      <h4 className={headline}>{words.headline}</h4>
      <p className={line}>{words.line}</p>
      <ul className={list} data-testid="explainer-task-document-ends">
        {words.ends.map((entry) => (
          <li key={entry}>{entry}</li>
        ))}
      </ul>
      <ul className={list} data-testid="explainer-task-document-stays">
        {words.stays.map((entry) => (
          <li key={entry}>{entry}</li>
        ))}
      </ul>
      <div className={row}>
        <button type="button" className={button} data-testid="explainer-task-document-cancel" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className={button} data-testid="explainer-task-document-confirm-send" onClick={onConfirm}>
          Retire chat
        </button>
      </div>
    </div>
  );
}

/** What Retire left behind after it ran. */
function ReceiptLine({ receipt }: { receipt: ExplainerRetireAnswer | null }) {
  if (!receipt) return null;
  const remaining = receipt.receipt.remaining;
  const words = remaining.length ? `Processes remain: ${remaining.join(', ')}.` : 'No process remains.';
  return (
    <p className={line} role="status" data-testid="explainer-task-document-receipt">
      Retired. {words}
    </p>
  );
}

/** Why Retire could not run. */
function ProblemLine({ problem }: { problem: string | null }) {
  if (!problem) return null;
  return (
    <p className={line} role="alert" data-testid="explainer-task-document-problem">
      {problem}
    </p>
  );
}

function useColdExplainerSeat(address: ExplainerAddress): ExplainerState | null {
  const { repository, master, leaf } = address;
  const target = useMemo(() => ({ repository, master, leaf }), [repository, master, leaf]);
  const seat = useSyncExternalStore(subscribe, () => knownExplainerSeat(target), () => null);
  useEffect(() => {
    if (!target.repository || !target.master || !target.leaf) return;
    if (knownExplainerSeat(target) !== null) return;
    let cancelled = false;
    void readExplainerState(target).then((read) => {
      if (!cancelled && read.kind === 'state') publishExplainerSeat(target, read.value);
    });
    return () => {
      cancelled = true;
    };
  }, [target]);
  return seat;
}

/** Retire on the task document: one control and its confirmation (MIK-R106 rule 7). */
export function ExplainerTaskDocumentControl({
  address,
  task,
}: {
  address: ExplainerAddress;
  task: string;
}) {
  const { repository, master, leaf } = address;
  const target = useMemo(() => ({ repository, master, leaf }), [repository, master, leaf]);
  const seat = useColdExplainerSeat(target);
  const [open, setOpen] = useState(false);
  const [receipt, setReceipt] = useState<ExplainerRetireAnswer | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const receiptIdRef = useRef<string | null>(null);
  const retire = useCallback(async () => {
    const receiptId = (receiptIdRef.current ??= newExplainerId('retire'));
    const answer = await retireExplainer(target, receiptId);
    if (answer.kind !== 'retired') {
      setOpen(false);
      setProblem(`${answer.reason}: ${answer.detail}`);
      return;
    }
    setProblem(null);
    setReceipt(answer.answer);
    const readback = await readExplainerState(target);
    const authoritative =
      readback.kind === 'state' && (readback.value.seat !== null || readback.value.retiredAt !== null)
        ? readback.value
        : answer.answer.state;
    const incomplete =
      answer.answer.receipt.state !== 'finished' || answer.answer.receipt.remaining.length > 0;
    if (incomplete) {
      setOpen(true);
      publishExplainerSeat(target, { ...authoritative, seat: 'closed', retiredAt: null });
      return;
    }
    receiptIdRef.current = null;
    setOpen(false);
    publishExplainerSeat(target, {
      ...authoritative,
      seat: null,
      retiredAt: authoritative.retiredAt ?? '',
    });
  }, [target]);

  if (!hasSeat(seat) && !receipt && !problem) return null;
  return (
    <div className={wrap} data-testid="explainer-task-document" data-seat={seat?.seat ?? 'none'}>
      <span className={label}>Explainer</span>
      {seat ? (
        <span className={line} data-testid="explainer-task-document-state">
          {seatWords(seat)}
        </span>
      ) : null}
      {canRetire(seat) ? (
        <button
          type="button"
          className={button}
          aria-expanded={open}
          data-testid="explainer-task-document-retire"
          onClick={() => setOpen((shown) => !shown)}
        >
          Retire chat
        </button>
      ) : null}
      {open ? (
        <RetirePanel
          words={retireConfirmation(task, seat)}
          onCancel={() => setOpen(false)}
          onConfirm={() => void retire()}
        />
      ) : null}
      <ReceiptLine receipt={receipt} />
      <ProblemLine problem={problem} />
    </div>
  );
}

function findViewedTaskEntry(
  selectedId: string | null,
  viewedTaskRef: TaskDocumentRef | undefined,
  tasks: TaskEntry[],
): TaskEntry | undefined {
  if (viewedTaskRef) {
    const match = tasks.find(
      (task) =>
        task.reference?.repository === viewedTaskRef.repository &&
        task.reference?.path === viewedTaskRef.path,
    );
    if (match) return match;
  }
  if (selectedId?.startsWith('taskdoc:')) {
    const key = selectedId.slice('taskdoc:'.length);
    return tasks.find((task) => task.key === key);
  }
  return undefined;
}

function isLeafTaskEntry(entry: TaskEntry): boolean {
  const form = entry.summary?.form;
  if (form === 'task' || entry.summary?.kind === 'subTask') return true;
  if (form === 'master' || form === 'sprint') return false;
  return entry.parent !== undefined && entry.children.length === 0;
}

export function ViewedLeafExplainerControl({
  active,
  selectedId,
  viewedTaskRef,
  tasks,
}: {
  active: boolean;
  selectedId: string | null;
  viewedTaskRef: TaskDocumentRef | undefined;
  tasks: TaskEntry[];
}) {
  if (!active) return null;
  const entry = findViewedTaskEntry(selectedId, viewedTaskRef, tasks);
  if (!entry?.reference || !isLeafTaskEntry(entry)) return null;
  const master = dirName(entry.reference.path);
  if (!master) return null;
  return (
    <ExplainerTaskDocumentControl
      address={{ repository: entry.reference.repository, master, leaf: entry.id }}
      task={entry.id}
    />
  );
}

const wrap = css({
  display: 'flex',
  alignItems: 'baseline',
  flexWrap: 'wrap',
  gap: '0.4rem',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.74rem',
  margin: '0.4rem 0',
});
const label = css({ color: 'var(--muted)', textTransform: 'uppercase', fontSize: '0.68rem', letterSpacing: '0.06em' });
const line = css({ color: 'var(--muted)', fontSize: '0.74rem' });
const headline = css({ margin: '0.2rem 0', fontSize: '0.8rem' });
const button = css({
  border: '1px solid var(--grid)',
  background: 'transparent',
  color: 'inherit',
  borderRadius: '0.3rem',
  padding: '0.15rem 0.45rem',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.72rem',
  cursor: 'pointer',
});
const panel = css({ border: '1px solid var(--grid)', borderRadius: '0.4rem', padding: '0.5rem', flexBasis: '100%', background: 'var(--bg-panel)' });
const list = css({ margin: '0.2rem 0 0 1rem', padding: '0', fontSize: '0.72rem', color: 'var(--muted)' });
const row = css({ display: 'flex', gap: '0.4rem', justifyContent: 'flex-end', marginTop: '0.4rem' });
