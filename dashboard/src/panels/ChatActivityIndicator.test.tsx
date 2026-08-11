import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { OpenSession } from "../data/sessions";
import { ChatActivityIndicator, summarizeChatActivity } from "./ChatActivityIndicator";

const TASK = {
  repository: "agents-remember",
  path: "260712_task-reader-body-priority-rc5/06_operations-chat-activity.json",
};

function session(over: Partial<OpenSession> & Pick<OpenSession, "id">): OpenSession {
  return {
    label: over.id,
    kind: "harness",
    status: "running",
    taskDocumentRef: TASK,
    seatRole: "worker",
    ...over,
  };
}

afterEach(cleanup);

describe("Operations chat activity", () => {
  it("maps an observed busy chat to working", () => {
    const summary = summarizeChatActivity(
      [session({ id: "worker", turnState: "working" })],
      { taskDocumentRef: TASK },
    );

    expect(summary).toEqual({ state: "working", label: "working", detail: "worker: working" });
  });

  it("maps awaiting input to needs input", () => {
    const summary = summarizeChatActivity(
      [
        session({ id: "worker", turnState: "awaiting-input" }),
        session({ id: "curator", seatRole: "curator", turnState: "working" }),
      ],
      { taskDocumentRef: TASK },
    );

    expect(summary?.state).toBe("needs-input");
    expect(summary?.label).toBe("needs input");
  });

  it("maps stale or missing catalog classification to unknown ahead of idle", () => {
    const summary = summarizeChatActivity(
      [
        session({ id: "worker", turnState: "turn-ended" }),
        session({ id: "curator", seatRole: "curator", turnState: "stale" }),
        session({ id: "reviewer", seatRole: "reviewer" }),
      ],
      { taskDocumentRef: TASK },
    );

    expect(summary?.state).toBe("unknown");
    expect(summary?.detail).toBe("curator: unknown; reviewer: unknown; worker: idle");
  });

  it("maps a fresh ready-idle chat (no turn claim yet) to idle, never unknown (260718-CHATS-L5I A2b)", () => {
    // The sweep no longer stamps stale/turn-ended on a fresh chat: a ready control with no
    // turnState IS the calm idle seat — the dot/label must not read unknown-alarming.
    const summary = summarizeChatActivity(
      [session({ id: "worker", controlState: "ready" })],
      { taskDocumentRef: TASK },
    );

    expect(summary).toEqual({ state: "idle", label: "idle", detail: "worker: idle" });
  });

  it("keeps a booting chat honest: a starting control with no turn claim stays unknown", () => {
    const summary = summarizeChatActivity(
      [session({ id: "worker", controlState: "starting" })],
      { taskDocumentRef: TASK },
    );

    expect(summary?.state).toBe("unknown");
  });

  it("aggregates multiple role seats deterministically without hiding individual states", () => {
    const summary = summarizeChatActivity(
      [
        session({ id: "worker", turnState: "turn-ended" }),
        session({ id: "curator", seatRole: "curator", turnState: "working" }),
      ],
      { taskDocumentRef: TASK },
    );
    const { getByRole } = render(<ChatActivityIndicator summary={summary} />);

    expect(summary?.state).toBe("working");
    expect(getByRole("status").getAttribute("aria-label")).toBe(
      "Chat activity: working. curator: working; worker: idle",
    );
    expect(getByRole("status").getAttribute("title")).toBe(
      "Chat activity: working. curator: working; worker: idle",
    );
  });

  it("isolates exact task-document identity before considering lifecycle", () => {
    const summary = summarizeChatActivity(
      [
        session({ id: "right", lifecycleId: "LC1", turnState: "turn-ended" }),
        session({
          id: "wrong",
          taskDocumentRef: { repository: "agents-remember", path: "other/task.json" },
          lifecycleId: "LC1",
          turnState: "working",
        }),
        session({ id: "legacy", taskDocumentRef: undefined, lifecycleId: "LC1", turnState: "working" }),
      ],
      { taskDocumentRef: TASK, lifecycleId: "LC1" },
    );

    expect(summary).toEqual({ state: "idle", label: "idle", detail: "worker: idle" });
  });

  it("falls back to lifecycle only for a lifecycle-bound row and an unclaimed session", () => {
    const lifecycleOnly = session({
      id: "worker",
      taskDocumentRef: undefined,
      lifecycleId: "LC1",
      turnState: "working",
    });

    expect(summarizeChatActivity([lifecycleOnly], { lifecycleId: "LC1" })?.state).toBe("working");
    expect(summarizeChatActivity([lifecycleOnly], {})).toBeUndefined();
  });

  it("omits activity without a live bound harness seat", () => {
    const sessions = [
      session({ id: "terminal", kind: "terminal", turnState: "working" }),
      session({ id: "landed", status: "landed", turnState: "working" }),
      session({ id: "missing-status", status: undefined, turnState: "working" }),
      session({ id: "other", taskDocumentRef: { repository: "agents-remember", path: "other/leaf.json" }, turnState: "working" }),
    ];

    expect(summarizeChatActivity(sessions, { taskDocumentRef: TASK })).toBeUndefined();
  });
});
