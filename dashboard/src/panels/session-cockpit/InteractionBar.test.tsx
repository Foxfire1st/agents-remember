// The InteractionBar (260715-FEUI-L6 R4/R9): kind-awareness, the gate-only answer path, and the
// full round-trip — answering… → verbatim error + retry | answered-waiting (poll-bounded).
// xterm never appears here: the bar has no terminal dependency by construction.
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { fromTerminalSessionInfo } from "../../data/sessions";
import { dashboardStore } from "../../data/store";
import type { LifecycleProjection } from "../../types/projection";
import {
  L6_INTERACTION_CHOICES,
  L6_INTERACTION_FREETEXT,
  L6_INTERACTION_UNREPRESENTABLE,
} from "../../test/fixtures/catalogRows";
import { InteractionBar } from "./InteractionBar";
import { INTERACTION_HONESTY_HINT } from "./lifecycleCopy";

function projectGate(lifecycleId: string, gateId: string, sessionId: string, interactionId: string) {
  dashboardStore.setState({
    lifecycles: {
      [lifecycleId]: {
        id: lifecycleId,
        gate: {
          id: gateId,
          kind: "agent-question",
          state: "open",
          decisions: [],
          ts: "2026-07-17T09:00:00Z",
          packet: { adapterInteraction: { sessionId, interactionId } },
        },
      } as unknown as LifecycleProjection,
    },
  });
}

const choicesSession = () => fromTerminalSessionInfo(L6_INTERACTION_CHOICES);
const freetextSession = () => fromTerminalSessionInfo(L6_INTERACTION_FREETEXT);

beforeEach(() => {
  sessionCockpitStore.setState({ perSession: {} });
  dashboardStore.setState({ lifecycles: {} });
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

  it("non-choice kinds mark the composer as the answer input (gate-routed, labeled)", () => {
    const composer = document.createElement("textarea");
    document.body.appendChild(composer);
    const ref = createRef<HTMLTextAreaElement>();
    Object.assign(ref, { current: composer });
    const { getByTestId } = render(
      <InteractionBar session={freetextSession()} composerRef={ref} />,
    );
    expect(getByTestId("interaction-bar-composer-mode").textContent).toContain("gate channel");
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
    projectGate("lc-l6-choices", "g-1", "l6-ix-choices", "ix_l6_choice");
    let release: (value: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => (release = resolve))),
    );
    const { getByTestId, getAllByTestId, queryByTestId } = render(
      <InteractionBar session={choicesSession()} />,
    );
    fireEvent.click(getAllByTestId("interaction-bar-choice")[0]);
    await waitFor(() => expect(getByTestId("interaction-bar-inflight").textContent).toContain("answering"));
    for (const button of getAllByTestId("interaction-bar-choice")) {
      expect(button.getAttribute("disabled")).not.toBeNull();
    }
    release({ status: 202, text: async () => "" } as Response);
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(getByTestId("interaction-bar-answered").textContent).toContain("waiting for the agent");
    expect(getByTestId("interaction-bar-answered").textContent).toContain("2.5");
  });

  it("POST failure renders the verbatim error and retry re-sends the SAME answer", async () => {
    projectGate("lc-l6-choices", "g-1", "l6-ix-choices", "ix_l6_choice");
    const bodies: string[] = [];
    let status = 500;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        bodies.push(String(init?.body));
        return { status, text: async () => "projection not ready" } as Response;
      }),
    );
    const { getByTestId, getAllByTestId, queryByTestId } = render(
      <InteractionBar session={choicesSession()} />,
    );
    fireEvent.click(getAllByTestId("interaction-bar-choice")[2]); // "deny"
    await waitFor(() => expect(queryByTestId("interaction-bar-error")).not.toBeNull());
    expect(getByTestId("interaction-bar-error").textContent).toContain("projection not ready");
    status = 202;
    fireEvent.click(getByTestId("interaction-bar-retry"));
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(bodies).toHaveLength(2);
    expect(JSON.parse(bodies[0])).toMatchObject({ note: "deny" });
    expect(JSON.parse(bodies[1])).toMatchObject({ note: "deny" });
  });

  it("a missing gate is stated as the poll-bounded truth, retryable — never a blind POST", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const { getAllByTestId, getByTestId, queryByTestId } = render(
      <InteractionBar session={choicesSession()} />,
    );
    fireEvent.click(getAllByTestId("interaction-bar-choice")[0]);
    await waitFor(() => expect(queryByTestId("interaction-bar-error")).not.toBeNull());
    expect(getByTestId("interaction-bar-error").textContent).toContain("poll-bounded");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("composer answer-mode routes the composer text through the gate channel, NOT /submit", async () => {
    projectGate("lc-l6-freetext", "g-2", "l6-ix-freetext", "ix_l6_text");
    const calls: Array<{ url: string; body: Record<string, string> }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), body: JSON.parse(String(init?.body)) });
        return { status: 202, text: async () => "" } as Response;
      }),
    );
    const composer = document.createElement("textarea");
    composer.value = "start from ar/base";
    document.body.appendChild(composer);
    const ref = createRef<HTMLTextAreaElement>();
    Object.assign(ref, { current: composer });
    const { getByTestId, queryByTestId } = render(
      <InteractionBar session={freetextSession()} composerRef={ref} />,
    );
    fireEvent.click(getByTestId("interaction-bar-composer-send"));
    await waitFor(() => expect(queryByTestId("interaction-bar-answered")).not.toBeNull());
    expect(calls).toEqual([
      {
        url: "/api/actions/approve",
        body: { target: "lc-l6-freetext", gateId: "g-2", note: "start from ar/base" },
      },
    ]);
    composer.remove();
  });
});

describe("stale round-trip state (review finding 5)", () => {
  it("clears a previous 'answered — waiting' before a FOLLOWING unrepresentable interaction renders", async () => {
    // The same seat answered ix_l6_choice, then the vendor replaced it with an opaque payload.
    sessionCockpitStore.getState().setInteractionAnswer("l6-ix-choices", {
      interactionId: "ix_l6_choice",
      inflight: false,
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
