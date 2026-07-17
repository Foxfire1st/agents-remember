import { lazy, Suspense, useEffect, useRef, useState } from "react";

import { css } from "../../../styled-system/css";
import { matchReservedChord } from "../../data/keymap/reserved";
import { parseOsc133, parseOsc94, ptyHarvestStore } from "../../data/ptyHarvest";
import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { useSessions, type OpenSession } from "../../data/sessions";
import { usePersistedFlag } from "../file-viewer/usePersistedFlag";
import {
  isControlledSession,
  paneAccessibleName,
  paneArchetypeCopy,
  SCREEN_READER_MODE_NOTE,
} from "./lifecycleCopy";

// The PtySurface (260715-FEUI-L6 R1–R3, S1/S2): the session stage's terminal half. Wraps the
// EXISTING lazy Terminal.tsx — keep-alive (previously focused panes stay mounted, hidden with
// display:none, exactly Chats' layer pattern so scrollback and the PTY winsize survive focus
// switches) and the fit rules live in Terminal.tsx unchanged. TWO ARCHETYPES (design §1.4):
// controlled sessions show the runner's line-log — no vendor TUI exists there, typed lines queue
// as ordinary messages; legacy raw ('unsupported') sessions host the actual vendor TUI, and ONLY
// those panes get client-side byte-stream harvesting (bell / title / OSC turn hints — R7).
//
// Renderer decision (master OQ-B, measured — full record in the L6 worker report): the DOM
// renderer is the default. Measured on /dev/pty-bench (headless Chromium, 20 line-log
// writes/s/pane, 10 s windows): DOM holds a locked 60 Hz frame budget at 1, 6, AND 12
// concurrent panes (mean ~16.7 ms, zero >33 ms frames). WebGL in that environment runs on
// SwiftShader (software GL) and collapses at fleet scale (12 panes ≈ 264 ms/frame) — real GPUs
// would do far better, but the hardware-honest fact is that DOM already meets the budget, and
// @xterm/addon-webgl allocates one GPU context PER PANE while browsers cap live WebGL contexts
// (~8–16): a 12+ pane fleet silently loses contexts exactly where the cockpit lives. webgl
// stays available behind this constant (Terminal falls back to DOM on load failure/context
// loss) for a future single-focused-pane escalation if DOM ever measures short there.
export const PTY_RENDERER: "dom" | "webgl" = "dom";

const SCREEN_READER_MODE_KEY = "cockpit.sessions.screen-reader-mode";
/** Throttle for freshness `lastOutputAt` writes — output arrives per-chunk, the store per ~1 s. */
const OUTPUT_STAMP_INTERVAL_MS = 1000;

const Terminal = lazy(() =>
  import("../Terminal").then((module) => ({ default: module.Terminal })),
);

const surface = css({
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  display: "flex",
  flexDirection: "column",
  gap: "0.3rem",
});
const paneChrome = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.5rem",
  minWidth: "0",
  fontSize: "0.66rem",
  color: "muted",
});
const archetypeNote = css({
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
// Reserved slot (R3, pane-freeze deferred spec fix 2): the "scrollback — paused" badge renders
// here once the server exposes copy-mode state on the row. Slot kept, never faked.
const badgeSlot = css({ flex: "none", display: "inline-flex", minWidth: "0" });
const srToggle = css({
  font: "inherit",
  fontSize: "0.64rem",
  marginLeft: "auto",
  flex: "none",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  cursor: "pointer",
  "&[data-on='true']": { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const layers = css({ flex: "1", minHeight: "0", minWidth: "0", position: "relative" });
const layer = css({
  position: "absolute",
  inset: "0",
  display: "flex",
  flexDirection: "column",
  minWidth: "0",
});
const loading = css({
  flex: "1",
  display: "grid",
  placeItems: "center",
  color: "muted",
  fontSize: "0.72rem",
});

/** The reserved-chord passthrough guard (defence-in-depth under the window-capture layer). */
function reservedChordFilter(event: KeyboardEvent): boolean {
  return matchReservedChord(event) === null;
}

function isInspectable(status: string | undefined): boolean {
  return (status ?? "running") === "running" || status === "landed";
}

export function PtySurface({
  focused,
  onVisibleCols,
}: {
  focused: OpenSession;
  /** The visible pane's REAL column count (R8: the ~80-col floor verified against real panes). */
  onVisibleCols?: (cols: number | null) => void;
}) {
  const sessions = useSessions((state) => state.sessions);
  const [screenReaderMode, setScreenReaderMode] = usePersistedFlag(SCREEN_READER_MODE_KEY, false);

  // Keep-alive: every session focused in this cockpit stays mounted (hidden) while it remains
  // inspectable — switching back must not lose scrollback (Chats' mountedSessionIds pattern).
  const [mountedIds, setMountedIds] = useState<readonly string[]>([]);
  useEffect(() => {
    setMountedIds((current) => (current.includes(focused.id) ? current : [...current, focused.id]));
  }, [focused.id]);
  const inspectableIds = new Set(
    sessions.filter((session) => isInspectable(session.status)).map((session) => session.id),
  );
  const mounted = mountedIds.filter((id) => inspectableIds.has(id));
  useEffect(() => {
    // Prune tombstones so a terminated seat's pane (and its WS) is torn down, not hidden forever.
    setMountedIds((current) => {
      const keep = current.filter((id) => inspectableIds.has(id));
      return keep.length === current.length ? current : keep;
    });
  }, [sessions]); // inspectableIds derives from sessions

  // Focusing a seat acknowledges its bell marker (the marker exists to pull attention here).
  useEffect(() => {
    ptyHarvestStore.getState().acknowledgeBell(focused.id);
  }, [focused.id]);

  // The visible pane's column truth resets when the focused pane changes (a fresh fit reports).
  const onVisibleColsRef = useRef(onVisibleCols);
  onVisibleColsRef.current = onVisibleCols;
  useEffect(() => {
    onVisibleColsRef.current?.(null);
  }, [focused.id]); // reset on focus switch only

  const lastStampRef = useRef<Record<string, number>>({});

  const paneFor = (session: OpenSession) => {
    const visible = session.id === focused.id;
    const controlled = isControlledSession(session);
    const cockpit = sessionCockpitStore.getState();
    const harvest = ptyHarvestStore.getState();
    return (
      <div
        key={session.id}
        className={layer}
        style={{ display: visible ? "flex" : "none" }}
        aria-hidden={!visible}
        data-pty-visible={visible ? "true" : undefined}
        data-pty-archetype={controlled ? "controlled" : "legacy-raw"}
        data-testid={`pty-layer-${session.id}`}
      >
        <Suspense fallback={<div className={loading}>opening terminal…</div>}>
          <Terminal
            sessionId={session.id}
            readOnly={session.status === "landed"}
            renderer={PTY_RENDERER}
            screenReaderMode={screenReaderMode}
            ariaLabel={paneAccessibleName(session)}
            keyEventFilter={reservedChordFilter}
            onSocketState={(state) => cockpit.setPtyWs(session.id, state)}
            onOutput={() => {
              const now = Date.now();
              if (now - (lastStampRef.current[session.id] ?? 0) < OUTPUT_STAMP_INTERVAL_MS) return;
              lastStampRef.current[session.id] = now;
              sessionCockpitStore.getState().recordPtyOutput(session.id, now);
            }}
            onResizeCols={
              visible ? (cols) => onVisibleCols?.(cols) : undefined
            }
            // Byte-stream harvesting (R7): LEGACY RAW panes only — the vendor TUI is the only
            // signal source those panes have. Controlled panes get none of this.
            hooks={
              controlled
                ? undefined
                : {
                    onBell: () => {
                      harvest.recordBell(session.id, Date.now());
                    },
                    onTitle: (title) => harvest.recordTitle(session.id, title),
                    onOsc133: (data) => {
                      const hint = parseOsc133(data, Date.now());
                      if (hint) harvest.recordTurnHint(session.id, hint);
                    },
                    onOsc9: (data) => {
                      const hint = parseOsc94(data, Date.now());
                      if (hint) harvest.recordTurnHint(session.id, hint);
                    },
                  }
            }
          />
        </Suspense>
      </div>
    );
  };

  return (
    <div
      className={surface}
      data-kbzone="pty"
      data-testid="pty-surface"
      tabIndex={-1}
      // The focus-terminal command targets `[data-kbzone="pty"]`; hand focus to the VISIBLE
      // pane's terminal host (which delegates into xterm's textarea).
      onFocus={(event) => {
        if (event.target !== event.currentTarget) return;
        event.currentTarget
          .querySelector<HTMLElement>('[data-pty-visible="true"] [data-testid="terminal-host"]')
          ?.focus();
      }}
    >
      <div className={paneChrome} data-testid="pty-pane-chrome">
        <span className={archetypeNote} data-testid="pty-archetype-note" title={paneArchetypeCopy(focused)}>
          {paneArchetypeCopy(focused)}
        </span>
        <span className={badgeSlot} data-slot="scrollback-paused-badge" data-testid="pty-scrollback-badge-slot">
          {/* Reserved: "scrollback — paused" renders here when the pane-freeze fields land
              server-side (260710 deferred spec, fix 2). Empty until then — never faked. */}
        </span>
        <button
          type="button"
          className={srToggle}
          data-on={screenReaderMode ? "true" : "false"}
          aria-pressed={screenReaderMode}
          title={SCREEN_READER_MODE_NOTE}
          onClick={() => setScreenReaderMode(!screenReaderMode)}
          data-testid="pty-screen-reader-toggle"
        >
          screen reader: {screenReaderMode ? "on" : "off"}
        </button>
      </div>
      <div className={layers}>
        {sessions.filter((session) => mounted.includes(session.id)).map(paneFor)}
      </div>
    </div>
  );
}
