import { useContext, useEffect, useMemo, useRef } from "react";
import type { Terminal as XtermTerminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

import { css } from "../../styled-system/css";
import { useDashboard } from "../data/store";
import {
  TerminalSocketContext,
  type TerminalConnection,
  type TerminalSocketFactory,
} from "../data/terminal";
import {
  mountTerminal,
  type TerminalStreamHooks,
} from "./terminalSession";

// The imperative xterm.js terminal (slice 6e): a render-not-scrape view of the 6d PTY stream.
// xterm is a DOM/canvas emulator (it cannot mount under jsdom), so — like the topology canvas — it
// stays an imperative engine wrapped via refs; the testable protocol logic lives in `data/terminal`
// and the mount machinery in `terminalSession`.

const host = css({
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  padding: "0.35rem 0.5rem",
  background: "well",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  overflow: "hidden",
  "& .xterm": { height: "100%" },
});

export type { TerminalStreamHooks };

export interface TerminalProps {
  sessionId: string;
  onConnection?: (conn: TerminalConnection | null) => void;
  readOnly?: boolean;
  /** Renderer choice: DOM baseline by measurement; `webgl`
   *  loads @xterm/addon-webgl lazily and falls back to DOM on failure or context loss. */
  renderer?: "dom" | "webgl";
  /** xterm screenReaderMode: opt-in — it maintains an a11y tree at a rendering cost. */
  screenReaderMode?: boolean;
  /** Accessible pane name: label + harness + state, applied to the host group. */
  ariaLabel?: string;
  /** Return false to keep xterm from consuming a key (the PTY reserved set, defence-in-depth
   *  under the window-capture tinykeys layer). Default: xterm handles everything. */
  keyEventFilter?: (event: KeyboardEvent) => boolean;
  /** Selection-first mouse ownership for a terminal whose application does not own useful click
   *  gestures (Codex and generic shell seats). tmux mouse reporting remains active for wheel/
   *  copy-mode navigation, but a primary-button drag is promoted to xterm's native selection path. */
  plainTextSelection?: boolean;
  hooks?: TerminalStreamHooks;
  /** Fires on every PTY output chunk (freshness `lastOutputAt`; caller throttles). */
  onOutput?: () => void;
  onSocketState?: (state: "connected" | "reconnecting" | "dropped") => void;
  /** The pane's REAL column count after every fit — the ~80-col floor truth. */
  onResizeCols?: (cols: number) => void;
}

function useTerminalRefs(props: TerminalProps) {
  // Hand the live connection to its owning keep-alive surface. Held in a ref so a changing callback
  // identity never re-runs the effect
  // (which would tear down + reconnect the terminal).
  const onConnRef = useRef(props.onConnection);
  onConnRef.current = props.onConnection;
  const hooksRef = useRef(props.hooks);
  hooksRef.current = props.hooks;
  const onOutputRef = useRef(props.onOutput);
  onOutputRef.current = props.onOutput;
  const onSocketStateRef = useRef(props.onSocketState);
  onSocketStateRef.current = props.onSocketState;
  const onResizeColsRef = useRef(props.onResizeCols);
  onResizeColsRef.current = props.onResizeCols;
  const keyEventFilterRef = useRef(props.keyEventFilter);
  keyEventFilterRef.current = props.keyEventFilter;
  const screenReaderModeRef = useRef(props.screenReaderMode);
  screenReaderModeRef.current = props.screenReaderMode;
  const termRef = useRef<XtermTerminal | null>(null);
  const connectionRef = useRef<TerminalConnection | null>(null);
  return useMemo(
    () => ({
      onConnRef,
      hooksRef,
      onOutputRef,
      onSocketStateRef,
      onResizeColsRef,
      keyEventFilterRef,
      screenReaderModeRef,
      termRef,
      connectionRef,
    }),
    [
      onConnRef,
      hooksRef,
      onOutputRef,
      onSocketStateRef,
      onResizeColsRef,
      keyEventFilterRef,
      screenReaderModeRef,
      termRef,
      connectionRef,
    ],
  );
}

export function Terminal(props: TerminalProps) {
  const {
    sessionId,
    readOnly = false,
    renderer = "dom",
    screenReaderMode = false,
    ariaLabel,
    plainTextSelection = false,
  } = props;
  const hostRef = useRef<HTMLDivElement>(null);
  const socketFactory: TerminalSocketFactory | null = useContext(
    TerminalSocketContext,
  );
  const servingBootIdentity = useDashboard(
    (state) => state.servingBuild?.bootedAt ?? null,
  );
  const observedServingBootRef = useRef<string | null>(null);
  const refs = useTerminalRefs(props);

  // screenReaderMode toggles live on the existing instance — never a teardown/reconnect.
  useEffect(() => {
    if (refs.termRef.current) {
      refs.termRef.current.options.screenReaderMode = screenReaderMode;
    }
  }, [screenReaderMode, refs]);

  // EventSource owns daemon-replacement discovery. Each newly observed non-null serving boot gets
  // exactly one chance to reattach a dropped socket to the same durable tmux session; there is no
  // timer, retry loop, xterm remount, or scrollback reset. A changed boot supersedes the old socket;
  // the first observed identity leaves an already-open initial socket in place.
  useEffect(() => {
    if (!servingBootIdentity || observedServingBootRef.current === servingBootIdentity) return;
    const previousBootIdentity = observedServingBootRef.current;
    observedServingBootRef.current = servingBootIdentity;
    refs.connectionRef.current?.reattach(servingBootIdentity, {
      // A changed process identity proves even an apparently-OPEN socket belongs to the old daemon;
      // its close callback may simply be queued behind the new state snapshot. The first identity
      // observation leaves an already-open initial connection alone, but still replaces CONNECTING.
      supersedeOpen: previousBootIdentity !== null,
    });
  }, [servingBootIdentity, refs]);

  useEffect(() => {
    const node = hostRef.current;
    if (!node) return undefined;
    return mountTerminal(node, {
      sessionId,
      socketFactory,
      readOnly,
      renderer,
      plainTextSelection,
      screenReaderMode: refs.screenReaderModeRef.current,
      refs,
    });
  }, [
    plainTextSelection,
    readOnly,
    renderer,
    sessionId,
    socketFactory,
    refs,
  ]);

  return (
    <div
      ref={hostRef}
      className={host}
      data-testid="terminal-host"
      role="group"
      // A group landmark must always carry a name: the cockpit passes the
      // full label+harness+state name; legacy call sites pass their session label; the
      // sessionId fallback keeps the landmark named even with neither.
      aria-label={ariaLabel ?? `terminal session ${sessionId}`}
      tabIndex={-1}
      // Focus delegation: the focus-terminal command / region routing focuses the host; typing
      // must land in xterm's own textarea.
      onFocus={(event) => {
        if (event.target !== hostRef.current) return;
        // Defer to the next frame: xterm's own focusin handling is mid-flight when this event
        // fires, and its internal guard can swallow the delegation (observed in headless
        // Chromium: the palette's focus-terminal command left the host focused and the
        // helper textarea inactive). A frame later the textarea is the honest keyboard target.
        window.requestAnimationFrame(() => {
          const textarea = hostRef.current?.querySelector<HTMLElement>(
            ".xterm-helper-textarea",
          );
          if (textarea) textarea.focus();
          else refs.termRef.current?.focus();
        });
      }}
    />
  );
}
