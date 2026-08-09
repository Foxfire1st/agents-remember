// The InteractionBar: kind-awareness, the session-direct answer path, and the
// full round-trip — answering… → verbatim error + retry | answered-waiting (poll-bounded).
// xterm never appears here: the bar has no terminal dependency by construction.
import { act, cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { fromTerminalSessionInfo } from "../../data/sessions";
import { dashboardStore } from "../../data/store";
import { clearSubmissionAuthorityCache } from "../../data/submissionLifecycleClient";
import {
  L5I_INTERACTION_LEGACY_RUNNER,
  L5I_INTERACTION_NO_LIFECYCLE,
  L5I_INTERACTION_PERMISSION,
  L5I_INTERACTION_QUESTIONS,
  L6_INTERACTION_CHOICES,
  L6_INTERACTION_FREETEXT,
  L6_INTERACTION_UNREPRESENTABLE,
  L7_MULTIPLEXED_INTERACTIONS,
} from "../../test/fixtures/catalogRows";
import type { SessionComposerHandle } from "../SessionComposer";
import { InteractionBar } from "./InteractionBar";
import { INTERACTION_HONESTY_HINT } from "./lifecycleCopy";

const choicesSession = () => fromTerminalSessionInfo(L6_INTERACTION_CHOICES);
const freetextSession = () => fromTerminalSessionInfo(L6_INTERACTION_FREETEXT);

function composerHandleRef(text = "") {
  const element = document.createElement("div");
  document.body.appendChild(element);
  const ref = createRef<SessionComposerHandle>();
  Object.assign(ref, {
    current: {
      submit: () => {},
      popBack: () => {},
      focus: () => {},
      getDraft: () => text,
      getElement: () => element,
    } satisfies SessionComposerHandle,
  });
  return { element, ref };
}

beforeEach(() => {
  sessionCockpitStore.setState({ perSession: {} });
  dashboardStore.setState({ lifecycles: {} });
  clearSubmissionAuthorityCache();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("kind-awareness (F8)", () => {
  it("choice kinds render one button per choice + the kind chip + the honesty hint", () => {
    const { getByTestId, getAllByTestId } = render(<InteractionBar session={choicesSession()} />);
    expect(getByTestId("interaction-bar-kind").textContent).toBe("approval");
    expect(getByTestId("interaction-bar-prompt").textContent).toContain("npm install");
    expect(getAllByTestId("interaction-bar-choice").map((b) => b.textContent)).toEqual([
      "allow",
      "allow for this session",
      "deny",
    ]);
    expect(getByTestId("interaction-bar-hint").textContent).toBe(INTERACTION_HONESTY_HINT);
  });

  it("non-choice kinds mark the composer as the direct answer input", () => {
    const { element: composer, ref } = composerHandleRef();
    const { getByTestId } = render(
      <InteractionBar session={freetextSession()} composerRef={ref} />,
    );
    expect(getByTestId("interaction-bar-composer-mode").textContent).toContain("agent session");
    expect(composer.getAttribute("data-answer-mode")).toBe("true");
    composer.remove();
  });

  it("unrepresentable kinds say so honestly — no dead buttons", () => {
    const session = fromTerminalSessionInfo(L6_INTERACTION_UNREPRESENTABLE);
    const { getByTestId, queryAllByTestId } = render(<InteractionBar session={session} />);
    expect(getByTestId("interaction-bar-unrepresentable").textContent).toContain(
      "cannot be answered",
    );
    expect(queryAllByTestId("interaction-bar-choice")).toHaveLength(0);
  });

  it("renders nothing when no interaction is pending", () => {
    const session = { ...choicesSession(), controlPendingInteraction: undefined };
    const { container } = render(<InteractionBar session={session} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("round-trip states (F7)", () => {
  it("answering… disables the buttons in flight, then lands on answered — waiting (poll-bounded copy)", async () => {
    let release: (value: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (String(url).includes("/submission-authority")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({ bridgeEpoch: "ep-1" }),
          } as Response);
        }
        return new Promise<Response>((resolve) => (release = resolve));
      }),
    );
    const { getByTestId, getAllByTestId, queryByTestId } = render(
      <InteractionBar session={choicesSession()} />,
    );
    fireEvent.click(getAllByTestId("interaction-bar-choice")[0]);
    await waitFor(() =>
      expect(getByTestId("interaction-bar-inflight").textContent).toContain("answering"),
    );
    for (const button of getAllByTestId("interaction-bar-choice")) {
      expect(button.getAttribute("disabled")).not.toBeNull();
    }
    release({ ok: true, status: 200, json: async () => ({ status: "accepted" }) } as Response);
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(getByTestId("interaction-bar-answered").textContent).toContain("waiting for the agent");
    expect(getByTestId("interaction-bar-answered").textContent).toContain("2.5");
  });

  it("POST failure renders the verbatim error and retry re-sends the SAME answer", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    let status = 409;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (String(url).includes("/submission-authority")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({ bridgeEpoch: "ep-1" }),
          } as Response;
        }
        bodies.push(JSON.parse(String(init?.body)));
        return {
          ok: status === 200,
          status,
          json: async () =>
            status === 200
              ? { status: "accepted" }
              : { status: "not-pending", detail: "adapter response was not accepted" },
        } as Response;
      }),
    );
    const { getByTestId, getAllByTestId, queryByTestId } = render(
      <InteractionBar session={choicesSession()} />,
    );
    fireEvent.click(getAllByTestId("interaction-bar-choice")[2]); // "deny"
    await waitFor(() => expect(queryByTestId("interaction-bar-error")).not.toBeNull());
    expect(getByTestId("interaction-bar-error").textContent).toContain(
      "adapter response was not accepted",
    );
    status = 200;
    fireEvent.click(getByTestId("interaction-bar-retry"));
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toMatchObject({ response: "deny" });
    expect(bodies[1]).toEqual(bodies[0]);
  });

  it("a lifecycle-less choice still POSTs to its exact session", async () => {
    const posts = stubDirectRoute();
    const session = { ...choicesSession(), lifecycleId: undefined };
    const { getAllByTestId, queryByTestId } = render(<InteractionBar session={session} />);
    fireEvent.click(getAllByTestId("interaction-bar-choice")[0]);
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(posts[0]?.url).toBe("/api/terminal/l6-ix-choices/interaction-response");
    expect(posts[0]?.body).toMatchObject({ response: "allow" });
  });

  it("composer answer-mode routes text to the exact session, NOT /submit", async () => {
    const posts = stubDirectRoute();
    const { element: composer, ref } = composerHandleRef("start from ar/base");
    const { getByTestId, queryByTestId } = render(
      <InteractionBar session={freetextSession()} composerRef={ref} />,
    );
    fireEvent.click(getByTestId("interaction-bar-composer-send"));
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(posts[0]?.url).toBe("/api/terminal/l6-ix-freetext/interaction-response");
    expect(posts[0]?.body).toMatchObject({ response: "start from ar/base" });
    composer.remove();
  });

  it("retries the exact failed composer answer with its original revision and preserves a newer edit", async () => {
    const session = freetextSession();
    sessionCockpitStore.getState().setComposerDraft(session.id, "start from ar/base");
    const originalRevision =
      sessionCockpitStore.getState().perSession[session.id].composer.draftRevision;
    const bodies: Array<Record<string, unknown>> = [];
    let status = 409;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (String(url).includes("/submission-authority")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({ bridgeEpoch: "ep-1" }),
          } as Response;
        }
        bodies.push(JSON.parse(String(init?.body)));
        return {
          ok: status === 200,
          status,
          json: async () =>
            status === 200
              ? { status: "accepted" }
              : { status: "not-pending", detail: "adapter not ready" },
        } as Response;
      }),
    );
    const { element: composer, ref } = composerHandleRef("start from ar/base");
    const { getByTestId, queryByTestId } = render(
      <InteractionBar session={session} composerRef={ref} />,
    );
    fireEvent.click(getByTestId("interaction-bar-composer-send"));
    await waitFor(() => expect(queryByTestId("interaction-bar-error")).not.toBeNull());
    expect(sessionCockpitStore.getState().perSession[session.id].interactionAnswer).toMatchObject({
      answer: "start from ar/base",
      draftRevision: originalRevision,
      inflight: false,
    });

    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "newer human edit"));
    status = 200;
    fireEvent.click(getByTestId("interaction-bar-retry"));
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(bodies).toHaveLength(2);
    expect(bodies[0]?.response).toBe("start from ar/base");
    expect(bodies[1]).toEqual(bodies[0]);
    expect(sessionCockpitStore.getState().perSession[session.id].composer.draft).toBe(
      "newer human edit",
    );
    composer.remove();
  });
});

describe("stale round-trip state (review finding 5)", () => {
  it("clears a previous 'answered — waiting' before a FOLLOWING unrepresentable interaction renders", async () => {
    // The same seat answered ix_l6_choice, then the vendor replaced it with an opaque payload.
    sessionCockpitStore.getState().setInteractionAnswer("l6-ix-choices", {
      interactionId: "ix_l6_choice",
      inflight: false,
      answer: "allow",
      answeredAt: Date.now(),
    });
    const followUp = fromTerminalSessionInfo({
      ...L6_INTERACTION_UNREPRESENTABLE,
      id: "l6-ix-choices",
    });
    const { getByTestId, queryByTestId } = render(<InteractionBar session={followUp} />);
    expect(getByTestId("interaction-bar-unrepresentable")).not.toBeNull();
    await waitFor(() =>
      expect(
        sessionCockpitStore.getState().perSession["l6-ix-choices"]?.interactionAnswer,
      ).toBeUndefined(),
    );
    expect(queryByTestId("interaction-bar-answered")).toBeNull();
  });
});

describe("focus + announce honesty", () => {
  it("never steals focus on appearance and announces via an assertive region", () => {
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    outside.focus();
    const { getByTestId } = render(<InteractionBar session={choicesSession()} />);
    expect(document.activeElement).toBe(outside);
    expect(getByTestId("interaction-bar-announce").getAttribute("role")).toBe("alert");
    expect(getByTestId("interaction-bar-announce").textContent).toContain("npm install");
    outside.remove();
  });

  it("returns focus to the invoker when the bar clears while holding focus", async () => {
    const invoker = document.createElement("button");
    document.body.appendChild(invoker);
    invoker.focus();
    const { getAllByTestId, unmount } = render(<InteractionBar session={choicesSession()} />);
    getAllByTestId("interaction-bar-choice")[0].focus();
    unmount();
    await waitFor(() => expect(document.activeElement).toBe(invoker));
    invoker.remove();
  });
});


// ── Structured decision items through the session-direct route ──────────────

/** Fetch stub for the direct route: authority descriptor + captured interaction-response POSTs. */
function stubDirectRoute(options: { postStatus?: number; postDetail?: string } = {}) {
  const posts: Array<{ url: string; body: Record<string, unknown> }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const target = String(url);
      if (target.includes("/submission-authority")) {
        return { ok: true, status: 200, json: async () => ({ bridgeEpoch: "ep-1" }) } as Response;
      }
      if (target.includes("/interaction-response")) {
        posts.push({ url: target, body: JSON.parse(String(init?.body)) });
        const status = options.postStatus ?? 200;
        return {
          ok: status >= 200 && status < 300,
          status,
          json: async () =>
            status === 200
              ? { status: "accepted" }
              : { status: "not-pending", detail: options.postDetail ?? "" },
        } as Response;
      }
      throw new Error(`unexpected fetch ${target}`);
    }),
  );
  return posts;
}

describe("structured questions (260718-CHATS-L5I)", () => {
  const questionsSession = () => fromTerminalSessionInfo(L5I_INTERACTION_QUESTIONS);
  const noLifecycleSession = () => fromTerminalSessionInfo(L5I_INTERACTION_NO_LIFECYCLE);
  const permissionSession = () => fromTerminalSessionInfo(L5I_INTERACTION_PERMISSION);

  it("renders each question with ITS OWN option group — never the flat concatenation", () => {
    stubDirectRoute();
    const { getByTestId, getAllByTestId, queryByTestId } = render(
      <InteractionBar session={questionsSession()} />,
    );
    const questions = getAllByTestId("interaction-bar-question");
    expect(questions).toHaveLength(2);
    expect(
      within(questions[0]).getAllByTestId("interaction-bar-question-option").map((b) => b.textContent),
    ).toEqual(["main", "develop"]);
    expect(
      within(questions[1]).getAllByTestId("interaction-bar-question-toggle").map((b) => b.textContent),
    ).toEqual(["tests", "docs", "bench"]);
    // The legacy flattened prompt ("Base: …\nExtras: …") must not double as the question text.
    expect(queryByTestId("interaction-bar-prompt")).toBeNull();
    expect(getByTestId("interaction-bar-progress").textContent).toContain("0 of 2");
    expect(getByTestId("interaction-bar-multiselect-hint")).toBeDefined();
    // Per-question headers render as their own chips.
    expect(getAllByTestId("interaction-bar-question-header").map((h) => h.textContent)).toEqual([
      "Base",
      "Extras",
    ]);
  });

  it("multi-page: records per question (changeable), submits ONE answers map when ALL are answered", async () => {
    const posts = stubDirectRoute();
    const { getByTestId, getAllByTestId, queryByTestId } = render(
      <InteractionBar session={questionsSession()} />,
    );
    const questions = getAllByTestId("interaction-bar-question");
    fireEvent.click(within(questions[0]).getAllByTestId("interaction-bar-question-option")[0]); // main
    await waitFor(() =>
      expect(getByTestId("interaction-bar-question-recorded").textContent).toContain("main"),
    );
    // All-or-nothing: one answered question sends NOTHING yet.
    expect(posts).toHaveLength(0);
    // The record stays changeable while siblings are open.
    fireEvent.click(within(questions[0]).getAllByTestId("interaction-bar-question-option")[1]); // develop
    await waitFor(() =>
      expect(getByTestId("interaction-bar-question-recorded").textContent).toContain("develop"),
    );
    // multiSelect toggles join into ONE answer for that question.
    fireEvent.click(within(questions[1]).getAllByTestId("interaction-bar-question-toggle")[0]); // tests
    fireEvent.click(within(questions[1]).getAllByTestId("interaction-bar-question-toggle")[2]); // bench
    fireEvent.click(getByTestId("interaction-bar-question-confirm"));
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({
      interactionId: "ix_l5i_questions",
      expectedBridgeEpoch: "ep-1",
      answers: { "Which base branch?": "develop", "Which extras should run?": "tests, bench" },
    });
  });

  it("a NO-LIFECYCLE seat answers a single-question page on click — the 'bad bug' fix", async () => {
    const posts = stubDirectRoute();
    // No gate is projected for this seat (it has no lifecycle) — the direct route must carry it.
    const { getAllByTestId, queryByTestId } = render(
      <InteractionBar session={noLifecycleSession()} />,
    );
    fireEvent.click(getAllByTestId("interaction-bar-question-option")[0]); // "A tiny dragon"
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(posts).toHaveLength(1);
    expect(posts[0]?.url).toBe("/api/terminal/l5i-ix-nolifecycle/interaction-response");
    expect(posts[0]?.body).toEqual({
      interactionId: "ix_l5i_nolifecycle",
      expectedBridgeEpoch: "ep-1",
      answers: { "Which would you rather have as a pet?": "A tiny dragon" },
    });
  });

  it("a PRE-FIX runner seat renders structured pages from raw.input.questions and answers directly", async () => {
    const posts = stubDirectRoute();
    const { getAllByTestId, getByTestId, queryByTestId } = render(
      <InteractionBar session={fromTerminalSessionInfo(L5I_INTERACTION_LEGACY_RUNNER)} />,
    );
    expect(getAllByTestId("interaction-bar-question")).toHaveLength(1);
    expect(getByTestId("interaction-bar-question-text").textContent).toBe(
      "Pick one — purely a rendering test?",
    );
    expect(getByTestId("interaction-bar-question-header").textContent).toBe("Test pick");
    fireEvent.click(getAllByTestId("interaction-bar-question-option")[0]); // "A tiny dragon"
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(posts).toHaveLength(1);
    expect(posts[0]?.url).toBe("/api/terminal/l5i-ix-legacy-runner/interaction-response");
    expect(posts[0]?.body).toEqual({
      interactionId: "ix_l5i_legacy_runner",
      expectedBridgeEpoch: "ep-1",
      answers: { "Pick one — purely a rendering test?": "A tiny dragon" },
    });
  });

  it("permission-kind still uses the `response` body through the direct route", async () => {
    const posts = stubDirectRoute();
    const { getAllByTestId, queryByTestId } = render(
      <InteractionBar session={permissionSession()} />,
    );
    fireEvent.click(getAllByTestId("interaction-bar-choice")[0]); // "allow"
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({
      interactionId: "ix_l5i_permission",
      expectedBridgeEpoch: "ep-1",
      response: "allow",
    });
    expect("answers" in (posts[0]?.body ?? {})).toBe(false);
  });

  it("a 409 not-pending surfaces honestly; retry re-sends the SAME answers map", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    let status = 409;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const target = String(url);
        if (target.includes("/submission-authority")) {
          return { ok: true, status: 200, json: async () => ({ bridgeEpoch: "ep-1" }) } as Response;
        }
        if (target.includes("/interaction-response")) {
          bodies.push(JSON.parse(String(init?.body)));
          return {
            ok: status < 300,
            status,
            json: async () =>
              status === 200
                ? { status: "accepted" }
                : { status: "not-pending", detail: "already answered from the terminal" },
          } as Response;
        }
        throw new Error(`unexpected fetch ${target}`);
      }),
    );
    const { getAllByTestId, getByTestId, queryByTestId } = render(
      <InteractionBar session={noLifecycleSession()} />,
    );
    fireEvent.click(getAllByTestId("interaction-bar-question-option")[1]); // "A talking cat"
    await waitFor(() => expect(queryByTestId("interaction-bar-error")).not.toBeNull());
    expect(getByTestId("interaction-bar-error").textContent).toContain("not-pending");
    expect(getByTestId("interaction-bar-error").textContent).toContain(
      "already answered from the terminal",
    );
    status = 200;
    fireEvent.click(getByTestId("interaction-bar-retry"));
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toEqual(bodies[1]);
    expect(bodies[0]?.answers).toEqual({
      "Which would you rather have as a pet?": "A talking cat",
    });
  });
});

describe("multiplexed sub-agent approvals", () => {
  const multiplexedSession = () => fromTerminalSessionInfo(L7_MULTIPLEXED_INTERACTIONS);

  it("renders one bar per pending interaction — parent first, agent badged", () => {
    const { getAllByTestId } = render(<InteractionBar session={multiplexedSession()} />);
    const bars = getAllByTestId("interaction-bar");
    expect(bars).toHaveLength(2);
    const [parentBar, agentBar] = bars as [HTMLElement, HTMLElement];
    expect(within(parentBar).queryByTestId("interaction-bar-agent")).toBeNull();
    expect(within(parentBar).getByTestId("interaction-bar-prompt").textContent).toContain(
      "parent command",
    );
    expect(within(agentBar).getByTestId("interaction-bar-agent").textContent).toBe(
      "agent agent-t",
    );
    expect(within(agentBar).getByTestId("interaction-bar-prompt").textContent).toContain(
      "sub-agent command",
    );
  });

  it("answers the AGENT approval through the existing interaction-response channel", async () => {
    const posts = stubDirectRoute();
    const { getAllByTestId } = render(<InteractionBar session={multiplexedSession()} />);
    const agentBar = getAllByTestId("interaction-bar")[1] as HTMLElement;
    fireEvent.click(within(agentBar).getAllByTestId("interaction-bar-choice")[0] as HTMLElement);
    await waitFor(() =>
      expect(within(agentBar).queryByTestId("interaction-bar-answered")).not.toBeNull(),
    );
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({
      interactionId: "ix_l7_agent",
      expectedBridgeEpoch: "ep-1",
      response: "allow",
    });
    // The parent's bar never inherited the agent's round-trip state.
    const parentBar = getAllByTestId("interaction-bar")[0] as HTMLElement;
    expect(within(parentBar).queryByTestId("interaction-bar-answered")).toBeNull();
    expect(within(parentBar).queryByTestId("interaction-bar-inflight")).toBeNull();
  });
});
