import { beforeEach, describe, expect, it } from "vitest";

import { catalogRow } from "../test/fixtures/catalogRows";
import { SET_RESULT_CLAMP, SET_RESULTS } from "../test/fixtures/capabilityEnvelopes";
import {
  announceAssertive,
  announceAssertiveBatch,
  announcePolite,
  announcerStore,
  startSeatStateAnnouncer,
  stateEntryAnnouncements,
} from "./announcer";
import { sessionCockpitStore } from "./sessionCockpitStore";
import { fromTerminalSessionInfo, sessionStore } from "./sessions";
import {
  promotionAnnouncement,
  sessionAwaitingInputAnnouncement,
  sessionFailedAnnouncement,
  setResultAnnouncement,
} from "./setControlsCopy";

// The two live regions' feed (260715-FEUI-L4 R8, design §9.5): polite for SetResult arrivals/
// promotions, assertive for ANY session entering failed/awaiting-input — with the L6
// InteractionBar coordination rule (never double-announce the focused pending question).

const seat = (id: string, overrides: Parameters<typeof catalogRow>[0] = {}) =>
  fromTerminalSessionInfo(catalogRow({ id, label: id, ...overrides }));

beforeEach(() => {
  announcerStore.setState({ polite: { text: "", seq: 0 }, assertive: { text: "", seq: 0 } });
  sessionStore.getState().hydrate([]);
  sessionCockpitStore.setState({ focusedSessionId: null, perSession: {} });
});

describe("announcement copy (one module, tests assert the words)", () => {
  it("SetResult arrival strings — the acceptance story in words, requested ≠ effective", () => {
    expect(setResultAnnouncement("effort", SET_RESULT_CLAMP)).toBe(
      "effort set clamped: requested max, effective high",
    );
    expect(setResultAnnouncement("model", SET_RESULTS.immediate)).toBe(
      "model gpt-5.6-sol was already effective",
    );
    expect(setResultAnnouncement("effort", SET_RESULTS.queued)).toBe(
      "effort xhigh queued — applies on next turn",
    );
    expect(setResultAnnouncement("effort", SET_RESULTS.unknown)).toBe(
      "effort set outcome unknown — verifying by snapshot readback",
    );
    expect(setResultAnnouncement("model", SET_RESULTS.unsupported)).toBe(
      "model set unsupported — prior value kept: requested model is absent from the dynamic catalog",
    );
  });

  it("promotion + assertive state strings", () => {
    expect(promotionAnnouncement("model", "gpt-5.6-terra")).toBe(
      "queued model change applied — now gpt-5.6-terra",
    );
    expect(sessionFailedAnnouncement("scout")).toBe("scout failed");
    expect(sessionAwaitingInputAnnouncement("scout")).toBe("scout awaiting input");
  });
});

describe("announcerStore", () => {
  it("bumps a sequence per announcement so identical texts still re-announce", () => {
    announcePolite("same");
    announcePolite("same");
    expect(announcerStore.getState().polite).toEqual({ text: "same", seq: 2 });
    announceAssertive("loud");
    expect(announcerStore.getState().assertive).toEqual({ text: "loud", seq: 1 });
  });

  it("commits a same-hydration alert batch without overwriting an earlier seat", () => {
    announceAssertiveBatch(["alpha failed", "beta awaiting input"]);
    expect(announcerStore.getState().assertive).toEqual({
      text: "alpha failed · beta awaiting input",
      seq: 1,
    });
  });
});

describe("stateEntryAnnouncements (pure transition detector)", () => {
  it("first observation seeds silently — a page load never announces the whole fleet", () => {
    const sessions = [seat("a", { controlState: "failed" }), seat("b", { turnState: "awaiting-input" })];
    const { announcements, next } = stateEntryAnnouncements(new Map(), sessions, null);
    expect(announcements).toEqual([]);
    expect(next.get("a")).toBe("failed");
    expect(next.get("b")).toBe("awaiting-input");
  });

  it("announces transitions INTO failed and awaiting-input for ANY session, focused or not", () => {
    const before = new Map([
      ["a", "working"],
      ["b", "working"],
    ]);
    const sessions = [
      seat("a", { controlState: "failed" }),
      seat("b", { turnState: "awaiting-input" }),
    ];
    const { announcements } = stateEntryAnnouncements(before, sessions, "a");
    expect(announcements).toEqual([sessionFailedAnnouncement("a"), sessionAwaitingInputAnnouncement("b")]);
  });

  it("NEVER double-announces the focused pending question — the InteractionBar's alert owns it", () => {
    const before = new Map([["a", "working"]]);
    const withBar = [
      seat("a", {
        turnState: "awaiting-input",
        controlPendingInteraction: { interactionId: "i1", prompt: "which one?" },
      }),
    ];
    // Focused + pending interaction ⇒ the bar announces; the region stays silent.
    expect(stateEntryAnnouncements(before, withBar, "a").announcements).toEqual([]);
    // Unfocused ⇒ the bar is not rendered for it; the region speaks.
    expect(stateEntryAnnouncements(before, withBar, "other").announcements).toEqual([
      sessionAwaitingInputAnnouncement("a"),
    ]);
  });

  it("announces a seat blocked SOLELY on a sub-agent approval — never claiming the parent asks", () => {
    const before = new Map([["a", "working"]]);
    const withAgentBar = [
      seat("a", {
        turnState: "working",
        controlPendingInteractions: [
          {
            interactionId: "ix_agent",
            kind: "permission",
            prompt: "Allow the sub-agent command?",
            raw: { threadId: "agent-thread-1", agentLabel: "agent agent-t" },
          },
        ],
      }),
    ];
    // Unfocused: the region speaks with the seat-level wording ("awaiting input") —
    // it never says the question is the parent's.
    expect(stateEntryAnnouncements(before, withAgentBar, "other").announcements).toEqual([
      sessionAwaitingInputAnnouncement("a"),
    ]);
    // Focused: the InteractionBar announces the agent bar itself; the region stays silent.
    expect(stateEntryAnnouncements(before, withAgentBar, "a").announcements).toEqual([]);
  });

  it("no announcement without a transition (steady failed stays silent)", () => {
    const before = new Map([["a", "failed"]]);
    const sessions = [seat("a", { controlState: "failed" })];
    expect(stateEntryAnnouncements(before, sessions, null).announcements).toEqual([]);
  });
});

describe("startSeatStateAnnouncer (the wired watcher)", () => {
  it("announces a live transition into failed via the assertive channel", () => {
    sessionStore.getState().hydrate([seat("w", { turnState: "working", controlState: "ready" })]);
    const release = startSeatStateAnnouncer();
    sessionStore.getState().hydrate([seat("w", { controlState: "failed" })]);
    expect(announcerStore.getState().assertive.text).toBe(sessionFailedAnnouncement("w"));
    release();
  });

  it("announces every urgent transition from one catalog hydration", () => {
    sessionStore.getState().hydrate([
      seat("a", { turnState: "working", controlState: "ready" }),
      seat("b", { turnState: "working", controlState: "ready" }),
    ]);
    const release = startSeatStateAnnouncer();
    sessionStore.getState().hydrate([
      seat("a", { controlState: "failed" }),
      seat("b", { turnState: "awaiting-input" }),
    ]);
    expect(announcerStore.getState().assertive.text).toBe("a failed · b awaiting input");
    release();
  });
});
