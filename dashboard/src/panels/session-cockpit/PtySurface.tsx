import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";

import { css } from "../../../styled-system/css";
import { matchReservedChord } from "../../data/keymap/reserved";
import {
  parseOsc133,
  parseOsc94,
  ptyHarvestStore,
} from "../../data/ptyHarvest";
import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { useSessions, type OpenSession } from "../../data/sessions";
import { usePersistedFlag } from "../file-viewer/usePersistedFlag";
import {
  isControlledSession,
  paneAccessibleName,
  paneArchetypeCopy,
  SCREEN_READER_MODE_NOTE,
} from "./lifecycleCopy";
import { EndedSessionState } from "./EndedSessionState";

// The PtySurface: the session stage's terminal half. Wraps the
// EXISTING lazy Terminal.tsx — keep-alive (previously focused panes stay mounted, hidden with
// display:none, exactly Chats' layer pattern so scrollback and the PTY winsize survive focus
// switches) and the fit rules live in Terminal.tsx unchanged. TWO ARCHETYPES (design §1.4):
// controlled sessions show the runner's line-log — no vendor TUI exists there, typed lines queue
// as ordinary messages; legacy raw ('unsupported') sessions host the actual vendor TUI, and ONLY
// those panes get client-side byte-stream harvesting (bell / title / OSC turn hints).
//
// Renderer decision (measured): the DOM
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
// Reserved slot: the "scrollback — paused" badge renders
// here once the server exposes copy-mode state on the row. Slot kept, never faked.
// Corner-chip overlay inside the pane (the chrome bar it lived on is gone). `well`
// background so it stays legible over vendor output; quiet until hover/focus.
const srToggle = css({
  position: "absolute",
  top: "0.3rem",
  right: "0.9rem",
  zIndex: "2",
  font: "inherit",
  fontSize: "0.64rem",
  color: "muted",
  background: "well",
  opacity: "0.75",
  _hover: { opacity: "1" },
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  cursor: "pointer",
  "&[data-on='true']": { color: "amber", borderColor: "amber" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
});
const layers = css({
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  position: "relative",
});
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
const noFocusedSession = css({
  position: "absolute",
  inset: "0",
  display: "grid",
  placeItems: "center",
  padding: "0.8rem",
  color: "muted",
  fontSize: "0.74rem",
  lineHeight: "1.5",
  textAlign: "center",
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  _focusVisible: {
    outlineWidth: "1px",
    outlineStyle: "solid",
    outlineColor: "amber",
    outlineOffset: "1px",
  },
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
  readOnly = false,
  hidden = false,
}: {
  focused?: OpenSession;
  /** The visible pane's REAL column count (the ~80-col floor verified against real panes). */
  onVisibleCols?: (cols: number | null) => void;
  /**
   * Force read-only regardless of status. The structured Chats terminal-diagnostics drawer hosts
   * the controlled runner log with xterm input disabled — it is a diagnostic stream, never a
   * conversation input path (design §12.6).
   */
  readOnly?: boolean;
  /**
   * The owning stage keeps this surface MOUNTED but invisible while a harness seat has
   * the stage (keptHidden: visibility + aria-hidden). The panes, sockets, and scrollback persist —
   * but the hidden layer must drop every focus/zone affordance (data-kbzone, data-focus-target,
   * the focus hand-off, the ended state's focus target) so the stage's keyboard/focus contract
   * (rail-click, the Focus-terminal command) resolves to the VISIBLE layer only.
   */
  hidden?: boolean;
}) {
  const sessions = useSessions((state) => state.sessions);
  const [screenReaderMode, setScreenReaderMode] = usePersistedFlag(
    SCREEN_READER_MODE_KEY,
    false,
  );
  const focusedId = focused?.id;
  const focusedStatus = focused?.status;
  const focusedInspectable = focused ? isInspectable(focusedStatus) : false;
  const focusedLanded = focusedStatus === "landed";

  // Keep-alive: every session focused in this cockpit stays mounted (hidden) while it remains
  // inspectable — switching back must not lose scrollback (Chats' mountedSessionIds pattern). The
  // owner itself stays mounted while a removed focused row is awaiting smart handoff; otherwise
  // that one transient no-focus render would dispose every unrelated visited terminal and socket.
  const [mountedIds, setMountedIds] = useState<readonly string[]>([]);
  useEffect(() => {
    if (!focusedId) return;
    setMountedIds((current) =>
      current.includes(focusedId) ? current : [...current, focusedId],
    );
  }, [focusedId]);
  const inspectableIds = useMemo(
    () =>
      new Set(
        sessions
          .filter((session) => isInspectable(session.status))
          .map((session) => session.id),
      ),
    [sessions],
  );
  const mounted = mountedIds.filter((id) => inspectableIds.has(id));
  useEffect(() => {
    // Prune tombstones so a terminated seat's pane (and its WS) is torn down, not hidden forever.
    setMountedIds((current) => {
      const keep = current.filter((id) => inspectableIds.has(id));
      return keep.length === current.length ? current : keep;
    });
  }, [inspectableIds]); // inspectableIds derives from sessions

  // Focusing a seat acknowledges its bell marker (the marker exists to pull attention here).
  useEffect(() => {
    if (focusedId) ptyHarvestStore.getState().acknowledgeBell(focusedId);
  }, [focusedId]);

  // The visible pane's column truth resets when the focused pane changes (a fresh fit reports).
  const onVisibleColsRef = useRef(onVisibleCols);
  onVisibleColsRef.current = onVisibleCols;
  useEffect(() => {
    onVisibleColsRef.current?.(null);
  }, [focusedId, focusedStatus]);

  const lastStampRef = useRef<Record<string, number>>({});

  const paneFor = (session: OpenSession) => {
    const visible = session.id === focusedId;
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
            readOnly={readOnly || session.status === "landed"}
            renderer={PTY_RENDERER}
            screenReaderMode={screenReaderMode}
            ariaLabel={paneAccessibleName(session)}
            keyEventFilter={reservedChordFilter}
            // Codex and generic shell seats request no useful in-app drag gestures; tmux's outer
            // mouse mode otherwise steals each drag into its private copy buffer and immediately
            // cancels the highlight. Harnesses with their own mouse protocol keep native ownership.
            plainTextSelection={session.kind === "terminal" || session.harness === "codex"}
            onSocketState={(state) => cockpit.setPtyWs(session.id, state)}
            onOutput={() => {
              const now = Date.now();
              if (
                now - (lastStampRef.current[session.id] ?? 0) <
                OUTPUT_STAMP_INTERVAL_MS
              )
                return;
              lastStampRef.current[session.id] = now;
              sessionCockpitStore.getState().recordPtyOutput(session.id, now);
            }}
            onResizeCols={visible ? (cols) => onVisibleCols?.(cols) : undefined}
            // Byte-stream harvesting: LEGACY RAW panes only — the vendor TUI is the only
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
      data-kbzone={!hidden && focusedInspectable ? "pty" : undefined}
      data-focus-target={
        !hidden && (focusedLanded || !focusedInspectable) ? "true" : undefined
      }
      data-testid="pty-surface"
      tabIndex={-1}
      // The focus-terminal command targets `[data-kbzone="pty"]`; hand focus to the VISIBLE
      // pane's terminal host (which delegates into xterm's textarea). A hidden layer never
      // delegates — it carries no zone marker either, so no command can land here.
      onFocus={(event) => {
        if (hidden || !focusedInspectable || event.target !== event.currentTarget) return;
        event.currentTarget
          .querySelector<HTMLElement>(
            '[data-pty-visible="true"] [data-testid="terminal-host"]',
          )
          ?.focus();
      }}
    >
      {/* Declutter: the pane-chrome BAR is gone — the
          archetype fact lives in the Inspector + pane tooltip, and the screen-reader toggle
          floats as a corner chip inside the pane so the bar's height returns to the terminal. */}
      <div className={layers}>
        {focused && focusedInspectable ? (
          <button
            type="button"
            className={srToggle}
            data-on={screenReaderMode ? "true" : "false"}
            aria-pressed={screenReaderMode}
            title={`${SCREEN_READER_MODE_NOTE} · ${paneArchetypeCopy(focused)}`}
            onClick={() => setScreenReaderMode(!screenReaderMode)}
            data-testid="pty-screen-reader-toggle"
          >
            screen reader: {screenReaderMode ? "on" : "off"}
          </button>
        ) : null}
        {sessions
          .filter((session) => mounted.includes(session.id))
          .map(paneFor)}
        {/* The ended state carries a data-focus-target — render it only while the layer is
            visible; a hidden layer must stay out of the stage's focus contract. */}
        {!hidden && focused && !focusedInspectable ? (
          <EndedSessionState session={focused} />
        ) : null}
        {!focused ? (
          <div
            className={noFocusedSession}
            data-kbzone={hidden ? undefined : "pty"}
            data-testid="sessions-pty-placeholder"
            tabIndex={-1}
            aria-label="Terminal placeholder"
          >
            no focused chat — the terminal renders here once a seat is focused;
            every key passes to the harness except the reserved set (? lists
            it); F6 exits to chrome
          </div>
        ) : null}
      </div>
    </div>
  );
}
