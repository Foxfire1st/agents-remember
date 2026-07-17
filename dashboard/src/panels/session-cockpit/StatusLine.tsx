import { useEffect, useState, type ReactNode } from "react";

import { css, cx } from "../../../styled-system/css";
import { launchTier } from "../../data/launchEvidence";
import { deriveEffortMenu, effectiveSelection } from "../../data/sessionCapabilities";
import type { OpenSession } from "../../data/sessions";
import type { PerSessionCockpit } from "../../data/sessionCockpitStore";
import { seatVisualState } from "../../data/stateGrammar";
import { EFFORT_NOT_ECHOED_COPY } from "../../data/setControlsCopy";
import { leafIdFromKey } from "../../data/taskIdentity";
import { EvidenceBadge } from "../../grammar/EvidenceBadge";
import { StateDot } from "./StateDot";
import { formatApproxElapsed } from "./WorkingLine";

// Persistent focused-seat summary. Segment order is contractual: harness → pair+evidence → state
// → leaf/seat → pending/queue → the visibly absent UA-5 slot. Freshness follows as diagnostics;
// controls and the palette hint stay at the right edge.

const root = css({
  display: "flex",
  flexShrink: 0,
  alignItems: "baseline",
  gap: "0.65rem",
  flexWrap: "wrap",
  minWidth: "0",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
  paddingTop: "0.35rem",
  fontSize: "0.7rem",
  color: "muted",
  fontVariantNumeric: "tabular-nums",
});
const segment = css({ display: "inline-flex", alignItems: "center", gap: "0.28rem", minWidth: "0" });
const pair = css({ color: "ink" });
const chip = css({
  display: "inline-flex",
  paddingInline: "0.3rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  whiteSpace: "nowrap",
});
const focusTarget = css({
  _focusVisible: {
    outlineWidth: "1px",
    outlineStyle: "solid",
    outlineColor: "amber",
    outlineOffset: "1px",
  },
});
const actionsSlot = css({ display: "inline-flex", alignItems: "center", flexWrap: "wrap", gap: "0.35rem" });
const hint = css({ marginLeft: "auto", whiteSpace: "nowrap" });

const WS_COPY: Record<PerSessionCockpit["freshness"]["ptyWs"], string> = {
  none: "pty ws —",
  connected: "pty ws ✓",
  reconnecting: "pty ws reconnecting",
  dropped: "pty ws dropped",
};

export interface StatusPollHealth {
  lastBeatAt: number | null;
  missedBeats: number;
  healthy: boolean;
}

function quietCopy(lastOutputAt: number | null, now: number): string | null {
  if (lastOutputAt === null) return null;
  const elapsed = Math.max(0, Math.floor((now - lastOutputAt) / 1000));
  if (elapsed < 120) return `quiet ${elapsed}s`;
  return `quiet ${Math.floor(elapsed / 60)}m${String(elapsed % 60).padStart(2, "0")}s`;
}

export function StatusLine({
  session,
  cockpit,
  pollHealth,
  actions,
  now,
}: {
  session: OpenSession | undefined;
  cockpit: PerSessionCockpit | undefined;
  pollHealth: StatusPollHealth;
  actions?: ReactNode;
  /** Test seam: freezes elapsed/freshness clocks. */
  now?: number;
}) {
  const visual = seatVisualState(session ?? {});
  const working = visual.key === "working";
  const lastOutputAt = cockpit?.freshness.lastOutputAt ?? null;
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    if ((!working && lastOutputAt === null) || now !== undefined) return undefined;
    const id = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [working, lastOutputAt, now]);

  const at = now ?? tick;
  const selection = effectiveSelection(cockpit);
  const snapshot = cockpit?.liveSnapshot?.payload;
  const effortMenu = snapshot ? deriveEffortMenu(snapshot) : undefined;
  const model =
    selection.modelKey ?? (snapshot === undefined ? (session?.resolvedModel ?? null) : null);
  const effort =
    selection.effort ??
    (snapshot === undefined
      ? (session?.resolvedEffort ?? null)
      : effortMenu?.kind === "menu" && effortMenu.selected === null
        ? EFFORT_NOT_ECHOED_COPY
        : null);
  const tier = session ? launchTier(session) : null;
  const seat = session?.seatRole ?? session?.spawnRole;
  const leaf = session?.leafKey ? leafIdFromKey(session.leafKey) : null;
  const pendingSets = cockpit ? Object.keys(cockpit.pendingSets).length : 0;
  const queued = cockpit?.queue.length ?? 0;
  const elapsed =
    working && cockpit?.turnClock.workingSince !== null && cockpit?.turnClock.workingSince !== undefined
      ? formatApproxElapsed(at - cockpit.turnClock.workingSince)
      : null;
  const freshness = cockpit?.freshness ?? { ptyWs: "none" as const, lastOutputAt: null };
  const quiet = quietCopy(freshness.lastOutputAt, at);
  const pollBeatAge =
    pollHealth.lastBeatAt === null
      ? "—"
      : `${Math.max(0, Math.floor((at - pollHealth.lastBeatAt) / 1000))}s`;

  return (
    <footer
      className={cx(root, "sessions__statusline")}
      data-region="statusline"
      data-testid="sessions-statusline"
    >
      <span
        className={cx(segment, focusTarget)}
        data-focus-target
        tabIndex={-1}
        data-testid="status-harness"
      >
        harness {session?.harness ?? "—"}
      </span>
      <span className={cx(segment, pair)} data-testid="status-pair">
        model {model ?? "—"} · effort {effort ?? "—"}
        {tier ? <EvidenceBadge tier={tier} size="sm" /> : null}
      </span>
      <span className={segment} data-testid="status-state">
        <StateDot state={visual} testId="status-dot" />
        <span>{visual.word}</span>
        {elapsed ? (
          <span
            title="client-measured from the observed turn-state transition — poll/sweep-bounded"
            data-testid="status-elapsed"
          >
            {elapsed}
          </span>
        ) : null}
      </span>
      <span className={segment} data-testid="status-leaf-seat">
        leaf {leaf ?? "—"} · seat {seat ?? "—"}
      </span>
      <span className={chip} data-testid="status-pending-sets">
        pending sets {pendingSets}/2
      </span>
      <span className={chip} data-testid="status-queued-messages">
        queued messages {queued} yours
      </span>
      <span className={segment} data-testid="status-ua5-slot">
        ctx — / cost — (UA-5 slot)
      </span>
      <span
        className={segment}
        data-testid="status-freshness"
        title="PTY freshness is pane-local. Turn state is catalog-poll / 10 s sweep bounded."
      >
        {WS_COPY[freshness.ptyWs]}
        {quiet ? ` · ${quiet}` : ""} · poll {pollHealth.healthy ? "healthy" : "stale"} · missed{" "}
        {pollHealth.missedBeats} · beat age {pollBeatAge}
      </span>
      {actions ? <span className={actionsSlot}>{actions}</span> : null}
      <span className={hint}>ctrl+k palette · ? keys · F6 regions</span>
    </footer>
  );
}
