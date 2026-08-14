import type { TerminalSocketFactory } from "../data/terminal";

// Controlled-pane PTY content fixture (260715-FEUI-L6 R1/R9, design §8.1): what a controlled
// session's terminal REALLY shows — the hosted runner's plain line-log (verified against
// harness_control_runner.py `_render_updates`/`_read_terminal_input`), NEVER a vendor TUI.
// Consumed by the dev bench mock sockets and the /dev/pty-bench renderer measurement.

/** One boot's worth of runner output, verbatim shapes from harness_control_runner.py. */
export const RUNNER_LINE_LOG_BOOT: string[] = [
  "Agents Remember protocol session claude: ready",
  "[control] ready activity=idle acceptance=unknown",
  "[2026-07-17T09:00:04.120+02:00] user request=req-01: read the leaf doc and start S1",
  "[control] ready activity=running acceptance=queued",
  "[2026-07-17T09:00:09.812+02:00] assistant: Reading 06_pty-stage-interactions-lifecycle.md…",
  "[2026-07-17T09:00:31.007+02:00] assistant: The PtySurface wraps the existing Terminal panel;",
  "[2026-07-17T09:00:31.007+02:00] assistant: keep-alive and fit rules stay in Terminal.tsx.",
  "[control] ready activity=idle acceptance=unknown",
  "[2026-07-17T09:01:02.114+02:00] result request=req-01 result=completed: turn finished",
];

/** Steady-state lines the bench cycles through to simulate a working controlled pane. */
export const RUNNER_LINE_LOG_STREAM: string[] = [
  "[control] ready activity=running acceptance=queued",
  "[2026-07-17T09:02:04.221+02:00] assistant: measuring renderer frame times across panes…",
  "[2026-07-17T09:02:05.410+02:00] assistant: pane 3: fit 132x41, webgl context acquired",
  "[control] submission req-77 queued: retained for the next turn",
  "[2026-07-17T09:02:07.588+02:00] user request=req-77: also record the serialize verdict",
  "[2026-07-17T09:02:09.101+02:00] assistant: serialize addon snapshots the buffer in one call;",
  "[control] ready activity=idle acceptance=unknown",
];

const encoder = new TextEncoder();

class MockLineLogSocket {
  binaryType = "blob";
  readyState = 1; // OPEN
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private cursor = 0;

  constructor(private readonly intervalMs: number) {
    queueMicrotask(() => {
      this.onopen?.();
      for (const line of RUNNER_LINE_LOG_BOOT) this.emit(`${line}\r\n`);
      this.timer = setInterval(() => {
        const line = RUNNER_LINE_LOG_STREAM[this.cursor % RUNNER_LINE_LOG_STREAM.length];
        this.cursor += 1;
        this.emit(`${line}\r\n`);
      }, this.intervalMs);
    });
  }

  send(raw: string): void {
    // A controlled pane's stdin is line-buffered into the bridge queue: echo the runner's
    // acceptance line, exactly the trap the InteractionBar honesty hint names.
    let message: { type?: string; data?: string };
    try {
      message = JSON.parse(raw);
    } catch {
      return;
    }
    if (message.type === "stdin" && typeof message.data === "string") {
      if (message.data.includes("\r")) {
        this.emit(`\r\n[control] submission req-dev queued: retained for the next turn\r\n`);
      } else {
        this.emit(message.data);
      }
    }
  }

  close(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.readyState = 3;
    this.onclose?.();
  }

  private emit(text: string): void {
    this.onmessage?.({ data: encoder.encode(text).buffer } as MessageEvent);
  }
}

/** A controlled-pane mock socket: boot log then a steady line-log drip (default one line/2 s). */
export const mockControlledLineLogSocketFactory: TerminalSocketFactory = () =>
  new MockLineLogSocket(2000) as unknown as WebSocket;

/** Bench variant: a configurable-rate firehose for the renderer measurement (OQ-B). */
export const benchLineLogSocketFactory = (linesPerSecond: number): TerminalSocketFactory =>
  (() => new MockLineLogSocket(Math.max(5, Math.round(1000 / linesPerSecond))) as unknown as WebSocket);
