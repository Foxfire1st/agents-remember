// The review page's chat column, as the leaf's explainer (MIK-R106 v2 rules 1–5, 7, 8 and 10).
//
// WHAT IT SELECTS. The leaf's explainer, or its placeholder while none exists — never the task's
// Worker or another seat (rule 1). The other chats of the task are rows
// of the header's agent menu, built from MIK-R188's seat list and filtered by task.
//
// WHAT STARTS NOTHING. Opening the column reads the explainer's state (`GET …/state`) and, while a
// build runs, re-reads it; it never posts. A model turn starts only from his Send in the
// first-request form, his message in the chat, or "Regenerate stale parts" with a live seat
// (rule 10). Showing a closed explainer resumes its process and starts no turn (rule 8).
//
// THE BUILD. The panel names each step as done, running or waiting, gives a percentage to a step
// that counts files or bytes, and shows his request as waiting to go out. A failure names its step
// and reason and keeps his request for a new Send (rule 4).
//
// This module is the column's shell: the state it reads lives in `columnState`, the blocks it draws
// in `ExplainerColumnPanels` and `FirstRequestForm`, and the styles in `columnStyles`.
import { TrayColumnDock } from '../tray/TrayDock';
import type { TrayPickInput } from '../tray/types';
import { PaseoChatFrame } from '../cockpit/PaseoChatFrame';
import type {
  ExplainerAddress,
  ExplainerHeldDelivery,
  ExplainerRead,
  ExplainerRetireAnswer,
} from './client';
import { columnSelection } from './model';
import { useExplainerColumn, type ExplainerColumnState } from './columnState';
import {
  BuildProgress,
  ExplainerHeader,
  ExplainerPlaceholder,
  RetireConfirm,
} from './ExplainerColumnPanels';
import { FirstRequestForm } from './FirstRequestForm';
import { column as columnLayout, line, notice, quietButton } from './columnStyles';

/** What a page says to mount the column; `active` comes from the pane that shows it. */
export interface ExplainerColumnTarget {
  address: ExplainerAddress;
  /** The task as the column names it, for example L181. */
  task: string;
  /** Preloaded while the column is closed (MIK-R106 rule 1): the client loads, no agent is opened. */
  preload?: boolean;
  repository?: string;
  master?: string;
  leaf?: string;
  planned?: boolean;
  comparisonNumber?: number | null;
  history?: 'live' | 'recorded';
}

export interface ExplainerColumnProps extends ExplainerColumnTarget {
  /** The page shows the column now. A closed seat resumes when it does; no model turn starts. */
  active: boolean;
}

/**
 * A read that failed, said plainly: the review stays usable, and Retry asks for another read. It
 * starts nothing either.
 */
function UnavailableNotice({ read, onRetry }: { read: ExplainerRead | null; onRetry: () => void }) {
  if (!read || read.kind !== 'unavailable') return null;
  return (
    <p className={notice} role="status" data-testid="explainer-unavailable">
      The explainer’s state could not be read: {read.detail}. The review stays usable.
      <button type="button" className={quietButton} onClick={onRetry}>
        Retry
      </button>
    </p>
  );
}

/** When the first turn is held in the host queue (MIK-R188 rule 9, MIK-R106). */
function HeldNotice({ held }: { held: ExplainerHeldDelivery | null }) {
  if (!held) return null;
  return (
    <p className={notice} role="status" data-testid="explainer-held-notice">
      First turn held{held.reason ? ` (${held.reason})` : ''}
      {held.detail ? `: ${held.detail}` : ''}.
    </p>
  );
}

/**
 * The first request's form, when the column is on it. It asks for nothing itself: his Send is the
 * column's.
 */
function FormPanel({ column, task }: { column: ExplainerColumnState; task: string }) {
  if (column.phase !== 'first-request' || !column.form) return null;
  return (
    <FirstRequestForm
      task={task}
      references={column.references}
      message={column.message}
      binding={column.state?.binding ?? null}
      prefill={column.form.prefill}
      staleGrades={column.form.staleGrades}
      failure={column.failure}
      sending={column.sending}
      onMessage={column.setMessage}
      onRemove={column.removePick}
      onNote={column.setNote}
      onCancel={column.cancel}
      onSend={() => void column.send()}
    />
  );
}

/** The build's panel, when a build is running or stopped at a failed step. */
function BuildPanel({ column }: { column: ExplainerColumnState }) {
  const build = column.activeBuild;
  if (!build || (column.phase !== 'building' && build.state !== 'failed')) return null;
  const request = column.sent?.message ?? column.message;
  const titles = column.sent?.titles ?? column.references.map((reference) => reference.title);
  return <BuildProgress build={build} request={request} titles={titles} />;
}

/** The placeholder: no seat, or a seat that ended and waits for a new request. */
function PlaceholderPanel({ column, task }: { column: ExplainerColumnState; task: string }) {
  if (column.phase !== 'placeholder' && column.phase !== 'retired') return null;
  return (
    <ExplainerPlaceholder
      task={task}
      state={column.state}
      archivedSend={column.archivedSend}
      onArchivedSend={column.openForm}
      onExplain={column.explain}
    />
  );
}

/** The one block the column shows under its header: Retire's confirmation, or the phase's panel. */
function ExplainerPanel({ column, task }: { column: ExplainerColumnState; task: string }) {
  if (column.retireOpen)
    return (
      <RetireConfirm
        task={task}
        state={column.state}
        receipt={column.receipt}
        onCancel={column.onRetireCancel}
        onConfirm={() => void column.onRetire()}
      />
    );
  return (
    <>
      <FormPanel column={column} task={task} />
      <BuildPanel column={column} />
      <PlaceholderPanel column={column} task={task} />
    </>
  );
}

/** What Retire ended, once the confirmation is closed: both counts, and what remains. */
function RetireReceipt({ receipt, hidden }: { receipt: ExplainerRetireAnswer | null; hidden: boolean }) {
  if (!receipt || hidden) return null;
  const remaining = receipt.receipt.remaining;
  return (
    <p className={line} role="status" data-testid="explainer-retire-receipt">
      Retired. {receipt.receipt.endedByTree} processes ended by the recorded tree,{' '}
      {receipt.receipt.endedByMarker} by the marker.{' '}
      {remaining.length ? `These remain: ${remaining.join(', ')}.` : 'No process remains.'}
    </p>
  );
}

export function ExplainerColumnChrome({
  column,
  task,
  unboundLine,
}: {
  column: ExplainerColumnState;
  task: string;
  unboundLine?: string | null;
}) {
  return (
    <>
      <ExplainerHeader
        task={task}
        phase={column.phase}
        state={column.state}
        rows={column.rows}
        onSelectAgent={column.selectAgent}
        onExplain={column.explain}
        onRetire={column.onRetireToggle}
        onRetireConfirm={column.retireOpen}
      />
      {unboundLine ? <p role="status">{unboundLine}</p> : null}
      <UnavailableNotice read={column.read} onRetry={column.refresh} />
      <HeldNotice held={column.held} />
      <ExplainerPanel column={column} task={task} />
      <RetireReceipt receipt={column.receipt} hidden={column.retireOpen} />
    </>
  );
}

/**
 * The one frame, kept mounted through every phase: switching phases never reloads it. Hidden, it is
 * neither shown nor active, and its preload still loads the client.
 */
function ExplainerFrame({
  address,
  target,
  active,
  preload,
  shown,
}: {
  address: ExplainerAddress;
  target: { agentId: string; workspaceId: string } | null;
  active: boolean;
  preload?: boolean;
  shown: boolean;
}) {
  return (
    <div
      className={columnLayout}
      style={shown ? undefined : { display: 'none' }}
      aria-hidden={shown ? undefined : true}
      data-testid="explainer-frame-holder"
    >
      <PaseoChatFrame
        active={active && shown}
        preload={preload}
        scope={`explainer:${address.repository}/${address.master}/${address.leaf}`}
        target={target}
        page="document"
      />
    </div>
  );
}

export function normalizeExplainerTarget(target: ExplainerColumnTarget): {
  address: ExplainerAddress;
  task: string;
  preload?: boolean;
  planned?: boolean;
  comparisonNumber?: number | null;
  history?: 'live' | 'recorded';
} {
  const address = target.address ?? {
    repository: target.repository ?? '',
    master: target.master ?? '',
    leaf: target.leaf ?? '',
  };
  return {
    address,
    task: target.task || address.leaf,
    preload: target.preload,
    planned: target.planned,
    comparisonNumber: target.comparisonNumber,
    history: target.history,
  };
}

export function ExplainerColumn(props: ExplainerColumnProps) {
  const { address, task, preload, planned, comparisonNumber, history } = normalizeExplainerTarget(props);
  const column = useExplainerColumn(address, task, props.active, {
    planned,
    comparisonNumber,
    history,
  });
  return (
    <section
      className={columnLayout}
      data-testid="explainer-column"
      data-phase={column.phase}
      data-selection={columnSelection(column.phase)}
    >
      <ExplainerColumnChrome column={column} task={task} />
      <ExplainerFrame
        address={address}
        target={column.frameTarget}
        active={props.active}
        preload={preload}
        shown={column.showFrame}
      />
      <TrayColumnDock visible={props.active} page="document" />
    </section>
  );
}

// The column's blocks and hooks are also reached directly, so a page may mount or reuse one alone.
export { BUILD_POLL_MS, useBuildPolling, useExplainerRead } from './columnState';
export { ExplainerHeader, ExplainerPlaceholder, BuildProgress, RetireConfirm } from './ExplainerColumnPanels';
export { FirstRequestForm } from './FirstRequestForm';

// A caller may hand the column's own pick to the tray; the type is the tray's.
export type { TrayPickInput };
