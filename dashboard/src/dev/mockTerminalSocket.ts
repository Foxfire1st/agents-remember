import type { TerminalSocketFactory } from "../data/terminal";

// A dev-only fake of the 6d terminal WebSocket so the Chats view renders a live-looking terminal on
// the bench with no backend: it emits a banner, echoes typed stdin (Enter → newline + prompt), and
// accepts resize frames. Dev harness only (`/dev/*` is dropped from the production bundle).

const BANNER =
  "\x1b[38;5;79mAgents Remember\x1b[0m \x1b[2m— terminal (dev mock · no backend)\x1b[0m\r\n" +
  "\x1b[2mtype to echo · drag the window to test fit/resize\x1b[0m\r\n$ ";

class MockTerminalSocket {
  binaryType = "blob";
  readyState = 1; // OPEN
  onmessage: ((event: MessageEvent) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  private readonly encoder = new TextEncoder();

  constructor(dropAfterOpen = false, emitBanner = true) {
    // Deliver the banner after `connectTerminal` has wired `onmessage` (same-tick, so a microtask).
    queueMicrotask(() => {
      if (this.readyState !== 1) return; // StrictMode may dispose the first mount before this runs.
      this.onopen?.();
      // Cockpit scenarios do not need terminal output. Omitting it there prevents an xterm render
      // callback from racing a Playwright navigation while preserving the legacy gallery mock.
      if (emitBanner) this.emit(BANNER);
      if (dropAfterOpen) {
        window.setTimeout(() => this.close(), 40);
      }
    });
  }

  send(raw: string): void {
    let message: { type?: string; data?: string };
    try {
      message = JSON.parse(raw);
    } catch {
      return;
    }
    if (message.type === "stdin" && typeof message.data === "string") {
      this.emit(message.data === "\r" ? "\r\n$ " : message.data);
    }
  }

  close(): void {
    if (this.readyState === 3) return;
    this.readyState = 3; // CLOSED
    this.onclose?.();
    this.onmessage = null;
    this.onopen = null;
  }

  private emit(text: string): void {
    this.onmessage?.({ data: this.encoder.encode(text).buffer } as MessageEvent);
  }
}

export function createMockTerminalSocketFactory(
  options: { dropAfterOpen?: boolean; emitBanner?: boolean } = {},
): TerminalSocketFactory {
  return () =>
    new MockTerminalSocket(options.dropAfterOpen, options.emitBanner ?? true) as unknown as WebSocket;
}

export const mockTerminalSocketFactory = createMockTerminalSocketFactory();
