import type { RefObject } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XtermTerminal } from "@xterm/xterm";

import { connectTerminal, type TerminalConnection, type TerminalSocketFactory } from "../data/terminal";

const WHEEL_PIXELS_PER_LINE = 40;
const APPLICATION_SCROLL_LINES_PER_STEP = 3;
const DOM_DELTA_LINE = 1;
const DOM_DELTA_PAGE = 2;
const PAGE_UP_SEQUENCE = "\x1b[5~";
const PAGE_DOWN_SEQUENCE = "\x1b[6~";

/** Byte-stream observation hooks — wired by PtySurface for LEGACY RAW panes
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

function cursorShouldBlink(): boolean {
  return typeof document === "undefined" || document.documentElement.dataset.effects !== "off";
}

function wheelScrollLines(
  event: WheelEvent,
  rows: number,
  pixelRemainder: number,
): [number, number] {
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

function isMacPlatform(): boolean {
  return typeof navigator !== "undefined" && navigator.platform.startsWith("Mac");
}

function isCopyShortcut(event: KeyboardEvent): boolean {
  // Platform-gated deliberately. macOS copies with Cmd and reserves Ctrl+C for the interrupt, so
  // accepting `ctrlKey` platform-blind made Ctrl+C over a selection a fully dead key there — no
  // copy (the OS chord is Cmd+C) and no ETX (this handler suppressed it). Ctrl held alongside Cmd
  // on macOS also reads as interrupt: an ambiguous chord must reach the running program, because a
  // missed SIGINT costs more than a missed copy.
  const copyModifierHeld = isMacPlatform() ? event.metaKey && !event.ctrlKey : event.ctrlKey;
  return (
    event.type === "keydown" &&
    event.key.toLowerCase() === "c" &&
    copyModifierHeld &&
    !event.altKey
  );
}

function createTerminal(
  node: HTMLDivElement,
  screenReaderMode: boolean | undefined,
  plainTextSelection: boolean,
  keyEventFilterRef: RefObject<((event: KeyboardEvent) => boolean) | undefined>,
): { term: XtermTerminal; fit: FitAddon } {
  const term = new XtermTerminal({
    cursorBlink: cursorShouldBlink(),
    fontFamily: '"IBM Plex Mono", ui-monospace, "SFMono-Regular", monospace',
    fontSize: 13,
    theme: {
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
    },
    convertEol: false,
    scrollback: 5000,
    screenReaderMode,
    macOptionClickForcesSelection: plainTextSelection,
    altClickMovesCursor: !plainTextSelection,
  });
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.attachCustomKeyEventHandler((event) => {
    // Selection-aware copy: xterm's default key path turns Ctrl+C into ETX and prevents the
    // browser copy event. Once a selection exists, leave this one chord to the browser; without
    // a selection it still reaches the running terminal as interrupt.
    if (isCopyShortcut(event) && term.hasSelection()) return false;
    // xterm-level guard for the reserved set: even when the window-capture layer is inactive,
    // a bound reserved chord is never consumed by (or leaked into) the pane.
    return keyEventFilterRef.current?.(event) ?? true;
  });
  term.open(node);
  return { term, fit };
}

function wireSelectionCopy(
  node: HTMLDivElement,
  term: XtermTerminal,
  plainTextSelection: boolean,
): () => void {
  let selectionSnapshot = "";
  const selectionSub = term.onSelectionChange(() => {
    // DOM-renderer row replacements do not move xterm's range, but the text underneath that range
    // can change while a TUI repaints. Copy the text the operator actually highlighted, not a
    // later screen occupying the same coordinates.
    selectionSnapshot = term.hasSelection() ? term.getSelection() : "";
  });
  const handleCopy = (event: ClipboardEvent) => {
    if (!selectionSnapshot || !term.hasSelection() || !event.clipboardData) return;
    event.clipboardData.setData("text/plain", selectionSnapshot);
    event.preventDefault();
    event.stopPropagation();
    // Release the range now that its bytes are on the clipboard. The copy chord is also the
    // interrupt chord: a selection outliving its own copy makes the NEXT Ctrl+C copy the same
    // text again instead of reaching the PTY, and xterm only clears a selection on some OTHER
    // key or a click — so Ctrl+C alone could never recover the interrupt.
    term.clearSelection();
  };
  node.addEventListener("copy", handleCopy, { capture: true });
  const forcePlainTextSelection = (event: MouseEvent) => {
    if (
      !plainTextSelection ||
      event.button !== 0 ||
      term.modes.mouseTrackingMode !== "drag"
    )
      return;
    // xterm deliberately gives mouse mode ownership to the PTY unless its platform selection
    // modifier is held. The `drag` protocol here is tmux's outer mouse mode for a pane whose own
    // app requests no mouse events; modes such as `any`/`vt200` remain app-owned. Promote the real
    // event before xterm's listeners see it, preserving wheel reporting while making a plain drag
    // take the exact native selection path (Option on macOS, Shift elsewhere).
    const modifier = isMacPlatform() ? "altKey" : "shiftKey";
    Object.defineProperty(event, modifier, { configurable: true, value: true });
  };
  node.addEventListener("mousedown", forcePlainTextSelection, { capture: true });
  return () => {
    node.removeEventListener("copy", handleCopy, { capture: true });
    node.removeEventListener("mousedown", forcePlainTextSelection, { capture: true });
    selectionSub.dispose();
  };
}

function wireStreamHooks(
  term: XtermTerminal,
  hooksRef: RefObject<TerminalStreamHooks | undefined>,
): () => void {
  // Stream-observation hooks — registration is unconditional-cheap; the callbacks decide.
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
  return () => {
    bellSub.dispose();
    titleSub.dispose();
    osc133Sub.dispose();
    osc9Sub.dispose();
  };
}

function wireWebglRenderer(
  term: XtermTerminal,
  renderer: "dom" | "webgl",
  isDisposed: () => boolean,
): { dispose(): void } {
  if (renderer !== "webgl") return { dispose: () => undefined };
  let webglAddon: { dispose(): void } | null = null;
  let disposed = false;
  // Renderer decision: DOM is the measured baseline; webgl loads lazily and demotes
  // itself back to DOM on load failure or GPU context loss (xterm's documented contract).
  void import("@xterm/addon-webgl").then(
    ({ WebglAddon }) => {
      if (disposed || isDisposed()) return;
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
  return {
    dispose: () => {
      disposed = true;
      webglAddon?.dispose();
      webglAddon = null;
    },
  };
}

interface TerminalRefs {
  keyEventFilterRef: RefObject<((event: KeyboardEvent) => boolean) | undefined>;
  hooksRef: RefObject<TerminalStreamHooks | undefined>;
  onOutputRef: RefObject<(() => void) | undefined>;
  onSocketStateRef: RefObject<
    ((state: "connected" | "reconnecting" | "dropped") => void) | undefined
  >;
  onConnRef: RefObject<((conn: TerminalConnection | null) => void) | undefined>;
  onResizeColsRef: RefObject<((cols: number) => void) | undefined>;
  connectionRef: RefObject<TerminalConnection | null>;
  termRef: RefObject<XtermTerminal | null>;
}

function wireConnection(
  node: HTMLDivElement,
  term: XtermTerminal,
  sessionId: string,
  socketFactory: TerminalSocketFactory | null,
  readOnly: boolean,
  refs: TerminalRefs,
) {
  const conn = connectTerminal(
    sessionId,
    {
      write: (bytes) => {
        term.write(bytes);
        refs.onOutputRef.current?.();
      },
      onExit: () => term.write("\r\n\x1b[2m— session ended —\x1b[0m\r\n"),
    },
    {
      ...(socketFactory ? { socketFactory } : {}),
      onSocketState: (state) => refs.onSocketStateRef.current?.(state),
    },
  );
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
    const [lines, nextPixelRemainder] = wheelScrollLines(
      event,
      term.rows,
      wheelPixelRemainder,
    );
    wheelPixelRemainder = nextPixelRemainder;
    if (lines !== 0) {
      if (hasViewportScrollback(term)) {
        applicationWheelLineRemainder = 0;
        term.scrollLines(lines);
      } else {
        const [input, nextLineRemainder] = applicationScrollInput(
          lines,
          applicationWheelLineRemainder,
        );
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
  return {
    conn,
    disposeWheel: () => {
      node.removeEventListener("wheel", handleWheel, { capture: true });
      dataSub?.dispose();
    },
  };
}

function installFitObserver(
  node: HTMLDivElement,
  term: XtermTerminal,
  fit: FitAddon,
  conn: TerminalConnection,
  onResizeColsRef: RefObject<((cols: number) => void) | undefined>,
  isDisposed: () => boolean,
): () => void {
  let fittedWidth = 0;
  let fittedHeight = 0;
  let fontSettlePending = false;
  // Fit to the host + keep the PTY winsize in lockstep (the one known Mode B2 risk). A single fit
  // at mount sticks at the wrong size because the flex layout + the mono web font settle *after*
  // this effect runs — so re-fit on the next frame and once `document.fonts` is ready, on top of
  // the ResizeObserver that catches every later container change.
  //
  // Switch-back glitch: keep-alive panes hide with display:none and re-show with the SAME
  // box, so the ResizeObserver fires on every switch-back. An unconditional re-fit is never silent
  // there: the fit proposal overshoots the live geometry by exactly the clipped-row shrink (the corrected
  // row count sits below the raw proposal), so the renderer clears + grows, the rAF correction
  // measures the same overflow and shrinks back — two resizes, two PTY winsize changes, and the
  // viewport re-syncing to its offset in between (the quick resize-then-scroll seen on every
  // switch-back). An unchanged box means the fitted geometry (and the clipped-row correction) is already
  // correct: skip the fit and only re-report the cols truth (the reported cols reset on every focus switch).
  const refit = (force = false) => {
    // Skip while the host is hidden (display:none on a view switch → 0×0): fitting to 0 would ship
    // a degenerate winsize and collapse the running app's layout. The ResizeObserver re-fits on show.
    if (!node.clientWidth || !node.clientHeight) return;
    if (
      !force &&
      !fontSettlePending &&
      node.clientWidth === fittedWidth &&
      node.clientHeight === fittedHeight
    ) {
      onResizeColsRef.current?.(term.cols);
      return;
    }
    fontSettlePending = false;
    fittedWidth = node.clientWidth;
    fittedHeight = node.clientHeight;
    fit.fit();
    // The terminal's last row can render clipped: the fit addon's
    // assumed cell height can undershoot the renderer's REAL cell height under fractional
    // devicePixelRatio (measured live: screen 756px inside a 744px host — one clipped row). After the renderer applies the fit, measure the actual overflow
    // and drop just enough rows. Shrink-only: real container growth re-enters through the
    // ResizeObserver → fit.fit() path above.
    window.requestAnimationFrame(() => {
      if (isDisposed()) return;
      const screen = node.querySelector(".xterm-screen");
      if (!(screen instanceof HTMLElement) || term.rows <= 4) return;
      const overflow =
        screen.getBoundingClientRect().bottom - node.getBoundingClientRect().bottom;
      if (overflow <= 1) return;
      const cell = screen.getBoundingClientRect().height / term.rows;
      const drop = Math.max(1, Math.ceil(overflow / cell));
      term.resize(term.cols, Math.max(4, term.rows - drop));
      conn.sendResize(term.cols, term.rows);
      onResizeColsRef.current?.(term.cols);
    });
    conn.sendResize(term.cols, term.rows);
    onResizeColsRef.current?.(term.cols);
  };
  refit();
  let alive = true;
  const raf = requestAnimationFrame(() => refit());
  void document.fonts.ready.then(() => {
    if (!alive) return;
    // The font settles cell metrics, never the box: force one refit past the unchanged-box
    // guard. If the pane is hidden when it lands, force the refit on the re-show instead of
    // fitting blind — the guard alone would keep the pre-settle geometry forever.
    if (!node.clientWidth || !node.clientHeight) {
      fontSettlePending = true;
      return;
    }
    refit(true);
  });
  const observer = new ResizeObserver(() => refit());
  observer.observe(node);
  return () => {
    alive = false;
    cancelAnimationFrame(raf);
    observer.disconnect();
  };
}

export interface TerminalMountOptions {
  sessionId: string;
  socketFactory: TerminalSocketFactory | null;
  readOnly: boolean;
  renderer: "dom" | "webgl";
  plainTextSelection: boolean;
  screenReaderMode: boolean | undefined;
  refs: TerminalRefs;
}

export function mountTerminal(
  node: HTMLDivElement,
  { sessionId, socketFactory, readOnly, renderer, plainTextSelection, screenReaderMode, refs }: TerminalMountOptions,
): () => void {
  let disposed = false;
  const { term, fit } = createTerminal(
    node,
    screenReaderMode,
    plainTextSelection,
    refs.keyEventFilterRef,
  );
  const disposeSelection = wireSelectionCopy(node, term, plainTextSelection);
  const disposeStreamHooks = wireStreamHooks(term, refs.hooksRef);
  const disposeWebgl = wireWebglRenderer(term, renderer, () => disposed);
  const { conn, disposeWheel } = wireConnection(
    node,
    term,
    sessionId,
    socketFactory,
    readOnly,
    refs,
  );
  const disposeFit = installFitObserver(
    node,
    term,
    fit,
    conn,
    refs.onResizeColsRef,
    () => disposed,
  );
  refs.connectionRef.current = conn;
  refs.onConnRef.current?.(conn);

  return () => {
    disposed = true;
    disposeFit();
    if (refs.connectionRef.current === conn) refs.connectionRef.current = null;
    refs.onConnRef.current?.(null);
    disposeWheel();
    disposeSelection();
    disposeStreamHooks();
    disposeWebgl.dispose();
    conn.dispose();
    if (refs.termRef.current === term) refs.termRef.current = null;
    term.dispose();
  };
}
