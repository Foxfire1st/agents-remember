import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Terminal } from "./Terminal";

interface MockTerminalSink {
  write(bytes: Uint8Array): void;
  onExit(): void;
}

const mocks = vi.hoisted(() => {
  const connection = {
    sendInput: vi.fn(),
    sendResize: vi.fn(),
    whenReady: vi.fn(() => Promise.resolve()),
    lastOutputAt: vi.fn(() => 0),
    dispose: vi.fn(),
  };
  return {
    connection,
    fit: vi.fn(),
    scrollLines: vi.fn(),
    clearSelection: vi.fn(),
    terminalOptions: vi.fn(),
    bufferType: "normal",
    baseY: 120,
    mouseTrackingMode: "none",
    terminalSink: null as MockTerminalSink | null,
    keyEventHandler: null as ((event: KeyboardEvent) => boolean) | null,
    selectionChangeHandler: null as (() => void) | null,
    selectionText: "",
  };
});

vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit = mocks.fit;
  },
}));

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    buffer = {
      active: {
        get type() {
          return mocks.bufferType;
        },
        get baseY() {
          return mocks.baseY;
        },
      },
    };

    get modes() {
      return { mouseTrackingMode: mocks.mouseTrackingMode };
    }

    // The terminal surface reads/writes these (screenReaderMode effect, stream hooks, key filter).
    options: Record<string, unknown> = {};
    parser = { registerOscHandler: () => ({ dispose: vi.fn() }) };

    constructor(options: unknown) {
      mocks.terminalOptions(options);
    }

    loadAddon() {}
    open() {}
    write(_data: string | Uint8Array, callback?: () => void) {
      callback?.();
    }
    focus() {}
    attachCustomKeyEventHandler(handler: (event: KeyboardEvent) => boolean) {
      mocks.keyEventHandler = handler;
    }
    hasSelection() {
      return mocks.selectionText.length > 0;
    }
    getSelection() {
      return mocks.selectionText;
    }
    clearSelection() {
      mocks.clearSelection();
      // Real xterm drops the range synchronously: hasSelection() is false on the very next read.
      mocks.selectionText = "";
    }
    onSelectionChange(handler: () => void) {
      mocks.selectionChangeHandler = handler;
      return { dispose: vi.fn() };
    }
    onData() {
      return { dispose: vi.fn() };
    }
    onBell() {
      return { dispose: vi.fn() };
    }
    onTitleChange() {
      return { dispose: vi.fn() };
    }
    scrollLines(amount: number) {
      mocks.scrollLines(amount);
    }
    dispose() {}
  },
}));

vi.mock("../data/terminal", async () => {
  const React = await import("react");
  return {
    TerminalSocketContext: React.createContext(null),
    connectTerminal: vi.fn((_sessionId: string, sink: MockTerminalSink) => {
      mocks.terminalSink = sink;
      return mocks.connection;
    }),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mocks.bufferType = "normal";
  mocks.baseY = 120;
  mocks.mouseTrackingMode = "none";
  mocks.terminalSink = null;
  mocks.keyEventHandler = null;
  mocks.selectionChangeHandler = null;
  mocks.selectionText = "";
});

describe("Terminal", () => {
  it("the group landmark is ALWAYS named — explicit label or the sessionId fallback (L6 review finding 6)", () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: Promise.resolve() },
    });
    const { getByTestId, rerender } = render(<Terminal sessionId="s1" />);
    const host = getByTestId("terminal-host");
    expect(host.getAttribute("role")).toBe("group");
    expect(host.getAttribute("aria-label")).toBe("terminal session s1"); // never an unnamed group
    rerender(<Terminal sessionId="s1" ariaLabel="terminal: scout-claude" />);
    expect(getByTestId("terminal-host").getAttribute("aria-label")).toBe("terminal: scout-claude");
  });

  it("enables scrollback on the xterm instance", () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: Promise.resolve() },
    });

    render(<Terminal sessionId="s1" />);

    expect(mocks.terminalOptions).toHaveBeenCalledWith(
      expect.objectContaining({ scrollback: 5000 }),
    );
  });

  it("promotes a plain primary drag to native selection when tmux owns mouse reporting", () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: Promise.resolve() },
    });
    mocks.mouseTrackingMode = "drag";

    const { getByTestId } = render(
      <Terminal sessionId="s1" plainTextSelection />,
    );
    const event = new MouseEvent("mousedown", {
      bubbles: true,
      cancelable: true,
      button: 0,
    });
    getByTestId("terminal-host").dispatchEvent(event);

    expect(event.shiftKey).toBe(true);
    expect(mocks.terminalOptions).toHaveBeenCalledWith(
      expect.objectContaining({
        macOptionClickForcesSelection: true,
        altClickMovesCursor: false,
      }),
    );
  });

  it("copies the selected snapshot while leaving Ctrl+C as interrupt when no selection exists", () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: Promise.resolve() },
    });
    const { getByTestId } = render(<Terminal sessionId="s1" />);
    const host = getByTestId("terminal-host");

    mocks.selectionText = "text highlighted at mouse-up";
    mocks.selectionChangeHandler?.();
    expect(
      mocks.keyEventHandler?.(
        new KeyboardEvent("keydown", { key: "c", ctrlKey: true }),
      ),
    ).toBe(false);

    // The TUI repaints the same coordinates before Copy; the operator's snapshot must win.
    mocks.selectionText = "later repaint under the same range";
    const setData = vi.fn();
    const copy = new Event("copy", {
      bubbles: true,
      cancelable: true,
    }) as ClipboardEvent;
    Object.defineProperty(copy, "clipboardData", {
      configurable: true,
      value: { setData },
    });
    host.dispatchEvent(copy);

    expect(setData).toHaveBeenCalledWith(
      "text/plain",
      "text highlighted at mouse-up",
    );
    expect(copy.defaultPrevented).toBe(true);

    mocks.selectionText = "";
    expect(
      mocks.keyEventHandler?.(
        new KeyboardEvent("keydown", { key: "c", ctrlKey: true }),
      ),
    ).toBe(true);
  });

  it("uses wheel events to scroll the xterm viewport instead of terminal input", () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: Promise.resolve() },
    });
    const parentWheel = vi.fn();

    const { getByTestId } = render(
      <div onWheel={parentWheel}>
        <Terminal sessionId="s1" />
      </div>,
    );
    const event = new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      deltaY: -120,
    });

    getByTestId("terminal-host").dispatchEvent(event);

    expect(mocks.scrollLines).toHaveBeenCalledWith(-3);
    expect(event.defaultPrevented).toBe(true);
    expect(parentWheel).not.toHaveBeenCalled();
    expect(mocks.connection.sendInput).not.toHaveBeenCalled();
  });

  it("maps wheel events to page navigation when the terminal is in the alternate buffer", () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: Promise.resolve() },
    });
    mocks.bufferType = "alternate";
    mocks.baseY = 0;
    const parentWheel = vi.fn();

    const { getByTestId } = render(
      <div onWheel={parentWheel}>
        <Terminal sessionId="s1" />
      </div>,
    );
    const event = new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      deltaY: -120,
    });

    getByTestId("terminal-host").dispatchEvent(event);

    expect(mocks.scrollLines).not.toHaveBeenCalled();
    expect(mocks.connection.sendInput).toHaveBeenCalledWith("\x1b[5~");
    expect(event.defaultPrevented).toBe(true);
    expect(parentWheel).not.toHaveBeenCalled();
  });

  it("leaves wheel events to xterm when the application tracks the mouse", () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: Promise.resolve() },
    });
    mocks.bufferType = "alternate";
    mocks.baseY = 0;
    mocks.mouseTrackingMode = "vt200";
    const parentWheel = vi.fn();

    const { getByTestId } = render(
      <div onWheel={parentWheel}>
        <Terminal sessionId="s1" />
      </div>,
    );
    const event = new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      deltaY: -120,
    });

    getByTestId("terminal-host").dispatchEvent(event);

    // Not intercepted: xterm's own path reports the wheel as mouse events to the app (tmux/TUI).
    expect(mocks.scrollLines).not.toHaveBeenCalled();
    expect(mocks.connection.sendInput).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
    expect(parentWheel).toHaveBeenCalled();
  });

  it("swallows partial pixel wheel movement before it reaches terminal input", () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: Promise.resolve() },
    });
    const parentWheel = vi.fn();

    const { getByTestId } = render(
      <div onWheel={parentWheel}>
        <Terminal sessionId="s1" />
      </div>,
    );
    const event = new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      deltaY: 10,
    });

    getByTestId("terminal-host").dispatchEvent(event);

    expect(mocks.scrollLines).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(true);
    expect(parentWheel).not.toHaveBeenCalled();
    expect(mocks.connection.sendInput).not.toHaveBeenCalled();
  });
});


// ── keep-alive re-show is geometry-silent ───────────────────────────────────────────────
// The symptom: switching back to a terminal seat flashed a quick resize, then the
// viewport snapped to its position. The mechanism: keep-alive panes hide with display:none and
// re-show with the SAME box, the ResizeObserver fires, and the unconditional re-fit oscillated
// around the clipped-row shrink correction (fit grows a row, the rAF correction drops it) — two resizes,
// two PTY winsize changes, and the viewport re-sync in between. The fit now keys on the container
// box: an unchanged re-show skips the fit entirely and only re-reports the cols truth.
describe("Terminal F-q: keep-alive re-show is geometry-silent", () => {
  const roCallbacks: Array<() => void> = [];
  const OriginalResizeObserver = globalThis.ResizeObserver;

  beforeEach(() => {
    roCallbacks.length = 0;
    globalThis.ResizeObserver = class {
      constructor(callback: () => void) {
        roCallbacks.push(callback);
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  });

  afterEach(() => {
    globalThis.ResizeObserver = OriginalResizeObserver;
  });

  // jsdom reports 0×0 for everything; the refit keys on the host's client box, so drive it.
  const setHostSize = (host: HTMLElement, width: number, height: number) => {
    Object.defineProperty(host, "clientWidth", { configurable: true, value: width });
    Object.defineProperty(host, "clientHeight", { configurable: true, value: height });
  };
  const fireResizeObserver = () => {
    for (const callback of roCallbacks) callback();
  };
  // The mount refit runs while jsdom still reports 0×0; this is the first VISIBLE fit.
  const fitInitially = (host: HTMLElement) => {
    setHostSize(host, 800, 400);
    fireResizeObserver();
    expect(mocks.fit).toHaveBeenCalledTimes(1);
    expect(mocks.connection.sendResize).toHaveBeenCalledWith(80, 24);
    vi.clearAllMocks();
  };

  it("a re-show with an UNCHANGED box does not refit, re-send the winsize, or scroll — only the cols truth re-reports", () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: new Promise(() => {}) }, // never settles: isolates the observer-driven path
    });
    const onResizeCols = vi.fn();
    const { getByTestId } = render(
      <Terminal sessionId="s1" onResizeCols={onResizeCols} />,
    );
    const host = getByTestId("terminal-host");
    fitInitially(host);

    // The view/seat switch hides the pane: 0×0 is never a fit (degenerate winsize guard).
    setHostSize(host, 0, 0);
    fireResizeObserver();
    expect(mocks.fit).not.toHaveBeenCalled();

    setHostSize(host, 800, 400); // switch BACK — the box is exactly what it was
    fireResizeObserver();

    expect(mocks.fit).not.toHaveBeenCalled(); // no geometry churn → no resize flicker
    expect(mocks.connection.sendResize).not.toHaveBeenCalled(); // no spurious PTY SIGWINCH
    expect(mocks.scrollLines).not.toHaveBeenCalled(); // no scroll jump
    // The visible pane's column truth resets to null on every focus switch — the silent
    // re-show still re-reports it (the floor chip never falls back to the pixel estimate).
    expect(onResizeCols).toHaveBeenCalledWith(80);
  });

  it("a container that genuinely changed while hidden still refits and re-sends the winsize on re-show", () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: new Promise(() => {}) },
    });
    const onResizeCols = vi.fn();
    const { getByTestId } = render(
      <Terminal sessionId="s1" onResizeCols={onResizeCols} />,
    );
    const host = getByTestId("terminal-host");
    fitInitially(host);

    setHostSize(host, 0, 0);
    fireResizeObserver();
    setHostSize(host, 600, 400); // the window moved while the pane was on another view
    fireResizeObserver();

    expect(mocks.fit).toHaveBeenCalledTimes(1);
    expect(mocks.connection.sendResize).toHaveBeenCalledWith(80, 24);
    expect(onResizeCols).toHaveBeenCalledWith(80);
  });

  it("a font settle that lands while hidden forces one refit on the re-show (cell metrics changed under an unchanged box)", async () => {
    let resolveFonts!: () => void;
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: {
        ready: new Promise<void>((resolve) => {
          resolveFonts = resolve;
        }),
      },
    });
    const { getByTestId } = render(<Terminal sessionId="s1" />);
    const host = getByTestId("terminal-host");
    fitInitially(host);

    setHostSize(host, 0, 0);
    fireResizeObserver();
    resolveFonts();
    await Promise.resolve();
    await Promise.resolve(); // flush the fonts.ready continuation — hidden: never a blind fit
    expect(mocks.fit).not.toHaveBeenCalled();

    setHostSize(host, 800, 400); // same box, but the settle invalidated the fitted geometry
    fireResizeObserver();
    expect(mocks.fit).toHaveBeenCalledTimes(1);
  });
});


// ── the copy chord must never swallow the interrupt ──────────────────────────────────────
// Ctrl+C is two chords wearing one key. An earlier fix correctly handed it to the browser while a selection
// exists (returning false keeps xterm from emitting ETX), but nothing released the range once the
// bytes were copied — so a SECOND Ctrl+C copied the same text again instead of interrupting, and
// only an unrelated key or a click could restore the interrupt. On macOS it was worse: `ctrlKey`
// counted as copy platform-blind, so Ctrl+C over a selection did nothing at all (the OS copy chord
// is Cmd+C, and ETX was suppressed). Interrupting a running TUI is safety-critical; ETX wins ties.
describe("Terminal m8: the copy chord never swallows the interrupt", () => {
  const ownPlatform = Object.getOwnPropertyDescriptor(navigator, "platform");

  afterEach(() => {
    if (ownPlatform) Object.defineProperty(navigator, "platform", ownPlatform);
    else Reflect.deleteProperty(navigator, "platform"); // back to the prototype getter (non-Mac)
  });

  const setPlatform = (value: string) => {
    Object.defineProperty(navigator, "platform", { configurable: true, value });
  };

  const renderTerminal = () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: new Promise(() => {}) }, // never settles: no stray refit in these tests
    });
    return render(<Terminal sessionId="s1" />);
  };

  // What xterm reports after a drag: a live range plus the selection-change snapshot.
  const selectText = (text: string) => {
    mocks.selectionText = text;
    mocks.selectionChangeHandler?.();
  };

  /** true = xterm handles the key (Ctrl+C → ETX to the PTY); false = left to the browser. */
  const pressC = (held: { ctrl?: boolean; meta?: boolean }) =>
    mocks.keyEventHandler?.(
      new KeyboardEvent("keydown", {
        key: "c",
        ctrlKey: held.ctrl ?? false,
        metaKey: held.meta ?? false,
      }),
    );

  const fireCopy = (host: HTMLElement) => {
    const setData = vi.fn();
    const copy = new Event("copy", { bubbles: true, cancelable: true }) as ClipboardEvent;
    Object.defineProperty(copy, "clipboardData", { configurable: true, value: { setData } });
    host.dispatchEvent(copy);
    return setData;
  };

  it("releases the selection once its bytes are copied, so the NEXT Ctrl+C interrupts", () => {
    const { getByTestId } = renderTerminal();
    const host = getByTestId("terminal-host");
    selectText("stack trace worth keeping");

    // First chord: the live selection wins it and ETX is deliberately withheld.
    expect(pressC({ ctrl: true })).toBe(false);
    expect(fireCopy(host)).toHaveBeenCalledWith("text/plain", "stack trace worth keeping");

    // Second chord: nothing is selected any more, so the runaway program gets its SIGINT — with
    // no intervening keystroke or click, which is the only thing that used to recover it.
    expect(pressC({ ctrl: true })).toBe(true);
    expect(mocks.clearSelection).toHaveBeenCalledTimes(1); // released by the copy, not by a repaint
  });

  it("on macOS Ctrl+C is interrupt-only and Cmd+C is the copy chord", () => {
    setPlatform("MacIntel");
    const { getByTestId } = renderTerminal();
    const host = getByTestId("terminal-host");
    selectText("selected while codex runs");

    // Not the macOS copy chord: withholding ETX here would leave a fully dead key.
    expect(pressC({ ctrl: true })).toBe(true);
    expect(mocks.clearSelection).not.toHaveBeenCalled(); // interrupt, not copy — the range stays

    // Cmd+C is, and it still copies the selection-change snapshot.
    expect(pressC({ meta: true })).toBe(false);
    expect(fireCopy(host)).toHaveBeenCalledWith("text/plain", "selected while codex runs");
    expect(mocks.clearSelection).toHaveBeenCalledTimes(1);

    // Ctrl alongside Cmd is ambiguous; an ambiguous chord reaches the program.
    selectText("selected again");
    expect(pressC({ ctrl: true, meta: true })).toBe(true);
  });
});
