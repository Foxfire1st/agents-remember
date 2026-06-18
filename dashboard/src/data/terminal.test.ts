import { describe, expect, it, vi } from "vitest";

import {
  connectTerminal,
  parseTerminalControl,
  terminalSocketUrl,
  type TerminalSink,
} from "./terminal";

// A minimal WebSocket stand-in: records sends, lets the test push frames + close events.
class FakeSocket {
  binaryType = "blob";
  readyState = 1; // OPEN
  sent: string[] = [];
  closed = false;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(public url: string) {}

  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
    this.closed = true;
  }

  pushBinary(bytes: Uint8Array): void {
    this.onmessage?.({ data: bytes.buffer } as MessageEvent);
  }
  pushText(text: string): void {
    this.onmessage?.({ data: text } as MessageEvent);
  }
  fireClose(): void {
    this.onclose?.();
  }
}

function sink(): TerminalSink & { written: Uint8Array[]; exits: number } {
  const written: Uint8Array[] = [];
  let exits = 0;
  return {
    written,
    get exits() {
      return exits;
    },
    write: (bytes) => {
      written.push(bytes);
    },
    onExit: () => {
      exits += 1;
    },
  };
}

function connect(s: TerminalSink): { conn: ReturnType<typeof connectTerminal>; socket: FakeSocket } {
  let socket!: FakeSocket;
  const conn = connectTerminal("lc-1", s, {
    socketFactory: (url) => {
      socket = new FakeSocket(url);
      return socket as unknown as WebSocket;
    },
  });
  return { conn, socket };
}

describe("terminalSocketUrl", () => {
  it("resolves a same-origin ws URL and encodes the id", () => {
    const url = terminalSocketUrl("lc/1", { protocol: "http:", host: "localhost:5273" });
    expect(url).toBe("ws://localhost:5273/api/terminal/lc%2F1");
  });

  it("upgrades to wss on https", () => {
    const url = terminalSocketUrl("x", { protocol: "https:", host: "h" });
    expect(url).toBe("wss://h/api/terminal/x");
  });
});

describe("parseTerminalControl", () => {
  it("recognizes the exit frame", () => {
    expect(parseTerminalControl(JSON.stringify({ type: "exit" }))).toBe("exit");
  });
  it("ignores other frames and malformed json", () => {
    expect(parseTerminalControl(JSON.stringify({ type: "other" }))).toBeNull();
    expect(parseTerminalControl("not json{")).toBeNull();
    expect(parseTerminalControl("[1,2]")).toBeNull();
  });
});

describe("connectTerminal", () => {
  it("sets arraybuffer and writes binary frames verbatim", () => {
    const s = sink();
    const { socket } = connect(s);
    expect(socket.binaryType).toBe("arraybuffer");
    socket.pushBinary(new Uint8Array([27, 91, 65])); // ESC [ A
    expect(s.written).toHaveLength(1);
    expect(Array.from(s.written[0])).toEqual([27, 91, 65]);
  });

  it("emits stdin and resize frames", () => {
    const s = sink();
    const { conn, socket } = connect(s);
    conn.sendInput("ls\n");
    conn.sendResize(120, 40);
    expect(socket.sent).toEqual([
      JSON.stringify({ type: "stdin", data: "ls\n" }),
      JSON.stringify({ type: "resize", cols: 120, rows: 40 }),
    ]);
  });

  it("does not send when the socket is not open", () => {
    const s = sink();
    const { conn, socket } = connect(s);
    socket.readyState = 0; // CONNECTING
    conn.sendInput("x");
    expect(socket.sent).toEqual([]);
  });

  it("ends the session once on an exit frame (close does not double-fire)", () => {
    const s = sink();
    const { socket } = connect(s);
    socket.pushText(JSON.stringify({ type: "exit" }));
    socket.fireClose();
    expect(s.exits).toBe(1);
  });

  it("ends the session on an unexpected socket close", () => {
    const s = sink();
    const { socket } = connect(s);
    socket.fireClose();
    expect(s.exits).toBe(1);
  });

  it("dispose closes the socket without echoing onExit", () => {
    const s = sink();
    const { conn, socket } = connect(s);
    conn.dispose();
    socket.fireClose(); // the browser fires close after an intentional dispose
    expect(socket.closed).toBe(true);
    expect(s.exits).toBe(0);
  });

  it("defaults to a real WebSocket factory (constructed lazily)", () => {
    // The default factory references the global WebSocket only when invoked; constructing the
    // connection with an injected factory must never touch it.
    const spy = vi.fn();
    const s = sink();
    connectTerminal("lc-2", s, { socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket });
    expect(spy).not.toHaveBeenCalled();
  });
});
