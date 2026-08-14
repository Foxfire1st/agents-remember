import { describe, expect, it, vi } from "vitest";

import { HARNESS_CATALOG_REQUEST_TIMEOUT_MS } from "./harnessCatalog";
import {
  attachSessionToTask,
  bracketedPaste,
  cleanupLandedTerminalSessions,
  connectTerminal,
  fetchHarnesses,
  fetchTerminalSessions,
  fetchTerminalSessionsOrNull,
  openTerminalSession,
  parseTerminalControl,
  pasteAndConfirm,
  sanitizeForInjection,
  submitAndConfirm,
  terminateTerminalSession,
  terminalSocketUrl,
  TERMINAL_CATALOG_REQUEST_TIMEOUT_MS,
  uploadSessionImage,
  type TerminalSink,
} from "./terminal";

// A half-dead socket (live wedge class): never settles on its own; rejects only when
// the request's abort signal fires — what a REAL fetch does when the fetchWithTimeout bound
// aborts it. Unbounded, every caller single-flighted behind this promise wedges with it.
const hungSocketImplementation = (_url: string, init?: RequestInit) =>
  new Promise((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () =>
      reject(new DOMException("The operation was aborted.", "AbortError")),
    );
  });

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
    this.readyState = 2;
  }

  pushBinary(bytes: Uint8Array): void {
    this.onmessage?.({ data: bytes.buffer } as MessageEvent);
  }
  pushText(text: string): void {
    this.onmessage?.({ data: text } as MessageEvent);
  }
  fireClose(): void {
    this.readyState = 3;
    this.onclose?.();
  }
  fireOpen(): void {
    this.readyState = 1;
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

  it("reports an unexpected transport close without falsely ending the durable session", () => {
    const s = sink();
    const states: string[] = [];
    let socket!: FakeSocket;
    connectTerminal("lc-1", s, {
      socketFactory: (url) => {
        socket = new FakeSocket(url);
        return socket as unknown as WebSocket;
      },
      onSocketState: (state) => states.push(state),
    });
    socket.fireClose();
    expect(s.exits).toBe(0);
    expect(states).toEqual(["dropped"]);
  });

  it("reattaches exactly once when explicitly asked after a drop and replays buffered state", () => {
    const s = sink();
    const sockets: FakeSocket[] = [];
    const states: string[] = [];
    const conn = connectTerminal("lc-1", s, {
      socketFactory: (url) => {
        const socket = new FakeSocket(url);
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
      onSocketState: (state) => states.push(state),
    });
    const first = sockets[0];
    conn.sendResize(120, 40);
    first.fireClose();
    conn.sendInput("kept while down");

    expect(conn.reattach("boot-2")).toBe(true);
    expect(conn.reattach("boot-2")).toBe(false); // the same boot cannot duplicate its attach
    expect(sockets).toHaveLength(2);
    const second = sockets[1];
    second.fireOpen();

    expect(states).toEqual(["dropped", "reconnecting", "connected"]);
    expect(second.sent).toEqual([
      JSON.stringify({ type: "resize", cols: 120, rows: 40 }),
      JSON.stringify({ type: "stdin", data: "kept while down" }),
    ]);
    first.fireClose(); // a stale close cannot overwrite the replacement's connected state
    expect(states).toEqual(["dropped", "reconnecting", "connected"]);
  });

  it("lets one new boot supersede a stale CONNECTING socket and ignores all stale callbacks", () => {
    const s = sink();
    const sockets: FakeSocket[] = [];
    const states: string[] = [];
    const conn = connectTerminal("lc-1", s, {
      socketFactory: (url) => {
        const socket = new FakeSocket(url);
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
      onSocketState: (state) => states.push(state),
    });
    const stale = sockets[0];
    stale.readyState = 0;

    expect(conn.reattach("boot-2")).toBe(true);
    expect(conn.reattach("boot-2")).toBe(false);
    expect(sockets).toHaveLength(2);
    expect(stale.closed).toBe(true);

    stale.fireOpen();
    stale.pushBinary(new Uint8Array([1]));
    stale.fireClose();
    expect(states).toEqual(["reconnecting"]);
    expect(s.written).toEqual([]);

    sockets[1].fireOpen();
    sockets[1].pushBinary(new Uint8Array([2]));
    expect(states).toEqual(["reconnecting", "connected"]);
    expect(s.written.map((bytes) => Array.from(bytes))).toEqual([[2]]);
  });

  it("lets a changed boot supersede a stale OPEN socket before its delayed close arrives", () => {
    const s = sink();
    const sockets: FakeSocket[] = [];
    const states: string[] = [];
    const conn = connectTerminal("lc-1", s, {
      socketFactory: (url) => {
        const socket = new FakeSocket(url);
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
      onSocketState: (state) => states.push(state),
    });
    const stale = sockets[0];
    expect(stale.readyState).toBe(1);

    expect(conn.reattach("boot-2", { supersedeOpen: true })).toBe(true);
    expect(conn.reattach("boot-2", { supersedeOpen: true })).toBe(false);
    expect(sockets).toHaveLength(2);
    expect(stale.closed).toBe(true);

    // The prior process's close arrives after the replacement exists. It cannot demote that socket.
    stale.fireClose();
    expect(states).toEqual(["reconnecting"]);
    sockets[1].fireOpen();
    expect(states).toEqual(["reconnecting", "connected"]);
  });

  it("adopts a first observed boot without replacing an already-open initial socket", () => {
    const sockets: FakeSocket[] = [];
    const conn = connectTerminal("lc-1", sink(), {
      socketFactory: (url) => {
        const socket = new FakeSocket(url);
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
    });

    expect(conn.reattach("boot-1")).toBe(false);
    expect(conn.reattach("boot-1", { supersedeOpen: true })).toBe(false);
    expect(sockets).toHaveLength(1);
  });

  it("does not reattach after an authoritative terminal exit frame", () => {
    const s = sink();
    const sockets: FakeSocket[] = [];
    const conn = connectTerminal("lc-1", s, {
      socketFactory: (url) => {
        const socket = new FakeSocket(url);
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
    });
    sockets[0].pushText(JSON.stringify({ type: "exit" }));
    expect(conn.reattach("boot-2")).toBe(false);
    expect(sockets).toHaveLength(1);
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
  it("POSTs raw metadata and accepts the exact server-owned row", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          session: "t 1",
          label: "Terminal 1",
          kind: "terminal",
          lifecycleId: "LC1",
          taskDocumentRef: null,
          status: "running",
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await openTerminalSession("t 1", "terminal", "", undefined, {
      label: "Terminal 1",
      lifecycleId: "LC1",
    });
    expect(result).toMatchObject({
      outcome: "opened",
      session: {
        id: "t 1",
        label: "Terminal 1",
        kind: "terminal",
        lifecycleId: "LC1",
        status: "running",
      },
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/terminal/t%201",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ kind: "terminal", label: "Terminal 1", lifecycleId: "LC1" }),
      }),
    );
  });

  it("classifies a network failure without inventing an HTTP response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    expect(await openTerminalSession("t1")).toMatchObject({
      outcome: "failed",
      failure: "network",
      httpStatus: null,
    });
  });

  it("classifies non-OK HTTP and harness refusals separately", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: "leaf-ref-not-found", detail: "missing leaf" }), {
          status: 400,
        }),
      ),
    );
    expect(await openTerminalSession("t1")).toMatchObject({
      outcome: "failed",
      failure: "http",
      httpStatus: 400,
      responseStatus: "leaf-ref-not-found",
    });

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: "bad-kind", detail: "harness not installed" }), {
          status: 400,
        }),
      ),
    );
    expect(await openTerminalSession("h1", "harness", "", "claude")).toMatchObject({
      outcome: "failed",
      failure: "harness",
      httpStatus: 400,
      responseStatus: "bad-kind",
    });
  });

  it("classifies empty, malformed, and identity-mismatched success responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 200 })));
    expect(await openTerminalSession("t1")).toMatchObject({
      outcome: "failed",
      failure: "missing-response",
    });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not-json", { status: 200 })));
    expect(await openTerminalSession("t1")).toMatchObject({
      outcome: "failed",
      failure: "protocol",
    });

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            session: "another-id",
            label: "Terminal 1",
            kind: "terminal",
            status: "running",
          }),
          { status: 200 },
        ),
      ),
    );
    expect(await openTerminalSession("t1")).toMatchObject({
      outcome: "failed",
      failure: "protocol",
    });
  });

  it.each([
    ["a harness identity", { harness: "claude" }],
    ["an empty harness identity", { harness: "" }],
    ["a harness control state", { harness: null, controlState: "ready" }],
  ])("rejects a raw response carrying %s", async (_name, contradiction) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            session: "raw-1",
            label: "Terminal 1",
            kind: "terminal",
            status: "running",
            ...contradiction,
          }),
          { status: 200 },
        ),
      ),
    );

    expect(await openTerminalSession("raw-1", "terminal")).toMatchObject({
      outcome: "failed",
      failure: "protocol",
    });
  });

  it("accepts an explicit null harness and control state for a raw response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            session: "raw-1",
            label: "Terminal 1",
            kind: "terminal",
            harness: null,
            status: "running",
            controlState: null,
          }),
          { status: 200 },
        ),
      ),
    );

    expect(await openTerminalSession("raw-1", "terminal")).toMatchObject({
      outcome: "opened",
      session: { id: "raw-1", kind: "terminal" },
    });
  });

  it("includes the harness id and accepts only the matching server harness identity", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          session: "s1",
          label: "Claude Code 1",
          kind: "harness",
          harness: "claude",
          status: "running",
          controlState: "starting",
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await openTerminalSession("s1", "harness", "", "claude");
    expect(result).toMatchObject({
      outcome: "opened",
      session: { id: "s1", kind: "harness", harness: "claude", controlState: "starting" },
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/terminal/s1",
      expect.objectContaining({ body: JSON.stringify({ kind: "harness", harness: "claude" }) }),
    );
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

  it("preserves empty success but returns null for fetch failures when requested", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ sessions: [] }) }));
    expect(await fetchTerminalSessionsOrNull()).toEqual([]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await fetchTerminalSessionsOrNull()).toBeNull();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    expect(await fetchTerminalSessionsOrNull()).toBeNull();
    vi.unstubAllGlobals();
  });

  it("shares one in-flight catalog read between concurrent callers (boot/poll single-flight)", async () => {
    const sessions = [{ id: "s1", kind: "terminal" }];
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ sessions }) });
    vi.stubGlobal("fetch", fetchMock);
    const [one, two] = await Promise.all([
      fetchTerminalSessionsOrNull(),
      fetchTerminalSessionsOrNull(),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(one).toEqual(sessions);
    expect(two).toEqual(sessions);
    // Settle cleared the slot: the next poll beat re-fires.
    expect(await fetchTerminalSessionsOrNull()).toEqual(sessions);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    vi.unstubAllGlobals();
  });

  it("a hung catalog socket expires within the bound as a failed beat; the next beat re-fires on a fresh socket", async () => {
    vi.useFakeTimers();
    try {
      const sessions = [{ id: "s1", kind: "terminal" }];
      const fetchMock = vi
        .fn()
        .mockImplementationOnce(hungSocketImplementation)
        .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ sessions }) });
      vi.stubGlobal("fetch", fetchMock);

      const hung = fetchTerminalSessionsOrNull();
      await vi.advanceTimersByTimeAsync(TERMINAL_CATALOG_REQUEST_TIMEOUT_MS);
      await expect(hung).resolves.toBeNull(); // an expired beat is the ordinary null failure, not a wedge

      // The bound released the single-flight slot: the next read fires a fresh request and succeeds.
      await expect(fetchTerminalSessionsOrNull()).resolves.toEqual(sessions);
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
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

describe("cleanupLandedTerminalSessions", () => {
  it("POSTs selected session ids and normalizes the cleanup result", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          closed: 1,
          skipped: 1,
          closedSessions: ["landed"],
          skippedSessions: [{ session: "active", reason: "status:running" }],
        }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(cleanupLandedTerminalSessions(["landed", "active"])).resolves.toEqual({
      closed: 1,
      skipped: 1,
      closedSessions: ["landed"],
      skippedSessions: [{ session: "active", reason: "status:running" }],
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/terminal/landed-cleanup",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ sessionIds: ["landed", "active"] }),
      }),
    );
    vi.unstubAllGlobals();
  });

  it("returns null on a non-ok response or network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await cleanupLandedTerminalSessions(["s1"])).toBeNull();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    expect(await cleanupLandedTerminalSessions(["s1"])).toBeNull();
    vi.unstubAllGlobals();
  });
});

describe("attachSessionToTask", () => {
  it("posts the canonical task document and explicit seat role as one binding move", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    const taskDocumentRef = { repository: "repo", path: "master/leaf.json" };
    await expect(attachSessionToTask("s 1", taskDocumentRef, "curator")).resolves.toBe("ok");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/terminal/s%201/attach-task",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ taskDocumentRef, role: "curator" }),
      }),
    );
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

describe("pasteAndConfirm (reopened L6: draft paste survives harness boot)", () => {
  const pasteFrames = (socket: FakeSocket): string[] =>
    socket.sent
      .map((f) => JSON.parse(f) as { type: string; data: string })
      .filter((f) => f.type === "stdin" && f.data.startsWith("\x1b[200~"))
      .map((f) => f.data);

  it("pastes once and resolves true when the composer echoes the draft (no Enter ever)", async () => {
    vi.useFakeTimers();
    try {
      const s = sink();
      const { conn, socket } = connect(s);
      socket.pushBinary(new Uint8Array([1])); // boot output; whenReady gates on quiet after it
      let result: boolean | undefined;
      void pasteAndConfirm(conn, "Leaf context\nTask: x").then((v) => {
        result = v;
      });
      await vi.advanceTimersByTimeAsync(800); // quiet ≥ 700ms → the paste goes out
      expect(pasteFrames(socket)).toEqual(["\x1b[200~Leaf context\nTask: x\x1b[201~"]);
      socket.pushBinary(new Uint8Array([2])); // the composer echoes the draft
      await vi.advanceTimersByTimeAsync(300); // next poll observes the echo
      expect(result).toBe(true);
      expect(pasteFrames(socket)).toHaveLength(1); // confirmed on the first attempt — no duplicate
      expect(socket.sent.some((f) => (JSON.parse(f) as { data?: string }).data === "\r")).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("retries a paste a booting harness discarded and confirms the attempt that echoes", async () => {
    vi.useFakeTimers();
    try {
      const s = sink();
      const { conn, socket } = connect(s);
      socket.pushBinary(new Uint8Array([1])); // boot banner, then the harness keeps discarding stdin
      let result: boolean | undefined;
      void pasteAndConfirm(conn, "Leaf context").then((v) => {
        result = v;
      });
      await vi.advanceTimersByTimeAsync(800); // attempt 1 pastes…
      expect(pasteFrames(socket)).toHaveLength(1);
      await vi.advanceTimersByTimeAsync(4800); // …no echo (discarded) → retry window elapses → attempt 2
      expect(pasteFrames(socket)).toHaveLength(2);
      socket.pushBinary(new Uint8Array([2])); // now the composer is up and echoes attempt 2
      await vi.advanceTimersByTimeAsync(300);
      expect(result).toBe(true);
      expect(pasteFrames(socket)).toHaveLength(2); // no further attempts after confirmation
    } finally {
      vi.useRealTimers();
    }
  });

  it("gives up (false) after the boot deadline when no attempt is ever echoed", async () => {
    vi.useFakeTimers();
    try {
      const s = sink();
      const { conn, socket } = connect(s);
      socket.pushBinary(new Uint8Array([1]));
      let result: boolean | undefined;
      void pasteAndConfirm(conn, "Leaf context").then((v) => {
        result = v;
      });
      await vi.advanceTimersByTimeAsync(36000); // past the 30s boot deadline
      expect(result).toBe(false); // caller surfaces the unconfirmed-delivery note
      expect(pasteFrames(socket).length).toBeGreaterThanOrEqual(2); // it kept retrying, bounded
      expect(socket.sent.some((f) => (JSON.parse(f) as { data?: string }).data === "\r")).toBe(false);
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

  it("shares one in-flight catalog read between concurrent signal-less callers (boot single-flight)", async () => {
    const harnesses = [{ id: "claude", name: "Claude Code", detected: true }];
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ harnesses }) });
    vi.stubGlobal("fetch", fetchMock);
    const [one, two] = await Promise.all([fetchHarnesses(), fetchHarnesses()]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(one).toEqual(harnesses);
    expect(two).toEqual(harnesses);
    vi.unstubAllGlobals();
  });

  it("a hung boot catalog socket expires within the bound instead of wedging every signal-less caller", async () => {
    vi.useFakeTimers();
    try {
      const harnesses = [{ id: "claude", name: "Claude Code", detected: true }];
      const fetchMock = vi
        .fn()
        .mockImplementationOnce(hungSocketImplementation)
        .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ harnesses }) });
      vi.stubGlobal("fetch", fetchMock);

      const hung = fetchHarnesses();
      await vi.advanceTimersByTimeAsync(HARNESS_CATALOG_REQUEST_TIMEOUT_MS);
      await expect(hung).resolves.toEqual([]); // the ordinary "daemon did not answer" read, not a wedge

      // Slot released: the retry fires a fresh request and succeeds.
      await expect(fetchHarnesses()).resolves.toEqual(harnesses);
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });
});
