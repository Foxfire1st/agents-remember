// The LaunchFlow jsdom matrix (260715-FEUI-L3 S2/S3/S6): dynamic-only pickers, miss/refresh
// cost-naming parity, complete-pair gating with default re-gating, advertised-order rendering,
// all four open-response paths, and the F9 unknown-outcome catalog reconciliation.
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { capabilityCatalogStore, capabilityCostNote } from "../../data/capabilityCatalog";
import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { fromTerminalSessionInfo, type OpenSession } from "../../data/sessions";
import { capabilityEnvelope } from "../../test/fixtures/capabilityEnvelopes";
import { catalogRow } from "../../test/fixtures/catalogRows";
import {
  INVALID_PARTIAL_PAIR,
  LAUNCH_CONFLICT,
  LEAF_TAKEN,
  OPENED_STARTING,
} from "../../test/fixtures/openResponses";
import { LaunchFlow } from "./LaunchFlow";

const HARNESSES = {
  harnesses: [
    { id: "claude", name: "Claude Code", detected: true, control: "starting" },
    { id: "codex", name: "Codex", detected: true, control: "starting" },
    { id: "pi", name: "Pi.dev", detected: false, control: "starting" },
  ],
};

type Router = (url: string, init?: RequestInit) => Promise<{ status: number; body: unknown }>;

function stubFetch(router: Router) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const { status, body } = await router(url, init);
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Default happy router: harness list, claude/codex envelopes, empty catalog, opened POST. */
function defaultRouter(openResponse: { status: number; body: unknown }): Router {
  return async (url) => {
    if (url === "/api/harnesses") return { status: 200, body: HARNESSES };
    if (url.startsWith("/api/harnesses/claude/capabilities")) {
      return { status: 200, body: capabilityEnvelope("claude", "hit") };
    }
    if (url.startsWith("/api/harnesses/codex/capabilities")) {
      return { status: 200, body: capabilityEnvelope("codex", "miss") };
    }
    if (url === "/api/terminal/sessions") return { status: 200, body: { sessions: [] } };
    if (url.startsWith("/api/terminal/")) return openResponse;
    throw new Error(`unrouted ${url}`);
  };
}

function renderFlow(overrides: Partial<Parameters<typeof LaunchFlow>[0]> = {}) {
  const onClose = vi.fn();
  const onFocusSession = vi.fn();
  const view = render(
    <LaunchFlow
      open
      sessions={[]}
      onClose={onClose}
      onFocusSession={onFocusSession}
      mintSessionId={() => "launch-1"}
      {...overrides}
    />,
  );
  return { ...view, onClose, onFocusSession };
}

beforeEach(() => {
  capabilityCatalogStore.setState({ perHarness: {} });
  sessionCockpitStore.setState({ perSession: {} });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("LaunchFlow — dynamic-only pickers (R1/R4)", () => {
  it("renders NO model/effort options before the daemon answers; populates from the envelope only", async () => {
    let releaseCapabilities: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      releaseCapabilities = resolve;
    });
    stubFetch(async (url) => {
      if (url === "/api/harnesses") return { status: 200, body: HARNESSES };
      if (url.startsWith("/api/harnesses/claude/capabilities")) {
        await gate;
        return { status: 200, body: capabilityEnvelope("claude", "hit") };
      }
      throw new Error(`unrouted ${url}`);
    });
    const { findByTestId, queryByTestId, getByTestId } = renderFlow();
    fireEvent.click(await findByTestId("launch-harness-claude"));
    // pending: cost-named loading, ZERO options — nothing is invented client-side
    await findByTestId("launch-cap-loading");
    expect(queryByTestId("launch-model-list")).toBeNull();
    expect(getByTestId("launch-cap-loading").textContent).toContain(capabilityCostNote("claude"));
    releaseCapabilities?.();
    const list = await findByTestId("launch-model-list");
    const keys = [...list.querySelectorAll("[data-testid^='launch-model-']")].map((node) =>
      node.getAttribute("data-testid"),
    );
    expect(keys).toEqual([
      "launch-model-default",
      "launch-model-opus[1m]",
      "launch-model-claude-fable-5[1m]",
      "launch-model-sonnet",
      "launch-model-haiku",
    ]);
  });

  it("an undetected harness is disabled; the adapter status renders VISIBLY (review finding 6)", async () => {
    stubFetch(defaultRouter({ status: 200, body: OPENED_STARTING }));
    const { findByTestId } = renderFlow();
    const pi = await findByTestId("launch-harness-pi");
    expect(pi.hasAttribute("disabled")).toBe(true);
    // R4 "detected + adapter status": the server's control word is visible text, not hover-only.
    expect(pi.textContent).toContain("adapter starting");
    expect((await findByTestId("launch-harness-claude")).textContent).toContain(
      "adapter starting",
    );
  });

  it("a hidden model row never renders; the rest keep advertised order", async () => {
    stubFetch(defaultRouter({ status: 200, body: OPENED_STARTING }));
    const { findByTestId, queryByTestId } = renderFlow();
    fireEvent.click(await findByTestId("launch-harness-codex"));
    await findByTestId("launch-model-list");
    expect(queryByTestId("launch-model-codex-auto-review")).toBeNull();
    expect(queryByTestId("launch-model-gpt-5.6-sol")).not.toBeNull();
  });

  it("capability-route errors render the VERBATIM status+detail with a retry (never an empty menu)", async () => {
    let failures = 0;
    stubFetch(async (url) => {
      if (url === "/api/harnesses") return { status: 200, body: HARNESSES };
      if (url.startsWith("/api/harnesses/claude/capabilities")) {
        failures += 1;
        if (failures === 1) {
          return {
            status: 503,
            body: { status: "control-unavailable", detail: "discovery quarantined after a failed refresh" },
          };
        }
        return { status: 200, body: capabilityEnvelope("claude", "miss") };
      }
      throw new Error(`unrouted ${url}`);
    });
    const { findByTestId } = renderFlow();
    fireEvent.click(await findByTestId("launch-harness-claude"));
    const error = await findByTestId("launch-cap-error");
    expect(error.textContent).toBe(
      "control-unavailable: discovery quarantined after a failed refresh",
    );
    fireEvent.click(await findByTestId("launch-cap-retry"));
    await findByTestId("launch-model-list");
  });
});

describe("LaunchFlow — cost honesty (R2)", () => {
  it("miss-loading and explicit refresh show the SAME cost naming", async () => {
    stubFetch(defaultRouter({ status: 200, body: OPENED_STARTING }));
    const { findByTestId } = renderFlow();
    fireEvent.click(await findByTestId("launch-harness-claude"));
    await findByTestId("launch-model-list");
    const refreshButton = await findByTestId("launch-cap-refresh");
    // the refresh affordance carries the same generic cost note the loading state used
    expect(refreshButton.getAttribute("title")).toBe(capabilityCostNote("claude"));
    fireEvent.click(refreshButton);
    const loading = await findByTestId("launch-cap-loading");
    expect(loading.textContent).toContain(capabilityCostNote("claude"));
    await findByTestId("launch-model-list");
  });

  it("the loaded state names the cache truth (hit vs miss-cost parity wording)", async () => {
    stubFetch(defaultRouter({ status: 200, body: OPENED_STARTING }));
    const { findByTestId } = renderFlow();
    fireEvent.click(await findByTestId("launch-harness-codex"));
    const note = await findByTestId("launch-cache-status");
    expect(note.textContent).toContain("cache miss");
    expect(note.textContent).toContain("same short-lived native discovery as a refresh");
  });
});

describe("LaunchFlow — complete-pair rules (R4)", () => {
  it("selecting a model re-gates effort to ITS advertised default; switching re-gates again", async () => {
    stubFetch(defaultRouter({ status: 200, body: OPENED_STARTING }));
    const { findByTestId } = renderFlow();
    fireEvent.click(await findByTestId("launch-harness-codex"));
    fireEvent.click(await findByTestId("launch-model-gpt-5.6-sol"));
    expect((await findByTestId("launch-effort-low")).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(await findByTestId("launch-model-gpt-5.3-codex-spark"));
    expect((await findByTestId("launch-effort-high")).getAttribute("aria-pressed")).toBe("true");
  });

  it("a model with no advertised launch default demands an explicit effort choice", async () => {
    stubFetch(defaultRouter({ status: 200, body: OPENED_STARTING }));
    const { findByTestId, getByTestId } = renderFlow();
    fireEvent.click(await findByTestId("launch-harness-claude"));
    fireEvent.click(await findByTestId("launch-model-sonnet"));
    await findByTestId("launch-effort-choose");
    expect(getByTestId("launch-submit").hasAttribute("disabled")).toBe(true);
    fireEvent.click(getByTestId("launch-effort-high"));
    expect(getByTestId("launch-submit").hasAttribute("disabled")).toBe(false);
  });

  it("efforts render in advertised native order with no reordering", async () => {
    stubFetch(defaultRouter({ status: 200, body: OPENED_STARTING }));
    const { findByTestId } = renderFlow();
    fireEvent.click(await findByTestId("launch-harness-codex"));
    fireEvent.click(await findByTestId("launch-model-gpt-5.6-sol"));
    const list = await findByTestId("launch-effort-list");
    const keys = [...list.querySelectorAll("[data-testid^='launch-effort-']")].map((node) =>
      node.textContent,
    );
    expect(keys).toEqual(["low", "medium", "high", "xhigh", "max", "ultra"]);
  });

  it("the effortless Haiku row cannot form a pair — the flow says so and stays disabled", async () => {
    stubFetch(defaultRouter({ status: 200, body: OPENED_STARTING }));
    const { findByTestId, getByTestId } = renderFlow();
    fireEvent.click(await findByTestId("launch-harness-claude"));
    fireEvent.click(await findByTestId("launch-model-haiku"));
    const note = await findByTestId("launch-effort-none");
    expect(note.textContent).toContain("no launch-settable efforts");
    expect(getByTestId("launch-submit").hasAttribute("disabled")).toBe(true);
  });

  it("vendor defaults sends NEITHER knob on the wire", async () => {
    const fetchMock = stubFetch(defaultRouter({ status: 200, body: OPENED_STARTING }));
    const { findByTestId, getByTestId, onFocusSession } = renderFlow();
    fireEvent.click(await findByTestId("launch-harness-claude"));
    fireEvent.click(await findByTestId("launch-vendor-defaults"));
    fireEvent.click(getByTestId("launch-submit"));
    await waitFor(() => expect(onFocusSession).toHaveBeenCalled());
    const openCall = fetchMock.mock.calls.find(
      ([url, init]) => (url as string).startsWith("/api/terminal/launch-1") && init,
    );
    const body = JSON.parse((openCall?.[1] as RequestInit).body as string) as Record<string, unknown>;
    expect("model" in body).toBe(false);
    expect("effort" in body).toBe(false);
  });
});

describe("LaunchFlow — response paths (R5)", () => {
  async function launchClaudePair(view: ReturnType<typeof renderFlow>) {
    fireEvent.click(await view.findByTestId("launch-harness-claude"));
    fireEvent.click(await view.findByTestId("launch-model-claude-fable-5[1m]"));
    fireEvent.click(await view.findByTestId("launch-effort-max"));
    fireEvent.click(view.getByTestId("launch-submit"));
  }

  it("200 → retained pair recorded at tier 'pending', new session focused, flow closed", async () => {
    stubFetch(defaultRouter({ status: 200, body: OPENED_STARTING }));
    const view = renderFlow();
    await launchClaudePair(view);
    await waitFor(() => expect(view.onFocusSession).toHaveBeenCalledWith("launch-1"));
    expect(view.onClose).toHaveBeenCalled();
    expect(sessionCockpitStore.getState().perSession["launch-1"].launchEvidence).toEqual({
      retainedModel: "claude-fable-5[1m]",
      retainedEffort: "max",
      tier: "pending",
    });
  });

  it("400 launch-selection-invalid → the verbatim detail, nothing retried", async () => {
    stubFetch(defaultRouter({ status: 400, body: INVALID_PARTIAL_PAIR }));
    const view = renderFlow();
    await launchClaudePair(view);
    const outcome = await view.findByTestId("launch-outcome-invalid");
    expect(outcome.textContent).toContain("model and effort must be provided together");
    expect(view.onClose).not.toHaveBeenCalled();
  });

  it("409 leaf-taken → names the owning session and offers focus", async () => {
    stubFetch(defaultRouter({ status: 409, body: LEAF_TAKEN }));
    const view = renderFlow();
    await launchClaudePair(view);
    const outcome = await view.findByTestId("launch-outcome-leaf-taken");
    expect(outcome.textContent).toContain("worker-l3-live");
    fireEvent.click(view.getByTestId("launch-focus-owner"));
    expect(view.onFocusSession).toHaveBeenCalledWith("worker-l3-live");
  });

  it("409 conflict → live retained pair vs attempted pair + 'focus existing session'; no evidence rewritten", async () => {
    stubFetch(defaultRouter({ status: 409, body: LAUNCH_CONFLICT }));
    const view = renderFlow();
    fireEvent.click(await view.findByTestId("launch-harness-claude"));
    fireEvent.click(await view.findByTestId("launch-model-sonnet"));
    fireEvent.click(await view.findByTestId("launch-effort-high"));
    fireEvent.click(view.getByTestId("launch-submit"));
    const pairs = await view.findByTestId("launch-conflict-pairs");
    expect(pairs.textContent).toContain("live retained pair: claude-fable-5[1m] · max");
    expect(pairs.textContent).toContain("attempted: sonnet · high");
    // provenance untouched: the flow wrote NO launch evidence for the live session
    expect(sessionCockpitStore.getState().perSession["launch-1"]).toBeUndefined();
    fireEvent.click(view.getByTestId("launch-focus-existing"));
    expect(view.onFocusSession).toHaveBeenCalledWith("launch-1");
  });

  it("transport loss → 'open outcome unknown — checking the catalog', resolved by row appearance (F9)", async () => {
    stubFetch(async (url) => {
      if (url === "/api/harnesses") return { status: 200, body: HARNESSES };
      if (url.startsWith("/api/harnesses/claude/capabilities")) {
        return { status: 200, body: capabilityEnvelope("claude", "hit") };
      }
      if (url.startsWith("/api/terminal/launch-1")) throw new Error("socket hang up");
      throw new Error(`unrouted ${url}`);
    });
    const view = renderFlow();
    await launchClaudePair(view);
    const unknown = await view.findByTestId("launch-outcome-unknown");
    expect(unknown.textContent).toContain("open outcome unknown — checking the catalog");
    expect(view.onClose).not.toHaveBeenCalled();
    // the caller-minted id appears on the next catalog beat → the flow resolves WITHOUT re-POST
    const appeared: OpenSession[] = [
      fromTerminalSessionInfo(catalogRow({ id: "launch-1", controlState: "starting" })),
    ];
    view.rerender(
      <LaunchFlow
        open
        sessions={appeared}
        onClose={view.onClose}
        onFocusSession={view.onFocusSession}
        mintSessionId={() => "launch-1"}
      />,
    );
    await waitFor(() => expect(view.onFocusSession).toHaveBeenCalledWith("launch-1"));
    expect(view.onClose).toHaveBeenCalled();
  });

  it("an explicit dismiss ENDS the unknown-outcome watch — a late row never steals focus (review finding 1)", async () => {
    stubFetch(async (url) => {
      if (url === "/api/harnesses") return { status: 200, body: HARNESSES };
      if (url.startsWith("/api/harnesses/claude/capabilities")) {
        return { status: 200, body: capabilityEnvelope("claude", "hit") };
      }
      if (url.startsWith("/api/terminal/launch-1")) throw new Error("socket hang up");
      throw new Error(`unrouted ${url}`);
    });
    const view = renderFlow();
    await launchClaudePair(view);
    await view.findByTestId("launch-outcome-unknown");
    // the operator dismisses; the parent closes the dialog (it stays mounted, open=false)
    fireEvent.click(view.getByTestId("launch-cancel"));
    expect(view.onClose).toHaveBeenCalledTimes(1);
    view.rerender(
      <LaunchFlow
        open={false}
        sessions={[]}
        onClose={view.onClose}
        onFocusSession={view.onFocusSession}
        mintSessionId={() => "launch-1"}
      />,
    );
    // minutes later the daemon-processed row surfaces on a catalog beat…
    const late: OpenSession[] = [
      fromTerminalSessionInfo(catalogRow({ id: "launch-1", controlState: "starting" })),
    ];
    view.rerender(
      <LaunchFlow
        open={false}
        sessions={late}
        onClose={view.onClose}
        onFocusSession={view.onFocusSession}
        mintSessionId={() => "launch-1"}
      />,
    );
    // …and NOTHING fires: no focus steal, no extra close. The rail's poll shows the row.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(view.onFocusSession).not.toHaveBeenCalled();
    expect(view.onClose).toHaveBeenCalledTimes(1);
  });

  it("REOPEN after a dismissed unknown outcome starts clean — the stale id never fires (delta-verify residual)", async () => {
    stubFetch(async (url) => {
      if (url === "/api/harnesses") return { status: 200, body: HARNESSES };
      if (url.startsWith("/api/harnesses/claude/capabilities")) {
        return { status: 200, body: capabilityEnvelope("claude", "hit") };
      }
      if (url.startsWith("/api/terminal/launch-1")) throw new Error("socket hang up");
      throw new Error(`unrouted ${url}`);
    });
    const view = renderFlow();
    await launchClaudePair(view);
    await view.findByTestId("launch-outcome-unknown");
    fireEvent.click(view.getByTestId("launch-cancel")); // dismiss clears the watch state itself
    expect(view.onClose).toHaveBeenCalledTimes(1);
    // the daemon-processed row lands WHILE the dialog is closed…
    const late: OpenSession[] = [
      fromTerminalSessionInfo(catalogRow({ id: "launch-1", controlState: "starting" })),
    ];
    view.rerender(
      <LaunchFlow
        open={false}
        sessions={late}
        onClose={view.onClose}
        onFocusSession={view.onFocusSession}
        mintSessionId={() => "launch-1"}
      />,
    );
    // …then the operator REOPENS the flow: the first effect pass must NOT fire the stale id
    // (before this fix, the watcher ran once with the surviving unknownId before the reset).
    view.rerender(
      <LaunchFlow
        open
        sessions={late}
        onClose={view.onClose}
        onFocusSession={view.onFocusSession}
        mintSessionId={() => "launch-1"}
      />,
    );
    await view.findByTestId("launch-flow"); // fully reopened
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(view.onFocusSession).not.toHaveBeenCalled();
    expect(view.onClose).toHaveBeenCalledTimes(1); // no phantom close either
  });
});
