import { useMemo, useState, type ReactNode } from "react";

import { css, cva } from "../../../styled-system/css";
import { useSessions, type OpenSession } from "../../data/sessions";
import {
  attentionZeroState,
  briefPendingSessionIds,
  buildSpawnTree,
  heldGatesByLeafKey,
  interactionPromptPreview,
  jumpToAttentionTarget,
  masterAttentionBadge,
  railRowTooltip,
  roleCode,
  type AttentionRollup,
  type RailMasterSection,
  type RailModel,
} from "../../data/railModel";
import { turnHintWord, usePtyHarvest } from "../../data/ptyHarvest";
import { useSessionCockpit } from "../../data/sessionCockpitStore";
import { hasUnackedSetAttention } from "../../data/setChips";
import {
  endLandedDetailed,
  endSessionDetailed,
  useLifecycleNotices,
} from "../../data/sessionLifecycle";
import { seatVisualState } from "../../data/stateGrammar";
import { useDashboard } from "../../data/store";
import { leafIdFromKey } from "../../data/taskIdentity";
import type { AgentPickupNode, TaskDocNode } from "../../types/projection";
import { cleanupOutcomeCopy, terminateConfirmCopy } from "./lifecycleCopy";
import { StateDot } from "./StateDot";

// The session rail (260715-FEUI-L2 S4): the ruled role-driven hierarchy (spec §1.6b) — flat
// command spine, indented per-leaf clusters with the active seat on top, per-master completed
// folders with master+sprint bulk end — plus the fleet-attention strip (R12), gate badges (R13),
// the two-state brief column (R8), the poll-health banner (R15), and the bus-summary footer (R8).
// The orchestration-tree (spawn-edge) view stays available as a palette/button toggle for
// provenance inspection (R5).

const railBody = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.35rem",
  flex: "1",
  minHeight: "0",
  overflowY: "auto",
  overflowX: "hidden",
});
const staleBanner = css({
  fontSize: "0.68rem",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.15rem",
});
const attnStrip = css({ display: "flex", gap: "0.3rem", flexWrap: "wrap", flexShrink: 0 });
const attnButton = cva({
  base: {
    font: "inherit",
    fontSize: "0.66rem",
    paddingInline: "0.4rem",
    paddingBlock: "0.08rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    background: "bg",
    color: "muted",
    cursor: "pointer",
    fontVariantNumeric: "tabular-nums",
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  },
  variants: {
    tone: {
      warn: { color: "amber", borderColor: "color-mix(in oklch, token(colors.amber) 45%, transparent)" },
      alarm: { color: "alarm", borderColor: "color-mix(in oklch, token(colors.alarm) 45%, transparent)" },
      info: { color: "cyan" },
      muted: {},
    },
  },
});
const sprintRow = css({
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "0.4rem",
  fontSize: "0.66rem",
  color: "muted",
  flexShrink: 0,
});
const bulkButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  color: "alarm",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "color-mix(in oklch, token(colors.alarm) 35%, transparent)",
  borderRadius: "2px",
  paddingInline: "0.35rem",
  cursor: "pointer",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const confirmRow = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.35rem",
  fontSize: "0.64rem",
  color: "amber",
  minWidth: "0",
  "& > span": { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
});
const masterBox = css({
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  background: "bgPanel",
  flexShrink: 0,
});
const masterHead = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  fontSize: "0.72rem",
  color: "ink",
  paddingInline: "0.5rem",
  paddingBlock: "0.3rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  minWidth: "0",
});
const masterName = css({ flex: "1", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
const masterBody = css({ padding: "0.3rem", display: "grid", gap: "0.25rem" });
// Leaf clusters: indented, separated by FINE hairlines + margins — never heavy boxes (RULED).
const leafGroup = css({
  marginLeft: "0.9rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "color-mix(in oklch, token(colors.grid) 55%, transparent)",
  paddingTop: "0.28rem",
  marginTop: "0.15rem",
  display: "grid",
  gap: "0.25rem",
});
const leafCaption = css({ fontSize: "0.62rem", color: "muted", letterSpacing: "0.04em" });
const doneFold = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  marginLeft: "0.9rem",
  marginTop: "0.15rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "color-mix(in oklch, token(colors.grid) 55%, transparent)",
  paddingTop: "0.28rem",
  color: "muted",
  fontSize: "0.7rem",
  minWidth: "0",
});
const doneToggle = css({
  font: "inherit",
  fontSize: "0.7rem",
  color: "muted",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  padding: "0",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const groupBox = css({
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  background: "bgPanel",
  flexShrink: 0,
});
const groupRows = css({ padding: "0.3rem", display: "grid", gap: "0.25rem" });
// Row anatomy (RULED R6): dot | role | title | status | End. The title is the flex segment;
// the status chip is the ONLY elidable segment (min-width 0) — dot/role/title always survive,
// the chip's truth stays in the row tooltip (railRowTooltip).
const rowShell = css({
  display: "flex",
  alignItems: "center",
  gap: "0.35rem",
  minWidth: "0",
  width: "100%",
  textAlign: "left",
  font: "inherit",
  background: "bg",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingLeft: "0.5rem",
  cursor: "pointer",
  "&[data-selected='true']": { color: "amber", borderColor: "amber" },
  "&[data-attention-highlight='true']": { borderColor: "cyan" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const roleChip = cva({
  base: {
    flex: "none",
    fontSize: "0.6rem",
    textTransform: "uppercase",
    letterSpacing: "0.08em",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "2px",
    paddingInline: "0.25rem",
    color: "muted",
  },
  variants: {
    role: {
      ARC: { color: "gold", borderColor: "color-mix(in oklch, token(colors.gold) 45%, transparent)" },
      ORC: { color: "gold", borderColor: "color-mix(in oklch, token(colors.gold) 45%, transparent)" },
      STR: { color: "gold" },
      DSG: { color: "gold" },
      MGR: { color: "purple", borderColor: "color-mix(in oklch, token(colors.purple) 45%, transparent)" },
      WKR: { color: "cyan" },
      CUR: { color: "cyan" },
      SYS: { color: "cyan" },
      REV: { color: "amber" },
    },
  },
});
const rowTitle = css({
  flex: "1",
  minWidth: "3.5rem", // the title always survives truncation (R6)
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: "0.74rem",
  paddingBlock: "0.25rem",
});
const attentionSlot = css({ display: "inline-flex", alignItems: "center", gap: "0.2rem", flex: "none" });
const markerChip = cva({
  base: {
    fontSize: "0.6rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "2px",
    paddingInline: "0.2rem",
    color: "muted",
  },
  variants: {
    tone: {
      warn: { color: "amber", borderColor: "color-mix(in oklch, token(colors.amber) 45%, transparent)" },
    },
  },
});
const statusChip = cva({
  base: {
    flexShrink: 1, // the ONLY elidable row segment (R6) — truth stays in the row tooltip
    minWidth: "0",
    maxWidth: "7rem",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    fontSize: "0.64rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "2px",
    paddingInline: "0.25rem",
    color: "muted",
  },
  variants: {
    tone: {
      warn: { color: "amber" },
      alarm: { color: "alarm" },
      muted: {},
    },
  },
});
const endButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  color: "alarm",
  background: "transparent",
  border: "none",
  borderLeftWidth: "1px",
  borderLeftStyle: "solid",
  borderLeftColor: "grid",
  paddingInline: "0.35rem",
  alignSelf: "stretch",
  cursor: "pointer",
  flex: "none",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "-1px" },
});
const treeIndent = css({ display: "grid", gap: "0.25rem" });
const railTop = css({ display: "flex", alignItems: "center", gap: "0.4rem", flexShrink: 0 });
const treeToggleButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  marginLeft: "auto",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.35rem",
  cursor: "pointer",
  "&[data-on='true']": { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const zeroState = css({
  flex: "1",
  minHeight: "0",
  display: "grid",
  placeItems: "center",
  padding: "0.8rem",
  color: "muted",
  fontSize: "0.72rem",
  lineHeight: "1.5",
  textAlign: "center",
  borderWidth: "1px",
  borderStyle: "dashed",
  borderColor: "grid",
  borderRadius: "2px",
});
const busFooter = css({
  marginTop: "auto",
  flexShrink: 0,
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
  paddingTop: "0.4rem",
  color: "muted",
  fontSize: "0.7rem",
  display: "grid",
  gap: "0.12rem",
  fontVariantNumeric: "tabular-nums",
  "& b": { color: "amber", fontWeight: "400" },
});

const EMPTY_DOCS: TaskDocNode[] = [];
const EMPTY_PICKUPS: AgentPickupNode[] = [];

/** Terminate one seat (L6 R5: the detailed flow keeps the stop residual — data/sessionLifecycle). */
export async function endSession(session: OpenSession): Promise<void> {
  await endSessionDetailed(session);
}

/** Bulk-end landed seats (master/sprint bulk affordances + their palette mirrors). The detailed
 *  flow records the route's honest outcome (closed + skipped with reasons) for the rail note. */
export async function endLanded(sessions: OpenSession[]): Promise<void> {
  await endLandedDetailed(sessions);
}

type BulkTarget = { scope: "sprint" } | { scope: "master"; key: string };

export interface SessionRailProps {
  onFocusSession: (id: string) => void;
  focusedSessionId: string | null;
  /** Precomputed joins the view shares with the palette commands (one derivation, two surfaces). */
  model: RailModel;
  rollup: AttentionRollup;
}

export function SessionRail({ onFocusSession, focusedSessionId, model, rollup }: SessionRailProps) {
  const sessions = useSessions((state) => state.sessions);
  const treeView = useSessionCockpit((state) => state.orchestrationTreeView);
  const setTreeView = useSessionCockpit((state) => state.setOrchestrationTreeView);
  const pollHealth = useSessionCockpit((state) => state.pollHealth);
  // L4 R6: unacknowledged set outcomes drive a per-row attention marker (the L2 slot).
  const perSessionCockpit = useSessionCockpit((state) => state.perSession);
  const taskDocuments = useDashboard((state) => state.analytics?.taskDocuments ?? EMPTY_DOCS);
  const lifecycles = useDashboard((state) => state.lifecycles);
  const pickups = useDashboard((state) => state.analytics?.agentPickups ?? EMPTY_PICKUPS);
  const heartbeat = useDashboard((state) => state.supervisorHeartbeat);

  const [openDoneFolders, setOpenDoneFolders] = useState<Record<string, boolean>>({});
  const [armedBulk, setArmedBulk] = useState<BulkTarget | null>(null);
  // L6 R5: single-End arms an inline honest confirm naming session · leaf · state.
  const [armedEnd, setArmedEnd] = useState<string | null>(null);
  // A FAILED terminate POST (review finding 4): the confirm implied execution, so the failure
  // renders verbatim with a retry — never a silent disarm. Distinct from stop residuals, which
  // are informational facts about SUCCESSFUL terminations.
  const [endFailure, setEndFailure] = useState<{ sessionId: string; error: string } | null>(null);
  const cleanupOutcome = useLifecycleNotices((state) => state.cleanupOutcome);
  const dismissCleanupOutcome = useLifecycleNotices((state) => state.dismissCleanupOutcome);
  // L6 R7: legacy-raw harvest (bell markers + title/turn hints) — raw panes only ever write it.
  const harvestBySession = usePtyHarvest((state) => state.bySession);
  // The clicked attention CLASS, not a snapshot of ids: the highlighted set derives from the
  // LIVE rollup each render, so a ring expires the moment the seat's state resolves (a stale
  // snapshot kept suggesting attention after it was gone — review finding 3).
  const [highlightKind, setHighlightKind] = useState<keyof AttentionRollup | null>(null);
  const highlight = highlightKind ? new Set(rollup[highlightKind]) : null;

  const heldGates = useMemo(
    () => heldGatesByLeafKey(taskDocuments, lifecycles),
    [taskDocuments, lifecycles],
  );
  const briefPending = useMemo(() => briefPendingSessionIds(pickups, sessions), [pickups, sessions]);

  const landedByMaster = new Map(model.masters.map((master) => [master.key, master.completed]));
  const allLanded = [
    ...model.masters.flatMap((master) => master.completed),
    ...model.completedUnattached,
  ];

  const focusSet = (kind: keyof AttentionRollup, first: string | null) => {
    setHighlightKind(kind);
    if (first) onFocusSession(first);
  };
  const jumpClass = (partial: Partial<AttentionRollup>) => {
    const scoped: AttentionRollup = {
      needsInput: [],
      failed: [],
      unacked: [],
      criticalBus: [],
      working: [],
      ...partial,
    };
    return jumpToAttentionTarget(scoped, sessions);
  };

  const executeEnd = async (session: OpenSession) => {
    setArmedEnd(null);
    const outcome = await endSessionDetailed(session);
    if (!outcome.ok) {
      setEndFailure({ sessionId: session.id, error: outcome.error ?? "terminate POST failed" });
    } else {
      setEndFailure((current) => (current?.sessionId === session.id ? null : current));
    }
  };

  const executeBulk = (target: BulkTarget) => {
    const doomed = target.scope === "sprint" ? allLanded : (landedByMaster.get(target.key) ?? []);
    setArmedBulk(null);
    void endLanded(doomed);
  };

  const bulkConfirm = (target: BulkTarget, doomed: OpenSession[]): ReactNode => (
    <span className={confirmRow} data-testid={`rail-bulk-confirm-${target.scope === "sprint" ? "sprint" : target.key}`}>
      {/* Honest preview: the count AND the names of what is removed. */}
      <span title={doomed.map((session) => session.label).join(", ")}>
        end {doomed.length}: {doomed.map((session) => session.label).join(", ")}
      </span>
      <button type="button" className={bulkButton} onClick={() => executeBulk(target)} data-testid="rail-bulk-execute">
        confirm
      </button>
      <button type="button" className={doneToggle} onClick={() => setArmedBulk(null)}>
        cancel
      </button>
    </span>
  );

  const renderRow = (session: OpenSession, options: { dormant?: boolean } = {}): ReactNode => {
    const visual = seatVisualState(session);
    const code = roleCode(session);
    const gate = session.leafKey ? heldGates.get(session.leafKey) : undefined;
    const prompt = interactionPromptPreview(session.controlPendingInteraction);
    const selected = session.id === focusedSessionId;
    const chipTone =
      visual.key === "failed" ? "alarm" : visual.key === "awaiting-input" || visual.key === "waiting" ? "warn" : "muted";
    // Harvested hints (R7) join the row TOOLTIP as clearly-labeled hints — never the grammar.
    const harvest = harvestBySession[session.id];
    const hintParts = [
      harvest?.title ? `pty title: ${harvest.title}` : null,
      harvest?.turnHint ? `pty hint: ${turnHintWord(harvest.turnHint)}` : null,
    ].filter((part): part is string => part !== null);
    const tooltip =
      railRowTooltip(session, session.leafKey ? leafIdFromKey(session.leafKey) : undefined) +
      (hintParts.length > 0 ? ` · ${hintParts.join(" · ")}` : "");
    return (
      <div
        key={session.id}
        role="button"
        tabIndex={selected ? 0 : -1}
        className={rowShell}
        data-selected={selected ? "true" : undefined}
        data-attention-highlight={highlight?.has(session.id) ? "true" : undefined}
        data-attention-gate={gate ? "true" : undefined}
        data-testid={`rail-row-${session.id}`}
        title={tooltip}
        onClick={() => onFocusSession(session.id)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onFocusSession(session.id);
          }
        }}
      >
        {/* L4 R8: rail dots carry the state WORD as their accessible name — the dot is the
            truncation-surviving signal and must speak, not just color. */}
        <StateDot
          state={visual}
          testId={`rail-dot-${session.id}`}
          ariaLabel={`state: ${visual.word}`}
        />
        {code ? (
          <span
            className={roleChip({ role: code in ROLE_CHIP_TONES ? (code as keyof typeof ROLE_CHIP_TONES) : undefined })}
            data-testid={`rail-role-${session.id}`}
          >
            {code}
          </span>
        ) : null}
        <span className={rowTitle}>{session.label}</span>
        <span className={attentionSlot} data-slot="attention-marker">
          {/* Legacy-raw bell (L6 R7): the vendor TUI rang — the ONLY attention signal a raw
              pane has. Cleared by focusing the seat. */}
          {harvest?.bellPending ? (
            <span
              className={markerChip({ tone: "warn" })}
              role="img"
              aria-label="terminal bell — the vendor TUI rang"
              title="terminal bell — the vendor TUI rang; focusing the seat clears this"
              data-testid={`rail-bell-${session.id}`}
            >
              bell
            </span>
          ) : null}
          {/* Two-state brief column (R8): marker present = brief pending; absent = none. */}
          {briefPending.has(session.id) ? (
            <span
              className={markerChip({ tone: "warn" })}
              title="brief pending — dispatch brief awaiting acknowledgment"
              data-testid={`rail-brief-${session.id}`}
            >
              ✉
            </span>
          ) : null}
          {gate ? (
            <span
              className={markerChip({ tone: "warn" })}
              title={`gate ${gate.kind} — decision pending`}
              data-testid={`rail-gate-${session.id}`}
            >
              gate
            </span>
          ) : null}
          {/* L4 R6: unacknowledged set outcomes (unsupported/clamp/unknown, pair failures) —
              cleared by viewing the ledger or dismissing the toast (mark seen). */}
          {hasUnackedSetAttention(perSessionCockpit[session.id]) ? (
            <span
              className={markerChip({ tone: "warn" })}
              role="img"
              aria-label="unacknowledged set outcome — view the set ledger to acknowledge"
              title="unacknowledged set outcome (unsupported / clamp / unknown) — view the set ledger in the inspector to acknowledge"
              data-testid={`rail-set-unacked-${session.id}`}
            >
              set!
            </span>
          ) : null}
        </span>
        {visual.chip ? (
          <span
            className={statusChip({ tone: chipTone })}
            // Question triage (R16): the input? chip's tooltip carries the prompt preview.
            title={visual.key === "awaiting-input" && prompt ? prompt : visual.word}
            data-testid={`rail-status-${session.id}`}
          >
            {visual.chip}
          </span>
        ) : null}
        {/* The End segment renders on EVERY row (ruled anatomy): live rows say "End", dormant
            (landed) rows carry the mockup's compact ✕. Arming shows the honest confirm naming
            session · leaf · state (L6 R5) before anything is killed. */}
        {endFailure?.sessionId === session.id ? (
          <span className={confirmRow} role="alert" data-testid={`rail-end-error-${session.id}`}>
            <span title={endFailure.error}>end failed: {endFailure.error}</span>
            <button
              type="button"
              className={bulkButton}
              onClick={(event) => {
                event.stopPropagation();
                void executeEnd(session);
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
                setEndFailure(null);
              }}
              data-testid={`rail-end-error-dismiss-${session.id}`}
            >
              dismiss
            </button>
          </span>
        ) : armedEnd === session.id ? (
          <span className={confirmRow} data-testid={`rail-end-confirm-${session.id}`}>
            <span title={terminateConfirmCopy(session)}>{terminateConfirmCopy(session)}</span>
            <button
              type="button"
              className={bulkButton}
              onClick={(event) => {
                event.stopPropagation();
                void executeEnd(session);
              }}
              data-testid={`rail-end-execute-${session.id}`}
            >
              confirm
            </button>
            <button
              type="button"
              className={doneToggle}
              onClick={(event) => {
                event.stopPropagation();
                setArmedEnd(null);
              }}
              data-testid={`rail-end-cancel-${session.id}`}
            >
              cancel
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
              setArmedEnd(session.id);
            }}
            data-testid={`rail-end-${session.id}`}
          >
            {options.dormant ? "✕" : "End"}
          </button>
        )}
      </div>
    );
  };

  const renderMaster = (master: RailMasterSection): ReactNode => {
    const badge = masterAttentionBadge(master, rollup);
    const doneOpen = openDoneFolders[master.key] ?? false; // collapsed by default (RULED)
    const armed = armedBulk?.scope === "master" && armedBulk.key === master.key;
    return (
      <section key={master.key} className={masterBox} data-testid={`rail-master-${master.key}`}>
        <div className={masterHead}>
          <span className={masterName} title={master.label}>
            {master.label}
          </span>
          {badge ? (
            <span
              className={attnButton({ tone: badge.kind === "failed" ? "alarm" : "warn" })}
              data-testid={`rail-master-attention-${master.key}`}
            >
              {badge.glyph}
              {badge.count}
            </span>
          ) : null}
          {master.completed.length > 0 ? (
            armed ? (
              bulkConfirm({ scope: "master", key: master.key }, master.completed)
            ) : (
              <button
                type="button"
                className={bulkButton}
                onClick={() => setArmedBulk({ scope: "master", key: master.key })}
                title={master.completed.map((session) => session.label).join(", ")}
                data-testid={`rail-bulk-master-${master.key}`}
              >
                ✕ end {master.completed.length} done
              </button>
            )
          ) : null}
        </div>
        <div className={masterBody}>
          {master.managers.map((manager) => renderRow(manager))}
          {master.clusters.map((cluster) => (
            <div key={cluster.key} className={leafGroup} data-testid={`rail-cluster-${cluster.key}`}>
              <span className={leafCaption}>└ {cluster.label}</span>
              {cluster.seats.map((seat) => renderRow(seat))}
            </div>
          ))}
          {master.completed.length > 0 ? (
            <div className={doneFold}>
              <button
                type="button"
                className={doneToggle}
                aria-expanded={doneOpen}
                onClick={() =>
                  setOpenDoneFolders((current) => ({ ...current, [master.key]: !doneOpen }))
                }
                data-testid={`rail-done-toggle-${master.key}`}
              >
                {doneOpen ? "▾" : "▸"} completed · {master.completed.length}
              </button>
            </div>
          ) : null}
          {doneOpen ? master.completed.map((session) => renderRow(session, { dormant: true })) : null}
        </div>
      </section>
    );
  };

  const strip = attentionZeroState(rollup) ? null : (
    <div className={attnStrip} data-testid="rail-attention-strip">
      {rollup.needsInput.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: "warn" })}
          onClick={() => focusSet("needsInput", jumpClass({ needsInput: rollup.needsInput }))}
          data-testid="rail-attention-input"
        >
          ❗ {rollup.needsInput.length} need input
        </button>
      ) : null}
      {rollup.failed.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: "alarm" })}
          onClick={() => focusSet("failed", jumpClass({ failed: rollup.failed }))}
          data-testid="rail-attention-failed"
        >
          ✖ {rollup.failed.length} failed
        </button>
      ) : null}
      {rollup.unacked.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: "warn" })}
          onClick={() => focusSet("unacked", jumpClass({ unacked: rollup.unacked }))}
          data-testid="rail-attention-unacked"
        >
          {rollup.unacked.length} unacked
        </button>
      ) : null}
      {rollup.criticalBus.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: "warn" })}
          onClick={() => focusSet("criticalBus", jumpClass({ criticalBus: rollup.criticalBus }))}
          data-testid="rail-attention-bus"
        >
          {rollup.criticalBus.length} bus
        </button>
      ) : null}
      {rollup.working.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: "info" })}
          onClick={() => focusSet("working", jumpClass({ working: rollup.working }))}
          data-testid="rail-attention-working"
        >
          {rollup.working.length} working
        </button>
      ) : null}
    </div>
  );

  const visibleCount =
    model.spine.length +
    model.masters.reduce(
      (sum, master) =>
        sum + master.managers.length + master.clusters.reduce((s, c) => s + c.seats.length, 0),
      0,
    ) +
    model.unattached.length;

  return (
    // The rail region's primary focus target (design §5.3) — always present, even empty.
    <div className={railBody} data-testid="session-rail" data-focus-target tabIndex={-1}>
      {!pollHealth.healthy ? (
        <div className={staleBanner} data-testid="rail-poll-stale" role="status">
          catalog poll stale — {pollHealth.missedBeats} beats missed; rows may be frozen
        </div>
      ) : null}
      {cleanupOutcome ? (
        // L6 R5: the landed-cleanup route's OWN outcome — closed AND skipped (with reasons) —
        // rendered after a bulk end instead of silently dropping the skips.
        <div className={sprintRow} role="status" data-testid="rail-cleanup-outcome">
          <span title={cleanupOutcomeCopy(cleanupOutcome)}>{cleanupOutcomeCopy(cleanupOutcome)}</span>
          <button
            type="button"
            className={doneToggle}
            onClick={dismissCleanupOutcome}
            aria-label="Dismiss cleanup outcome"
            data-testid="rail-cleanup-outcome-dismiss"
          >
            ✕
          </button>
        </div>
      ) : null}
      {strip}
      <div className={railTop}>
        {model.masters.length > 0 ? (
          <span className={sprintRow} data-testid="rail-sprint-row">
            sprint · {model.masters.length} master{model.masters.length === 1 ? "" : "s"}
          </span>
        ) : null}
        {model.completedTotal > 0 ? (
          armedBulk?.scope === "sprint" ? (
            bulkConfirm({ scope: "sprint" }, allLanded)
          ) : (
            <button
              type="button"
              className={bulkButton}
              onClick={() => setArmedBulk({ scope: "sprint" })}
              title={allLanded.map((session) => session.label).join(", ")}
              data-testid="rail-bulk-sprint"
            >
              ✕ end {model.completedTotal} completed
            </button>
          )
        ) : null}
        <button
          type="button"
          className={treeToggleButton}
          data-on={treeView ? "true" : "false"}
          onClick={() => setTreeView(!treeView)}
          title="Toggle the orchestration-tree (spawn-edge) view — provenance inspection only"
          data-testid="rail-tree-toggle"
        >
          {treeView ? "roles" : "tree"}
        </button>
      </div>
      {visibleCount === 0 && allLanded.length === 0 ? (
        <div className={zeroState} data-testid="rail-zero-state">
          no sessions — launch one from Chats (the cockpit launcher lands in L5)
        </div>
      ) : treeView ? (
        <div className={treeIndent} data-testid="rail-spawn-tree">
          {buildSpawnTree(sessions).map(({ session, depth }) => (
            <div key={session.id} style={{ marginLeft: `${depth * 0.9}rem` }}>
              {renderRow(session, { dormant: session.status === "landed" })}
            </div>
          ))}
        </div>
      ) : (
        <>
          {model.spine.map((session) => renderRow(session))}
          {model.masters.map(renderMaster)}
          {model.unattached.length > 0 || model.completedUnattached.length > 0 ? (
            <section className={groupBox} data-testid="rail-unattached">
              <div className={masterHead}>
                <span className={masterName}>unattached</span>
                <span className={sprintRow}>
                  {model.unattached.length + model.completedUnattached.length}
                </span>
              </div>
              <div className={groupRows}>
                {model.unattached.map((session) => renderRow(session))}
                {model.completedUnattached.map((session) => renderRow(session, { dormant: true }))}
              </div>
            </section>
          ) : null}
        </>
      )}
      <footer className={busFooter} data-testid="rail-bus-footer">
        {/* Bus summary (R8): anchored numbers — pending vs redeliverable, heartbeat vs cutoff. */}
        {heartbeat && heartbeat.lastTickAt !== null ? (
          <>
            <span>
              inbox <b>{heartbeat.pendingInboxCount} pending</b> / {heartbeat.redeliverableInboxCount}{" "}
              redeliverable
            </span>
            <span title="Turn-state freshness is bounded by the 10 s liveness sweep; the supervisor heartbeat is the bus liveness anchor.">
              heartbeat {heartbeat.ageSeconds ?? "—"}s / stale {heartbeat.staleCutoffSeconds}s
            </span>
          </>
        ) : (
          <span>inbox — · supervisor has not ticked in this workspace</span>
        )}
      </footer>
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
