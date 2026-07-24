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
} from "../../data/sessionLifecycle";
import { seatVisualState } from "../../data/stateGrammar";
import { useDashboard } from "../../data/store";
import { leafIdFromKey } from "../../data/taskIdentity";
import type { AgentPickupNode, TaskDocNode } from "../../types/projection";
import { terminateConfirmCopy } from "./lifecycleCopy";
import { StateDot } from "./StateDot";

// The session rail: the ruled role-driven hierarchy — flat
// command spine, indented per-leaf clusters with the active seat on top, per-master completed
// folders with master+sprint bulk end — plus the fleet-attention strip, gate badges,
// the two-state brief column, the poll-health banner, and the bus-summary footer.
// The orchestration-tree (spawn-edge) view stays available as a palette/button toggle for
// provenance inspection.

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
const attnStrip = css({
  display: "flex",
  gap: "0.3rem",
  flexWrap: "wrap",
  flexShrink: 0,
});
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
    _focusVisible: {
      outline: "1px solid token(colors.amber)",
      outlineOffset: "1px",
    },
  },
  variants: {
    tone: {
      warn: {
        color: "amber",
        borderColor:
          "color-mix(in oklch, token(colors.amber) 45%, transparent)",
      },
      alarm: {
        color: "alarm",
        borderColor:
          "color-mix(in oklch, token(colors.alarm) 45%, transparent)",
      },
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
  // An action control never crushes into a letter column: it holds its intrinsic width and
  // its own line, so the flex row elides the long copy span (below), never the buttons.
  flex: "none",
  whiteSpace: "nowrap",
  color: "alarm",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "color-mix(in oklch, token(colors.alarm) 35%, transparent)",
  borderRadius: "2px",
  paddingInline: "0.35rem",
  cursor: "pointer",
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});
const confirmRow = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.35rem",
  fontSize: "0.64rem",
  color: "amber",
  minWidth: "0",
  "& > span": {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
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
const masterName = css({
  flex: "1",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
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
const leafCaption = css({
  fontSize: "0.62rem",
  color: "muted",
  letterSpacing: "0.04em",
  // A long leaf id truncates at the end with the full
  // value on hover, never breaking mid-word down the narrow rail.
  display: "block",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
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
  // Hold width + own line so the confirm/cancel controls never wrap letters vertically.
  flex: "none",
  whiteSpace: "nowrap",
  color: "muted",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  padding: "0",
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});
const groupBox = css({
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  background: "bgPanel",
  flexShrink: 0,
});
const groupRows = css({ padding: "0.3rem", display: "grid", gap: "0.25rem" });
// Row anatomy (RULED): a LABEL group (dot | role | title | markers | chip) and an ACTION
// group (End, or the armed confirm/cancel). The row is `flex-wrap: wrap`: when the two groups cannot
// share one line at a narrow rail, the ACTION group wraps whole to a second line — the actions stay
// single-line and reachable at EVERY width down to the collapse threshold, never letter-wrapping,
// clipping, or overflowing the `overflow:hidden` aside. Priority when squeezed: actions > chip >
// inline copy — the label group's title/chip elide (min-width:0 through every nested level) and the
// armed state drops the chip entirely (the confirm copy already carries the state).
const rowLabelGroup = css({
  display: "flex",
  alignItems: "center",
  gap: "0.35rem",
  flex: "1 1 auto",
  minWidth: "0",
  overflow: "hidden",
});
const rowActionGroup = css({
  display: "flex",
  alignItems: "center",
  gap: "0.35rem",
  // Grows never, shrinks yes: it takes only the width its controls need on line 1, and on a wrapped
  // line 2 it shrinks so the elidable inline copy yields while the confirm/cancel buttons hold.
  flex: "0 1 auto",
  minWidth: "0",
});
const rowShell = css({
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  gap: "0.35rem",
  rowGap: "0.2rem",
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
  transition: "border-color 0.12s ease",
  // The row gives approach feedback before the click that arms End (there was none).
  _hover: { borderColor: "color-mix(in oklch, token(colors.amber) 45%, token(colors.grid))" },
  "&[data-selected='true']": { color: "amber", borderColor: "amber" },
  "&[data-attention-highlight='true']": { borderColor: "cyan" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
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
      ARC: {
        color: "gold",
        borderColor: "color-mix(in oklch, token(colors.gold) 45%, transparent)",
      },
      ORC: {
        color: "gold",
        borderColor: "color-mix(in oklch, token(colors.gold) 45%, transparent)",
      },
      STR: { color: "gold" },
      DSG: { color: "gold" },
      MGR: {
        color: "purple",
        borderColor:
          "color-mix(in oklch, token(colors.purple) 45%, transparent)",
      },
      WKR: { color: "cyan" },
      CUR: { color: "cyan" },
      SYS: { color: "cyan" },
      REV: { color: "amber" },
    },
  },
});
const rowTitle = css({
  // The flexible segment truly absorbs (min-width:0): it grows to show the label when there is
  // room and elides to `…` when the rail is tight, so it never forces the row past the aside. The full
  // label stays in the row tooltip (railRowTooltip).
  flex: "1 1 auto",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: "0.74rem",
  paddingBlock: "0.25rem",
});
const attentionSlot = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.2rem",
  flex: "none",
});
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
      warn: {
        color: "amber",
        borderColor:
          "color-mix(in oklch, token(colors.amber) 45%, transparent)",
      },
    },
  },
});
const statusChip = cva({
  base: {
    // The state chip shows its whole word when the rail has room (`turn-ended` ~72px < 7rem
    // ceiling). It is elidable (shrinks after the title yields) so it never forces the End
    // action off the row — the whole word stays in the chip tooltip, and the state also lives in the
    // StateDot's accessible name. It is dropped entirely while the row is armed (the confirm copy
    // carries the state), so the confirm/cancel controls always fit inside the aside.
    flex: "0 1 auto",
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
// A destructive control carries DEMOTED weight until the moment of consequence: muted by
// default (red would be six alarms shouting from every row and diluting the danger signal), and it
// warms to alarm only on hover / keyboard focus / the selected row — the approach that arms it.
const endButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  color: "muted",
  background: "transparent",
  border: "none",
  borderLeftWidth: "1px",
  borderLeftStyle: "solid",
  borderLeftColor: "grid",
  paddingInline: "0.35rem",
  alignSelf: "stretch",
  cursor: "pointer",
  flex: "none",
  transition: "color 0.12s ease",
  _hover: { color: "alarm" },
  "[data-selected='true'] &": { color: "alarm" },
  _focusVisible: {
    color: "alarm",
    outline: "1px solid token(colors.amber)",
    outlineOffset: "-1px",
  },
});
const treeIndent = css({ display: "grid", gap: "0.25rem" });
const railTop = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  flexShrink: 0,
});
const treeToggleButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  marginLeft: "auto",
  // The toggle never wraps its own label (`rol/e vie/w`): it holds width + its own line.
  flexShrink: 0,
  whiteSpace: "nowrap",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.35rem",
  cursor: "pointer",
  "&[data-on='true']": { color: "amber", borderColor: "amber" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
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

const EMPTY_DOCS: TaskDocNode[] = [];
const EMPTY_PICKUPS: AgentPickupNode[] = [];

/** Terminate one seat (the detailed flow keeps the stop residual — data/sessionLifecycle). */
export async function endSession(session: OpenSession): Promise<void> {
  await endSessionDetailed(session);
}

/** Bulk-end landed seats (master/sprint bulk affordances + their palette mirrors). The detailed
 *  flow records the route's honest outcome (closed + skipped with reasons) for the rail note. */
export async function endLanded(
  sessions: readonly Pick<OpenSession, "id" | "label">[],
): Promise<void> {
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

/** Browser-render virtualization starts only beyond the measured 50-row fleet envelope. */
export const RAIL_VIRTUALIZE_THRESHOLD = 50;

export function SessionRail({
  onFocusSession,
  focusedSessionId,
  model,
  rollup,
}: SessionRailProps) {
  const sessions = useSessions((state) => state.sessions);
  const treeView = useSessionCockpit((state) => state.orchestrationTreeView);
  const setTreeView = useSessionCockpit(
    (state) => state.setOrchestrationTreeView,
  );
  // A healthy catalog beat updates only lastBeatAt every 2.5 s. This rail renders no beat-age
  // value, so subscribe only to the two facts it actually shows; otherwise every successful poll
  // reconstructs the full role hierarchy for an invisible timestamp.
  const pollHealthy = useSessionCockpit((state) => state.pollHealth.healthy);
  const pollMissedBeats = useSessionCockpit((state) => state.pollHealth.missedBeats);
  // Unacknowledged set outcomes drive a per-row attention marker.
  const perSessionCockpit = useSessionCockpit((state) => state.perSession);
  const taskDocuments = useDashboard(
    (state) => state.analytics?.taskDocuments ?? EMPTY_DOCS,
  );
  const lifecycles = useDashboard((state) => state.lifecycles);
  const pickups = useDashboard(
    (state) => state.analytics?.agentPickups ?? EMPTY_PICKUPS,
  );

  const [openDoneFolders, setOpenDoneFolders] = useState<
    Record<string, boolean>
  >({});
  const [armedBulk, setArmedBulk] = useState<BulkTarget | null>(null);
  // Single-row End acts IMMEDIATELY — no armed inline confirm. The earlier armed confirm added a
  // step for a single seat and its overlay glitched; only bulk end keeps a confirm, because it
  // names multiple sessions at once.
  // A FAILED terminate POST: the failure renders verbatim with a retry —
  // never silent. Distinct from stop residuals, which are informational facts about
  // SUCCESSFUL terminations.
  const [endFailure, setEndFailure] = useState<{
    sessionId: string;
    error: string;
  } | null>(null);
  // Legacy-raw harvest (bell markers + title/turn hints) — raw panes only ever write it.
  const harvestBySession = usePtyHarvest((state) => state.bySession);
  // The clicked attention CLASS, not a snapshot of ids: the highlighted set derives from the
  // LIVE rollup each render, so a ring expires the moment the seat's state resolves (a stale
  // snapshot kept suggesting attention after it was gone).
  const [highlightKind, setHighlightKind] = useState<
    keyof AttentionRollup | null
  >(null);
  const highlight = highlightKind ? new Set(rollup[highlightKind]) : null;

  const heldGates = useMemo(
    () => heldGatesByLeafKey(taskDocuments, lifecycles),
    [taskDocuments, lifecycles],
  );
  const briefPending = useMemo(
    () => briefPendingSessionIds(pickups, sessions),
    [pickups, sessions],
  );

  const landedByMaster = new Map(
    model.masters.map((master) => [master.key, master.completed]),
  );
  const allLanded = [
    ...model.masters.flatMap((master) => master.completed),
    ...model.completedUnattached,
  ];
  const treeRows = useMemo(() => buildSpawnTree(sessions), [sessions]);
  // The threshold is a rendered-surface contract. Count the row shells the active view actually
  // emits, not a live-seat subtotal: completed-unattached is always visible, master-completed is
  // visible only while its folder is expanded, and tree mode has its own flattened population.
  const rolesRenderedRowCount =
    model.spine.length +
    model.masters.reduce(
      (sum, master) =>
        sum +
        master.managers.length +
        master.clusters.reduce(
          (clusterSum, cluster) => clusterSum + cluster.seats.length,
          0,
        ) +
        (openDoneFolders[master.key] ?? false ? master.completed.length : 0),
      0,
    ) +
    model.unattached.length +
    model.completedUnattached.length;
  const renderedRowCount = treeView ? treeRows.length : rolesRenderedRowCount;
  const virtualized = renderedRowCount > RAIL_VIRTUALIZE_THRESHOLD;

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
    const outcome = await endSessionDetailed(session);
    if (!outcome.ok) {
      setEndFailure({
        sessionId: session.id,
        error: outcome.error ?? "terminate POST failed",
      });
    } else {
      setEndFailure((current) =>
        current?.sessionId === session.id ? null : current,
      );
    }
  };

  const executeBulk = (target: BulkTarget) => {
    const doomed =
      target.scope === "sprint"
        ? allLanded
        : landedByMaster.get(target.key) ?? [];
    setArmedBulk(null);
    void endLanded(doomed);
  };

  const bulkConfirm = (
    target: BulkTarget,
    doomed: OpenSession[],
  ): ReactNode => (
    <span
      className={confirmRow}
      data-testid={`rail-bulk-confirm-${target.scope === "sprint" ? "sprint" : target.key}`}
    >
      {/* Honest preview: the count AND the names of what is removed. */}
      <span title={doomed.map((session) => session.label).join(", ")}>
        end {doomed.length}: {doomed.map((session) => session.label).join(", ")}
      </span>
      <button
        type="button"
        className={bulkButton}
        onClick={() => executeBulk(target)}
        data-testid="rail-bulk-execute"
      >
        confirm
      </button>
      <button
        type="button"
        className={doneToggle}
        onClick={() => setArmedBulk(null)}
      >
        cancel
      </button>
    </span>
  );

  const renderRow = (
    session: OpenSession,
    options: { dormant?: boolean } = {},
  ): ReactNode => {
    const visual = seatVisualState(session);
    const code = roleCode(session);
    const gate = session.leafKey ? heldGates.get(session.leafKey) : undefined;
    const prompt = interactionPromptPreview(session.controlPendingInteraction);
    const selected = session.id === focusedSessionId;
    // While the row shows an end-failure the status chip is dropped: the failure copy
    // already names the state, and dropping the chip guarantees the two controls fit inside
    // the aside at every rail width.
    const hasEndFailure = endFailure?.sessionId === session.id;
    const showChip = visual.chip !== undefined && !hasEndFailure;
    const chipTone =
      visual.key === "failed"
        ? "alarm"
        : visual.key === "awaiting-input" || visual.key === "waiting"
          ? "warn"
          : "muted";
    // Harvested hints join the row TOOLTIP as clearly-labeled hints — never the grammar.
    const harvest = harvestBySession[session.id];
    const hintParts = [
      harvest?.title ? `pty title: ${harvest.title}` : null,
      harvest?.turnHint ? `pty hint: ${turnHintWord(harvest.turnHint)}` : null,
    ].filter((part): part is string => part !== null);
    const tooltip =
      railRowTooltip(
        session,
        session.leafKey ? leafIdFromKey(session.leafKey) : undefined,
      ) + (hintParts.length > 0 ? ` · ${hintParts.join(" · ")}` : "");
    return (
      <div
        key={session.id}
        role="button"
        tabIndex={selected ? 0 : -1}
        className={rowShell}
        style={
          virtualized
            ? { contentVisibility: "auto", containIntrinsicSize: "auto 2rem" }
            : undefined
        }
        data-selected={selected ? "true" : undefined}
        data-focus-target={selected ? "true" : undefined}
        data-attention-highlight={
          highlight?.has(session.id) ? "true" : undefined
        }
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
              role:
                code in ROLE_CHIP_TONES
                  ? (code as keyof typeof ROLE_CHIP_TONES)
                  : undefined,
            })}
            data-testid={`rail-role-${session.id}`}
          >
            {code}
          </span>
        ) : null}
        <span className={rowTitle}>{session.label}</span>
        <span className={attentionSlot} data-slot="attention-marker">
          {/* Legacy-raw bell: the vendor TUI rang — the ONLY attention signal a raw
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
          {/* Two-state brief column: marker present = brief pending; absent = none. */}
          {briefPending.has(session.id) ? (
            <span
              className={markerChip({ tone: "warn" })}
              title="brief pending — dispatch brief awaiting acknowledgment"
              data-testid={`rail-brief-${session.id}`}
            >
              <span aria-hidden="true">✉</span> brief
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
          {/* Unacknowledged set outcomes (unsupported/clamp/unknown, pair failures) clear
              only through the explicit `mark seen` action in the ledger or attention overlay. */}
          {hasUnackedSetAttention(perSessionCockpit[session.id]) ? (
            <span
              className={markerChip({ tone: "warn" })}
              role="img"
              aria-label="unacknowledged set outcome — use mark seen in the set ledger to acknowledge"
              title="unacknowledged set outcome (unsupported / clamp / unknown) — use mark seen in the inspector ledger or attention overlay"
              data-testid={`rail-set-unacked-${session.id}`}
            >
              set!
            </span>
          ) : null}
        </span>
        {showChip ? (
          <span
            className={statusChip({ tone: chipTone })}
            // Question triage: the input? chip's tooltip carries the prompt preview.
            title={
              visual.key === "awaiting-input" && prompt ? prompt : visual.word
            }
            data-testid={`rail-status-${session.id}`}
          >
            {visual.chip}
          </span>
        ) : null}
        </div>
        {/* The End segment renders on EVERY row (ruled anatomy): live rows say "End", dormant
            (landed) rows carry a compact ✕. End acts IMMEDIATELY — no armed confirm. The ACTION
            group wraps below the label group at narrow rails so the failure retry/dismiss are
            always reachable + single-line. */}
        <div className={rowActionGroup}>
        {hasEndFailure ? (
          <span
            className={confirmRow}
            role="alert"
            data-testid={`rail-end-error-${session.id}`}
          >
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
        ) : (
          <button
            type="button"
            className={endButton}
            aria-label={`End ${session.label}`}
            title={terminateConfirmCopy(session)}
            onClick={(event) => {
              event.stopPropagation();
              void executeEnd(session);
            }}
            data-testid={`rail-end-${session.id}`}
          >
            {options.dormant ? "✕" : "End"}
          </button>
        )}
        </div>
      </div>
    );
  };

  const renderMaster = (master: RailMasterSection): ReactNode => {
    const badge = masterAttentionBadge(master, rollup);
    const doneOpen = openDoneFolders[master.key] ?? false; // collapsed by default (RULED)
    const armed = armedBulk?.scope === "master" && armedBulk.key === master.key;
    return (
      <section
        key={master.key}
        className={masterBox}
        data-testid={`rail-master-${master.key}`}
      >
        <div className={masterHead}>
          <span className={masterName} title={master.label}>
            {master.label}
          </span>
          {badge ? (
            <span
              className={attnButton({
                tone: badge.kind === "failed" ? "alarm" : "warn",
              })}
              data-testid={`rail-master-attention-${master.key}`}
            >
              <span aria-hidden="true">{badge.glyph}</span>
              {badge.count} {badge.kind === "failed" ? "failed" : "need input"}
            </span>
          ) : null}
          {master.completed.length > 0 ? (
            armed ? (
              bulkConfirm(
                { scope: "master", key: master.key },
                master.completed,
              )
            ) : (
              <button
                type="button"
                className={bulkButton}
                onClick={() =>
                  setArmedBulk({ scope: "master", key: master.key })
                }
                title={master.completed
                  .map((session) => session.label)
                  .join(", ")}
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
            <div
              key={cluster.key}
              className={leafGroup}
              data-testid={`rail-cluster-${cluster.key}`}
            >
              <span className={leafCaption} title={cluster.label}>
                <span aria-hidden="true">└</span> {cluster.label}
              </span>
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
                  setOpenDoneFolders((current) => ({
                    ...current,
                    [master.key]: !doneOpen,
                  }))
                }
                data-testid={`rail-done-toggle-${master.key}`}
              >
                {doneOpen ? "▾" : "▸"} completed · {master.completed.length}
              </button>
            </div>
          ) : null}
          {doneOpen
            ? master.completed.map((session) =>
                renderRow(session, { dormant: true }),
              )
            : null}
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
          onClick={() =>
            focusSet("needsInput", jumpClass({ needsInput: rollup.needsInput }))
          }
          data-testid="rail-attention-input"
        >
          ❗ {rollup.needsInput.length} need input
        </button>
      ) : null}
      {rollup.failed.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: "alarm" })}
          onClick={() =>
            focusSet("failed", jumpClass({ failed: rollup.failed }))
          }
          data-testid="rail-attention-failed"
        >
          ✖ {rollup.failed.length} failed
        </button>
      ) : null}
      {rollup.unacked.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: "warn" })}
          onClick={() =>
            focusSet("unacked", jumpClass({ unacked: rollup.unacked }))
          }
          data-testid="rail-attention-unacked"
        >
          {rollup.unacked.length} unacked
        </button>
      ) : null}
      {rollup.criticalBus.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: "warn" })}
          onClick={() =>
            focusSet(
              "criticalBus",
              jumpClass({ criticalBus: rollup.criticalBus }),
            )
          }
          data-testid="rail-attention-bus"
        >
          {rollup.criticalBus.length} bus
        </button>
      ) : null}
      {rollup.working.length > 0 ? (
        <button
          type="button"
          className={attnButton({ tone: "info" })}
          onClick={() =>
            focusSet("working", jumpClass({ working: rollup.working }))
          }
          data-testid="rail-attention-working"
        >
          {rollup.working.length} working
        </button>
      ) : null}
    </div>
  );

  return (
    // The rail region's primary focus target (design §5.3) — always present, even empty.
    <div
      className={railBody}
      data-testid="session-rail"
      data-focus-target
      data-rendered-row-count={renderedRowCount}
      data-virtualized={virtualized ? "true" : "false"}
      tabIndex={-1}
    >
      {!pollHealthy ? (
        <div
          className={staleBanner}
          data-testid="rail-poll-stale"
          role="status"
        >
          catalog poll stale — {pollMissedBeats} beats missed; rows may
          be frozen
        </div>
      ) : null}
      {strip}
      <div className={railTop}>
        {model.masters.length > 0 ? (
          <span className={sprintRow} data-testid="rail-sprint-row">
            sprint · {model.masters.length} master
            {model.masters.length === 1 ? "" : "s"}
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
          aria-pressed={treeView}
          title={
            treeView
              ? "Rail view: orchestration tree (spawn-edge provenance). Switch back to the role hierarchy."
              : "Rail view: role hierarchy. Switch to the orchestration tree (spawn-edge provenance)."
          }
          data-testid="rail-tree-toggle"
        >
          {/* Reads as a view toggle (switch glyph), not a bare taxonomy noun. */}
          <span aria-hidden="true">⇄</span> {treeView ? "tree view" : "role view"}
        </button>
      </div>
      {treeRows.length === 0 ? (
        <div className={zeroState} data-testid="rail-zero-state">
          no chats — launch a hosted chat or raw terminal above
        </div>
      ) : treeView ? (
        <div className={treeIndent} data-testid="rail-spawn-tree">
          {treeRows.map(({ session, depth }) => (
            <div key={session.id} style={{ marginLeft: `${depth * 0.9}rem` }}>
              {renderRow(session, { dormant: session.status === "landed" })}
            </div>
          ))}
        </div>
      ) : (
        <>
          {model.spine.map((session) => renderRow(session))}
          {model.masters.map(renderMaster)}
          {model.unattached.length > 0 ||
          model.completedUnattached.length > 0 ? (
            <section className={groupBox} data-testid="rail-unattached">
              <div className={masterHead}>
                <span className={masterName}>unattached</span>
                <span className={sprintRow}>
                  {model.unattached.length + model.completedUnattached.length}
                </span>
              </div>
              <div className={groupRows}>
                {model.unattached.map((session) => renderRow(session))}
                {model.completedUnattached.map((session) =>
                  renderRow(session, { dormant: true }),
                )}
              </div>
            </section>
          ) : null}
        </>
      )}
      {/* To declutter, the rail bus footer is REMOVED — inbox counts and supervisor liveness
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
