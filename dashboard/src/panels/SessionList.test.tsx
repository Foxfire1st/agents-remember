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
// (no backend, no xterm), so — unlike the Chats render-only tests — it can drive selection + actions.
describe("SessionList (6e-2c)", () => {
  it("renders a row per session and marks the active one as selected", () => {
    const { getByTestId } = render(
      <SessionList
        sessions={sessions}
        activeId="b"
        onSelect={() => {}}
        onTerminate={() => {}}
      />,
    );
    expect(getByTestId("chats-session-a").getAttribute("data-selected")).toBeNull();
    expect(getByTestId("chats-session-b").getAttribute("data-selected")).not.toBeNull();
  });

  it("renders a lifecycle tag when a session is attached", () => {
    const { getByTestId } = render(
      <SessionList
        sessions={[{ id: "a", label: "Terminal 1", lifecycleId: "LC1" }]}
        activeId="a"
        onSelect={() => {}}
        onTerminate={() => {}}
      />,
    );
    expect(getByTestId("chats-session-a").textContent).toContain("LC1");
  });

  it("renders a status tag for sessions that are no longer running", () => {
    const { getByTestId } = render(
      <SessionList
        sessions={[{ id: "a", label: "Terminal 1", status: "exited" }]}
        activeId="a"
        onSelect={() => {}}
        onTerminate={() => {}}
      />,
    );
    expect(getByTestId("chats-session-a").textContent).toContain("exited");
  });

  it("selecting a row reports the new active id", () => {
    const onSelect = vi.fn();
    const { getByTestId } = render(
      <SessionList
        sessions={sessions}
        activeId="a"
        onSelect={onSelect}
        onTerminate={() => {}}
      />,
    );
    fireEvent.click(getByTestId("chats-session-b"));
    expect(onSelect).toHaveBeenCalledWith("b");
  });

  it("truncates a long name and exposes the full name via a hover title (fix 4)", () => {
    const longLabel =
      "Claude Code working on the sidebar chat attachment leaf for the operations integration series";
    const { getByTitle } = render(
      <SessionList
        sessions={[{ id: "a", label: longLabel }]}
        activeId="a"
        onSelect={() => {}}
        onTerminate={() => {}}
      />,
    );
    // The label span carries the full text as a `title` so the CSS ellipsis stays readable on hover.
    expect(getByTitle(longLabel)).not.toBeNull();
  });

  it("includes the bound leaf name in the hover title (fix 4)", () => {
    const { getByTitle } = render(
      <SessionList
        sessions={[{ id: "a", label: "Claude Code 1", leafKey: "repo/master/leaf-1" }]}
        activeId="a"
        onSelect={() => {}}
        onTerminate={() => {}}
        leafNameFor={() => "Sidebar chat"}
      />,
    );
    expect(getByTitle("Claude Code 1 · Sidebar chat")).not.toBeNull();
  });

  it("the row terminate action reports the destructive action separately", () => {
    const onSelect = vi.fn();
    const onTerminate = vi.fn();
    const { getByLabelText } = render(
      <SessionList
        sessions={sessions}
        activeId="a"
        onSelect={onSelect}
        onTerminate={onTerminate}
      />,
    );
    fireEvent.click(getByLabelText("Terminate Claude Code 2"));
    expect(onTerminate).toHaveBeenCalledWith("b");
    expect(onSelect).not.toHaveBeenCalled();
  });
});
