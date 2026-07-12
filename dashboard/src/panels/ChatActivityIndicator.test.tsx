import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { OpenSession } from "../data/sessions";
import { ChatActivityIndicator, summarizeChatActivity } from "./ChatActivityIndicator";

const LEAF = "agents-remember/260712_task-reader-body-priority-rc5/260712-TRH-L6";

function session(over: Partial<OpenSession> & Pick<OpenSession, "id">): OpenSession {
  return {
    label: over.id,
    kind: "harness",
    status: "running",
    leafKey: LEAF,
    seatRole: "worker",
    ...over,
  };
}

afterEach(cleanup);

describe("Operations chat activity", () => {
  it("maps an observed busy chat to working", () => {
    const summary = summarizeChatActivity(
      [session({ id: "worker", turnState: "working" })],
      { leafKey: LEAF },
    );

    expect(summary).toEqual({ state: "working", label: "working", detail: "worker: working" });
  });

  it("maps awaiting input to needs input", () => {
    const summary = summarizeChatActivity(
      [
        session({ id: "worker", turnState: "awaiting-input" }),
        session({ id: "curator", seatRole: "curator", turnState: "working" }),
      ],
      { leafKey: LEAF },
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
      { leafKey: LEAF },
    );

    expect(summary?.state).toBe("unknown");
    expect(summary?.detail).toBe("curator: unknown; reviewer: unknown; worker: idle");
  });

  it("aggregates multiple role seats deterministically without hiding individual states", () => {
    const summary = summarizeChatActivity(
      [
        session({ id: "worker", turnState: "turn-ended" }),
        session({ id: "curator", seatRole: "curator", turnState: "working" }),
      ],
      { leafKey: LEAF },
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

  it("isolates exact qualified leaf identity before considering lifecycle", () => {
    const summary = summarizeChatActivity(
      [
        session({ id: "right", lifecycleId: "LC1", turnState: "turn-ended" }),
        session({
          id: "wrong",
          leafKey: "agents-remember/other-master/260712-TRH-L6",
          lifecycleId: "LC1",
          turnState: "working",
        }),
        session({ id: "legacy", leafKey: undefined, lifecycleId: "LC1", turnState: "working" }),
      ],
      { leafKey: LEAF, lifecycleId: "LC1" },
    );

    expect(summary).toEqual({ state: "idle", label: "idle", detail: "worker: idle" });
  });

  it("falls back to lifecycle only for a lifecycle-bound row and an unclaimed session", () => {
    const lifecycleOnly = session({
      id: "worker",
      leafKey: undefined,
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
      session({ id: "other", leafKey: "agents-remember/other/leaf", turnState: "working" }),
    ];

    expect(summarizeChatActivity(sessions, { leafKey: LEAF })).toBeUndefined();
  });
});
