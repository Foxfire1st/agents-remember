// The ModelEffortControl (260715-FEUI-L4 R1/R2/R3/R5/F16): exact-session sourcing, the
// disabled-with-verbatim-error popover, the corrected effort-menu derivation on screen, staged
// re-gating with the defaultEffort pre-highlight, the serialized pair apply, and the chips row
// carrying acceptance WORDS as text.
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sessionCockpitStore, useSessionCockpit } from "../../data/sessionCockpitStore";
import { fromTerminalSessionInfo } from "../../data/sessions";
import {
  CLAUDE_FRESH_SESSION_SNAPSHOT,
  codexLiveSessionSnapshot,
  SESSION_CAPABILITY_ERROR_BODIES,
} from "../../test/fixtures/capabilityEnvelopes";
import { catalogRow } from "../../test/fixtures/catalogRows";
import type { OpenSession } from "../../data/sessions";
import { ModelEffortControl } from "./ModelEffortControl";

const store = sessionCockpitStore;

let seatCounter = 0;
function liveSeat(overrides: Parameters<typeof catalogRow>[0] = {}): OpenSession {
  seatCounter += 1;
  return fromTerminalSessionInfo(
    catalogRow({
      id: `seat-${seatCounter}`,
      harness: "codex",
      controlState: "ready",
      status: "running",
      ...overrides,
    }),
  );
}

function Harness({ session, open = true }: { session: OpenSession; open?: boolean }) {
  const cockpit = useSessionCockpit((state) => state.perSession[session.id]);
  return (
    <ModelEffortControl session={session} cockpit={cockpit} open={open} onOpenChange={() => {}} />
  );
}

type Routes = Record<string, { status: number; body: unknown }>;
function stubFetch(routes: Routes): ReturnType<typeof vi.fn> {
  const mock = vi.fn(async (url: string) => {
    const routed = routes[url];
    if (!routed) throw new Error(`unhandled fetch: ${url}`);
    return { status: routed.status, json: async () => routed.body } as Response;
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

beforeEach(() => {
  store.setState({ focusedSessionId: null, perSession: {} });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("sourcing (R1)", () => {
  it("popover open GETs the EXACT-SESSION route — never the pre-session harness cache", async () => {
    const session = liveSeat();
    const mock = stubFetch({
      [`/api/terminal/${session.id}/capabilities`]: {
        status: 200,
        body: codexLiveSessionSnapshot("gpt-5.6-sol", "low"),
      },
    });
    const { getByTestId } = render(<Harness session={session} />);
    await waitFor(() => expect(getByTestId("model-effort-models")).toBeTruthy());
    expect(mock.mock.calls.every(([url]) => !String(url).includes("/api/harnesses/"))).toBe(true);
    // Advertised NATIVE order, hidden row excluded, effective row marked.
    const keys = [...getByTestId("model-effort-models").querySelectorAll("[data-testid^='model-option-']")].map(
      (node) => node.getAttribute("data-testid"),
    );
    expect(keys).toEqual([
      "model-option-gpt-5.6-sol",
      "model-option-gpt-5.6-terra",
      "model-option-gpt-5.6-luna",
      "model-option-gpt-5.5",
      "model-option-gpt-5.4",
      "model-option-gpt-5.4-mini",
      "model-option-gpt-5.3-codex-spark",
    ]);
    expect(getByTestId("model-option-gpt-5.6-sol").getAttribute("data-effective")).toBe("true");
  });

  it("the trigger words read the snapshot's effective pair once known", async () => {
    const session = liveSeat();
    stubFetch({
      [`/api/terminal/${session.id}/capabilities`]: {
        status: 200,
        body: codexLiveSessionSnapshot("gpt-5.6-sol", "low"),
      },
    });
    const { getByTestId } = render(<Harness session={session} />);
    await waitFor(() =>
      expect(getByTestId("model-effort-trigger-model").textContent).toBe("gpt-5.6-sol"),
    );
    expect(getByTestId("model-effort-trigger-effort").textContent).toBe("low");
    expect(getByTestId("model-effort-trigger").getAttribute("data-effective-source")).toBe("snapshot");
  });

  it("renders NOTHING for a non-harness or an ended session (controls are live-session-only)", () => {
    const plain = fromTerminalSessionInfo(
      catalogRow({ id: "plain-1", kind: "terminal", harness: undefined }),
    );
    const landed = liveSeat({ status: "landed" });
    const { queryByTestId, rerender } = render(<Harness session={plain} />);
    expect(queryByTestId("model-effort-control")).toBeNull();
    rerender(<Harness session={landed} />);
    expect(queryByTestId("model-effort-control")).toBeNull();
  });
});

describe("fetch failure (F16) + the 409 disable reason (R3)", () => {
  it("a 503 renders the control disabled with the VERBATIM error + retry — never an empty menu", async () => {
    const session = liveSeat();
    const url = `/api/terminal/${session.id}/capabilities`;
    const routes: Routes = {
      [url]: {
        status: SESSION_CAPABILITY_ERROR_BODIES.outage.httpStatus,
        body: SESSION_CAPABILITY_ERROR_BODIES.outage.body,
      },
    };
    stubFetch(routes);
    const { getByTestId, queryByTestId } = render(<Harness session={session} />);
    await waitFor(() =>
      expect(getByTestId("model-effort-error").textContent).toContain(
        "harness control socket did not answer",
      ),
    );
    expect(getByTestId("model-effort-error").textContent).toContain("control-unavailable");
    expect(queryByTestId("model-effort-models")).toBeNull();
    expect(queryByTestId("model-effort-efforts")).toBeNull();
    // Retry is a fresh GET — a success replaces the error with the real menu.
    routes[url] = { status: 200, body: codexLiveSessionSnapshot("gpt-5.6-sol", "low") };
    fireEvent.click(getByTestId("model-effort-retry"));
    await waitFor(() => expect(queryByTestId("model-effort-models")).toBeTruthy());
    expect(queryByTestId("model-effort-error")).toBeNull();
  });

  it("a 409 names 'no native protocol control endpoint' as the disable reason", async () => {
    const session = liveSeat({ controlState: "unsupported" });
    stubFetch({
      [`/api/terminal/${session.id}/capabilities`]: {
        status: SESSION_CAPABILITY_ERROR_BODIES.noNativeControl.httpStatus,
        body: SESSION_CAPABILITY_ERROR_BODIES.noNativeControl.body,
      },
    });
    const { getByTestId } = render(<Harness session={session} />);
    await waitFor(() =>
      expect(getByTestId("model-effort-error").textContent).toContain(
        "session has no native protocol control endpoint",
      ),
    );
  });
});

describe("the corrected effort menu on screen (R1)", () => {
  it("FRESH CLAUDE: null selectedEffort + omitted thought_level configOption still renders the " +
    "FULL menu with 'effort not echoed' — never 'no effort control'", async () => {
    const session = liveSeat({ harness: "claude" });
    stubFetch({
      [`/api/terminal/${session.id}/capabilities`]: { status: 200, body: CLAUDE_FRESH_SESSION_SNAPSHOT },
    });
    const { getByTestId, queryByTestId } = render(<Harness session={session} />);
    await waitFor(() => expect(getByTestId("model-effort-efforts")).toBeTruthy());
    expect(getByTestId("effort-not-echoed").textContent).toContain("effort not echoed");
    expect(queryByTestId("effort-menu-none")).toBeNull();
    const efforts = [...getByTestId("model-effort-efforts").querySelectorAll("[data-testid^='effort-option-']")];
    expect(efforts.map((node) => node.getAttribute("data-testid"))).toEqual([
      "effort-option-low",
      "effort-option-medium",
      "effort-option-high",
      "effort-option-xhigh",
      "effort-option-max",
    ]);
    expect(getByTestId("model-effort-trigger-effort").textContent).toBe("effort not echoed");
  });

  it("staging an effortless row (haiku) renders 'no effort control for this model' — never inherited", async () => {
    const session = liveSeat({ harness: "claude" });
    stubFetch({
      [`/api/terminal/${session.id}/capabilities`]: { status: 200, body: CLAUDE_FRESH_SESSION_SNAPSHOT },
    });
    const { getByTestId, queryByTestId } = render(<Harness session={session} />);
    await waitFor(() => expect(getByTestId("model-option-haiku")).toBeTruthy());
    fireEvent.click(getByTestId("model-option-haiku"));
    await waitFor(() =>
      expect(getByTestId("effort-menu-none").textContent).toBe("no effort control for this model"),
    );
    expect(queryByTestId("model-effort-efforts")).toBeNull();
  });

  it("selecting a new model RE-GATES the menu and pre-highlights THAT row's defaultEffort", async () => {
    const session = liveSeat();
    stubFetch({
      [`/api/terminal/${session.id}/capabilities`]: {
        status: 200,
        body: codexLiveSessionSnapshot("gpt-5.6-sol", "low"),
      },
    });
    const { getByTestId, queryByTestId } = render(<Harness session={session} />);
    await waitFor(() => expect(getByTestId("model-option-gpt-5.6-terra")).toBeTruthy());
    fireEvent.click(getByTestId("model-option-gpt-5.6-terra"));
    await waitFor(() =>
      expect(getByTestId("effort-option-medium").getAttribute("data-prehighlight")).toBe("true"),
    );
    // The staged row has no echoed selection: nothing is marked effective in ITS menu.
    expect(queryByTestId("effort-not-echoed")).toBeNull();
    expect(
      [...getByTestId("model-effort-efforts").querySelectorAll("[data-effective='true']")],
    ).toHaveLength(0);
  });
});

describe("apply (R2/R5)", () => {
  it("model-only staging POSTs ONE set-model — a pre-highlight is a suggestion, not a request", async () => {
    const session = liveSeat();
    const mock = stubFetch({
      [`/api/terminal/${session.id}/capabilities`]: {
        status: 200,
        body: codexLiveSessionSnapshot("gpt-5.6-sol", "low"),
      },
      [`/api/terminal/${session.id}/set-model`]: {
        status: 200,
        body: {
          ok: true,
          acceptance: "queued",
          requestedValue: "gpt-5.6-terra",
          effectiveValue: null,
          detail: null,
        },
      },
    });
    const { getByTestId } = render(<Harness session={session} />);
    await waitFor(() => expect(getByTestId("model-option-gpt-5.6-terra")).toBeTruthy());
    fireEvent.click(getByTestId("model-option-gpt-5.6-terra"));
    fireEvent.click(getByTestId("model-effort-apply"));
    await waitFor(() =>
      expect(mock.mock.calls.some(([url]) => String(url).endsWith("/set-model"))).toBe(true),
    );
    expect(mock.mock.calls.every(([url]) => !String(url).endsWith("/set-effort"))).toBe(true);
    expect(store.getState().perSession[session.id].pairChange).toBeUndefined();
  });

  it("model + effort staged runs the SERIALIZED pair flow", async () => {
    const session = liveSeat();
    stubFetch({
      [`/api/terminal/${session.id}/capabilities`]: {
        status: 200,
        body: codexLiveSessionSnapshot("gpt-5.6-sol", "low"),
      },
      [`/api/terminal/${session.id}/set-model`]: {
        status: 200,
        body: {
          ok: true,
          acceptance: "queued",
          requestedValue: "gpt-5.6-terra",
          effectiveValue: null,
          detail: null,
        },
      },
      [`/api/terminal/${session.id}/set-effort`]: {
        status: 200,
        body: { ok: true, acceptance: "queued", requestedValue: "xhigh", effectiveValue: null, detail: null },
      },
    });
    const { getByTestId } = render(<Harness session={session} />);
    await waitFor(() => expect(getByTestId("model-option-gpt-5.6-terra")).toBeTruthy());
    fireEvent.click(getByTestId("model-option-gpt-5.6-terra"));
    fireEvent.click(getByTestId("effort-option-xhigh"));
    expect(getByTestId("model-effort-apply").textContent).toBe("apply model + effort (serialized)");
    fireEvent.click(getByTestId("model-effort-apply"));
    // Both queued pendings land per kind — never clobbered (R5).
    await waitFor(() => {
      const pendings = store.getState().perSession[session.id].pendingSets;
      expect(pendings.model?.requestedValue).toBe("gpt-5.6-terra");
      expect(pendings.effort?.requestedValue).toBe("xhigh");
    });
  });

  it("keeps an explicitly staged effort in the pair when it equals the old model's effort", async () => {
    const session = liveSeat();
    const mock = stubFetch({
      [`/api/terminal/${session.id}/capabilities`]: {
        status: 200,
        body: codexLiveSessionSnapshot("gpt-5.6-sol", "high"),
      },
      [`/api/terminal/${session.id}/set-model`]: {
        status: 200,
        body: {
          ok: true,
          acceptance: "queued",
          requestedValue: "gpt-5.6-terra",
          effectiveValue: null,
          detail: null,
        },
      },
      [`/api/terminal/${session.id}/set-effort`]: {
        status: 200,
        body: {
          ok: true,
          acceptance: "queued",
          requestedValue: "high",
          effectiveValue: null,
          detail: null,
        },
      },
    });
    const { getByTestId } = render(<Harness session={session} />);
    await waitFor(() => expect(getByTestId("model-option-gpt-5.6-terra")).toBeTruthy());
    fireEvent.click(getByTestId("model-option-gpt-5.6-terra"));
    fireEvent.click(getByTestId("effort-option-high"));

    expect(getByTestId("model-effort-apply").textContent).toBe(
      "apply model + effort (serialized)",
    );
    fireEvent.click(getByTestId("model-effort-apply"));
    await waitFor(() =>
      expect(mock.mock.calls.some(([url]) => String(url).endsWith("/set-effort"))).toBe(true),
    );
    expect(store.getState().perSession[session.id].pendingSets).toMatchObject({
      model: { requestedValue: "gpt-5.6-terra" },
      effort: { requestedValue: "high" },
    });
  });
});

describe("the chips row (R2 — words as text)", () => {
  it("a queued pending renders its pinned chip with the acceptance WORD in the text", async () => {
    const session = liveSeat();
    stubFetch({
      [`/api/terminal/${session.id}/capabilities`]: {
        status: 200,
        body: codexLiveSessionSnapshot("gpt-5.6-sol", "low"),
      },
    });
    store.getState().recordPendingSet(session.id, "effort", {
      requestedValue: "xhigh",
      sentAt: 1,
      phase: "queued-awaiting-turn",
    });
    const { getByTestId } = render(<Harness session={session} open={false} />);
    const chip = getByTestId("set-chip-queued-effort");
    expect(chip.textContent).toContain("queued");
    expect(chip.textContent).toContain("applies on next turn");
    expect(chip.getAttribute("data-chip-acceptance")).toBe("queued");
  });

  it("a clamp chip persists with BOTH values and clears only on explicit acknowledgment", async () => {
    const session = liveSeat({ harness: "pi" });
    store.getState().appendSetLedger(session.id, {
      at: 1,
      kind: "effort",
      requestedValue: "max",
      result: { acceptance: "echo-verified", requestedValue: "max", effectiveValue: "high" },
      acknowledged: false,
    });
    const { getByTestId, queryByTestId } = render(<Harness session={session} open={false} />);
    expect(getByTestId("set-chip-clamp-effort").textContent).toContain(
      "requested max → effective high",
    );
    fireEvent.click(getByTestId("set-chip-ack-clamp-effort"));
    await waitFor(() => expect(queryByTestId("set-chip-clamp-effort")).toBeNull());
    expect(store.getState().perSession[session.id].setLedger[0].acknowledged).toBe(true);
  });
});
