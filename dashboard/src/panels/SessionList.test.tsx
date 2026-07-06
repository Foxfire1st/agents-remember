import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SessionGroup } from "../data/sessionGroups";
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

// The G1 command tree (L14): grouped rendering — collapsible headers with insignia + counts, the
// landed archive collapsed by default, unattached sessions flat below, and a group-less model
// falling back to today's flat list.
describe("SessionList command tree (L14)", () => {
  const group = (over: Partial<SessionGroup>) => ({
    key: "master:m",
    kind: "master" as const,
    label: "260706 management-repo",
    nested: false,
    defaultCollapsed: false,
    sessions: [] as OpenSession[],
    countLabel: "0 chats",
    ...over,
  });

  it("renders group headers with chevron, insignia, name and counts", () => {
    const { getByTestId } = render(
      <SessionList
        sessions={sessions}
        activeId={null}
        onSelect={() => {}}
        onTerminate={() => {}}
        grouped={{
          groups: [
            group({
              key: "command",
              kind: "command",
              label: "SPRINT 02 · command deck",
              tier: "orchestration",
              sessions: [{ id: "a", label: "Terminal 1" }],
              countLabel: "1 chat · 1 live",
            }),
            group({
              key: "master:260706",
              tier: "management",
              nested: true,
              sessions: [{ id: "b", label: "Claude Code 2" }],
              countLabel: "1 chat · 1 live",
            }),
          ],
          ungrouped: [],
        }}
      />,
    );
    const deckToggle = getByTestId("chats-group-toggle-command");
    expect(deckToggle.textContent).toContain("SPRINT 02 · command deck");
    expect(deckToggle.textContent).toContain("1 chat · 1 live");
    expect(deckToggle.getAttribute("aria-expanded")).toBe("true");
    expect(deckToggle.querySelector("[data-rank-tier='orchestration']")).not.toBeNull();
    expect(deckToggle.querySelector("[data-rank-size='sm']")).not.toBeNull();
    // The commanded master group nests one step and carries the purple insignia.
    const master = getByTestId("chats-group-master:260706");
    expect(master.getAttribute("data-nested")).toBe("true");
    expect(master.querySelector("[data-rank-tier='management']")).not.toBeNull();
    // Members render inside their group.
    expect(master.contains(getByTestId("chats-session-b"))).toBe(true);
  });

  it("collapses the landed archive by default and toggles collapse per header click (UI-local)", () => {
    const { getByTestId, queryByTestId } = render(
      <SessionList
        sessions={sessions}
        activeId={null}
        onSelect={() => {}}
        onTerminate={() => {}}
        grouped={{
          groups: [
            group({
              key: "landed",
              kind: "landed",
              label: "landed",
              defaultCollapsed: true,
              sessions: [{ id: "a", label: "Terminal 1", status: "exited" }],
              countLabel: "1 chat · archived",
            }),
          ],
          ungrouped: [],
        }}
      />,
    );
    const toggle = getByTestId("chats-group-toggle-landed");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.querySelector("[data-rank-tier]")).toBeNull(); // the archive is unmarked
    expect(queryByTestId("chats-session-a")).toBeNull(); // collapsed ⇒ rows unmounted

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(queryByTestId("chats-session-a")).not.toBeNull();

    fireEvent.click(toggle);
    expect(queryByTestId("chats-session-a")).toBeNull();
  });

  it("auto-expands a default-collapsed group that holds the ACTIVE session", () => {
    // The active chat must never be hidden by a collapse default (an explicit user toggle still wins).
    const { getByTestId, queryByTestId } = render(
      <SessionList
        sessions={sessions}
        activeId="a"
        onSelect={() => {}}
        onTerminate={() => {}}
        grouped={{
          groups: [
            group({
              key: "landed",
              kind: "landed",
              label: "landed",
              defaultCollapsed: true,
              sessions: [{ id: "a", label: "Terminal 1", status: "exited" }],
              countLabel: "1 chat · archived",
            }),
          ],
          ungrouped: [],
        }}
      />,
    );
    expect(getByTestId("chats-group-toggle-landed").getAttribute("aria-expanded")).toBe("true");
    expect(queryByTestId("chats-session-a")).not.toBeNull();

    fireEvent.click(getByTestId("chats-group-toggle-landed")); // the user's collapse still wins
    expect(queryByTestId("chats-session-a")).toBeNull();
  });

  it("keeps unattached sessions in a flat list below the groups", () => {
    const { getByTestId } = render(
      <SessionList
        sessions={sessions}
        activeId={null}
        onSelect={() => {}}
        onTerminate={() => {}}
        grouped={{
          groups: [
            group({ sessions: [{ id: "b", label: "Claude Code 2" }], countLabel: "1 chat" }),
          ],
          ungrouped: [{ id: "a", label: "Terminal 1" }],
        }}
      />,
    );
    const tree = getByTestId("chats-session-tree");
    const flatRow = getByTestId("chats-session-a");
    expect(tree.contains(flatRow)).toBe(true);
    expect(flatRow.closest("[data-testid^='chats-group-']")).toBeNull(); // outside every group
  });

  it("renders today's flat list when the grouped model derives zero groups", () => {
    const { getByTestId, queryByTestId } = render(
      <SessionList
        sessions={sessions}
        activeId="a"
        onSelect={() => {}}
        onTerminate={() => {}}
        grouped={{ groups: [], ungrouped: sessions }}
      />,
    );
    expect(queryByTestId("chats-session-tree")).toBeNull();
    expect(getByTestId("chats-session-a").getAttribute("data-selected")).not.toBeNull();
  });

  it("shows a spawn-role chip on rows with role provenance", () => {
    const { getByTestId, queryByTestId } = render(
      <SessionList
        sessions={[
          { id: "a", label: "Claude Code 1", spawnRole: "manager" },
          { id: "b", label: "Terminal 1" },
        ]}
        activeId={null}
        onSelect={() => {}}
        onTerminate={() => {}}
      />,
    );
    expect(getByTestId("chats-session-role-a").textContent).toBe("manager");
    expect(queryByTestId("chats-session-role-b")).toBeNull();
  });
});
