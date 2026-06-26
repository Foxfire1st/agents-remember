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
  /** Resolves once the session looks ready for input — its output has gone quiet (the harness finished
   *  booting and is at its prompt) or a timeout elapses. The highlight composer waits on this so a
   *  context package isn't dropped into a still-starting harness (slice 6f). */
  whenReady(): Promise<void>;
  /** Epoch ms of the most recent PTY output (0 if none yet). The submit-confirm loop watches this to
   *  tell whether a programmatic Enter actually submitted — once it does, the harness starts responding
   *  and output resumes. */
  lastOutputAt(): number;
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
 * Wrap text as a terminal **bracketed paste** (`ESC[200~ … ESC[201~`) so a TUI (Claude Code / Codex)
 * treats injected context as one paste, not line-by-line typed input (slice 6e-3 context injection).
 * Pure — unit-tested. Typed keystrokes (xterm `onData`) stay raw; only composer injection is wrapped.
 */
export function bracketedPaste(text: string): string {
  return `\x1b[200~${text}\x1b[201~`;
}

/**
 * Strip bytes that an interactive TUI reads as *keystrokes* (not data) when injected over stdin —
 * most importantly `0x1a` (Ctrl-Z → suspend) — plus any bracketed-paste markers already embedded in
 * the text, so a selection can never break out of the paste that wraps it. Keeps `\n` (multi-line)
 * and `\t`; drops the rest of the C0 range (incl. raw `\r`, ESC) and `0x7f` (DEL). Pure — unit-tested.
 * The backend also strips `0x1a` from every write (defence in depth); this keeps the injected *package*
 * clean of all control noise, not just the suspend byte.
 */
export function sanitizeForInjection(text: string): string {
  return (
    text
      // eslint-disable-next-line no-control-regex -- strip embedded (ESC-framed) bracketed-paste markers
      .replace(/\x1b\[20[01]~/g, "")
      // eslint-disable-next-line no-control-regex -- intentional control-byte scrub of injected text
      .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "") // C0 except \t (0x09) / \n (0x0a); drops \r, ESC, 0x1a, DEL
  );
}

const _delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

const PASTE_SETTLE_MS = 250; // let the paste→pending-input widget render before submitting
const CR_ECHO_SETTLE_MS = 250; // let the Enter's OWN echo land so it folds into the baseline, not a response
const SUBMIT_RETRY_MS = 1800; // re-send Enter once on this cadence (tuned in S0 against a live harness)
const SUBMIT_TIMEOUT_MS = 9000; // give up confirming after this and surface an error, never silently

/**
 * Submit a just-injected paste and confirm it took. A single programmatic `\r` is unreliable: Claude
 * Code's Ink input treats a written CR as a *newline*, not a submit, and the paste→widget conversion is
 * async, so a same-tick Enter is swallowed (leaving `[Pasted text #N]` unsent). So: let the paste render,
 * send `\r`, **let the CR's own echo settle into the baseline** (so the echo can't be mistaken for a
 * response — the false-positive that would report a phantom "delivered"), then watch for output that
 * advances *past* that baseline = the harness actually responding. Re-send Enter at most ONCE (in case
 * the first was swallowed) rather than spamming CRs into a pane the operator may be typing into.
 * Resolves `true` once a response is observed, `false` on timeout. The output-activity signal is a
 * best-effort heuristic; a Claude Code `UserPromptSubmit` hook is the robust upgrade if it proves flaky.
 */
export async function submitAndConfirm(conn: TerminalConnection): Promise<boolean> {
  await _delay(PASTE_SETTLE_MS);
  conn.sendInput("\r"); // submit
  await _delay(CR_ECHO_SETTLE_MS); // fold the Enter's echo into the baseline below
  const baseline = conn.lastOutputAt();
  const startedAt = Date.now();
  let resent = false;
  while (Date.now() - startedAt < SUBMIT_TIMEOUT_MS) {
    await _delay(SUBMIT_RETRY_MS);
    if (conn.lastOutputAt() > baseline) return true; // output after the CR echo settled ⇒ responded
    if (!resent) {
      conn.sendInput("\r"); // one more Enter in case the first was swallowed, then stop re-sending
      resent = true;
    }
  }
  return false;
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
  // The latest requested winsize, replayed on open: the first fit() runs before the WS handshake
  // completes, so its resize frame would be dropped (send requires OPEN) and the PTY/tmux would stay
  // at the spawn-default size — the terminal renders small until something else triggers a resize.
  let pendingResize: { cols: number; rows: number } | null = null;
  // Stdin queued before the socket opens — a create-then-send (slice 6f) injects into a brand-new
  // session whose handshake hasn't completed, so the first package would be dropped. Replayed on open;
  // normal typed keystrokes arrive after open and send directly.
  let pendingInput: string[] = [];
  // Output-readiness (slice 6f): track when PTY output last arrived so `whenReady` can wait for a
  // booting harness to settle at its prompt before a package is injected.
  let lastOutputAt = 0;
  let sawOutput = false;
  const end = () => {
    if (!ended) {
      ended = true;
      sink.onExit();
    }
  };

  socket.onmessage = (event: MessageEvent) => {
    if (event.data instanceof ArrayBuffer) {
      sawOutput = true;
      lastOutputAt = Date.now();
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

  // Flush the buffered size once the socket is OPEN so the PTY winsize syncs to the fitted xterm even
  // though the first fit() fired mid-handshake (the resize race that left the terminal rendering small).
  socket.onopen = () => {
    if (pendingResize) send({ type: "resize", cols: pendingResize.cols, rows: pendingResize.rows });
    for (const data of pendingInput) send({ type: "stdin", data });
    pendingInput = [];
  };

  return {
    sendInput: (data) => {
      if (socket.readyState === WS_OPEN) send({ type: "stdin", data });
      else pendingInput.push(data);
    },
    sendResize: (cols, rows) => {
      pendingResize = { cols, rows };
      send({ type: "resize", cols, rows });
    },
    whenReady: () =>
      new Promise<void>((resolve) => {
        const IDLE_MS = 700; // output quiet this long ⇒ the harness has settled at its prompt
        const TIMEOUT_MS = 8000; // fallback so a chatty/animated harness still receives the package
        const startedAt = Date.now();
        const tick = () => {
          const now = Date.now();
          if (now - startedAt >= TIMEOUT_MS) return resolve();
          if (sawOutput && now - lastOutputAt >= IDLE_MS) return resolve();
          setTimeout(tick, 150);
        };
        tick();
      }),
    lastOutputAt: () => lastOutputAt,
    dispose: () => {
      ended = true; // an intentional teardown must not echo `onExit` via the close handler
      socket.close();
    },
  };
}

/** The launch kinds the opener understands: a plain shell, or a named harness (slice 6e-2b). */
export type TerminalOpenKind = "terminal" | "harness";

/** One supported harness as `GET /api/harnesses` reports it — `detected` ⇒ a launch button appears. */
export interface HarnessInfo {
  id: string;
  name: string;
  detected: boolean;
}

export type TerminalSessionStatus = "running" | "exited" | "terminated";

export interface TerminalSessionInfo {
  id: string;
  label: string;
  kind: TerminalOpenKind;
  harness?: string;
  lifecycleId?: string;
  cwd: string;
  tmuxName: string;
  createdAt: string;
  lastAttachedAt: string;
  status: TerminalSessionStatus;
  terminatedAt?: string;
}

interface OpenTerminalOptions {
  label?: string;
  lifecycleId?: string;
}

/**
 * Ask the server which supported harnesses are installed (slice 6e-2b `GET /api/harnesses`). Returns
 * `[]` on any failure — the dev bench has no backend, so the Chats strip just shows ＋ Terminal.
 */
export async function fetchHarnesses(base = ""): Promise<HarnessInfo[]> {
  try {
    const response = await fetch(`${base}/api/harnesses`);
    if (!response.ok) return [];
    const body = (await response.json()) as { harnesses?: HarnessInfo[] };
    return body.harnesses ?? [];
  } catch {
    return [];
  }
}

export async function fetchTerminalSessions(base = ""): Promise<TerminalSessionInfo[]> {
  try {
    const response = await fetch(`${base}/api/terminal/sessions`);
    if (!response.ok) return [];
    const body = (await response.json()) as { sessions?: TerminalSessionInfo[] };
    return Array.isArray(body.sessions) ? body.sessions : [];
  } catch {
    return [];
  }
}

/**
 * Ask the server to **spawn + own** a session (slice 6e-2a opener): `POST /api/terminal/{id}` →
 * `TerminalHost.open` (the command is server-resolved from `kind` + `harness`, never sent). Returns
 * `true` on success. Best-effort — the dev bench has no backend, so the caller still opens the (mock)
 * socket. For `kind="harness"`, pass the harness id (slice 6e-2b).
 */
export async function openTerminalSession(
  sessionId: string,
  kind: TerminalOpenKind = "terminal",
  base = "",
  harness?: string,
  options: OpenTerminalOptions = {},
): Promise<boolean> {
  try {
    const body = {
      kind,
      ...(harness ? { harness } : {}),
      ...(options.label ? { label: options.label } : {}),
      ...(options.lifecycleId ? { lifecycleId: options.lifecycleId } : {}),
    };
    const response = await fetch(`${base}/api/terminal/${encodeURIComponent(sessionId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return response.ok;
  } catch {
    return false;
  }
}

export async function terminateTerminalSession(sessionId: string, base = ""): Promise<boolean> {
  try {
    const response = await fetch(`${base}/api/terminal/${encodeURIComponent(sessionId)}/terminate`, {
      method: "POST",
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Upload a pasted image to the session's host (slice 6f images): `POST /api/terminal/{id}/image`
 * (multipart) → the backend saves it under the session cwd and returns the absolute on-disk `path`.
 * The terminal channel is text-only, so an image is carried by injecting this path (Claude Code's TUI
 * auto-detects an on-disk image path and attaches it before the model runs). Returns `null` on any
 * failure (bad type / too large / unknown session / network) so the composer can surface a fallback.
 */
export async function uploadSessionImage(
  sessionId: string,
  file: File,
  base = "",
): Promise<string | null> {
  try {
    const form = new FormData();
    form.append("file", file, file.name);
    const response = await fetch(`${base}/api/terminal/${encodeURIComponent(sessionId)}/image`, {
      method: "POST",
      body: form,
    });
    if (!response.ok) return null;
    const body = (await response.json()) as { path?: string };
    return body.path ?? null;
  } catch {
    return null;
  }
}

/**
 * Dev/test seam: a provider supplies a fake socket factory so the bench renders a live-looking
 * terminal with no backend. `null` (production) ⇒ a real same-origin `WebSocket`.
 */
export const TerminalSocketContext = createContext<TerminalSocketFactory | null>(null);
