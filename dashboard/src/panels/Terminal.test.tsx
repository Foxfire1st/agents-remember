import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Terminal } from "./Terminal";

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
    terminalOptions: vi.fn(),
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

    constructor(options: unknown) {
      mocks.terminalOptions(options);
    }

    loadAddon() {}
    open() {}
    write() {}
    onData() {
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
    connectTerminal: vi.fn(() => mocks.connection),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Terminal", () => {
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
