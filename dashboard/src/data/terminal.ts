import { createContext } from "react";

// The client half of Mode B2 (slice 6e): a thin WebSocket bridge to the 6d terminal host at
// `/api/terminal/{session}`. It is deliberately **xterm-agnostic** — it writes raw PTY bytes into
// an injected `TerminalSink` and never imports xterm — so the protocol logic unit-tests against a
// fake socket (the mirror of the backend's pure `_apply_terminal_input`), while `panels/Terminal`
// owns the actual xterm rendering.

const WS_OPEN = 1; // WebSocket.OPEN — referenced as a literal so tests need no real WebSocket.

/** Where terminal output is written — an xterm `Terminal` in the app, a fake in tests. */
export interface TerminalSink {
  write(bytes: Uint8Array): void;
  onExit(): void;
}

/** The live handle from {@link connectTerminal}: push input/resize, or tear the socket down. */
export interface TerminalConnection {
  sendInput(data: string): void;
  sendResize(cols: number, rows: number): void;
  dispose(): void;
}

/** Builds the WebSocket for a resolved URL — injectable so the dev bench/tests pass a fake. */
export type TerminalSocketFactory = (url: string) => WebSocket;

export interface ConnectTerminalOptions {
  socketFactory?: TerminalSocketFactory;
  /** Override the resolved `ws(s)://…` URL (tests). */
  url?: string;
}

/** Resolve the same-origin `ws(s)://<host>/api/terminal/{id}` URL for a session id (pure). */
export function terminalSocketUrl(
  sessionId: string,
  location: { protocol: string; host: string } = window.location,
): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/api/terminal/${encodeURIComponent(sessionId)}`;
}

/** Classify one server text frame: `{type:"exit"}` ends the session; everything else is inert. Pure. */
export function parseTerminalControl(text: string): "exit" | null {
  let message: unknown;
  try {
    message = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof message === "object" && message !== null) {
    const type = (message as { type?: unknown }).type;
    if (type === "exit") return "exit";
  }
  return null;
}

/**
 * Open a WebSocket to the 6d bridge and pump it into `sink`. **Binary** frames are raw PTY bytes
 * (written verbatim — the VT stream xterm renders); a `{type:"exit"}` text frame or a socket close
 * ends the session exactly once. The returned handle emits the `{type:stdin|resize}` text frames
 * `_apply_terminal_input` parses server-side and tears the socket down.
 */
export function connectTerminal(
  sessionId: string,
  sink: TerminalSink,
  options: ConnectTerminalOptions = {},
): TerminalConnection {
  const factory = options.socketFactory ?? ((url) => new WebSocket(url));
  const socket = factory(options.url ?? terminalSocketUrl(sessionId));
  socket.binaryType = "arraybuffer";

  let ended = false;
  const end = () => {
    if (!ended) {
      ended = true;
      sink.onExit();
    }
  };

  socket.onmessage = (event: MessageEvent) => {
    if (event.data instanceof ArrayBuffer) {
      sink.write(new Uint8Array(event.data));
    } else if (typeof event.data === "string" && parseTerminalControl(event.data) === "exit") {
      end();
    }
  };
  socket.onclose = end;

  const send = (payload: Record<string, unknown>) => {
    if (socket.readyState === WS_OPEN) {
      socket.send(JSON.stringify(payload));
    }
  };

  return {
    sendInput: (data) => send({ type: "stdin", data }),
    sendResize: (cols, rows) => send({ type: "resize", cols, rows }),
    dispose: () => {
      ended = true; // an intentional teardown must not echo `onExit` via the close handler
      socket.close();
    },
  };
}

/** The launch kinds the opener understands (slice 6e-2b adds `"harness"`). */
export type TerminalOpenKind = "terminal";

/**
 * Ask the server to **spawn + own** a session (slice 6e-2a opener): `POST /api/terminal/{id}` →
 * `TerminalHost.open` (the command is server-resolved from `kind`, never sent). Returns `true` on
 * success. Best-effort — the dev bench has no backend, so the caller still opens the (mock) socket.
 */
export async function openTerminalSession(
  sessionId: string,
  kind: TerminalOpenKind = "terminal",
  base = "",
): Promise<boolean> {
  try {
    const response = await fetch(`${base}/api/terminal/${encodeURIComponent(sessionId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Dev/test seam: a provider supplies a fake socket factory so the bench renders a live-looking
 * terminal with no backend. `null` (production) ⇒ a real same-origin `WebSocket`.
 */
export const TerminalSocketContext = createContext<TerminalSocketFactory | null>(null);
