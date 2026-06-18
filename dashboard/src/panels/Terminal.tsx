import { useContext, useEffect, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XtermTerminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

import { css } from "../../styled-system/css";
import { connectTerminal, TerminalSocketContext } from "../data/terminal";

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

function cursorShouldBlink(): boolean {
  return typeof document === "undefined" || document.documentElement.dataset.effects !== "off";
}

export function Terminal({ sessionId }: { sessionId: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const socketFactory = useContext(TerminalSocketContext);

  useEffect(() => {
    const node = hostRef.current;
    if (!node) return undefined;

    const term = new XtermTerminal({
      cursorBlink: cursorShouldBlink(),
      fontFamily: '"IBM Plex Mono", ui-monospace, "SFMono-Regular", monospace',
      fontSize: 13,
      theme: THEME,
      convertEol: false,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(node);
    fit.fit();

    const conn = connectTerminal(
      sessionId,
      {
        write: (bytes) => term.write(bytes),
        onExit: () => term.write("\r\n\x1b[2m— session ended —\x1b[0m\r\n"),
      },
      socketFactory ? { socketFactory } : {},
    );

    const dataSub = term.onData((data) => conn.sendInput(data));
    const pushResize = () => {
      fit.fit();
      conn.sendResize(term.cols, term.rows); // the one known risk: keep the PTY winsize in lockstep
    };
    pushResize();
    const observer = new ResizeObserver(pushResize);
    observer.observe(node);

    return () => {
      observer.disconnect();
      dataSub.dispose();
      conn.dispose();
      term.dispose();
    };
  }, [sessionId, socketFactory]);

  return <div ref={hostRef} className={host} data-testid="terminal-host" />;
}
