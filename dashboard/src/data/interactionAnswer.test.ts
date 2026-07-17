// The gate-decision answer path (260715-FEUI-L6 R4): kind classification, gate matching, and
// the answer POST — the SOLE answer channel; never a PTY write (there is no terminal code here
// at all, by construction).
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LifecycleProjection } from "../types/projection";
import {
  answerPendingInteraction,
  findInteractionGate,
  representPendingInteraction,
} from "./interactionAnswer";

function lifecycleWithGate(overrides: {
  lifecycleId: string;
  gateId: string;
  state?: string;
  kind?: string;
  sessionId?: string;
  interactionId?: string;
}): LifecycleProjection {
  return {
    id: overrides.lifecycleId,
    gate: {
      id: overrides.gateId,
      kind: overrides.kind ?? "agent-question",
      state: overrides.state ?? "open",
      decisions: [],
      ts: "2026-07-17T09:00:00Z",
      packet: {
        adapterInteraction: {
          sessionId: overrides.sessionId ?? "seat-1",
          interactionId: overrides.interactionId ?? "ix-1",
          kind: "approval",
          prompt: "Allow?",
          choices: ["allow", "deny"],
        },
      },
    },
  } as unknown as LifecycleProjection;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("representPendingInteraction (kind-awareness, F8)", () => {
  it("choices present → buttons mode with the validated view", () => {
    const result = representPendingInteraction({
      interactionId: "ix-1",
      kind: "approval",
      prompt: "Allow?",
      choices: ["allow", "deny"],
    });
    expect(result).toEqual({
      mode: "choices",
      view: { interactionId: "ix-1", kind: "approval", prompt: "Allow?", choices: ["allow", "deny"] },
    });
  });

  it("no choices → composer answer-mode (free-text/confirm kinds)", () => {
    const result = representPendingInteraction({
      interactionId: "ix-2",
      kind: "input",
      prompt: "Which branch?",
      choices: [],
    });
    expect(result?.mode).toBe("composer");
  });

  it("missing interactionId → honestly unrepresentable, never dead buttons", () => {
    const result = representPendingInteraction({ kind: "vendor-custom", payload: { x: 1 } });
    expect(result?.mode).toBe("unrepresentable");
    if (result?.mode === "unrepresentable") {
      expect(result.reason).toContain("cannot be answered");
      expect(result.reason).toContain("inspector");
    }
  });

  it("missing prompt stays answerable — the empty prompt is reported, not invented", () => {
    const result = representPendingInteraction({ interactionId: "ix-3", kind: "confirm" });
    expect(result?.mode).toBe("composer");
    if (result && result.mode !== "unrepresentable") expect(result.view.prompt).toBe("");
  });

  it("absent payload → null (no bar)", () => {
    expect(representPendingInteraction(undefined)).toBeNull();
  });
});

describe("findInteractionGate", () => {
  it("matches the open agent-question gate by (sessionId, interactionId)", () => {
    const lifecycles = {
      "lc-a": lifecycleWithGate({ lifecycleId: "lc-a", gateId: "g-a", sessionId: "other" }),
      "lc-b": lifecycleWithGate({ lifecycleId: "lc-b", gateId: "g-b", sessionId: "seat-1" }),
    };
    const ref = findInteractionGate(lifecycles, "seat-1", "ix-1");
    expect(ref?.lifecycleId).toBe("lc-b");
    expect(ref?.gate.id).toBe("g-b");
  });

  it("ignores decided gates and non-question kinds", () => {
    const lifecycles = {
      "lc-a": lifecycleWithGate({ lifecycleId: "lc-a", gateId: "g-a", state: "approved" }),
      "lc-b": lifecycleWithGate({ lifecycleId: "lc-b", gateId: "g-b", kind: "worktree-closeout" }),
    };
    expect(findInteractionGate(lifecycles, "seat-1", "ix-1")).toBeNull();
  });
});

describe("answerPendingInteraction (round-trip, F7)", () => {
  it("POSTs the answer as the gate decision note on the approve verb", async () => {
    const calls: Array<{ url: string; body: Record<string, string> }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), body: JSON.parse(String(init?.body)) });
        return { status: 202, text: async () => "" } as Response;
      }),
    );
    const outcome = await answerPendingInteraction({
      lifecycles: { "lc-b": lifecycleWithGate({ lifecycleId: "lc-b", gateId: "g-b" }) },
      sessionId: "seat-1",
      sessionLifecycleId: "lc-b",
      interactionId: "ix-1",
      answer: "allow",
    });
    expect(outcome).toEqual({ status: "answered" });
    expect(calls).toEqual([
      {
        url: "/api/actions/approve",
        body: { target: "lc-b", gateId: "g-b", note: "allow" },
      },
    ]);
  });

  it("keeps the server's words VERBATIM on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        status: 409,
        text: async () => '{"status":"stale-gate","detail":"gate g-b was superseded"}',
      })) as unknown as typeof fetch,
    );
    const outcome = await answerPendingInteraction({
      lifecycles: { "lc-b": lifecycleWithGate({ lifecycleId: "lc-b", gateId: "g-b" }) },
      sessionId: "seat-1",
      sessionLifecycleId: "lc-b",
      interactionId: "ix-1",
      answer: "allow",
    });
    expect(outcome.status).toBe("error");
    if (outcome.status === "error") {
      expect(outcome.error).toContain("stale-gate");
      expect(outcome.error).toContain("gate g-b was superseded");
    }
  });

  it("states the poll-bounded truth when the gate has not been projected YET (seat has a lifecycle)", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const outcome = await answerPendingInteraction({
      lifecycles: {},
      sessionId: "seat-1",
      sessionLifecycleId: "lc-b",
      interactionId: "ix-1",
      answer: "allow",
    });
    expect(outcome.status).toBe("error");
    if (outcome.status === "error") expect(outcome.error).toContain("poll-bounded");
    expect(fetchSpy).not.toHaveBeenCalled(); // no blind POST without a target gate
  });

  it("says CANNOT (not 'retry in a moment') for a lifecycle-less seat — its gate never projects (review finding 2)", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const outcome = await answerPendingInteraction({
      lifecycles: {},
      sessionId: "seat-1",
      sessionLifecycleId: undefined,
      interactionId: "ix-1",
      answer: "allow",
    });
    expect(outcome.status).toBe("error");
    if (outcome.status === "error") {
      expect(outcome.error).toContain("cannot be answered from the cockpit");
      expect(outcome.error).not.toContain("retry in a moment");
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
