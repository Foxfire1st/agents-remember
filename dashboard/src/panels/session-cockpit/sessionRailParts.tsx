import { pendingInteractionAgentLabel } from '../../data/interactionAnswer';
import {
  attentionZeroState,
  interactionPromptPreview,
  masterAttentionBadge,
  railRowTooltip,
  roleCode,
  type AttentionRollup,
  type RailMasterSection as RailMasterRow,
  type RailModel,
  type SpawnTreeRow,
} from '../../data/railModel';
import { turnHintWord, type PtyHarvest } from '../../data/ptyHarvest';
import { sessionPendingInteractionPayload, type OpenSession } from '../../data/sessions';
import { hasUnackedSetAttention } from '../../data/setChips';
import { seatVisualState, type SeatVisualState } from '../../data/stateGrammar';
import { leafIdFromKey } from '../../data/taskIdentity';
import type { GateNode } from '../../types/projection';
import { terminateConfirmCopy } from './lifecycleCopy';
import {
  attentionSlot,
  attnButton,
  attnStrip,
  bulkButton,
  confirmRow,
  doneFold,
  doneToggle,
  endButton,
  groupBox,
  groupRows,
  leafCaption,
  leafGroup,
  markerChip,
  masterBody,
  masterBox,
  masterHead,
  masterName,
  railBody,
  railTop,
  roleChip,
  rowActionGroup,
  rowLabelGroup,
  rowShell,
  rowTitle,
  sprintRow,
  staleBanner,
  statusChip,
  treeIndent,
  treeToggleButton,
  zeroState,
} from './sessionRailStyles';
import { StateDot } from './StateDot';

export type BulkTarget = { scope: 'sprint' } | { scope: 'master'; key: string };

export interface RailRowProps {
  session: OpenSession;
  dormant?: boolean;
  focusedSessionId: string | null;
  heldGates: ReadonlyMap<string, GateNode>;
  briefPending: ReadonlySet<string>;
  perSessionCockpit: Record<string, Parameters<typeof hasUnackedSetAttention>[0]>;
  harvestBySession: Record<string, PtyHarvest>;
  endFailure: { sessionId: string; error: string } | null;
  virtualized: boolean;
  highlight: ReadonlySet<string> | null;
  onFocusSession: (id: string) => void;
  onEnd: (session: OpenSession) => void;
  onDismissEndFailure: () => void;
}

function chipToneFor(visual: SeatVisualState): 'alarm' | 'warn' | 'muted' {
  if (visual.key === 'failed') return 'alarm';
  if (visual.key === 'awaiting-input' || visual.key === 'waiting') return 'warn';
  return 'muted';
}

function railTooltipFor(session: OpenSession, harvest: PtyHarvest | undefined): string {
  // Harvested hints join the row TOOLTIP as clearly-labeled hints — never the grammar.
  const hintParts = [
    harvest?.title ? `pty title: ${harvest.title}` : null,
    harvest?.turnHint ? `pty hint: ${turnHintWord(harvest.turnHint)}` : null,
  ].filter((part): part is string => part !== null);
  return (
    railRowTooltip(session, session.leafKey ? leafIdFromKey(session.leafKey) : undefined) +
    (hintParts.length > 0 ? ` · ${hintParts.join(' · ')}` : '')
  );
}

function RailRowLabelGroup({
  session,
  visual,
  code,
  gate,
  briefPending,
  perSessionCockpit,
  harvest,
  showChip,
  chipTone,
  promptTitled,
}: {
  session: OpenSession;
  visual: SeatVisualState;
  code: string | undefined;
  gate: GateNode | undefined;
  briefPending: ReadonlySet<string>;
  perSessionCockpit: RailRowProps['perSessionCockpit'];
  harvest: PtyHarvest | undefined;
  showChip: boolean;
  chipTone: 'alarm' | 'warn' | 'muted';
  promptTitled: string | undefined;
}) {
  return (
    <div className={rowLabelGroup}>
      {/* Rail dots carry the state WORD as their accessible name — the dot is the
          truncation-surviving signal and must speak, not just color. */}
      <StateDot
        state={visual}
        testId={`rail-dot-${session.id}`}
        ariaLabel={`state: ${visual.word}`}
      />
      {code ? (
        <span
          className={roleChip({
            role: code in ROLE_CHIP_TONES ? (code as keyof typeof ROLE_CHIP_TONES) : undefined,
          })}
          data-testid={`rail-role-${session.id}`}
        >
          {code}
        </span>
      ) : null}
      <span className={rowTitle}>{session.label}</span>
      <AttentionMarkers
        session={session}
        gate={gate}
        briefPending={briefPending}
        perSessionCockpit={perSessionCockpit}
        harvest={harvest}
      />
      {showChip ? (
        <span
          className={statusChip({ tone: chipTone })}
          // Question triage: the input? chip's tooltip carries the prompt preview.
          title={visual.key === 'awaiting-input' && promptTitled ? promptTitled : visual.word}
          data-testid={`rail-status-${session.id}`}
        >
          {visual.chip}
        </span>
      ) : null}
    </div>
  );
}

function AttentionMarkers({
  session,
  gate,
  briefPending,
  perSessionCockpit,
  harvest,
}: {
  session: OpenSession;
  gate: GateNode | undefined;
  briefPending: ReadonlySet<string>;
  perSessionCockpit: RailRowProps['perSessionCockpit'];
  harvest: PtyHarvest | undefined;
}) {
  return (
    <span className={attentionSlot} data-slot="attention-marker">
      {/* Legacy-raw bell: the vendor TUI rang — the ONLY attention signal a raw
          pane has. Cleared by focusing the seat. */}
      {harvest?.bellPending ? (
        <span
          className={markerChip({ tone: 'warn' })}
          role="img"
          aria-label="terminal bell — the vendor TUI rang"
          title="terminal bell — the vendor TUI rang; focusing the seat clears this"
          data-testid={`rail-bell-${session.id}`}
        >
          bell
        </span>
      ) : null}
      {/* Two-state brief column: marker present = brief pending; absent = none. */}
      {briefPending.has(session.id) ? (
        <span
          className={markerChip({ tone: 'warn' })}
          title="brief pending — dispatch brief awaiting acknowledgment"
          data-testid={`rail-brief-${session.id}`}
        >
          <span aria-hidden="true">✉</span> brief
        </span>
      ) : null}
      {gate ? (
        <span
          className={markerChip({ tone: 'warn' })}
          title={`gate ${gate.kind} — decision pending`}
          data-testid={`rail-gate-${session.id}`}
        >
          gate
        </span>
      ) : null}
      {/* Unacknowledged set outcomes (unsupported/clamp/unknown, pair failures) clear
          only through the explicit `mark seen` action in the ledger or attention overlay. */}
      {hasUnackedSetAttention(perSessionCockpit[session.id]) ? (
        <span
          className={markerChip({ tone: 'warn' })}
          role="img"
          aria-label="unacknowledged set outcome — use mark seen in the set ledger to acknowledge"
          title="unacknowledged set outcome (unsupported / clamp / unknown) — use mark seen in the inspector ledger or attention overlay"
          data-testid={`rail-set-unacked-${session.id}`}
        >
          set!
        </span>
      ) : null}
    </span>
  );
}

function RailRowActions({
  session,
  dormant,
  hasEndFailure,
  endFailure,
  onEnd,
  onDismissEndFailure,
}: {
  session: OpenSession;
  dormant: boolean;
  hasEndFailure: boolean;
  endFailure: { sessionId: string; error: string } | null;
  onEnd: (session: OpenSession) => void;
  onDismissEndFailure: () => void;
}) {
  return (
    <div className={rowActionGroup}>
      {hasEndFailure ? (
        <span className={confirmRow} role="alert" data-testid={`rail-end-error-${session.id}`}>
          <span title={endFailure?.error}>end failed: {endFailure?.error}</span>
          <button
            type="button"
            className={bulkButton}
            onClick={(event) => {
              event.stopPropagation();
              onEnd(session);
            }}
            data-testid={`rail-end-retry-${session.id}`}
          >
            retry
          </button>
          <button
            type="button"
            className={doneToggle}
            onClick={(event) => {
              event.stopPropagation();
              onDismissEndFailure();
            }}
            data-testid={`rail-end-error-dismiss-${session.id}`}
          >
            dismiss
          </button>
        </span>
      ) : (
        <button
          type="button"
          className={endButton}
          aria-label={`End ${session.label}`}
          title={terminateConfirmCopy(session)}
          onClick={(event) => {
            event.stopPropagation();
            onEnd(session);
          }}
          data-testid={`rail-end-${session.id}`}
        >
          {dormant ? '✕' : 'End'}
        </button>
      )}
    </div>
  );
}

function railRowDerived(
  session: OpenSession,
  heldGates: ReadonlyMap<string, GateNode>,
  endFailure: RailRowProps['endFailure'],
  focusedSessionId: string | null,
  harvestBySession: Record<string, PtyHarvest>,
) {
  const visual = seatVisualState(session);
  const code = roleCode(session);
  const gate = session.leafKey ? heldGates.get(session.leafKey) : undefined;
  const payload = sessionPendingInteractionPayload(session);
  const prompt = interactionPromptPreview(payload);
  // The preview names WHO asks when it is a multiplexed sub-agent approval —
  // the tooltip never implies the parent is asking.
  const agentLabel = pendingInteractionAgentLabel(payload);
  const promptTitled =
    prompt !== undefined && agentLabel !== undefined ? `${agentLabel}: ${prompt}` : prompt;
  const selected = session.id === focusedSessionId;
  // While the row shows an end-failure the status chip is dropped: the failure copy
  // already names the state, and dropping the chip guarantees the two controls fit inside
  // the aside at every rail width.
  const hasEndFailure = endFailure?.sessionId === session.id;
  const showChip = visual.chip !== undefined && !hasEndFailure;
  return {
    visual,
    code,
    gate,
    promptTitled,
    selected,
    hasEndFailure,
    showChip,
    chipTone: chipToneFor(visual),
    harvest: harvestBySession[session.id],
  };
}

export function RailRow({
  session,
  dormant = false,
  focusedSessionId,
  heldGates,
  briefPending,
  perSessionCockpit,
  harvestBySession,
  endFailure,
  virtualized,
  highlight,
  onFocusSession,
  onEnd,
  onDismissEndFailure,
}: RailRowProps) {
  const derived = railRowDerived(
    session,
    heldGates,
    endFailure,
    focusedSessionId,
    harvestBySession,
  );
  const { visual, code, gate, promptTitled, selected, hasEndFailure, showChip } = derived;
  const { chipTone, harvest } = derived;
  const tooltip = railTooltipFor(session, harvest);
  return (
    <div
      key={session.id}
      role="button"
      tabIndex={selected ? 0 : -1}
      className={rowShell}
      style={
        virtualized ? { contentVisibility: 'auto', containIntrinsicSize: 'auto 2rem' } : undefined
      }
      data-selected={selected ? 'true' : undefined}
      data-focus-target={selected ? 'true' : undefined}
      data-attention-highlight={highlight?.has(session.id) ? 'true' : undefined}
      data-attention-gate={gate ? 'true' : undefined}
      data-testid={`rail-row-${session.id}`}
      title={tooltip}
      onClick={() => onFocusSession(session.id)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onFocusSession(session.id);
        }
      }}
    >
      <RailRowLabelGroup
        session={session}
        visual={visual}
        code={code}
        gate={gate}
        briefPending={briefPending}
        perSessionCockpit={perSessionCockpit}
        harvest={harvest}
        showChip={showChip}
        chipTone={chipTone}
        promptTitled={promptTitled}
      />
      <RailRowActions
        session={session}
        dormant={dormant}
        hasEndFailure={hasEndFailure}
        endFailure={endFailure}
        onEnd={onEnd}
        onDismissEndFailure={onDismissEndFailure}
      />
    </div>
  );
}

function MasterAttentionBadge({
  badge,
  masterKey,
}: {
  badge: { glyph: string; count: number; kind: 'needsInput' | 'failed' } | null;
  masterKey: string;
}) {
  if (badge === null) return null;
  return (
    <span
      className={attnButton({
        tone: badge.kind === 'failed' ? 'alarm' : 'warn',
      })}
      data-testid={`rail-master-attention-${masterKey}`}
    >
      <span aria-hidden="true">{badge.glyph}</span>
      {badge.count} {badge.kind === 'failed' ? 'failed' : 'need input'}
    </span>
  );
}

export function BulkConfirm({
  target,
  doomed,
  onConfirm,
  onCancel,
}: {
  target: BulkTarget;
  doomed: OpenSession[];
  onConfirm: (target: BulkTarget) => void;
  onCancel: () => void;
}) {
  return (
    <span
      className={confirmRow}
      data-testid={`rail-bulk-confirm-${target.scope === 'sprint' ? 'sprint' : target.key}`}
    >
      {/* Honest preview: the count AND the names of what is removed. */}
      <span title={doomed.map((session) => session.label).join(', ')}>
        end {doomed.length}: {doomed.map((session) => session.label).join(', ')}
      </span>
      <button
        type="button"
        className={bulkButton}
        onClick={() => onConfirm(target)}
        data-testid="rail-bulk-execute"
      >
        confirm
      </button>
      <button type="button" className={doneToggle} onClick={onCancel}>
        cancel
      </button>
    </span>
  );
}

function RailMasterHead({
  master,
  badge,
  armed,
  onArmBulk,
  onConfirmBulk,
  onCancelBulk,
}: {
  master: RailMasterRow;
  badge: { glyph: string; count: number; kind: 'needsInput' | 'failed' } | null;
  armed: boolean;
  onArmBulk: (target: BulkTarget) => void;
  onConfirmBulk: (target: BulkTarget) => void;
  onCancelBulk: () => void;
}) {
  return (
    <div className={masterHead}>
      <span className={masterName} title={master.label}>
        {master.label}
      </span>
      <MasterAttentionBadge badge={badge} masterKey={master.key} />
      {master.completed.length > 0 ? (
        armed ? (
          <BulkConfirm
            target={{ scope: 'master', key: master.key }}
            doomed={master.completed}
            onConfirm={onConfirmBulk}
            onCancel={onCancelBulk}
          />
        ) : (
          <button
            type="button"
            className={bulkButton}
            onClick={() => onArmBulk({ scope: 'master', key: master.key })}
            title={master.completed.map((session) => session.label).join(', ')}
            data-testid={`rail-bulk-master-${master.key}`}
          >
            ✕ end {master.completed.length} done
          </button>
        )
      ) : null}
    </div>
  );
}

function RailMasterBody({
  master,
  doneOpen,
  rowProps,
  onToggleDone,
}: {
  master: RailMasterRow;
  doneOpen: boolean;
  rowProps: Omit<RailRowProps, 'session' | 'dormant'>;
  onToggleDone: (key: string) => void;
}) {
  return (
    <div className={masterBody}>
      {master.commandSeats.map((manager) => (
        <RailRow key={manager.id} session={manager} {...rowProps} />
      ))}
      {master.clusters.map((cluster) => (
        <div key={cluster.key} className={leafGroup} data-testid={`rail-cluster-${cluster.key}`}>
          <span className={leafCaption} title={cluster.label}>
            <span aria-hidden="true">└</span> {cluster.label}
          </span>
          {cluster.seats.map((seat) => (
            <RailRow key={seat.id} session={seat} {...rowProps} />
          ))}
        </div>
      ))}
      {master.completed.length > 0 ? (
        <div className={doneFold}>
          <button
            type="button"
            className={doneToggle}
            aria-expanded={doneOpen}
            onClick={() => onToggleDone(master.key)}
            data-testid={`rail-done-toggle-${master.key}`}
          >
            {doneOpen ? '▾' : '▸'} completed · {master.completed.length}
          </button>
        </div>
      ) : null}
      {doneOpen
        ? master.completed.map((session) => (
            <RailRow key={session.id} session={session} dormant {...rowProps} />
          ))
        : null}
    </div>
  );
}

export function RailMasterBlock({
  master,
  rollup,
  openDoneFolders,
  armedBulk,
  rowProps,
  onToggleDone,
  onArmBulk,
  onConfirmBulk,
  onCancelBulk,
}: {
  master: RailMasterRow;
  rollup: AttentionRollup;
  openDoneFolders: Record<string, boolean>;
  armedBulk: BulkTarget | null;
  rowProps: Omit<RailRowProps, 'session' | 'dormant'>;
  onToggleDone: (key: string) => void;
  onArmBulk: (target: BulkTarget) => void;
  onConfirmBulk: (target: BulkTarget) => void;
  onCancelBulk: () => void;
}) {
  const badge = masterAttentionBadge(master, rollup);
  const doneOpen = openDoneFolders[master.key] ?? false; // collapsed by default (RULED)
  const armed = armedBulk?.scope === 'master' && armedBulk.key === master.key;
  return (
    <section key={master.key} className={masterBox} data-testid={`rail-master-${master.key}`}>
      <RailMasterHead
        master={master}
        badge={badge}
        armed={armed}
        onArmBulk={onArmBulk}
        onConfirmBulk={onConfirmBulk}
        onCancelBulk={onCancelBulk}
      />
      <RailMasterBody
        master={master}
        doneOpen={doneOpen}
        rowProps={rowProps}
        onToggleDone={onToggleDone}
      />
    </section>
  );
}

export function AttentionStrip({
  rollup,
  onFocusSet,
  jumpClass,
}: {
  rollup: AttentionRollup;
  onFocusSet: (kind: keyof AttentionRollup, first: string | null) => void;
  jumpClass: (partial: Partial<AttentionRollup>) => string | null;
}) {
  if (attentionZeroState(rollup)) return null;
  return (
    <div className={attnStrip} data-testid="rail-attention-strip">
      {rollup.needsInput.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: 'warn' })}
          onClick={() => onFocusSet('needsInput', jumpClass({ needsInput: rollup.needsInput }))}
          data-testid="rail-attention-input"
        >
          ❗ {rollup.needsInput.length} need input
        </button>
      ) : null}
      {rollup.failed.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: 'alarm' })}
          onClick={() => onFocusSet('failed', jumpClass({ failed: rollup.failed }))}
          data-testid="rail-attention-failed"
        >
          ✖ {rollup.failed.length} failed
        </button>
      ) : null}
      {rollup.unacked.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: 'warn' })}
          onClick={() => onFocusSet('unacked', jumpClass({ unacked: rollup.unacked }))}
          data-testid="rail-attention-unacked"
        >
          {rollup.unacked.length} unacked
        </button>
      ) : null}
      {rollup.criticalBus.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: 'warn' })}
          onClick={() => onFocusSet('criticalBus', jumpClass({ criticalBus: rollup.criticalBus }))}
          data-testid="rail-attention-bus"
        >
          {rollup.criticalBus.length} bus
        </button>
      ) : null}
      {rollup.working.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: 'info' })}
          onClick={() => onFocusSet('working', jumpClass({ working: rollup.working }))}
          data-testid="rail-attention-working"
        >
          {rollup.working.length} working
        </button>
      ) : null}
    </div>
  );
}

export function RailTop({
  model,
  armedBulk,
  allLanded,
  treeView,
  onToggleTree,
  onArmSprint,
  onConfirmBulk,
  onCancelBulk,
}: {
  model: RailModel;
  armedBulk: BulkTarget | null;
  allLanded: OpenSession[];
  treeView: boolean;
  onToggleTree: () => void;
  onArmSprint: () => void;
  onConfirmBulk: (target: BulkTarget) => void;
  onCancelBulk: () => void;
}) {
  return (
    <div className={railTop}>
      {model.masters.length > 0 ? (
        <span className={sprintRow} data-testid="rail-sprint-row">
          sprint · {model.masters.length} master
          {model.masters.length === 1 ? '' : 's'}
        </span>
      ) : null}
      {model.completedTotal > 0 ? (
        armedBulk?.scope === 'sprint' ? (
          <BulkConfirm
            target={{ scope: 'sprint' }}
            doomed={allLanded}
            onConfirm={onConfirmBulk}
            onCancel={onCancelBulk}
          />
        ) : (
          <button
            type="button"
            className={bulkButton}
            onClick={onArmSprint}
            title={allLanded.map((session) => session.label).join(', ')}
            data-testid="rail-bulk-sprint"
          >
            ✕ end {model.completedTotal} completed
          </button>
        )
      ) : null}
      <button
        type="button"
        className={treeToggleButton}
        data-on={treeView ? 'true' : 'false'}
        onClick={onToggleTree}
        aria-pressed={treeView}
        title={
          treeView
            ? 'Rail view: orchestration tree (spawn-edge provenance). Switch back to the role hierarchy.'
            : 'Rail view: role hierarchy. Switch to the orchestration tree (spawn-edge provenance).'
        }
        data-testid="rail-tree-toggle"
      >
        {/* Reads as a view toggle (switch glyph), not a bare taxonomy noun. */}
        <span aria-hidden="true">⇄</span> {treeView ? 'tree view' : 'role view'}
      </button>
    </div>
  );
}

export interface RailBodyProps {
  pollHealthy: boolean;
  pollMissedBeats: number;
  model: RailModel;
  treeView: boolean;
  treeRows: SpawnTreeRow[];
  virtualized: boolean;
  renderedRowCount: number;
  armedBulk: BulkTarget | null;
  allLanded: OpenSession[];
  openDoneFolders: Record<string, boolean>;
  rollup: AttentionRollup;
  rowProps: Omit<RailRowProps, 'session' | 'dormant'>;
  onFocusSet: (kind: keyof AttentionRollup, first: string | null) => void;
  jumpClass: (partial: Partial<AttentionRollup>) => string | null;
  onToggleTree: () => void;
  onArmSprint: () => void;
  onConfirmBulk: (target: BulkTarget) => void;
  onCancelBulk: () => void;
  onToggleDone: (key: string) => void;
  onArmBulk: (target: BulkTarget) => void;
}

function RailTreeRows({
  treeRows,
  rowProps,
}: {
  treeRows: SpawnTreeRow[];
  rowProps: Omit<RailRowProps, 'session' | 'dormant'>;
}) {
  return (
    <div className={treeIndent} data-testid="rail-spawn-tree">
      {treeRows.map(({ session, depth }) => (
        <div key={session.id} style={{ marginLeft: `${depth * 0.9}rem` }}>
          <RailRow session={session} dormant={session.status === 'landed'} {...rowProps} />
        </div>
      ))}
    </div>
  );
}

function RailHierarchy({
  model,
  rollup,
  openDoneFolders,
  armedBulk,
  rowProps,
  onToggleDone,
  onArmBulk,
  onConfirmBulk,
  onCancelBulk,
}: {
  model: RailModel;
  rollup: AttentionRollup;
  openDoneFolders: Record<string, boolean>;
  armedBulk: BulkTarget | null;
  rowProps: Omit<RailRowProps, 'session' | 'dormant'>;
  onToggleDone: (key: string) => void;
  onArmBulk: (target: BulkTarget) => void;
  onConfirmBulk: (target: BulkTarget) => void;
  onCancelBulk: () => void;
}) {
  return (
    <>
      {model.spine.map((session) => (
        <RailRow key={session.id} session={session} {...rowProps} />
      ))}
      {model.masters.map((master) => (
        <RailMasterBlock
          key={master.key}
          master={master}
          rollup={rollup}
          openDoneFolders={openDoneFolders}
          armedBulk={armedBulk}
          rowProps={rowProps}
          onToggleDone={onToggleDone}
          onArmBulk={onArmBulk}
          onConfirmBulk={onConfirmBulk}
          onCancelBulk={onCancelBulk}
        />
      ))}
      {model.unattached.length > 0 || model.completedUnattached.length > 0 ? (
        <section className={groupBox} data-testid="rail-unattached">
          <div className={masterHead}>
            <span className={masterName}>unattached</span>
            <span className={sprintRow}>
              {model.unattached.length + model.completedUnattached.length}
            </span>
          </div>
          <div className={groupRows}>
            {model.unattached.map((session) => (
              <RailRow key={session.id} session={session} {...rowProps} />
            ))}
            {model.completedUnattached.map((session) => (
              <RailRow key={session.id} session={session} dormant {...rowProps} />
            ))}
          </div>
        </section>
      ) : null}
    </>
  );
}

export function RailBody(props: RailBodyProps) {
  return (
    // The rail region's primary focus target (design §5.3) — always present, even empty.
    <div
      className={railBody}
      data-testid="session-rail"
      data-focus-target
      data-rendered-row-count={props.renderedRowCount}
      data-virtualized={props.virtualized ? 'true' : 'false'}
      tabIndex={-1}
    >
      {!props.pollHealthy ? (
        <div className={staleBanner} data-testid="rail-poll-stale" role="status">
          catalog poll stale — {props.pollMissedBeats} beats missed; rows may be frozen
        </div>
      ) : null}
      <AttentionStrip
        rollup={props.rollup}
        onFocusSet={props.onFocusSet}
        jumpClass={props.jumpClass}
      />
      <RailTop
        model={props.model}
        armedBulk={props.armedBulk}
        allLanded={props.allLanded}
        treeView={props.treeView}
        onToggleTree={props.onToggleTree}
        onArmSprint={props.onArmSprint}
        onConfirmBulk={props.onConfirmBulk}
        onCancelBulk={props.onCancelBulk}
      />
      {props.treeRows.length === 0 ? (
        <div className={zeroState} data-testid="rail-zero-state">
          no chats — launch a hosted chat or raw terminal above
        </div>
      ) : props.treeView ? (
        <RailTreeRows treeRows={props.treeRows} rowProps={props.rowProps} />
      ) : (
        <RailHierarchy
          model={props.model}
          rollup={props.rollup}
          openDoneFolders={props.openDoneFolders}
          armedBulk={props.armedBulk}
          rowProps={props.rowProps}
          onToggleDone={props.onToggleDone}
          onArmBulk={props.onArmBulk}
          onConfirmBulk={props.onConfirmBulk}
          onCancelBulk={props.onCancelBulk}
        />
      )}
      {/* To declutter, the rail bus footer is REMOVED — inbox counts and agent-notifier liveness
          already live in the top bar (one authority per fact); the anchored detail stays in the
          Inspector's BusPane. */}
    </div>
  );
}

// Role-chip color variants present in the cva above — used to gate the variant lookup.
const ROLE_CHIP_TONES = {
  ARC: true,
  ORC: true,
  STR: true,
  DSG: true,
  MGR: true,
  WKR: true,
  CUR: true,
  SYS: true,
  REV: true,
} as const;
