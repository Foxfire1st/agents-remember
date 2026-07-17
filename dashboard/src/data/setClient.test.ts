import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { catalogRow } from "../test/fixtures/catalogRows";
import {
  codexLiveSessionSnapshot,
  QUEUED_THEN_IMMEDIATE_SEQUENCE,
  SESSION_CAPABILITY_ERROR_BODIES,
  SET_RESULT_CLAMP,
  SET_RESULTS,
  UNKNOWN_THEN_READBACK,
} from "../test/fixtures/capabilityEnvelopes";
import type { CapabilitySnapshotWire, SetResultWire } from "../types/harnessCapabilities";
import { announcerStore } from "./announcer";
import { sessionCockpitStore } from "./sessionCockpitStore";
import { fromTerminalSessionInfo, sessionStore } from "./sessions";
import { deriveSetChips } from "./setChips";
import {
  cycleEffortRequested,
  refreshSessionSnapshot,
  sendSet,
  startPairChangeFlow,
  startSetPromotionWatcher,
} from "./setClient";
import {
  cycleNoSnapshotAnnouncement,
  promotionAnnouncement,
  readbackKeptPriorAnnouncement,
} from "./setControlsCopy";

// The set-controls driver against a mocked wire (260715-FEUI-L4 S1/S3/S4): exact URLs, the
// honesty table applied to the store, the ONE automatic unknown re-GET, queued promotion on
// turn-ended readback, the serialized pair flow, and the drift/focus re-GET triggers.

const store = sessionCockpitStore;
const per = (id: string) => store.getState().perSession[id];

type Handler = (url: string, init?: RequestInit) => { status: number; body: unknown } | undefined;

function stubFetch(handler: Handler): ReturnType<typeof vi.fn> {
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    const routed = handler(url, init);
    if (!routed) throw new Error(`unhandled fetch: ${url}`);
    return { status: routed.status, json: async () => routed.body } as Response;
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

const capabilitiesUrl = (id: string) => `/api/terminal/${id}/capabilities`;

function respond(routes: Record<string, { status: number; body: unknown }>): Handler {
  return (url) => routes[url];
}

beforeEach(() => {
  store.setState({ focusedSessionId: null, perSession: {} });
  sessionStore.getState().hydrate([]);
  announcerStore.setState({ polite: { text: "", seq: 0 }, assertive: { text: "", seq: 0 } });
});

afterEach(() => vi.unstubAllGlobals());

describe("sendSet — wire + honesty table application", () => {
  it("POSTs the exact set-model route and applies echo-verified: pending cleared, echo evidence " +
    "recorded, ledger acknowledged", async () => {
    const result: SetResultWire = {
      ok: true,
      acceptance: "echo-verified",
      requestedValue: "claude-fable-5[1m]",
      effectiveValue: "claude-fable-5[1m]",
      detail: null,
    };
    const mock = stubFetch(respond({ "/api/terminal/s1/set-model": { status: 200, body: result } }));
    await sendSet("s1", "model", "claude-fable-5[1m]");
    expect(mock).toHaveBeenCalledTimes(1);
    const [url, init] = mock.mock.calls[0];
    expect(url).toBe("/api/terminal/s1/set-model");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ model: "claude-fable-5[1m]" });
    expect(per("s1").pendingSets.model).toBeUndefined();
    expect(per("s1").echoEvidence.model?.value).toBe("claude-fable-5[1m]");
    expect(per("s1").setLedger).toEqual([
      expect.objectContaining({ kind: "model", acknowledged: true }),
    ]);
  });

  it("a CLAMP ledgers unacknowledged and records the CLAMPED effective value (never the request)", async () => {
    stubFetch(respond({ "/api/terminal/s1/set-effort": { status: 200, body: SET_RESULT_CLAMP } }));
    await sendSet("s1", "effort", "max");
    expect(per("s1").echoEvidence.effort?.value).toBe("high");
    expect(per("s1").setLedger[0]).toMatchObject({
      acknowledged: false,
      result: { acceptance: "echo-verified", requestedValue: "max", effectiveValue: "high" },
    });
  });

  it("queued pins the pending (marker untouched) with an acknowledged ledger entry", async () => {
    stubFetch(respond({ "/api/terminal/s1/set-effort": { status: 200, body: SET_RESULTS.queued } }));
    await sendSet("s1", "effort", "xhigh");
    expect(per("s1").pendingSets.effort).toMatchObject({
      requestedValue: "xhigh",
      phase: "queued-awaiting-turn",
    });
    expect(per("s1").echoEvidence.effort).toBeUndefined();
    expect(per("s1").setLedger[0].acknowledged).toBe(true);
  });

  it("unknown triggers EXACTLY ONE automatic exact-session re-GET and keeps the badge", async () => {
    const mock = stubFetch(
      respond({
        "/api/terminal/s1/set-effort": { status: 200, body: SET_RESULTS.unknown },
        [capabilitiesUrl("s1")]: {
          status: 200,
          body: UNKNOWN_THEN_READBACK.disprovingSnapshot,
        },
      }),
    );
    await sendSet("s1", "effort", "high");
    // The disproving readback resolved the uncertainty DEFINITIVELY: pending cleared, the
    // unknown ledger entry still unacknowledged (it demands attention), marker = snapshot truth.
    await vi.waitFor(() => expect(per("s1").pendingSets.effort).toBeUndefined());
    expect(per("s1").setLedger[0]).toMatchObject({ acknowledged: false, result: { acceptance: "unknown" } });
    expect(per("s1").liveSnapshot?.payload.selectedEffort).toBe("low");
    // No further automatic GETs happen (no blind retry).
    expect(mock.mock.calls.filter(([url]) => url === capabilitiesUrl("s1"))).toHaveLength(1);
  });

  it("an unknown CONFIRMED by its one readback acknowledges its own ledger entry", async () => {
    stubFetch(
      respond({
        "/api/terminal/s1/set-effort": { status: 200, body: SET_RESULTS.unknown },
        [capabilitiesUrl("s1")]: { status: 200, body: UNKNOWN_THEN_READBACK.confirmingSnapshot },
      }),
    );
    await sendSet("s1", "effort", "high");
    await vi.waitFor(() => expect(per("s1").pendingSets.effort).toBeUndefined());
    expect(per("s1").setLedger[0].acknowledged).toBe(true);
    expect(per("s1").liveSnapshot?.payload.selectedEffort).toBe("high");
  });

  it("unsupported reverts to prior effective (nothing moves) with the verbatim detail, unacked", async () => {
    stubFetch(respond({ "/api/terminal/s1/set-model": { status: 200, body: SET_RESULTS.unsupported } }));
    await sendSet("s1", "model", "ar-unknown-model");
    expect(per("s1").pendingSets.model).toBeUndefined();
    expect(per("s1").echoEvidence.model).toBeUndefined();
    expect(per("s1").setLedger[0]).toMatchObject({
      acknowledged: false,
      result: { detail: "requested model is absent from the dynamic catalog" },
    });
  });

  it("HTTP 503 is a ROUTE error with a retry affordance — never a ledger entry", async () => {
    stubFetch(
      respond({
        "/api/terminal/s1/set-effort": {
          status: 503,
          body: { status: "control-unavailable", detail: "control socket refused" },
        },
      }),
    );
    await sendSet("s1", "effort", "high");
    expect(per("s1").setLedger).toEqual([]);
    expect(per("s1").pendingSets.effort).toBeUndefined();
    expect(per("s1").setRouteError).toMatchObject({
      kind: "effort",
      requestedValue: "high",
      httpStatus: 503,
      retryable: true,
      detail: "control socket refused",
    });
  });

  it("HTTP 409 disables with the server's no-native-control reason (not retryable)", async () => {
    stubFetch(
      respond({
        "/api/terminal/s1/set-model": {
          status: 409,
          body: SESSION_CAPABILITY_ERROR_BODIES.noNativeControl.body,
        },
      }),
    );
    await sendSet("s1", "model", "m");
    expect(per("s1").setRouteError).toMatchObject({
      httpStatus: 409,
      retryable: false,
      detail: "session has no native protocol control endpoint",
    });
  });

  it("SUPERSEDE GUARD: an older response never clears a newer request's pending (both ledgered)", async () => {
    let resolveFirst: (value: Response) => void = () => {};
    const first = new Promise<Response>((resolve) => {
      resolveFirst = resolve;
    });
    const mock = vi
      .fn()
      // Default: the older result's automatic unknown re-GET gets an unrelated snapshot.
      .mockImplementation(async () => ({
        status: 200,
        json: async () => codexLiveSessionSnapshot("gpt-5.6-sol", "low"),
      }) as Response)
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce({ status: 200, json: async () => SET_RESULTS.queued } as Response);
    vi.stubGlobal("fetch", mock);
    const older = sendSet("s1", "effort", "medium");
    const newer = sendSet("s1", "effort", "xhigh"); // replaces the pending
    await newer;
    expect(per("s1").pendingSets.effort).toMatchObject({ requestedValue: "xhigh", phase: "queued-awaiting-turn" });
    resolveFirst({
      status: 200,
      json: async () => ({ ...SET_RESULTS.unknown, requestedValue: "medium" }),
    } as Response);
    await older;
    // The older 'unknown' answer is ledgered but the NEWER pending survives untouched.
    expect(per("s1").pendingSets.effort).toMatchObject({ requestedValue: "xhigh", phase: "queued-awaiting-turn" });
    expect(per("s1").setLedger).toHaveLength(2);
  });

  it("announces politely ONLY for the focused session (R8)", async () => {
    stubFetch(respond({ "/api/terminal/s1/set-effort": { status: 200, body: SET_RESULTS.queued } }));
    await sendSet("s1", "effort", "xhigh");
    expect(announcerStore.getState().polite.seq).toBe(0);
    store.getState().setFocusedSession("s1");
    stubFetch(respond({ "/api/terminal/s1/set-effort": { status: 200, body: SET_RESULTS.queued } }));
    await sendSet("s1", "effort", "xhigh");
    expect(announcerStore.getState().polite.text).toBe("effort xhigh queued — applies on next turn");
  });
});

describe("refreshSessionSnapshot (R1/F16)", () => {
  it("mirrors a snapshot whole and clears any prior error", async () => {
    stubFetch(
      respond({ [capabilitiesUrl("s1")]: { status: 200, body: codexLiveSessionSnapshot("gpt-5.6-sol", "low") } }),
    );
    store.getState().setSnapshotError("s1", {
      httpStatus: 503,
      status: "control-unavailable",
      detail: "old outage",
      at: 1,
    });
    await refreshSessionSnapshot("s1");
    expect(per("s1").liveSnapshot?.payload.selectedModelKey).toBe("gpt-5.6-sol");
    expect(per("s1").snapshotError).toBeUndefined();
    expect(per("s1").snapshotLoading).toBe(false);
  });

  it("keeps the verbatim error per HTTP path (404/409/503) — F16's disable reason", async () => {
    for (const [name, { httpStatus, body }] of Object.entries(SESSION_CAPABILITY_ERROR_BODIES)) {
      store.setState({ perSession: {} });
      stubFetch(respond({ [capabilitiesUrl("s1")]: { status: httpStatus, body } }));
      await refreshSessionSnapshot("s1");
      expect(per("s1").snapshotError, name).toMatchObject({ httpStatus });
      if ("detail" in body) expect(per("s1").snapshotError?.detail).toBe(body.detail);
      expect(per("s1").liveSnapshot).toBeUndefined();
    }
  });

  it("single-flight: concurrent refreshes share one GET", async () => {
    const mock = stubFetch(
      respond({ [capabilitiesUrl("s1")]: { status: 200, body: codexLiveSessionSnapshot("gpt-5.6-sol", "low") } }),
    );
    await Promise.all([refreshSessionSnapshot("s1"), refreshSessionSnapshot("s1")]);
    expect(mock).toHaveBeenCalledTimes(1);
  });
});

describe("queued promotion by readback (R4)", () => {
  it("a confirming readback resolves the queued pending and announces the promotion (focused)", async () => {
    store.getState().setFocusedSession("s1");
    stubFetch(respond({ "/api/terminal/s1/set-effort": { status: 200, body: SET_RESULTS.queued } }));
    await sendSet("s1", "effort", "xhigh");
    stubFetch(
      respond({ [capabilitiesUrl("s1")]: { status: 200, body: codexLiveSessionSnapshot("gpt-5.6-sol", "xhigh") } }),
    );
    await refreshSessionSnapshot("s1");
    expect(per("s1").pendingSets.effort).toBeUndefined();
    expect(announcerStore.getState().polite.text).toBe(promotionAnnouncement("effort", "xhigh"));
  });

  it("an unconfirming readback keeps the queued pending pinned (Codex promotes on an ACCEPTED turn)", async () => {
    stubFetch(respond({ "/api/terminal/s1/set-effort": { status: 200, body: SET_RESULTS.queued } }));
    await sendSet("s1", "effort", "xhigh");
    stubFetch(
      respond({ [capabilitiesUrl("s1")]: { status: 200, body: codexLiveSessionSnapshot("gpt-5.6-sol", "low") } }),
    );
    await refreshSessionSnapshot("s1");
    expect(per("s1").pendingSets.effort).toMatchObject({ phase: "queued-awaiting-turn" });
  });

  it("CODEX BOTH-QUEUED pair resolves together on ONE readback", async () => {
    stubFetch(
      respond({
        "/api/terminal/s1/set-model": {
          status: 200,
          body: { ...SET_RESULTS.queued, requestedValue: "gpt-5.6-terra" },
        },
        "/api/terminal/s1/set-effort": {
          status: 200,
          body: { ...SET_RESULTS.queued, requestedValue: "xhigh" },
        },
      }),
    );
    await sendSet("s1", "model", "gpt-5.6-terra");
    await sendSet("s1", "effort", "xhigh");
    expect(per("s1").pendingSets.model?.phase).toBe("queued-awaiting-turn");
    expect(per("s1").pendingSets.effort?.phase).toBe("queued-awaiting-turn");
    stubFetch(
      respond({ [capabilitiesUrl("s1")]: { status: 200, body: codexLiveSessionSnapshot("gpt-5.6-terra", "xhigh") } }),
    );
    await refreshSessionSnapshot("s1");
    expect(per("s1").pendingSets.model).toBeUndefined();
    expect(per("s1").pendingSets.effort).toBeUndefined();
  });

  it("a REPEAT-SET returning 'immediate' also resolves the queued pending (R4)", async () => {
    const [queued, immediate] = QUEUED_THEN_IMMEDIATE_SEQUENCE;
    stubFetch(respond({ "/api/terminal/s1/set-effort": { status: 200, body: queued } }));
    await sendSet("s1", "effort", "xhigh");
    expect(per("s1").pendingSets.effort?.phase).toBe("queued-awaiting-turn");
    stubFetch(respond({ "/api/terminal/s1/set-effort": { status: 200, body: immediate } }));
    await sendSet("s1", "effort", "xhigh");
    expect(per("s1").pendingSets.effort).toBeUndefined();
  });

  it("a not-applied unknown announces that the prior value was kept (focused)", async () => {
    store.getState().setFocusedSession("s1");
    stubFetch(
      respond({
        "/api/terminal/s1/set-effort": { status: 200, body: SET_RESULTS.unknown },
        [capabilitiesUrl("s1")]: { status: 200, body: UNKNOWN_THEN_READBACK.disprovingSnapshot },
      }),
    );
    await sendSet("s1", "effort", "high");
    await vi.waitFor(() =>
      expect(announcerStore.getState().polite.text).toBe(readbackKeptPriorAnnouncement("effort", "high")),
    );
  });
});

describe("serialized pair change (R5)", () => {
  it("model evidence gates the effort POST; both SetResults land in the ledger", async () => {
    const calls: string[] = [];
    const mock = vi.fn(async (url: string) => {
      calls.push(url);
      if (url === "/api/terminal/s1/set-model") {
        return {
          status: 200,
          json: async () => ({
            ok: true,
            acceptance: "echo-verified",
            requestedValue: "gpt-5.6-terra",
            effectiveValue: "gpt-5.6-terra",
            detail: null,
          }),
        } as Response;
      }
      if (url === "/api/terminal/s1/set-effort") {
        // Serialization: by the time the effort POST goes out, the model result was applied.
        expect(per("s1").setLedger.some((entry) => entry.kind === "model")).toBe(true);
        return {
          status: 200,
          json: async () => ({
            ok: true,
            acceptance: "echo-verified",
            requestedValue: "xhigh",
            effectiveValue: "xhigh",
            detail: null,
          }),
        } as Response;
      }
      throw new Error(`unhandled ${url}`);
    });
    vi.stubGlobal("fetch", mock);
    startPairChangeFlow("s1", "gpt-5.6-terra", "xhigh");
    await vi.waitFor(() => expect(per("s1").setLedger).toHaveLength(2));
    expect(calls).toEqual(["/api/terminal/s1/set-model", "/api/terminal/s1/set-effort"]);
    // Completed cleanly: the pair banner clears; evidence stands on its own.
    expect(per("s1").pairChange).toBeUndefined();
  });

  it("model unsupported ABORTS the pair — the effort POST is never sent", async () => {
    const mock = stubFetch(
      respond({ "/api/terminal/s1/set-model": { status: 200, body: SET_RESULTS.unsupported } }),
    );
    startPairChangeFlow("s1", "ar-unknown-model", "xhigh");
    await vi.waitFor(() => expect(per("s1").pairChange?.outcome?.kind).toBe("aborted"));
    expect(mock.mock.calls.every(([url]) => url !== "/api/terminal/s1/set-effort")).toBe(true);
    expect(per("s1").setLedger).toHaveLength(1);
  });

  it("a model route error aborts the pair and leaves no in-flight pair spinner", async () => {
    const mock = stubFetch(
      respond({
        "/api/terminal/s1/set-model": {
          status: 404,
          body: { status: "unknown-session", detail: "session not found" },
        },
      }),
    );
    startPairChangeFlow("s1", "gpt-5.6-terra", "xhigh");
    await vi.waitFor(() => expect(per("s1").setRouteError?.httpStatus).toBe(404));

    expect(per("s1").pairChange?.outcome).toMatchObject({ kind: "aborted" });
    const chips = deriveSetChips(per("s1"));
    expect(chips).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "pair-aborted", spinner: false }),
        expect.objectContaining({ id: "route-model", spinner: false }),
      ]),
    );
    expect(chips.some((chip) => chip.spinner)).toBe(false);
    expect(mock.mock.calls.every(([url]) => url !== "/api/terminal/s1/set-effort")).toBe(true);
  });

  it("a thrown model fetch stops the pair without fabricating an effectiveness verdict", async () => {
    const mock = vi.fn(async () => {
      throw new Error("connection dropped after the POST left the browser");
    });
    vi.stubGlobal("fetch", mock);

    startPairChangeFlow("s1", "gpt-5.6-terra", "xhigh");
    await vi.waitFor(() => expect(per("s1").setRouteError?.status).toBe("transport"));

    const chips = deriveSetChips(per("s1"));
    const pairChip = chips.find((chip) => chip.id === "pair-aborted");
    expect(pairChip).toMatchObject({ spinner: false });
    expect(pairChip?.text).toContain("effective outcome is unknown");
    expect(chips).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "route-model", spinner: false }),
      ]),
    );
    expect(chips.some((chip) => chip.spinner)).toBe(false);
    expect(chips.map((chip) => chip.text).join(" ")).not.toMatch(
      /nothing changed|model switched|refused/,
    );
    expect(mock).toHaveBeenCalledTimes(1);
  });

  it("a malformed effort response stops after model evidence with unknown effort effectiveness", async () => {
    const mock = vi.fn(async (url: string) => {
      if (url === "/api/terminal/s1/set-model") {
        return {
          status: 200,
          json: async () => ({
            ok: true,
            acceptance: "echo-verified",
            requestedValue: "gpt-5.6-terra",
            effectiveValue: "gpt-5.6-terra",
            detail: null,
          }),
        } as Response;
      }
      if (url === "/api/terminal/s1/set-effort") {
        return { status: 200, json: async () => ({ ok: true }) } as Response;
      }
      throw new Error(`unhandled ${url}`);
    });
    vi.stubGlobal("fetch", mock);

    startPairChangeFlow("s1", "gpt-5.6-terra", "xhigh");
    await vi.waitFor(() => expect(per("s1").setRouteError?.kind).toBe("effort"));

    expect(per("s1").setLedger.map((entry) => entry.kind)).toEqual(["model"]);
    const chips = deriveSetChips(per("s1"));
    const pairChip = chips.find((chip) => chip.id === "pair-partial");
    expect(pairChip).toMatchObject({ spinner: false });
    expect(pairChip?.text).toContain("effective outcome is unknown");
    expect(chips).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "route-effort", spinner: false }),
      ]),
    );
    expect(chips.some((chip) => chip.spinner)).toBe(false);
    expect(chips.map((chip) => chip.text).join(" ")).not.toMatch(
      /nothing changed|model switched|refused/,
    );
    expect(mock).toHaveBeenCalledTimes(2);
  });

  it("effort refusal is the DESIGNED PARTIAL FAILURE with both SetResults in the ledger", async () => {
    stubFetch(
      respond({
        "/api/terminal/s1/set-model": {
          status: 200,
          body: {
            ok: true,
            acceptance: "echo-verified",
            requestedValue: "gpt-5.6-terra",
            effectiveValue: "gpt-5.6-terra",
            detail: null,
          },
        },
        "/api/terminal/s1/set-effort": {
          status: 200,
          body: {
            ok: false,
            acceptance: "unsupported",
            requestedValue: "xhigh",
            effectiveValue: null,
            detail: "effort token rejected",
          },
        },
      }),
    );
    startPairChangeFlow("s1", "gpt-5.6-terra", "xhigh");
    await vi.waitFor(() => expect(per("s1").pairChange?.outcome?.kind).toBe("partial"));
    expect(per("s1").pairChange?.outcome).toMatchObject({
      model: "gpt-5.6-terra",
      detail: "effort token rejected",
    });
    expect(per("s1").setLedger.map((entry) => entry.kind)).toEqual(["model", "effort"]);
    // The model DID switch (echo evidence); the effort marker never moved.
    expect(per("s1").echoEvidence.model?.value).toBe("gpt-5.6-terra");
    expect(per("s1").echoEvidence.effort).toBeUndefined();
  });

  it("Codex both-queued pair: queued model evidence still opens the gate; the pair completes", async () => {
    stubFetch(
      respond({
        "/api/terminal/s1/set-model": {
          status: 200,
          body: { ...SET_RESULTS.queued, requestedValue: "gpt-5.6-terra" },
        },
        "/api/terminal/s1/set-effort": {
          status: 200,
          body: { ...SET_RESULTS.queued, requestedValue: "xhigh" },
        },
      }),
    );
    startPairChangeFlow("s1", "gpt-5.6-terra", "xhigh");
    await vi.waitFor(() => expect(per("s1").pendingSets.effort?.phase).toBe("queued-awaiting-turn"));
    expect(per("s1").pairChange).toBeUndefined(); // completed; the queued pendings carry honesty
    expect(per("s1").pendingSets.model?.phase).toBe("queued-awaiting-turn");
  });
});

describe("cycleEffortRequested (R7)", () => {
  const snapshotFor = (effort: string | null): CapabilitySnapshotWire =>
    codexLiveSessionSnapshot("gpt-5.6-sol", effort);

  it("cycles the REQUESTED value from the effective one and POSTs set-effort — no dialog", async () => {
    store.getState().setLiveSnapshot("s1", { sessionId: "s1", fetchedAt: 1, payload: snapshotFor("low") });
    const mock = stubFetch(
      respond({
        "/api/terminal/s1/set-effort": {
          status: 200,
          body: { ...SET_RESULTS.queued, requestedValue: "medium" },
        },
      }),
    );
    cycleEffortRequested("s1", 1);
    await vi.waitFor(() => expect(mock).toHaveBeenCalled());
    expect(JSON.parse(mock.mock.calls[0][1].body as string)).toEqual({ effort: "medium" });
  });

  it("cycles from a PENDING requested value when one is in flight (requested, not effective)", async () => {
    store.getState().setLiveSnapshot("s1", { sessionId: "s1", fetchedAt: 1, payload: snapshotFor("low") });
    store.getState().recordPendingSet("s1", "effort", {
      requestedValue: "high",
      sentAt: 1,
      phase: "queued-awaiting-turn",
    });
    const mock = stubFetch(
      respond({
        "/api/terminal/s1/set-effort": {
          status: 200,
          body: { ...SET_RESULTS.queued, requestedValue: "xhigh" },
        },
      }),
    );
    cycleEffortRequested("s1", 1);
    await vi.waitFor(() => expect(mock).toHaveBeenCalled());
    expect(JSON.parse(mock.mock.calls[0][1].body as string)).toEqual({ effort: "xhigh" });
  });

  it("without a snapshot: announces, kicks the fetch, sets nothing blindly", async () => {
    const mock = stubFetch(
      respond({ [capabilitiesUrl("s1")]: { status: 200, body: snapshotFor("low") } }),
    );
    cycleEffortRequested("s1", 1);
    expect(announcerStore.getState().polite.text).toBe(cycleNoSnapshotAnnouncement());
    await vi.waitFor(() =>
      expect(mock.mock.calls.some(([url]) => url === capabilitiesUrl("s1"))).toBe(true),
    );
    expect(mock.mock.calls.every(([url]) => !String(url).endsWith("set-effort"))).toBe(true);
  });

  it("a model without effort control announces the exact copy and never POSTs", () => {
    store.getState().setLiveSnapshot("s1", {
      sessionId: "s1",
      fetchedAt: 1,
      payload: {
        models: [
          { key: "m1", displayName: "m1", resolvedModel: null, description: null, supportsEffort: false, effortOptions: [], defaultEffort: null, isDefault: false, hidden: false, selectable: true, provider: null },
        ],
        selectedModelKey: "m1",
        selectedEffort: null,
        configOptions: [],
      },
    });
    const mock = stubFetch(() => undefined);
    cycleEffortRequested("s1", 1);
    expect(announcerStore.getState().polite.text).toBe("no effort control for this model");
    expect(mock).not.toHaveBeenCalled();
  });
});

describe("startSetPromotionWatcher (R4 + v3 drift delta)", () => {
  const seat = (id: string, turnState: "working" | "turn-ended") =>
    fromTerminalSessionInfo(
      catalogRow({ id, label: id, turnState, controlState: "ready", status: "running" }),
    );

  it("re-GETs on EVERY turn-ended of the FOCUSED session — even with no pending set", async () => {
    sessionStore.getState().hydrate([seat("s1", "working")]);
    store.getState().setFocusedSession("s1");
    const mock = stubFetch(
      respond({ [capabilitiesUrl("s1")]: { status: 200, body: codexLiveSessionSnapshot("gpt-5.6-sol", "low") } }),
    );
    const release = startSetPromotionWatcher();
    sessionStore.getState().hydrate([seat("s1", "turn-ended")]);
    await vi.waitFor(() =>
      expect(mock.mock.calls.filter(([url]) => url === capabilitiesUrl("s1"))).toHaveLength(1),
    );
    release();
  });

  it("re-GETs an UNFOCUSED session's turn-ended only while it holds a queued/unknown pending", async () => {
    sessionStore.getState().hydrate([seat("s1", "working"), seat("s2", "working")]);
    store.getState().setFocusedSession(null);
    store.getState().recordPendingSet("s1", "effort", {
      requestedValue: "xhigh",
      sentAt: 1,
      phase: "queued-awaiting-turn",
    });
    const mock = stubFetch(
      respond({
        [capabilitiesUrl("s1")]: { status: 200, body: codexLiveSessionSnapshot("gpt-5.6-sol", "xhigh") },
      }),
    );
    const release = startSetPromotionWatcher();
    sessionStore.getState().hydrate([seat("s1", "turn-ended"), seat("s2", "turn-ended")]);
    await vi.waitFor(() =>
      expect(mock.mock.calls.filter(([url]) => url === capabilitiesUrl("s1"))).toHaveLength(1),
    );
    // s2 had no pending and no focus: no GET for it.
    expect(mock.mock.calls.filter(([url]) => url === capabilitiesUrl("s2"))).toHaveLength(0);
    // ...and the readback promoted s1's queued pending (turns start from PTY/inbox too — the
    // trigger read the observed turn state, not a cockpit submit receipt).
    await vi.waitFor(() => expect(per("s1").pendingSets.effort).toBeUndefined());
    release();
  });

  it("fires on a turn-ended delivered by L2's SEAT-EVENT channel too — turns started from PTY " +
    "lines and inbox deliveries promote queued sets, not only cockpit submits", async () => {
    const { applySeatEvent } = await import("./seatEvents");
    sessionStore.getState().hydrate([seat("s1", "working")]);
    store.getState().recordPendingSet("s1", "effort", {
      requestedValue: "xhigh",
      sentAt: 1,
      phase: "queued-awaiting-turn",
    });
    const mock = stubFetch(
      respond({
        [capabilitiesUrl("s1")]: { status: 200, body: codexLiveSessionSnapshot("gpt-5.6-sol", "xhigh") },
      }),
    );
    const release = startSetPromotionWatcher();
    applySeatEvent({
      kind: "seat.turn-state-changed",
      sessionId: "s1",
      ts: "2099-01-01T00:00:00Z", // strictly newer than the stored transition
      data: { turnState: "turn-ended" },
    } as never);
    await vi.waitFor(() =>
      expect(mock.mock.calls.filter(([url]) => url === capabilitiesUrl("s1"))).toHaveLength(1),
    );
    await vi.waitFor(() => expect(per("s1").pendingSets.effort).toBeUndefined());
    release();
  });

  it("an unfocused session re-GETs when it GAINS focus (drift catch-up)", async () => {
    sessionStore.getState().hydrate([seat("s1", "working")]);
    const mock = stubFetch(
      respond({ [capabilitiesUrl("s1")]: { status: 200, body: codexLiveSessionSnapshot("gpt-5.6-sol", "low") } }),
    );
    const release = startSetPromotionWatcher();
    store.getState().setFocusedSession("s1");
    await vi.waitFor(() =>
      expect(mock.mock.calls.filter(([url]) => url === capabilitiesUrl("s1"))).toHaveLength(1),
    );
    release();
  });
});
