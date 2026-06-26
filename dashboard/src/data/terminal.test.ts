import { describe, expect, it, vi } from "vitest";

import {
  bracketedPaste,
  connectTerminal,
  fetchHarnesses,
  fetchTerminalSessions,
  openTerminalSession,
  parseTerminalControl,
  sanitizeForInjection,
  submitAndConfirm,
  terminateTerminalSession,
  terminalSocketUrl,
  uploadSessionImage,
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
  onopen: (() => void) | null = null;

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
  fireOpen(): void {
    this.onopen?.();
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

describe("bracketedPaste", () => {
  it("wraps text in the bracketed-paste markers so a TUI treats it as one paste", () => {
    expect(bracketedPaste("hello")).toBe("\x1b[200~hello\x1b[201~");
  });
  it("preserves multi-line content verbatim between the markers", () => {
    expect(bracketedPaste("a\nb")).toBe("\x1b[200~a\nb\x1b[201~");
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

  it("whenReady resolves once PTY output goes quiet (the booting-harness gate, slice 6f)", async () => {
    vi.useFakeTimers();
    try {
      const { conn, socket } = connect(sink());
      socket.pushBinary(new Uint8Array([66, 79, 79, 84])); // boot output
      let ready = false;
      void conn.whenReady().then(() => {
        ready = true;
      });
      await vi.advanceTimersByTimeAsync(300); // < the 700ms idle window
      expect(ready).toBe(false);
      await vi.advanceTimersByTimeAsync(600); // now quiet > 700ms ⇒ ready
      expect(ready).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("flushes the latest resize once the socket opens (the handshake race)", () => {
    const s = sink();
    const { conn, socket } = connect(s);
    socket.readyState = 0; // CONNECTING — the first fit() runs before the handshake completes
    conn.sendResize(100, 30);
    conn.sendResize(120, 40); // a later fit supersedes the earlier size
    expect(socket.sent).toEqual([]); // both dropped while connecting
    socket.readyState = 1; // OPEN
    socket.fireOpen();
    // Only the latest size is replayed, so the PTY winsize syncs to the final fitted xterm.
    expect(socket.sent).toEqual([JSON.stringify({ type: "resize", cols: 120, rows: 40 })]);
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

describe("openTerminalSession", () => {
  it("POSTs the kind and catalog metadata to the session route and returns true on ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    const ok = await openTerminalSession("t 1", "terminal", "", undefined, {
      label: "Terminal 1",
      lifecycleId: "LC1",
    });
    expect(ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/terminal/t%201",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ kind: "terminal", label: "Terminal 1", lifecycleId: "LC1" }),
      }),
    );
    vi.unstubAllGlobals();
  });

  it("returns false on a non-ok response or a network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await openTerminalSession("t1")).toBe(false);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    expect(await openTerminalSession("t1")).toBe(false);
    vi.unstubAllGlobals();
  });

  it("includes the harness id in the body for kind=harness", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    const ok = await openTerminalSession("s1", "harness", "", "claude");
    expect(ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/terminal/s1",
      expect.objectContaining({ body: JSON.stringify({ kind: "harness", harness: "claude" }) }),
    );
    vi.unstubAllGlobals();
  });
});

describe("fetchTerminalSessions", () => {
  it("returns the durable terminal sessions the endpoint reports", async () => {
    const sessions = [
      {
        id: "s1",
        label: "Terminal 1",
        kind: "terminal",
        cwd: "/ws",
        tmuxName: "ar-s1",
        createdAt: "2026-06-26T00:00:00Z",
        lastAttachedAt: "2026-06-26T00:00:00Z",
        status: "running",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ sessions }) }),
    );
    expect(await fetchTerminalSessions()).toEqual(sessions);
    vi.unstubAllGlobals();
  });

  it("returns [] on a non-ok response, a missing key, or a network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await fetchTerminalSessions()).toEqual([]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) }));
    expect(await fetchTerminalSessions()).toEqual([]);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    expect(await fetchTerminalSessions()).toEqual([]);
    vi.unstubAllGlobals();
  });
});

describe("terminateTerminalSession", () => {
  it("POSTs to the terminate route and returns true on ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    expect(await terminateTerminalSession("s 1")).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/terminal/s%201/terminate",
      expect.objectContaining({ method: "POST" }),
    );
    vi.unstubAllGlobals();
  });

  it("returns false on a non-ok response or a network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await terminateTerminalSession("s1")).toBe(false);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    expect(await terminateTerminalSession("s1")).toBe(false);
    vi.unstubAllGlobals();
  });
});

describe("sanitizeForInjection (6f hardening)", () => {
  it("strips the Ctrl-Z suspend byte and other C0 controls but keeps \\n and \\t", () => {
    expect(sanitizeForInjection("a\x1ab")).toBe("ab"); // 0x1a (Ctrl-Z) — the suspend byte
    expect(sanitizeForInjection("a\x03b")).toBe("ab"); // 0x03 (Ctrl-C)
    expect(sanitizeForInjection("a\rb")).toBe("ab"); // 0x0d (CR) — never inside a paste body
    expect(sanitizeForInjection("a\x7fb")).toBe("ab"); // DEL
    expect(sanitizeForInjection("l1\nl2\tend")).toBe("l1\nl2\tend"); // multi-line + tab preserved
    expect(sanitizeForInjection("hello 🌍")).toBe("hello 🌍"); // ordinary unicode untouched
  });

  it("removes embedded bracketed-paste markers so a selection cannot break out of its own paste", () => {
    expect(sanitizeForInjection("a\x1b[200~x\x1b[201~b")).toBe("axb");
  });
});

describe("submitAndConfirm (6f hardening)", () => {
  it("resolves true once the harness responds AFTER the CR echo settles", async () => {
    vi.useFakeTimers();
    try {
      const s = sink();
      const { conn, socket } = connect(s);
      let result: boolean | undefined;
      void submitAndConfirm(conn).then((v) => {
        result = v;
      });
      await vi.advanceTimersByTimeAsync(250); // paste-settle → Enter sent
      expect(socket.sent).toContainEqual(JSON.stringify({ type: "stdin", data: "\r" }));
      await vi.advanceTimersByTimeAsync(250); // CR-echo-settle → baseline captured
      socket.pushBinary(new Uint8Array([1])); // harness responds AFTER the baseline
      await vi.advanceTimersByTimeAsync(1800); // next tick observes the new output
      expect(result).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does NOT false-positive on the Enter's own echo (the phantom-delivered guard)", async () => {
    vi.useFakeTimers();
    try {
      const s = sink();
      const { conn, socket } = connect(s);
      let result: boolean | undefined;
      void submitAndConfirm(conn).then((v) => {
        result = v;
      });
      await vi.advanceTimersByTimeAsync(250); // paste-settle → Enter sent
      socket.pushBinary(new Uint8Array([2])); // the Enter's OWN echo lands during the echo-settle window
      await vi.advanceTimersByTimeAsync(250); // echo folds INTO the baseline
      await vi.advanceTimersByTimeAsync(9500); // no further output (submit swallowed) → times out
      expect(result).toBe(false); // echo alone is not treated as a response
    } finally {
      vi.useRealTimers();
    }
  });

  it("gives up (false) and re-sends Enter exactly once (capped, not spamming) when nothing responds", async () => {
    vi.useFakeTimers();
    try {
      const s = sink();
      const { conn, socket } = connect(s);
      let result: boolean | undefined;
      void submitAndConfirm(conn).then((v) => {
        result = v;
      });
      await vi.advanceTimersByTimeAsync(250 + 250 + 9500); // settle windows + past the submit timeout
      expect(result).toBe(false);
      const enters = socket.sent.filter((f) => JSON.parse(f).data === "\r").length;
      expect(enters).toBe(2); // initial Enter + ONE resend, then it stops re-sending into the live pane
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("uploadSessionImage", () => {
  it("POSTs the image to the session route and returns the saved path", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: () => Promise.resolve({ path: "/cwd/.dashboard-pastes/a.png" }) });
    vi.stubGlobal("fetch", fetchMock);
    const file = new File([new Uint8Array([1, 2])], "shot.png", { type: "image/png" });
    expect(await uploadSessionImage("s 1", file)).toBe("/cwd/.dashboard-pastes/a.png");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/terminal/s%201/image",
      expect.objectContaining({ method: "POST" }),
    );
    vi.unstubAllGlobals();
  });

  it("returns null on a non-ok response, a missing path, or a network error", async () => {
    const file = new File([new Uint8Array([1])], "x.png", { type: "image/png" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await uploadSessionImage("s1", file)).toBeNull();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) }));
    expect(await uploadSessionImage("s1", file)).toBeNull();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    expect(await uploadSessionImage("s1", file)).toBeNull();
    vi.unstubAllGlobals();
  });
});

describe("fetchHarnesses", () => {
  it("returns the harness list the endpoint reports", async () => {
    const harnesses = [
      { id: "claude", name: "Claude Code", detected: true },
      { id: "pi", name: "Pi.dev", detected: false },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ harnesses }) }),
    );
    expect(await fetchHarnesses()).toEqual(harnesses);
    vi.unstubAllGlobals();
  });

  it("returns [] on a non-ok response, a missing key, or a network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await fetchHarnesses()).toEqual([]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) }));
    expect(await fetchHarnesses()).toEqual([]);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    expect(await fetchHarnesses()).toEqual([]);
    vi.unstubAllGlobals();
  });
});
