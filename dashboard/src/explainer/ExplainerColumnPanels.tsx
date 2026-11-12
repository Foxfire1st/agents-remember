// The explainer column's sub-blocks (MIK-R106 v2 rules 1, 2, 4.1 and 7): the header with the task's
// chats, the placeholder, the build's panel and Retire's confirmation. Each one draws what the
// column's state holds and calls back for the one act it offers; none of them reads or posts.
import { useState } from 'react';

import { cx } from '../../styled-system/css';
import type {
  ExplainerBuild,
  ExplainerRetireAnswer,
  ExplainerState,
} from './client';
import {
  STEP_MARK,
  buildFailure,
  buildPercent,
  canRetire,
  columnSelection,
  phaseWords,
  placeholderWords,
  retireConfirmation,
  stepDetail,
  stepPercent,
  type ExplainerMenuRow,
  type ExplainerPhase,
} from './model';
import {
  bar,
  body,
  card,
  dangerButton,
  failureLine,
  fixedField,
  head,
  label,
  layer,
  line,
  menu,
  menuRow,
  quietButton,
  row,
  spacer,
  statusWord,
  step,
  stepWaiting,
  steps,
  who,
} from './columnStyles';

// ---- the header ----

/** The task's chats, from MIK-R188's seat list: the explainer first, then the task's other seats. */
function AgentMenu({
  rows,
  onSelect,
  onClose,
}: {
  rows: ExplainerMenuRow[];
  onSelect?: (agentId: string) => void;
  onClose: () => void;
}) {
  return (
    <div className={menu} role="menu" aria-label="Chats of this task" data-testid="explainer-agent-menu">
      {rows.length === 0 ? <p className={line}>No chat of this task is live.</p> : null}
      {rows.map((entry) => (
        <button
          key={entry.agentId}
          type="button"
          role="menuitem"
          className={menuRow}
          data-testid="explainer-agent-row"
          data-agent={entry.agentId}
          data-current={entry.current || undefined}
          onClick={() => {
            onSelect?.(entry.agentId);
            onClose();
          }}
        >
          <span>{entry.current ? '●' : '·'}</span>
          <span>{entry.role}</span>
          <span className={spacer} />
          <span className={statusWord}>{entry.state}</span>
        </button>
      ))}
    </div>
  );
}

export function ExplainerHeader({
  task,
  phase,
  state,
  rows,
  onSelectAgent,
  onExplain,
  onRetire,
  onRetireConfirm,
}: {
  task: string;
  phase: ExplainerPhase;
  state: ExplainerState | null;
  rows: ExplainerMenuRow[];
  onSelectAgent?: (agentId: string) => void;
  onExplain: () => void;
  onRetire: () => void;
  onRetireConfirm: boolean;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const selection = columnSelection(phase);
  return (
    <div className={cx(head, layer)} data-testid="explainer-header" data-selection={selection}>
      <div className={who}>
        <b data-testid="explainer-title">Explainer · {task}</b>
        <span className={statusWord} data-testid="explainer-status">
          {phaseWords(phase, state)}
        </span>
      </div>
      {rows.length > 0 ? (
        <button
          type="button"
          className={quietButton}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="The chats of this task"
          title="Chats of this task, in the same chat frame"
          data-testid="explainer-agent-menu-button"
          onClick={() => setMenuOpen((open) => !open)}
        >
          ▾
        </button>
      ) : null}
      {menuOpen ? (
        <AgentMenu rows={rows} onSelect={onSelectAgent} onClose={() => setMenuOpen(false)} />
      ) : null}
      <button type="button" className={quietButton} data-testid="explainer-explain" onClick={onExplain}>
        ✦ Explain
      </button>
      {canRetire(state) ? (
        <button
          type="button"
          className={quietButton}
          aria-expanded={onRetireConfirm}
          data-testid="explainer-retire"
          onClick={onRetire}
        >
          Retire chat
        </button>
      ) : null}
    </div>
  );
}

// ---- the placeholder (rule 2) ----

export function ExplainerPlaceholder({
  task,
  state,
  archivedSend,
  onArchivedSend,
  onExplain,
}: {
  task: string;
  state: ExplainerState | null;
  archivedSend: boolean;
  onArchivedSend: () => void;
  onExplain: () => void;
}) {
  const words = placeholderWords(state);
  return (
    <div className={body} data-testid="explainer-placeholder">
      <div className={card}>
        <div className={fixedField}>
          <span className={label}>Role</span>
          <span data-testid="explainer-placeholder-role">
            {words.role} 🔒
          </span>
        </div>
        <div className={fixedField} style={{ marginTop: '0.35rem' }}>
          <span className={label}>Agent</span>
          <span data-testid="explainer-placeholder-agent">{words.settingsRole}</span>
          <span className={statusWord}>from the settings role</span>
        </div>
        <p className={line} style={{ marginTop: '0.6rem' }} data-testid="explainer-placeholder-headline">
          <b>{words.headline}</b>
        </p>
        <p className={line} data-testid="explainer-placeholder-line">
          {words.line}
        </p>
        {words.retiredLine ? (
          <p className={line} data-testid="explainer-placeholder-retired">
            {words.retiredLine}
          </p>
        ) : null}
        <p className={line} data-testid="explainer-placeholder-hint">
          Press ✦ Explain or ⊕ on any block or invariant. The reference goes into the tray; you write your request
          before anything is sent. No Start button: nothing runs until you ask.
        </p>
        {archivedSend ? (
          <div className={row}>
            <button type="button" className={quietButton} data-testid="explainer-archived-send" onClick={onArchivedSend}>
              Send to the explainer · {task}
            </button>
            <span className={statusWord}>opens the first-request form with the tray’s picks</span>
          </div>
        ) : null}
        <div className={row}>
          <button type="button" className={quietButton} data-testid="explainer-placeholder-explain" onClick={onExplain}>
            ✦ Explain the change set
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- the build's progress (rule 4.1) ----

/**
 * Whether a build record is still live. The server writes `building`; the dashboard's parser settles
 * on `running` (`readBuilding`), and a record read straight from `/state` may carry either. A build
 * that ended is cleared from the record, so only these two spellings mean work is still happening.
 */
export function isRunningBuild(build: ExplainerBuild): boolean {
  const state = build.state as string;
  return state === 'running' || state === 'building';
}

/** One step of the build: its mark, its name and its own count. */
function BuildStepRow({ entry }: { entry: ExplainerBuild['steps'][number] }) {
  const detail = stepDetail(entry);
  const own = stepPercent(entry);
  return (
    <li
      className={cx(step, entry.state === 'waiting' && stepWaiting)}
      data-testid="explainer-build-step"
      data-step={entry.name}
      data-step-state={entry.state}
      {...(own === null ? {} : { 'data-step-percent': own })}
    >
      <span aria-hidden>{STEP_MARK[entry.state]}</span>
      <span>{entry.name}</span>
      {detail ? <span className={statusWord}>{detail}</span> : null}
    </li>
  );
}

export function BuildProgress({
  build,
  request,
  titles,
  onCancel,
  cancelling = false,
}: {
  build: ExplainerBuild;
  request: string;
  /** His references, as the build panel names them while they wait to go out. */
  titles: string[];
  /**
   * His Cancel while the build runs (MIK-R211 v2 rules 4 and 5): the one press that ends a stuck
   * attempt. It names the build's own request id — `attempt` holds it, because the parser keeps a
   * `requestId` there — and it is drawn only while the build runs, so a stopped or cleared build
   * offers no dead button.
   */
  onCancel?: () => void;
  cancelling?: boolean;
}) {
  const percent = buildPercent(build);
  const failure = buildFailure(build);
  const running = isRunningBuild(build);
  return (
    <div className={body} data-testid="explainer-build">
      <div className={card}>
        <h4 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>Building the explainer’s environment</h4>
        <div className={bar} data-testid="explainer-build-bar">
          <i style={{ display: 'block', height: '100%', width: `${percent}%`, background: 'var(--purple, #8b5cf6)' }} />
        </div>
        <p className={line} data-testid="explainer-build-percent">
          {percent}%
        </p>
        <ol className={steps} aria-label="The build’s steps" data-testid="explainer-build-steps">
          {build.steps.map((entry) => (
            <BuildStepRow key={entry.name} entry={entry} />
          ))}
        </ol>
        {failure ? (
          <p className={failureLine} role="alert" data-testid="explainer-build-failure" data-step={failure.step}>
            {failure.step} failed: {failure.reason}. Your request stays here for a new Send.
          </p>
        ) : null}
        {failure ? null : (
          <p className={line} data-testid="explainer-build-waiting">
            Your request waits until the explainer is ready, then goes out as its first turn:
            {titles.length ? ` ${titles.join(', ')}` : ''}
            {request.trim() ? ` “${request.trim()}”` : ''}
          </p>
        )}
        {onCancel && running ? (
          <div className={row}>
            <span className={spacer} />
            <button
              type="button"
              className={dangerButton}
              disabled={cancelling}
              data-testid="explainer-build-cancel"
              onClick={onCancel}
            >
              {cancelling ? 'Cancelling…' : 'Cancel the build'}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ---- Retire's confirmation (rule 7) ----

/** One of Retire's two lists: what ends, or what stays. */
function RetireList({ mark, entries, testId }: { mark: string; entries: string[]; testId: string }) {
  return (
    <ul className={steps} data-testid={testId}>
      {entries.map((entry) => (
        <li key={entry} className={step}>
          <span aria-hidden>{mark}</span>
          <span>{entry}</span>
        </li>
      ))}
    </ul>
  );
}

export function RetireConfirm({
  task,
  state,
  receipt,
  onCancel,
  onConfirm,
}: {
  task: string;
  state: ExplainerState | null;
  receipt: ExplainerRetireAnswer | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const words = retireConfirmation(task, state);
  return (
    <div className={body} data-testid="explainer-retire-confirm" role="group" aria-label="Retire the explainer">
      <div className={card}>
        <h4 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: '0.82rem' }}>{words.headline}</h4>
        <p className={line}>{words.line}</p>
        <p className={label} style={{ marginTop: '0.5rem' }}>
          What ends
        </p>
        <RetireList mark="✗" entries={words.ends} testId="explainer-retire-ends" />
        <p className={label} style={{ marginTop: '0.5rem' }}>
          What stays
        </p>
        <RetireList mark="✓" entries={words.stays} testId="explainer-retire-stays" />
        {receipt ? (
          <p className={failureLine} data-testid="explainer-retire-leftovers">
            Retire ended {receipt.receipt.endedByTree} by the recorded tree and {receipt.receipt.endedByMarker} by the
            marker.
            {receipt.receipt.remaining.length
              ? ` These processes remain: ${receipt.receipt.remaining.join(', ')}.`
              : ' No process remains.'}
          </p>
        ) : null}
        <div className={row}>
          <span className={spacer} />
          <button type="button" className={quietButton} data-testid="explainer-retire-cancel" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className={dangerButton} data-testid="explainer-retire-confirm-send" onClick={onConfirm}>
            Retire chat
          </button>
        </div>
      </div>
    </div>
  );
}
