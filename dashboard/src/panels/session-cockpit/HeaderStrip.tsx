import { css } from "../../../styled-system/css";
import type { OpenSession } from "../../data/sessions";
import type { PerSessionCockpit } from "../../data/sessionCockpitStore";
import { seatVisualState } from "../../data/stateGrammar";
import { leafIdFromKey } from "../../data/taskIdentity";
import { StateDot } from "./StateDot";

// The HeaderStrip (260715-FEUI-L2 S5, spec §1.2 — R10): identity → controls → state →
// diagnostics, in that order. Elision runs diagnostics-first (highest flex-shrink), then
// leaf/seat; identity + state NEVER elide (flex: none). The ModelEffortControl slot ships EMPTY —
// L4 fills it; reserving it now keeps the layout stable when the control arrives. Provenance
// badges (R7 — moat 1, read-only) ride the diagnostics cluster: honest requested-tier wording
// only, never a claim the server did not make.

const strip = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.5rem",
  minWidth: "0",
  flexWrap: "nowrap",
  overflow: "hidden",
});
const identity = css({ flex: "none", display: "inline-flex", alignItems: "baseline", gap: "0.4rem" });
const sessionName = css({ fontSize: "0.82rem", color: "ink" });
const harnessName = css({ fontSize: "0.74rem", color: "muted" });
const controlSlot = css({ flex: "none", display: "inline-flex", gap: "0.35rem", minHeight: "1rem" });
const stateCluster = css({
  flex: "none",
  display: "inline-flex",
  alignItems: "center",
  gap: "0.35rem",
  fontSize: "0.72rem",
  color: "muted",
});
// Diagnostics elide FIRST: the only min-width:0 shrinking segment; leaf/seat elides after it.
const diagnostics = css({
  flex: "0 4 auto",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: "0.7rem",
  color: "muted",
  marginLeft: "auto",
});
const leafSeat = css({
  flex: "0 2 auto",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: "0.7rem",
  color: "muted",
});
const provenanceChip = css({
  fontSize: "0.62rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.25rem",
  color: "muted",
  whiteSpace: "nowrap",
});

function quietFor(lastOutputAt: number | null, now: number): string | undefined {
  if (lastOutputAt === null) return undefined;
  const seconds = Math.max(0, Math.round((now - lastOutputAt) / 1000));
  return seconds >= 120 ? `quiet ${Math.round(seconds / 60)}m` : `quiet ${seconds}s`;
}

const WS_WORDS: Record<PerSessionCockpit["freshness"]["ptyWs"], string> = {
  none: "ws —", // no PTY attached in this cockpit (the pane lands in L6) — absent, never faked
  connected: "ws ✓",
  reconnecting: "ws reconnecting",
  dropped: "ws dropped",
};

export function HeaderStrip({
  session,
  cockpit,
  now = Date.now(),
}: {
  session: OpenSession;
  cockpit: PerSessionCockpit | undefined;
  now?: number;
}) {
  const visual = seatVisualState(session);
  const freshness = cockpit?.freshness ?? { ptyWs: "none" as const, lastOutputAt: null };
  const quiet = quietFor(freshness.lastOutputAt, now);
  const evidenceTier = cockpit?.launchEvidence.tier ?? "pending";
  const seatRole = session.spawnRole ?? session.seatRole;

  return (
    <div className={strip} data-testid="header-strip">
      <span className={identity} data-header-segment="identity">
        <span className={sessionName}>{session.label}</span>
        {session.harness ? <span className={harnessName}>{session.harness}</span> : null}
      </span>
      <span
        className={controlSlot}
        data-header-segment="controls"
        data-slot="model-effort-control"
        data-testid="header-control-slot"
        // EMPTY by design: the ModelEffortControl lands in L4; the slot reserves its place.
      />
      <span className={stateCluster} data-header-segment="state" data-testid="header-state">
        <StateDot state={visual} testId="header-dot" />
        <span>{visual.word}</span>
      </span>
      {session.leafKey || seatRole ? (
        <span className={leafSeat} data-header-segment="leaf-seat" data-testid="header-leaf-seat">
          {session.leafKey ? `leaf ${leafIdFromKey(session.leafKey)}` : null}
          {session.leafKey && seatRole ? " · " : null}
          {seatRole ? `seat ${seatRole}` : null}
        </span>
      ) : null}
      <span
        className={diagnostics}
        data-header-segment="diagnostics"
        data-testid="header-diagnostics"
        title="Freshness: PTY WebSocket state + last-output age (this cockpit's pane, L6). Turn-state freshness is bounded by the 10 s liveness sweep."
      >
        {WS_WORDS[freshness.ptyWs]}
        {quiet ? ` · ${quiet}` : ""}
        {/* Provenance badges (R7): read-only moat-1 facts at their honest tier. */}
        {session.resolvedModel ? (
          <span className={provenanceChip} data-testid="header-provenance-model">
            {" "}
            model {session.resolvedModel}
            {session.resolvedEffort ? ` · ${session.resolvedEffort}` : ""} (
            {evidenceTier === "pending" ? "requested" : evidenceTier})
          </span>
        ) : null}
        {session.spawnLevel ? (
          <span className={provenanceChip} data-testid="header-provenance-level">
            {" "}
            {session.spawnLevel}
            {session.spawnLevelSource ? ` (${session.spawnLevelSource})` : ""}
          </span>
        ) : null}
      </span>
    </div>
  );
}
