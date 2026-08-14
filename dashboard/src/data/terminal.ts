import { createContext } from "react";

import { readHarnessCatalog, type HarnessInfo } from "./harnessCatalog";
import { fetchWithTimeout } from "./fetchWithTimeout";
import { shareInflight } from "./inflight";
export type { HarnessInfo } from "./harnessCatalog";

// The client half of Mode B2 (slice 6e): a thin WebSocket bridge to the 6d terminal host at
// `/api/terminal/{session}`. It is deliberately **xterm-agnostic** — it writes raw PTY bytes into
// an injected `TerminalSink` and never imports xterm — so the protocol logic unit-tests against a
// fake socket (the mirror of the backend's pure `_apply_terminal_input`), while `panels/Terminal`
// owns the actual xterm rendering.

const WS_CONNECTING = 0;
const WS_OPEN = 1; // WebSocket constants as literals so tests need no real WebSocket global.

/** Where terminal output is written — an xterm `Terminal` in the app, a fake in tests. */
export interface TerminalSink {
  write(bytes: Uint8Array): void;
  onExit(): void;
}

/** The live handle from {@link connectTerminal}: push input/resize, or tear the socket down. */
export interface TerminalConnection {
  sendInput(data: string): void;
  sendResize(cols: number, rows: number): void;
  /** Attach a fresh socket to the same durable tmux session after an authoritative serving-boot
   *  change. Each boot identity is consumed at most once. Returns true only when a replacement
   *  socket was opened; this is an explicit one-shot action, never a timer/retry loop. */
  reattach(
    servingBootIdentity: string,
    options?: { supersedeOpen?: boolean },
  ): boolean;
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
  /** Freshness honesty: `connected` on the WS handshake,
   *  `reconnecting` when an explicit reattach starts, and `dropped` on a non-deliberate
   *  close/exit. A deliberate `dispose()` reports nothing — the pane is gone, not unhealthy. */
  onSocketState?: (state: "connected" | "reconnecting" | "dropped") => void;
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
const SUBMIT_RETRY_MS = 1800; // re-send Enter once on this cadence (tuned against a live harness)
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

const PASTE_ECHO_MS = 4000; // a ready composer echoes a paste (content or a paste chip) well within this
const PASTE_RETRY_DELAY_MS = 500; // breather between attempts so retries don't hammer a booting harness
const PASTE_BOOT_DEADLINE_MS = 30000; // give a slow harness boot (MCP loading) this long to accept a paste

/** Poll `lastOutputAt` until it advances past `baseline` (echo observed) or `timeoutMs` elapses. */
async function outputAdvanced(
  conn: TerminalConnection,
  baseline: number,
  timeoutMs: number,
): Promise<boolean> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (conn.lastOutputAt() > baseline) return true;
    await _delay(150);
  }
  return conn.lastOutputAt() > baseline;
}

/**
 * Paste a package as an editable draft and confirm the composer accepted it — no Enter is ever sent.
 * A single fire-and-forget paste is unreliable: a booting harness (Claude Code loading MCP servers)
 * silently DISCARDS stdin until its composer mounts, and tmux masks every readiness signal from the
 * client (it enables bracketed paste and the alternate screen for all clients unconditionally, and
 * exposes no pane-level composer state). So: wait for an output-quiet window (`whenReady`), paste,
 * and watch for the paste's own echo (the draft render or a `[Pasted text #N]` chip) past the
 * pre-paste baseline. No echo means the paste was discarded — retry in the next quiet window until
 * {@link PASTE_BOOT_DEADLINE_MS}. The long {@link PASTE_ECHO_MS} window doubles as a late-echo catch
 * so a paste the harness queued (rather than discarded) is not re-sent as a duplicate.
 */
export async function pasteAndConfirm(
  conn: TerminalConnection,
  packageText: string,
): Promise<boolean> {
  const payload = bracketedPaste(sanitizeForInjection(packageText));
  const startedAt = Date.now();
  do {
    await conn.whenReady(); // paste only into an output-quiet window, never a repainting boot screen
    const baseline = conn.lastOutputAt();
    conn.sendInput(payload);
    if (await outputAdvanced(conn, baseline, PASTE_ECHO_MS)) return true; // composer echoed the draft
    await _delay(PASTE_RETRY_DELAY_MS);
  } while (Date.now() - startedAt < PASTE_BOOT_DEADLINE_MS);
  return false; // never accepted — the caller surfaces the unconfirmed-delivery note
}

/**
 * Open a WebSocket to the 6d bridge and pump it into `sink`. **Binary** frames are raw PTY bytes
 * (written verbatim — the VT stream xterm renders); only a `{type:"exit"}` text frame ends the
 * durable session. A transport close reports dropped state and remains eligible for a boot-owned
 * reattach. The returned handle emits the `{type:stdin|resize}` text frames `_apply_terminal_input`
 * parses server-side and tears the socket down.
 */
interface TerminalSocketState {
  socket: WebSocket | null;
  disposed: boolean;
  terminalExited: boolean;
  lastReattachIdentity: string | null;
  // The latest requested winsize, replayed on open: the first fit() runs before the WS handshake
  // completes, so its resize frame would be dropped (send requires OPEN) and the PTY/tmux would stay
  // at the spawn-default size — the terminal renders small until something else triggers a resize.
  pendingResize: { cols: number; rows: number } | null;
  // Stdin queued before the socket opens — a create-then-send (slice 6f) injects into a brand-new
  // session whose handshake hasn't completed, so the first package would be dropped. Replayed on open;
  // normal typed keystrokes arrive after open and send directly.
  pendingInput: string[];
  // Output-readiness (slice 6f): track when PTY output last arrived so `whenReady` can wait for a
  // booting harness to settle at its prompt before a package is injected.
  lastOutputAt: number;
  sawOutput: boolean;
}

function wireSocket(
  state: TerminalSocketState,
  current: WebSocket,
  sink: TerminalSink,
  options: ConnectTerminalOptions,
): void {
  let dropped = false;
  const reportDropped = () => {
    // A close from the superseded daemon must never overwrite the active replacement's state.
    if (state.disposed || dropped || state.socket !== current) return;
    dropped = true;
    options.onSocketState?.("dropped");
  };

  current.onmessage = (event: MessageEvent) => {
    if (state.socket !== current) return;
    if (event.data instanceof ArrayBuffer) {
      state.sawOutput = true;
      state.lastOutputAt = Date.now();
      sink.write(new Uint8Array(event.data));
    } else if (typeof event.data === "string" && parseTerminalControl(event.data) === "exit") {
      state.terminalExited = true;
      reportDropped();
      sink.onExit();
    }
  };
  current.onclose = reportDropped;
  // Flush the buffered size once the socket is OPEN so the PTY winsize syncs to the fitted xterm
  // even though the first fit() fired mid-handshake. The same replay restores the exact viewport
  // after a serving restart; input typed while disconnected follows only after the resize.
  current.onopen = () => {
    if (state.disposed || state.socket !== current) return;
    options.onSocketState?.("connected");
    if (state.pendingResize) {
      sendFrame(state, { type: "resize", cols: state.pendingResize.cols, rows: state.pendingResize.rows });
    }
    for (const data of state.pendingInput) sendFrame(state, { type: "stdin", data });
    state.pendingInput = [];
  };
}

function sendFrame(state: TerminalSocketState, payload: Record<string, unknown>): void {
  if (state.socket?.readyState === WS_OPEN) {
    state.socket.send(JSON.stringify(payload));
  }
}

function alreadyConnected(state: TerminalSocketState): boolean {
  return Boolean(
    state.socket &&
      (state.socket.readyState === WS_CONNECTING || state.socket.readyState === WS_OPEN),
  );
}

function openSocketSupersedes(
  state: TerminalSocketState,
  reattachOptions: { supersedeOpen?: boolean },
): boolean {
  return state.socket?.readyState === WS_OPEN && !reattachOptions.supersedeOpen;
}

function closeSuperseded(previous: WebSocket | null, current: WebSocket): void {
  if (previous && previous !== current) previous.close();
}

function openTerminalSocket(
  state: TerminalSocketState,
  url: string,
  factory: (url: string) => WebSocket,
  sink: TerminalSink,
  options: ConnectTerminalOptions,
  reattachIdentity?: string,
  reattachOptions: { supersedeOpen?: boolean } = {},
): boolean {
  if (state.disposed || state.terminalExited) return false;
  if (reattachIdentity !== undefined) {
    if (state.lastReattachIdentity === reattachIdentity) return false;
    state.lastReattachIdentity = reattachIdentity;
    if (openSocketSupersedes(state, reattachOptions)) return false;
    options.onSocketState?.("reconnecting");
  } else if (alreadyConnected(state)) {
    return false;
  }
  const previous = state.socket;
  const current = factory(url);
  state.socket = current;
  // A boot-owned replacement supersedes even a still-CONNECTING socket that belonged to the dead
  // daemon. Set active identity first, then close it, so any synchronous stale callback is inert.
  closeSuperseded(previous, current);
  current.binaryType = "arraybuffer";
  wireSocket(state, current, sink, options);
  return true;
}

export function connectTerminal(
  sessionId: string,
  sink: TerminalSink,
  options: ConnectTerminalOptions = {},
): TerminalConnection {
  const factory = options.socketFactory ?? ((url) => new WebSocket(url));
  const url = options.url ?? terminalSocketUrl(sessionId);
  const state: TerminalSocketState = {
    socket: null,
    disposed: false,
    terminalExited: false,
    lastReattachIdentity: null,
    pendingResize: null,
    pendingInput: [],
    lastOutputAt: 0,
    sawOutput: false,
  };
  openTerminalSocket(state, url, factory, sink, options);

  return {
    sendInput: (data) => {
      if (state.socket?.readyState === WS_OPEN) sendFrame(state, { type: "stdin", data });
      else state.pendingInput.push(data);
    },
    sendResize: (cols, rows) => {
      state.pendingResize = { cols, rows };
      sendFrame(state, { type: "resize", cols, rows });
    },
    reattach: (servingBootIdentity, reattachOptions) =>
      openTerminalSocket(state, url, factory, sink, options, servingBootIdentity, reattachOptions),
    whenReady: () =>
      new Promise<void>((resolve) => {
        const IDLE_MS = 700; // output quiet this long ⇒ the harness has settled at its prompt
        const TIMEOUT_MS = 8000; // fallback so a chatty/animated harness still receives the package
        const startedAt = Date.now();
        const tick = () => {
          const now = Date.now();
          if (now - startedAt >= TIMEOUT_MS) return resolve();
          if (state.sawOutput && now - state.lastOutputAt >= IDLE_MS) return resolve();
          setTimeout(tick, 150);
        };
        tick();
      }),
    lastOutputAt: () => state.lastOutputAt,
    dispose: () => {
      state.disposed = true; // an intentional teardown must not report a dropped live pane
      state.socket?.close();
    },
  };
}

// The catalog-row wire shape moved to types/terminalCatalog.ts (the cockpit
// consumes the FULL `TerminalCatalogEntry.to_json()` shape). Re-exported here so existing
// consumers keep their import site; `TerminalSessionInfo` is that full row.
export type {
  HarnessAcceptanceState,
  HarnessActivityState,
  HarnessControlState,
  SeatTurnState,
  TerminalCatalogRow as TerminalSessionInfo,
  TaskDocumentRef,
  TerminalLivenessEvidence,
  TerminalOpenKind,
  TerminalSessionStatus,
} from "../types/terminalCatalog";
import type { TaskDocumentRef, TerminalCatalogRow } from "../types/terminalCatalog";

export {
  openTerminalSession,
  terminalOpenFailureMessage,
  type OpenedTerminalSession,
  type OpenTerminalOptions,
  type TerminalOpenFailure,
  type TerminalOpenFailureKind,
  type TerminalOpenResult,
} from "./terminalOpen";

/**
 * Ask the server which supported harnesses are installed (slice 6e-2b `GET /api/harnesses`). Returns
 * `[]` on any failure — the dev bench has no backend, so the Chats strip just shows ＋ Terminal.
 */
export async function fetchHarnesses(base = ""): Promise<HarnessInfo[]> {
  return (await fetchHarnessesOrNull(base)) ?? [];
}

/**
 * Same read, failure-distinguishing: the launch flow must render a fetch
 * failure loudly rather than an empty (and therefore lying) harness list — `null` = failed.
 */
export async function fetchHarnessesOrNull(base = ""): Promise<HarnessInfo[] | null> {
  const result = await readHarnessCatalog(base);
  if (result.status === "ready") return result.harnesses;
  if (result.status === "empty") return [];
  return null;
}

/**
 * Hard bound for one terminal-catalog read (the live-wedge guard). The normal poll beat is
 * milliseconds and boot contention ~1 s; 10 s lets a half-dead socket expire as ONE missed
 * beat instead of wedging every beat that single-flights behind it.
 */
export const TERMINAL_CATALOG_REQUEST_TIMEOUT_MS = 10_000;

export async function fetchTerminalSessionsOrNull(base = ""): Promise<TerminalCatalogRow[] | null> {
  // Boot hydrate + the 2500 ms poll beat can stack concurrent reads of the same catalog (and
  // StrictMode doubles the boot hydrate): concurrent callers share one in-flight GET. Failure
  // still resolves `null` for every caller; settle clears the slot so the next beat re-fires.
  // The read is BOUNDED: a hung (half-dead) socket must expire as an ordinary `null` beat —
  // unbounded, it wedges the single-flighted promise and the whole poll loop behind it.
  return shareInflight(`terminal:sessions:${base}`, async () => {
    try {
      const response = await fetchWithTimeout(
        `${base}/api/terminal/sessions`,
        TERMINAL_CATALOG_REQUEST_TIMEOUT_MS,
      );
      if (!response.ok) return null;
      const body = (await response.json()) as { sessions?: TerminalCatalogRow[] };
      return Array.isArray(body.sessions) ? body.sessions : [];
    } catch {
      return null;
    }
  });
}

export async function fetchTerminalSessions(base = ""): Promise<TerminalCatalogRow[]> {
  const sessions = await fetchTerminalSessionsOrNull(base);
  if (sessions === null) {
    return [];
  }
  return sessions;
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

export interface LandedCleanupResult {
  closed: number;
  skipped: number;
  closedSessions: string[];
  skippedSessions: Array<{ session: string; reason: string }>;
}

export async function cleanupLandedTerminalSessions(
  sessionIds: string[],
  base = "",
): Promise<LandedCleanupResult | null> {
  try {
    const response = await fetch(`${base}/api/terminal/landed-cleanup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionIds }),
    });
    if (!response.ok) return null;
    const body = (await response.json()) as Partial<LandedCleanupResult>;
    return {
      closed: typeof body.closed === "number" ? body.closed : 0,
      skipped: typeof body.skipped === "number" ? body.skipped : 0,
      closedSessions: Array.isArray(body.closedSessions) ? body.closedSessions : [],
      skippedSessions: Array.isArray(body.skippedSessions) ? body.skippedSessions : [],
    };
  } catch {
    return null;
  }
}

/** The outcome of a trusted task-seat attachment. */
export type AttachTaskResult = "ok" | "seat-taken" | "error";

/**
 * Claim one document-role seat for an existing session. The server is the uniqueness arbiter:
 * `200` atomically binds the pair, `409` means another live session owns it.
 */
export async function attachSessionToTask(
  sessionId: string,
  taskDocumentRef: TaskDocumentRef,
  role: string,
  base = "",
): Promise<AttachTaskResult> {
  try {
    const response = await fetch(
      `${base}/api/terminal/${encodeURIComponent(sessionId)}/attach-task`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ taskDocumentRef, role }),
      },
    );
    if (response.ok) return "ok";
    if (response.status === 409) return "seat-taken";
    return "error";
  } catch {
    return "error";
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
