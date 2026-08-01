// The answer paths (structured decision items): kind
// classification, gate matching, the legacy gate answer POST, and the session-direct
// interaction-response route (structured questions + permission-kind — NO lifecycle required).
// Never a PTY write (there is no terminal code here at all, by construction).
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import type { LifecycleProjection } from "../types/projection";
import {
  answerPendingInteraction,
  findInteractionGate,
  pendingInteractionAgentLabel,
  readAdapterDecisionFailure,
  representPendingInteraction,
  submitInteractionAnswer,
} from "./interactionAnswer";
import { sessionCockpitStore } from "./sessionCockpitStore";
import { fromTerminalSessionInfo } from "./sessions";
import { clearSubmissionAuthorityCache } from "./submissionLifecycleClient";
import {
  L5I_INTERACTION_LEGACY_RUNNER,
  L5I_INTERACTION_NO_LIFECYCLE,
  L5I_INTERACTION_PERMISSION,
  L5I_INTERACTION_QUESTIONS,
} from "../test/fixtures/catalogRows";
import { lifecycleWithGate as servedLifecycleWithGate } from "../test/fixtures/wire";

/** The one shape these tests care about: a lifecycle holding an open agent-question gate whose
 *  packet carries the adapter interaction identity. Everything else is served default, so the
 *  fixture is checked against the mirror instead of asserted past it. */
function lifecycleWithGate(overrides: {
  lifecycleId: string;
  gateId: string;
  state?: string;
  kind?: string;
  sessionId?: string;
  interactionId?: string;
}): LifecycleProjection {
  return servedLifecycleWithGate(
    { id: overrides.lifecycleId },
    {
      id: overrides.gateId,
      kind: overrides.kind ?? "agent-question",
      state: overrides.state ?? "open",
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
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  sessionCockpitStore.setState({ perSession: {} });
  clearSubmissionAuthorityCache();
});

describe("representPendingInteraction (kind-awareness, F8)", () => {
  it("allow/deny choices → permission mode with the validated view (direct-route shape)", () => {
    const result = representPendingInteraction({
      interactionId: "ix-1",
      kind: "approval",
      prompt: "Allow?",
      choices: ["allow", "deny"],
    });
    expect(result).toEqual({
      mode: "permission",
      view: {
        interactionId: "ix-1",
        kind: "approval",
        prompt: "Allow?",
        choices: ["allow", "deny"],
        questions: [],
      },
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

describe("readAdapterDecisionFailure (M6 reopen honesty)", () => {
  it("parses a well-formed failure record and drops the decided-attribution fields it does not use", () => {
    const failure = readAdapterDecisionFailure({
      adapterInteraction: { sessionId: "seat-1", interactionId: "ix-1" },
      adapterDecisionFailure: {
        delivery: "not-sent",
        decision: "rejected",
        decisionNote: "use the shorter message",
        reason: "control bridge is not connected",
        attempts: 3,
        decidedAt: "2026-07-24T00:00:00Z",
        decidedBy: "developer",
      },
    });
    expect(failure).toEqual({
      delivery: "not-sent",
      decision: "rejected",
      decisionNote: "use the shorter message",
      reason: "control bridge is not connected",
      attempts: 3,
    });
  });

  it("returns null when the packet carries no failure record (a normal open gate)", () => {
    expect(readAdapterDecisionFailure({ adapterInteraction: { sessionId: "seat-1" } })).toBeNull();
    expect(readAdapterDecisionFailure(undefined)).toBeNull();
  });

  it("returns null for a malformed record with no delivery word — never a half-formed notice", () => {
    expect(readAdapterDecisionFailure({ adapterDecisionFailure: { reason: "bridge down" } })).toBeNull();
    expect(readAdapterDecisionFailure({ adapterDecisionFailure: "not-an-object" })).toBeNull();
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


// ── Structured decision items + the session-direct route ────────────────────

/** Fetch stub for the direct route: serves the authority descriptor + captures response POSTs. */
function stubDirectRoute(options: {
  /** Epochs served in order per authority GET (re-reads after a mismatch see the next one). */
  epochs?: string[];
  postStatus?: number;
  postBody?: unknown;
  authorityFails?: boolean;
}): Array<{ url: string; body: Record<string, unknown> }> {
  const posts: Array<{ url: string; body: Record<string, unknown> }> = [];
  const epochs = [...(options.epochs ?? ["ep-1"])];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const target = String(url);
      if (target.includes("/submission-authority")) {
        if (options.authorityFails) {
          return {
            ok: false,
            status: 409,
            json: async () => ({ status: "unsupported", detail: "no native control endpoint" }),
          } as Response;
        }
        const bridgeEpoch = epochs.length > 1 ? epochs.shift()! : epochs[0]!;
        return { ok: true, status: 200, json: async () => ({ bridgeEpoch }) } as Response;
      }
      if (target.includes("/interaction-response")) {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        posts.push({ url: target, body });
        const status = options.postStatus ?? 200;
        return {
          ok: status >= 200 && status < 300,
          status,
          json: async () => options.postBody ?? { status: "accepted" },
        } as Response;
      }
      throw new Error(`unexpected fetch ${target}`);
    }),
  );
  return posts;
}

describe("representPendingInteraction — structured questions (L5I)", () => {
  it("parses per-question pages with their OWN option groups and multiSelect flags", () => {
    const result = representPendingInteraction(
      L5I_INTERACTION_QUESTIONS.controlPendingInteraction,
    );
    expect(result?.mode).toBe("questions");
    if (result?.mode !== "questions") return;
    expect(result.view.questions).toEqual([
      {
        text: "Which base branch?",
        header: "Base",
        options: [
          { label: "main", description: "the gated main line" },
          { label: "develop", description: "the integration branch" },
        ],
        multiSelect: false,
      },
      {
        text: "Which extras should run?",
        header: "Extras",
        options: [{ label: "tests" }, { label: "docs" }, { label: "bench" }],
        multiSelect: true,
      },
    ]);
  });

  it("choices exactly allow/deny with no structured questions → permission mode", () => {
    const result = representPendingInteraction(
      L5I_INTERACTION_PERMISSION.controlPendingInteraction,
    );
    expect(result?.mode).toBe("permission");
    if (result && result.mode !== "unrepresentable") {
      expect(result.view.questions).toEqual([]);
    }
  });

  it("a choice set beyond allow/deny stays LEGACY choices mode (gate fallback)", () => {
    const result = representPendingInteraction({
      interactionId: "ix-9",
      kind: "approval",
      prompt: "Allow?",
      choices: ["allow", "allow for this session", "deny"],
    });
    expect(result?.mode).toBe("choices");
  });

  it("drops malformed question entries honestly; a payload with NO valid question falls back", () => {
    const result = representPendingInteraction({
      interactionId: "ix-10",
      kind: "user-input",
      prompt: "p",
      choices: ["a", "b"],
      questions: [
        { header: "no text", options: [{ label: "x" }] },
        { text: "kept?", options: [{ label: "a" }, { broken: true }], multiSelect: true },
      ],
    });
    expect(result?.mode).toBe("questions");
    if (result?.mode !== "questions") return;
    expect(result.view.questions).toEqual([
      { text: "kept?", options: [{ label: "a" }], multiSelect: true },
    ]);
    const fallenBack = representPendingInteraction({
      interactionId: "ix-11",
      kind: "user-input",
      prompt: "p",
      choices: ["a", "b"],
      questions: [{ header: "no text at all" }],
    });
    expect(fallenBack?.mode).toBe("choices");
  });

  it("m9: an option-less question makes the WHOLE payload unrepresentable, never a stuck all-or-nothing form", () => {
    // `options: []` is backend-legal (`_question_pages` checks only isinstance(options, list)) but
    // has nothing to pick, so the operator can never record an answer for it — and the direct route
    // is all-or-nothing, so the combined submit could NEVER fire. Without the fallback this renders
    // as `mode:"questions"` and strands the operator at a submit that can never complete. The whole
    // payload must fall back to unrepresentable, and the real prompt must be surfaced, not dropped.
    const result = representPendingInteraction({
      interactionId: "ix_optionless",
      kind: "user-input",
      prompt: "p",
      choices: ["a", "b"],
      questions: [
        { text: "Which base branch?", options: [{ label: "main" }], multiSelect: false },
        { text: "Free-form thing with no options?", options: [], multiSelect: false },
      ],
    });
    expect(result?.mode).toBe("unrepresentable");
    if (result?.mode !== "unrepresentable") return;
    // The real prompt is surfaced (never silently dropped) and the bar states it cannot be answered.
    expect(result.reason).toContain("Free-form thing with no options?");
    expect(result.reason).toContain("cannot be answered");
    expect(result.reason).toContain("inspector");
  });

  it("m9: a question whose options all collapse to none (malformed entries) is caught the same way", () => {
    // parseQuestions KEEPS the question with `options: []` when every option entry is malformed
    // (the exact `interactionAnswer.ts:94` behavior) — so the option-less guard,
    // not a silent drop, is what must catch it. A lone such question has no other channel either.
    const result = representPendingInteraction({
      interactionId: "ix_optionless_collapsed",
      kind: "user-input",
      prompt: "p",
      choices: [],
      questions: [{ text: "Broken options collapse to none?", options: [{ nope: true }], multiSelect: false }],
    });
    expect(result?.mode).toBe("unrepresentable");
    if (result?.mode !== "unrepresentable") return;
    expect(result.reason).toContain("Broken options collapse to none?");
  });

  it("PRE-FIX runner fallback: parses claude-native raw.input.questions (text key `question`)", () => {
    const result = representPendingInteraction(
      L5I_INTERACTION_LEGACY_RUNNER.controlPendingInteraction,
    );
    expect(result?.mode).toBe("questions");
    if (result?.mode !== "questions") return;
    expect(result.view.questions).toEqual([
      {
        text: "Pick one — purely a rendering test?",
        header: "Test pick",
        options: [
          { label: "A tiny dragon", description: "breathes warm air" },
          { label: "A talking cat", description: "judges your code" },
        ],
        multiSelect: false,
      },
    ]);
  });

  it("malformed raw.input.questions falls back to legacy safely (never dead structured chrome)", () => {
    const allMalformed = representPendingInteraction({
      interactionId: "ix-12",
      kind: "user-input",
      prompt: "p",
      choices: ["a", "b"],
      raw: {
        input: { questions: [{ header: "no question key" }, "junk", null] },
        toolName: "AskUserQuestion",
      },
    });
    expect(allMalformed?.mode).toBe("choices");
    // Non-object raw / input shapes are equally safe.
    expect(
      representPendingInteraction({ interactionId: "ix-13", kind: "k", choices: [], raw: "junk" })
        ?.mode,
    ).toBe("composer");
    expect(
      representPendingInteraction({
        interactionId: "ix-14",
        kind: "k",
        choices: [],
        raw: { input: 42 },
      })?.mode,
    ).toBe("composer");
  });
});

describe("submitInteractionAnswer — the session-direct route (L5I, no lifecycle required)", () => {
  it("a NO-LIFECYCLE seat answers structured questions: answers map covers EVERY question", async () => {
    const session = fromTerminalSessionInfo(L5I_INTERACTION_NO_LIFECYCLE);
    expect(session.lifecycleId).toBeUndefined();
    const posts = stubDirectRoute({});
    const outcome = await submitInteractionAnswer({
      session,
      interactionId: "ix_l5i_nolifecycle",
      answers: { "Which would you rather have as a pet?": "A tiny dragon" },
    });
    expect(outcome).toEqual({ status: "answered" });
    expect(posts).toHaveLength(1);
    expect(posts[0]?.url).toBe(`/api/terminal/${session.id}/interaction-response`);
    expect(posts[0]?.body).toEqual({
      interactionId: "ix_l5i_nolifecycle",
      expectedBridgeEpoch: "ep-1",
      answers: { "Which would you rather have as a pet?": "A tiny dragon" },
    });
    expect("response" in (posts[0]?.body ?? {})).toBe(false);
    // Answered through the direct route — never a gate POST (the mock throws on /api/actions).
    expect(
      sessionCockpitStore.getState().perSession[session.id]?.interactionAnswer?.answeredAt,
    ).toBeDefined();
  });

  it("a PRE-FIX runner seat answers via the direct route, keyed by the exact `question` text", async () => {
    const session = fromTerminalSessionInfo(L5I_INTERACTION_LEGACY_RUNNER);
    expect(session.lifecycleId).toBeUndefined();
    const posts = stubDirectRoute({});
    const outcome = await submitInteractionAnswer({
      session,
      interactionId: "ix_l5i_legacy_runner",
      answers: { "Pick one — purely a rendering test?": "A tiny dragon" },
    });
    expect(outcome).toEqual({ status: "answered" });
    expect(posts).toHaveLength(1);
    expect(posts[0]?.url).toBe(`/api/terminal/${session.id}/interaction-response`);
    expect(posts[0]?.body).toEqual({
      interactionId: "ix_l5i_legacy_runner",
      expectedBridgeEpoch: "ep-1",
      answers: { "Pick one — purely a rendering test?": "A tiny dragon" },
    });
  });

  it("refuses a partial answers map CLIENT-SIDE (all-or-nothing) — nothing is POSTed", async () => {
    const session = fromTerminalSessionInfo(L5I_INTERACTION_QUESTIONS);
    const posts = stubDirectRoute({});
    const outcome = await submitInteractionAnswer({
      session,
      interactionId: "ix_l5i_questions",
      answers: { "Which base branch?": "main" },
    });
    expect(outcome.status).toBe("error");
    if (outcome.status === "error") expect(outcome.error).toContain("every question");
    expect(posts).toHaveLength(0);
  });

  it("permission-kind uses the `response` body (never an answers map)", async () => {
    const session = fromTerminalSessionInfo(L5I_INTERACTION_PERMISSION);
    const posts = stubDirectRoute({});
    const outcome = await submitInteractionAnswer({
      session,
      interactionId: "ix_l5i_permission",
      answer: "allow",
    });
    expect(outcome).toEqual({ status: "answered" });
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({
      interactionId: "ix_l5i_permission",
      expectedBridgeEpoch: "ep-1",
      response: "allow",
    });
    expect("answers" in (posts[0]?.body ?? {})).toBe(false);
  });

  it("a 409 not-pending surfaces the server's words VERBATIM", async () => {
    const session = fromTerminalSessionInfo(L5I_INTERACTION_NO_LIFECYCLE);
    stubDirectRoute({
      postStatus: 409,
      postBody: { status: "not-pending", detail: "interaction ix_l5i_nolifecycle is not pending" },
    });
    const outcome = await submitInteractionAnswer({
      session,
      interactionId: "ix_l5i_nolifecycle",
      answers: { "Which would you rather have as a pet?": "A talking cat" },
    });
    expect(outcome.status).toBe("error");
    if (outcome.status === "error") {
      expect(outcome.error).toContain("not-pending");
      expect(outcome.error).toContain("interaction ix_l5i_nolifecycle is not pending");
    }
  });

  it("a bridge-epoch-mismatch drops the cached epoch and retries ONCE on the fresh one", async () => {
    const session = fromTerminalSessionInfo(L5I_INTERACTION_NO_LIFECYCLE);
    const posts: Array<Record<string, unknown>> = [];
    let postCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const target = String(url);
        if (target.includes("/submission-authority")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({ bridgeEpoch: postCount === 0 ? "ep-old" : "ep-new" }),
          } as Response;
        }
        if (target.includes("/interaction-response")) {
          const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
          posts.push(body);
          postCount += 1;
          if (postCount === 1) {
            return {
              ok: false,
              status: 409,
              json: async () => ({ status: "bridge-epoch-mismatch", detail: "bridge restarted" }),
            } as Response;
          }
          return { ok: true, status: 200, json: async () => ({ status: "accepted" }) } as Response;
        }
        throw new Error(`unexpected fetch ${target}`);
      }),
    );
    const outcome = await submitInteractionAnswer({
      session,
      interactionId: "ix_l5i_nolifecycle",
      answers: { "Which would you rather have as a pet?": "A tiny dragon" },
    });
    expect(outcome).toEqual({ status: "answered" });
    expect(posts).toHaveLength(2);
    expect(posts[0]?.expectedBridgeEpoch).toBe("ep-old");
    expect(posts[1]?.expectedBridgeEpoch).toBe("ep-new");
  });

  it("an unavailable submission authority blocks the answer honestly — no blind POST", async () => {
    const session = fromTerminalSessionInfo(L5I_INTERACTION_NO_LIFECYCLE);
    const posts = stubDirectRoute({ authorityFails: true });
    const outcome = await submitInteractionAnswer({
      session,
      interactionId: "ix_l5i_nolifecycle",
      answers: { "Which would you rather have as a pet?": "A tiny dragon" },
    });
    expect(outcome.status).toBe("error");
    if (outcome.status === "error") {
      expect(outcome.error).toContain("submission authority unavailable");
    }
    expect(posts).toHaveLength(0);
  });
});

describe("pendingInteractionAgentLabel", () => {
  it("reads the adapter-bound sub-agent label from raw.agentLabel only", () => {
    expect(
      pendingInteractionAgentLabel({
        interactionId: "ix-1",
        kind: "permission",
        raw: { threadId: "t-1", agentLabel: "scout" },
      }),
    ).toBe("scout");
    expect(pendingInteractionAgentLabel({ interactionId: "ix-1", raw: {} })).toBeUndefined();
    expect(pendingInteractionAgentLabel({ interactionId: "ix-1" })).toBeUndefined();
    expect(pendingInteractionAgentLabel(undefined)).toBeUndefined();
    expect(
      pendingInteractionAgentLabel({ interactionId: "ix-1", raw: { agentLabel: "  " } }),
    ).toBeUndefined();
  });
});
