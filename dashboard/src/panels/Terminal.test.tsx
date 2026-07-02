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
});
