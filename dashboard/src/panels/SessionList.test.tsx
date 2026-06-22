import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OpenSession } from "../data/sessions";
import { SessionList } from "./SessionList";

afterEach(cleanup);

const sessions: OpenSession[] = [
  { id: "a", label: "Terminal 1" },
  { id: "b", label: "Claude Code 2" },
];

// SessionList is the slice-6e-2c session switcher (a React Aria GridList). It is pure + presentational
// (no backend, no xterm), so — unlike the Chats render-only tests — it can drive selection + close.
describe("SessionList (6e-2c)", () => {
  it("renders a row per session and marks the active one as selected", () => {
    const { getByTestId } = render(
      <SessionList sessions={sessions} activeId="b" onSelect={() => {}} onClose={() => {}} />,
    );
    expect(getByTestId("chats-session-a").getAttribute("data-selected")).toBeNull();
    expect(getByTestId("chats-session-b").getAttribute("data-selected")).not.toBeNull();
  });

  it("selecting a row reports the new active id", () => {
    const onSelect = vi.fn();
    const { getByTestId } = render(
      <SessionList sessions={sessions} activeId="a" onSelect={onSelect} onClose={() => {}} />,
    );
    fireEvent.click(getByTestId("chats-session-b"));
    expect(onSelect).toHaveBeenCalledWith("b");
  });

  it("the row ✕ closes that session without switching to it", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    const { getByLabelText } = render(
      <SessionList sessions={sessions} activeId="a" onSelect={onSelect} onClose={onClose} />,
    );
    fireEvent.click(getByLabelText("Close Claude Code 2"));
    expect(onClose).toHaveBeenCalledWith("b");
    expect(onSelect).not.toHaveBeenCalled();
  });
});
