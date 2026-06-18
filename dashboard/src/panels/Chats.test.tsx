import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Chats } from "./Chats";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// These render-only tests deliberately never click a launch button: opening a session would
// Suspense-load the lazy `Terminal` and pull xterm (a canvas probe) into jsdom. The 6e-2b contract
// under test is purely "a button appears per *detected* harness", which needs no live terminal.
describe("Chats harness launch buttons (6e-2b)", () => {
  it("renders a launch button only for detected harnesses", async () => {
    const harnesses = [
      { id: "claude", name: "Claude Code", detected: true },
      { id: "codex", name: "Codex", detected: true },
      { id: "pi", name: "Pi.dev", detected: false },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ harnesses }) }),
    );

    const { findByTestId, queryByTestId, getByTestId } = render(<Chats />);

    // Detection resolves async, so await the first detected button, then assert the rest synchronously.
    const claude = await findByTestId("chats-new-harness-claude");
    expect(claude.textContent).toContain("Claude Code");
    expect(getByTestId("chats-new-harness-codex")).not.toBeNull();

    // The undetected harness gets no button; the always-present ＋ Terminal control stays.
    expect(queryByTestId("chats-new-harness-pi")).toBeNull();
    expect(getByTestId("chats-new-terminal")).not.toBeNull();
  });

  it("shows only ＋ Terminal when no backend reports harnesses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no backend")));
    const { findByTestId, queryByTestId } = render(<Chats />);
    expect(await findByTestId("chats-new-terminal")).not.toBeNull();
    expect(queryByTestId("chats-new-harness-claude")).toBeNull();
  });
});
