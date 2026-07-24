import { css } from "../../../styled-system/css";
import type { OpenSession } from "../../data/sessions";
import type { PerSessionCockpit } from "../../data/sessionCockpitStore";
import { seatVisualState } from "../../data/stateGrammar";
import { leafIdFromKey } from "../../data/taskIdentity";
import { ModelEffortControl } from "./ModelEffortControl";
import { StateDot } from "./StateDot";

// The HeaderStrip: identity → controls → state →
// diagnostics, in that order. Elision runs diagnostics-first (highest flex-shrink), then
// leaf context; identity + state NEVER elide (flex: none). The controls slot hosts the
// ModelEffortControl (design §6 — the chrome is the ONLY place model/effort exist for controlled
// sessions; one plain pair, no provenance duplication in diagnostics).

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
// The control + its chips may shrink (after diagnostics, before leaf context) — never the trigger's
// identity words themselves; chip text elides inside AcceptanceChip.
const controlSlot = css({
  flex: "0 1 auto",
  minWidth: "0",
  overflow: "hidden",
  display: "inline-flex",
  gap: "0.35rem",
  minHeight: "1rem",
});
const stateCluster = css({
  flex: "none",
  display: "inline-flex",
  alignItems: "center",
  gap: "0.35rem",
  fontSize: "0.72rem",
  color: "muted",
});
// Diagnostics elide FIRST: the only min-width:0 shrinking segment; leaf context elides after it.
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
const leafContext = css({
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
  none: "ws —", // no PTY attached in this cockpit (the pane is owned by the PtySurface) — absent, never faked
  connected: "ws ✓",
  reconnecting: "ws reconnecting",
  dropped: "ws dropped",
};

export function HeaderStrip({
  session,
  cockpit,
  controlPopover,
  now = Date.now(),
}: {
  session: OpenSession;
  cockpit: PerSessionCockpit | undefined;
  /** Controlled ModelEffortControl popover state — the palette commands open the same popover. */
  controlPopover?: { open: boolean; onOpenChange: (open: boolean) => void };
  now?: number;
}) {
  const visual = seatVisualState(session);
  const freshness = cockpit?.freshness ?? { ptyWs: "none" as const, lastOutputAt: null };
  const quiet = quietFor(freshness.lastOutputAt, now);

  return (
    <div className={strip} data-testid="header-strip">
      <span className={identity} data-header-segment="identity">
        <span className={sessionName}>{session.label}</span>
        {/* No `codex codex` stutter: the harness label is dropped when it merely repeats the
            session name (a raw terminal literally named after its harness). */}
        {session.harness &&
        session.harness.toLowerCase() !== session.label.toLowerCase() ? (
          <span className={harnessName}>{session.harness}</span>
        ) : null}
      </span>
      <span
        className={controlSlot}
        data-header-segment="controls"
        data-slot="model-effort-control"
        data-testid="header-control-slot"
      >
        {/* The one ModelEffortControl — renders nothing for non-harness/ended sessions. */}
        <ModelEffortControl
          session={session}
          cockpit={cockpit}
          open={controlPopover?.open}
          onOpenChange={controlPopover?.onOpenChange}
        />
      </span>
      <span className={stateCluster} data-header-segment="state" data-testid="header-state">
        <StateDot
          state={visual}
          testId="header-dot"
          ariaLabel={visual.key === "unclassified" ? "state unavailable" : undefined}
        />
        {visual.key === "unclassified" ? null : <span>{visual.word}</span>}
      </span>
      {session.leafKey ? (
        <span className={leafContext} data-header-segment="leaf" data-testid="header-leaf">
          leaf {leafIdFromKey(session.leafKey)}
        </span>
      ) : null}
      <span
        className={diagnostics}
        data-header-segment="diagnostics"
        data-testid="header-diagnostics"
        title="Freshness: PTY WebSocket state + last-output age (this cockpit's pane, L6). Turn-state freshness is bounded by the 10 s liveness sweep."
      >
        {/* `ws —` on a seat with no pane is an em-dash placeholder: the ws word
            shows only when a pane actually reports a ws state; the last-output age still shows when
            known, and the provenance chips below carry the rest. */}
        {[
          freshness.ptyWs !== "none" ? WS_WORDS[freshness.ptyWs] : null,
          quiet ? quiet : null,
        ]
          .filter(Boolean)
          .join(" · ")}
        {/* No model/effort duplication here — the ModelEffortControl is the one
            header surface for the running pair; launch problems raise the FailedLaunchBanner. */}
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
