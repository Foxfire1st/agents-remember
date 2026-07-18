import { useContext, useEffect, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XtermTerminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

import { css } from "../../styled-system/css";
import { connectTerminal, TerminalSocketContext, type TerminalConnection } from "../data/terminal";

// The imperative xterm.js terminal (slice 6e): a render-not-scrape view of the 6d PTY stream.
// xterm is a DOM/canvas emulator (it cannot mount under jsdom), so — like the topology canvas — it
// stays an imperative engine wrapped via refs; the testable protocol logic lives in `data/terminal`.

const host = css({
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  padding: "0.35rem 0.5rem",
  background: "#070b0f",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  overflow: "hidden",
  "& .xterm": { height: "100%" },
  "& .xterm-viewport": { overflowY: "auto !important" },
});

// A dark VT theme keyed to the cockpit's CRT palette (xterm needs concrete colours, not tokens).
const THEME = {
  background: "#070b0f",
  foreground: "#d6e7da",
  cursor: "#7fe0b0",
  cursorAccent: "#070b0f",
  selectionBackground: "#1f3b30",
  black: "#0b0f14",
  green: "#7fe0b0",
  yellow: "#e8c170",
  cyan: "#6fb8d6",
  red: "#e06c75",
  brightBlack: "#3a4750",
  white: "#d6e7da",
};

const WHEEL_PIXELS_PER_LINE = 40;
const APPLICATION_SCROLL_LINES_PER_STEP = 3;
const DOM_DELTA_LINE = 1;
const DOM_DELTA_PAGE = 2;
const PAGE_UP_SEQUENCE = "\x1b[5~";
const PAGE_DOWN_SEQUENCE = "\x1b[6~";

function cursorShouldBlink(): boolean {
  return typeof document === "undefined" || document.documentElement.dataset.effects !== "off";
}

function wheelScrollLines(event: WheelEvent, rows: number, pixelRemainder: number): [number, number] {
  if (event.deltaY === 0) return [0, pixelRemainder];
  const direction = event.deltaY > 0 ? 1 : -1;
  const magnitude = Math.abs(event.deltaY);

  if (event.deltaMode === DOM_DELTA_PAGE) {
    return [direction * Math.max(1, Math.ceil(magnitude) * Math.max(1, rows - 1)), 0];
  }
  if (event.deltaMode === DOM_DELTA_LINE) {
    return [direction * Math.max(1, Math.ceil(magnitude)), 0];
  }
  const pixels = pixelRemainder + event.deltaY;
  const lines = Math.trunc(pixels / WHEEL_PIXELS_PER_LINE);
  return [lines, pixels - lines * WHEEL_PIXELS_PER_LINE];
}

function applicationScrollInput(lines: number, lineRemainder: number): [string, number] {
  const nextLineRemainder = lineRemainder + lines;
  const steps = Math.trunc(nextLineRemainder / APPLICATION_SCROLL_LINES_PER_STEP);
  if (steps === 0) return ["", nextLineRemainder];
  return [
    (steps < 0 ? PAGE_UP_SEQUENCE : PAGE_DOWN_SEQUENCE).repeat(Math.abs(steps)),
    nextLineRemainder - steps * APPLICATION_SCROLL_LINES_PER_STEP,
  ];
}

function hasViewportScrollback(term: XtermTerminal): boolean {
  return term.buffer.active.type === "normal" && term.buffer.active.baseY > 0;
}

/** Byte-stream observation hooks (260715-FEUI-L6 R7) — wired by PtySurface for LEGACY RAW panes
 *  only; controlled panes render the runner line-log and need none of this. Held in refs so a
 *  changing identity never tears the terminal down. */
export interface TerminalStreamHooks {
  onBell?: () => void;
  /** OSC 0/2 window-title changes (xterm's own title tracking stays active). */
  onTitle?: (title: string) => void;
  /** OSC 133 payload (`A`/`B`/`C`/`D;exit` shell-integration marks), verbatim. */
  onOsc133?: (data: string) => void;
  /** OSC 9 payload (`4;st;pr` ConEmu progress and friends), verbatim. */
  onOsc9?: (data: string) => void;
}

export function Terminal({
  sessionId,
  onConnection,
  readOnly = false,
  renderer = "dom",
  screenReaderMode = false,
  ariaLabel,
  keyEventFilter,
  hooks,
  onOutput,
  onSocketState,
  onResizeCols,
}: {
  sessionId: string;
  onConnection?: (conn: TerminalConnection | null) => void;
  readOnly?: boolean;
  /** Renderer choice (260715-FEUI-L6 R1 / master OQ-B): DOM baseline by measurement; `webgl`
   *  loads @xterm/addon-webgl lazily and falls back to DOM on failure or context loss. */
  renderer?: "dom" | "webgl";
  /** xterm screenReaderMode (R2): opt-in — it maintains an a11y tree at a rendering cost. */
  screenReaderMode?: boolean;
  /** Accessible pane name (R2): label + harness + state, applied to the host group. */
  ariaLabel?: string;
  /** Return false to keep xterm from consuming a key (the PTY reserved set, defence-in-depth
   *  under the window-capture tinykeys layer). Default: xterm handles everything. */
  keyEventFilter?: (event: KeyboardEvent) => boolean;
  hooks?: TerminalStreamHooks;
  /** Fires on every PTY output chunk (freshness `lastOutputAt`; caller throttles). */
  onOutput?: () => void;
  onSocketState?: (state: "connected" | "dropped") => void;
  /** The pane's REAL column count after every fit — the ~80-col floor truth (R8). */
  onResizeCols?: (cols: number) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const socketFactory = useContext(TerminalSocketContext);
  // Hand the live connection to its owning keep-alive surface. Held in a ref so a changing callback
  // identity never re-runs the effect
  // (which would tear down + reconnect the terminal).
  const onConnRef = useRef(onConnection);
  onConnRef.current = onConnection;
  const hooksRef = useRef(hooks);
  hooksRef.current = hooks;
  const onOutputRef = useRef(onOutput);
  onOutputRef.current = onOutput;
  const onSocketStateRef = useRef(onSocketState);
  onSocketStateRef.current = onSocketState;
  const onResizeColsRef = useRef(onResizeCols);
  onResizeColsRef.current = onResizeCols;
  const keyEventFilterRef = useRef(keyEventFilter);
  keyEventFilterRef.current = keyEventFilter;
  const termRef = useRef<XtermTerminal | null>(null);
  const screenReaderModeRef = useRef(screenReaderMode);
  screenReaderModeRef.current = screenReaderMode;

  // screenReaderMode toggles live on the existing instance — never a teardown/reconnect.
  useEffect(() => {
    if (termRef.current) termRef.current.options.screenReaderMode = screenReaderMode;
  }, [screenReaderMode]);

  useEffect(() => {
    const node = hostRef.current;
    if (!node) return undefined;

    const term = new XtermTerminal({
      cursorBlink: cursorShouldBlink(),
      fontFamily: '"IBM Plex Mono", ui-monospace, "SFMono-Regular", monospace',
      fontSize: 13,
      theme: THEME,
      convertEol: false,
      scrollback: 5000,
      screenReaderMode: screenReaderModeRef.current,
    });
    termRef.current = term;
    const fit = new FitAddon();
    term.loadAddon(fit);
    if (keyEventFilterRef.current) {
      // xterm-level guard for the reserved set: even when the window-capture layer is inactive,
      // a reserved chord is never consumed by (or leaked into) the pane.
      term.attachCustomKeyEventHandler((event) => keyEventFilterRef.current?.(event) ?? true);
    }
    term.open(node);
    // Stream-observation hooks (R7) — registration is unconditional-cheap; the callbacks decide.
    const bellSub = term.onBell(() => hooksRef.current?.onBell?.());
    const titleSub = term.onTitleChange((title) => hooksRef.current?.onTitle?.(title));
    const osc133Sub = term.parser.registerOscHandler(133, (data) => {
      hooksRef.current?.onOsc133?.(data);
      return false; // observe only — never swallow the sequence from other handlers
    });
    const osc9Sub = term.parser.registerOscHandler(9, (data) => {
      hooksRef.current?.onOsc9?.(data);
      return false;
    });
    // Renderer decision (OQ-B): DOM is the measured baseline; webgl loads lazily and demotes
    // itself back to DOM on load failure or GPU context loss (xterm's documented contract).
    let webglAddon: { dispose(): void } | null = null;
    let disposed = false;
    if (renderer === "webgl") {
      void import("@xterm/addon-webgl").then(
        ({ WebglAddon }) => {
          if (disposed) return;
          try {
            const addon = new WebglAddon();
            addon.onContextLoss(() => {
              addon.dispose();
              webglAddon = null;
            });
            term.loadAddon(addon);
            webglAddon = addon;
          } catch {
            webglAddon = null; // DOM renderer stays active — never a dead pane
          }
        },
        () => undefined,
      );
    }
    const conn = connectTerminal(
      sessionId,
      {
        write: (bytes) => {
          term.write(bytes);
          onOutputRef.current?.();
        },
        onExit: () => term.write("\r\n\x1b[2m— session ended —\x1b[0m\r\n"),
      },
      {
        ...(socketFactory ? { socketFactory } : {}),
        onSocketState: (state) => onSocketStateRef.current?.(state),
      },
    );
    onConnRef.current?.(conn);

    let wheelPixelRemainder = 0;
    let applicationWheelLineRemainder = 0;
    const handleWheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      // App-managed wheel: when the attached application tracks the mouse (tmux with `mouse on`,
      // mouse-aware TUIs), xterm's native path reports the wheel as mouse events the app scrolls
      // with — tmux scrolls its pane history for normal-buffer apps and passes the events through
      // to panes that requested them. Synthesizing PageUp/PageDown here instead would only scroll
      // TUIs that happen to bind those keys.
      if (term.modes.mouseTrackingMode !== "none") return;
      const [lines, nextPixelRemainder] = wheelScrollLines(event, term.rows, wheelPixelRemainder);
      wheelPixelRemainder = nextPixelRemainder;
      if (lines !== 0) {
        if (hasViewportScrollback(term)) {
          applicationWheelLineRemainder = 0;
          term.scrollLines(lines);
        } else {
          const [input, nextLineRemainder] = applicationScrollInput(lines, applicationWheelLineRemainder);
          applicationWheelLineRemainder = nextLineRemainder;
          if (input && !readOnly) conn.sendInput(input);
        }
      }
      if (event.cancelable) event.preventDefault();
      event.stopPropagation();
    };
    // Wheel precedence: an app that tracks the mouse owns the wheel (xterm reports it as mouse
    // events); otherwise normal scrollback scrolls xterm's viewport; otherwise (alternate buffer,
    // no mouse tracking) translate wheel steps to page navigation instead of xterm's default
    // wheel-to-arrow-history mapping.
    node.addEventListener("wheel", handleWheel, { passive: false, capture: true });

    const dataSub = readOnly ? null : term.onData((data) => conn.sendInput(data));
    // Fit to the host + keep the PTY winsize in lockstep (the one known Mode B2 risk). A single fit
    // at mount sticks at the wrong size because the flex layout + the mono web font settle *after*
    // this effect runs — so re-fit on the next frame and once `document.fonts` is ready, on top of
    // the ResizeObserver that catches every later container change.
    const refit = () => {
      // Skip while the host is hidden (display:none on a view switch → 0×0): fitting to 0 would ship
      // a degenerate winsize and collapse the running app's layout. The ResizeObserver re-fits on show.
      if (!node.clientWidth || !node.clientHeight) return;
      fit.fit();
      conn.sendResize(term.cols, term.rows);
      onResizeColsRef.current?.(term.cols);
    };
    refit();
    let alive = true;
    const raf = requestAnimationFrame(refit);
    void document.fonts.ready.then(() => {
      if (alive) refit();
    });
    const observer = new ResizeObserver(refit);
    observer.observe(node);

    return () => {
      alive = false;
      disposed = true;
      cancelAnimationFrame(raf);
      onConnRef.current?.(null);
      node.removeEventListener("wheel", handleWheel, { capture: true });
      observer.disconnect();
      dataSub?.dispose();
      bellSub.dispose();
      titleSub.dispose();
      osc133Sub.dispose();
      osc9Sub.dispose();
      webglAddon?.dispose();
      webglAddon = null;
      conn.dispose();
      if (termRef.current === term) termRef.current = null;
      // xterm 5.5's Viewport constructor owns a setTimeout that its disposer does not cancel.
      // React StrictMode tears down the probe mount in the same task, so synchronous disposal
      // makes that timer read an already-disposed RenderService (`dimensions`). Let xterm's own
      // queued sync run first; all application listeners and the socket are already detached.
      window.setTimeout(() => term.dispose(), 0);
    };
  }, [readOnly, renderer, sessionId, socketFactory]);

  return (
    <div
      ref={hostRef}
      className={host}
      data-testid="terminal-host"
      role="group"
      // A group landmark must always carry a name (review finding 6): the cockpit passes the
      // full label+harness+state name; legacy call sites pass their session label; the
      // sessionId fallback keeps the landmark named even with neither.
      aria-label={ariaLabel ?? `terminal session ${sessionId}`}
      tabIndex={-1}
      // Focus delegation: the focus-terminal command / region routing focuses the host; typing
      // must land in xterm's own textarea.
      onFocus={(event) => {
        if (event.target === hostRef.current) termRef.current?.focus();
      }}
    />
  );
}
