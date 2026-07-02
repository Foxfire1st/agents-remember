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
const DOM_DELTA_LINE = 1;
const DOM_DELTA_PAGE = 2;

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

export function Terminal({
  sessionId,
  onConnection,
}: {
  sessionId: string;
  onConnection?: (conn: TerminalConnection | null) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const socketFactory = useContext(TerminalSocketContext);
  // Hand the live connection up to the parent (Chats) so the context composer (6e-3) can inject into
  // this session's stdin. Held in a ref so a changing callback identity never re-runs the effect
  // (which would tear down + reconnect the terminal).
  const onConnRef = useRef(onConnection);
  onConnRef.current = onConnection;

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
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(node);
    let wheelPixelRemainder = 0;
    const handleWheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      const [lines, nextPixelRemainder] = wheelScrollLines(event, term.rows, wheelPixelRemainder);
      wheelPixelRemainder = nextPixelRemainder;
      if (lines !== 0) term.scrollLines(lines);
      if (event.cancelable) event.preventDefault();
      event.stopPropagation();
    };
    // This rail is a transcript surface: wheel input should always scroll xterm's viewport, even
    // when xterm would otherwise translate it into PTY up/down input because the buffer cannot scroll.
    node.addEventListener("wheel", handleWheel, { passive: false, capture: true });

    const conn = connectTerminal(
      sessionId,
      {
        write: (bytes) => term.write(bytes),
        onExit: () => term.write("\r\n\x1b[2m— session ended —\x1b[0m\r\n"),
      },
      socketFactory ? { socketFactory } : {},
    );
    onConnRef.current?.(conn);

    const dataSub = term.onData((data) => conn.sendInput(data));
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
      cancelAnimationFrame(raf);
      onConnRef.current?.(null);
      node.removeEventListener("wheel", handleWheel, { capture: true });
      observer.disconnect();
      dataSub.dispose();
      conn.dispose();
      term.dispose();
    };
  }, [sessionId, socketFactory]);

  return <div ref={hostRef} className={host} data-testid="terminal-host" />;
}
