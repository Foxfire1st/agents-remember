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

function harnessLabelVisible(session: OpenSession): boolean {
  return Boolean(
    session.harness && session.harness.toLowerCase() !== session.label.toLowerCase(),
  );
}

function stateWord(visual: ReturnType<typeof seatVisualState>): string | null {
  return visual.key === "unclassified" ? null : visual.word;
}

function leafContextLabel(session: OpenSession): string | null {
  return session.leafKey ? `leaf ${leafIdFromKey(session.leafKey)}` : null;
}

function freshnessWords(
  freshness: PerSessionCockpit["freshness"],
  quiet: string | undefined,
): string {
  return [
    freshness.ptyWs !== "none" ? WS_WORDS[freshness.ptyWs] : null,
    quiet ? quiet : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function provenanceLabel(session: OpenSession): string | null {
  if (!session.spawnLevel) return null;
  return `${session.spawnLevel}${session.spawnLevelSource ? ` (${session.spawnLevelSource})` : ""}`;
}

function controlPopoverProps(
  controlPopover: { open: boolean; onOpenChange: (open: boolean) => void } | undefined,
): { open: boolean | undefined; onOpenChange: ((open: boolean) => void) | undefined } {
  return {
    open: controlPopover?.open,
    onOpenChange: controlPopover?.onOpenChange,
  };
}

function dotAriaLabel(visual: ReturnType<typeof seatVisualState>): string | undefined {
  return visual.key === "unclassified" ? "state unavailable" : undefined;
}

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
  const word = stateWord(visual);
  const leaf = leafContextLabel(session);
  const provenance = provenanceLabel(session);
  const popoverProps = controlPopoverProps(controlPopover);

  return (
    <div className={strip} data-testid="header-strip">
      <span className={identity} data-header-segment="identity">
        <span className={sessionName}>{session.label}</span>
        {/* No `codex codex` stutter: the harness label is dropped when it merely repeats the
            session name (a raw terminal literally named after its harness). */}
        {harnessLabelVisible(session) ? (
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
          open={popoverProps.open}
          onOpenChange={popoverProps.onOpenChange}
        />
      </span>
      <span className={stateCluster} data-header-segment="state" data-testid="header-state">
        <StateDot
          state={visual}
          testId="header-dot"
          ariaLabel={dotAriaLabel(visual)}
        />
        {word !== null ? <span>{word}</span> : null}
      </span>
      {leaf !== null ? (
        <span className={leafContext} data-header-segment="leaf" data-testid="header-leaf">
          {leaf}
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
        {freshnessWords(freshness, quiet)}
        {/* No model/effort duplication here — the ModelEffortControl is the one
            header surface for the running pair; launch problems raise the FailedLaunchBanner. */}
        {provenance !== null ? (
          <span className={provenanceChip} data-testid="header-provenance-level">
            {provenance}
          </span>
        ) : null}
      </span>
    </div>
  );
}
