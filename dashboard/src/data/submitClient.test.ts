import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  L5_EXACT_TEXT,
  L5_CAPPED_ENDGAME_RECONCILIATION_FIXTURE,
  L5_RAW_SESSION,
  L5_READY_SESSION,
  L5_REQUEST_ID,
  L5_STARTING_SESSION,
  reconciliationResult,
  submitReceipt,
} from "../test/fixtures/submitScenarios";
import {
  AmbiguousSubmitTransportError,
  createFetchSubmitTransport,
  executeReliableSubmit,
  PreDispatchTransportError,
  releaseSubmitDraft,
  retryRouteFailure,
  submissionGate,
  submitSessionText,
  SubmitRouteError,
  type ReliableSubmitTransport,
} from "./submitClient";
import { sessionCockpitStore } from "./sessionCockpitStore";
import { fromTerminalSessionInfo, sessionStore } from "./sessions";
import { stopSubmissionLifecyclePolling } from "./submissionLifecycleClient";
import { startSubmitRecord } from "./submitMachine";

const start = () =>
  startSubmitRecord({
    requestId: L5_REQUEST_ID,
    text: L5_EXACT_TEXT,
    expectedBridgeEpoch: "bridge-epoch-l5",
    submittedRevision: 3,
    at: 100,
  });

beforeEach(() => {
  sessionCockpitStore.setState({ perSession: {} });
  sessionStore.getState().hydrate([fromTerminalSessionInfo(L5_READY_SESSION)]);
});

afterEach(() => vi.useRealTimers());

describe("reliable transport", () => {
  it("posts the epoch-bound immutable request and preserves multiline/non-ASCII bytes", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const request = JSON.parse(String(init?.body)) as { requestId: string };
      return { ok: true, json: async () => submitReceipt("immediate", request) } as Response;
    });
    const transport = createFetchSubmitTransport(fetchMock as typeof fetch);
    const result = await transport.submit("seat / one", {
      requestId: L5_REQUEST_ID,
      text: L5_EXACT_TEXT,
      expectedBridgeEpoch: "bridge-epoch-l5",
    });
    expect(result.acceptance).toBe("immediate");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/terminal/seat%20%2F%20one/submit");
    expect(JSON.parse(String(init?.body))).toEqual({
      requestId: L5_REQUEST_ID,
      text: L5_EXACT_TEXT,
      expectedBridgeEpoch: "bridge-epoch-l5",
    });
  });

  it("turns fetch rejection/malformed 200 into ambiguity and preserves definitive route detail", async () => {
    const rejectedFetch = vi.fn(async () => {
      throw new Error("socket vanished");
    });
    await expect(
      createFetchSubmitTransport(rejectedFetch as typeof fetch).submit("s", {
        requestId: L5_REQUEST_ID,
        text: L5_EXACT_TEXT,
        expectedBridgeEpoch: "bridge-epoch-l5",
      }),
    ).rejects.toBeInstanceOf(AmbiguousSubmitTransportError);

    const routeFetch = vi.fn(async () => ({
      ok: false,
      status: 409,
      json: async () => ({ status: "unsupported", detail: "raw sessions cannot submit" }),
    })) as unknown as typeof fetch;
    await expect(
      createFetchSubmitTransport(routeFetch).submit("s", {
        requestId: L5_REQUEST_ID,
        text: L5_EXACT_TEXT,
        expectedBridgeEpoch: "bridge-epoch-l5",
      }),
    ).rejects.toMatchObject({
      failure: { httpStatus: 409, status: "unsupported", detail: "raw sessions cannot submit" },
    });
  });

  it("retries only an explicitly proven pre-dispatch loss, using the same id and immutable text", async () => {
    const submits: Array<{ requestId: string; text: string }> = [];
    const transport: ReliableSubmitTransport = {
      submit: vi.fn(async (_sessionId, request) => {
        submits.push(request);
        if (submits.length === 1) throw new PreDispatchTransportError("no byte dispatched");
        return submitReceipt("immediate");
      }),
      reconcile: vi.fn(),
    };
    const result = await executeReliableSubmit("s", start(), { transport });
    expect(result.phase).toBe("accepted");
    expect(submits).toEqual([
      {
        requestId: L5_REQUEST_ID,
        text: L5_EXACT_TEXT,
        expectedBridgeEpoch: "bridge-epoch-l5",
      },
      {
        requestId: L5_REQUEST_ID,
        text: L5_EXACT_TEXT,
        expectedBridgeEpoch: "bridge-epoch-l5",
      },
    ]);
    expect(transport.reconcile).not.toHaveBeenCalled();
  });

  it("retries once when the submit route certifies a pre-dispatch control-IPC failure", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        json: async () => ({
          status: "pre-dispatch-failed",
          detail: "control socket refused before write",
          retrySafe: true,
          stage: "control-ipc",
        }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => submitReceipt("immediate"),
      } as Response);
    const transport = createFetchSubmitTransport(fetchMock as typeof fetch);

    const result = await executeReliableSubmit("s", start(), { transport });

    expect(result.phase).toBe("accepted");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(
      fetchMock.mock.calls.map(([, init]) => JSON.parse(String(init?.body))),
    ).toEqual([
      {
        requestId: L5_REQUEST_ID,
        text: L5_EXACT_TEXT,
        expectedBridgeEpoch: "bridge-epoch-l5",
      },
      {
        requestId: L5_REQUEST_ID,
        text: L5_EXACT_TEXT,
        expectedBridgeEpoch: "bridge-epoch-l5",
      },
    ]);
  });

  it("does not auto-retry a generic 503 without the exact pre-dispatch certificate", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({ status: "control-unavailable", detail: "bridge restarting" }),
    } as Response));
    const result = await executeReliableSubmit("s", start(), {
      transport: createFetchSubmitTransport(fetchMock as typeof fetch),
    });

    expect(result).toMatchObject({
      phase: "route-error",
      routeFailure: {
        httpStatus: 503,
        status: "control-unavailable",
        detail: "bridge restarting",
      },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not auto-retry a pre-dispatch lookalike with the wrong evidence stage", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({
        status: "pre-dispatch-failed",
        detail: "ordinary browser failure",
        retrySafe: true,
        stage: "browser-fetch",
      }),
    } as Response));
    const result = await executeReliableSubmit("s", start(), {
      transport: createFetchSubmitTransport(fetchMock as typeof fetch),
    });

    expect(result.phase).toBe("route-error");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("treats a browser rejection as ambiguous and reconciles without resending", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/submit")) throw new Error("browser connection reset");
      return {
        ok: true,
        status: 200,
        json: async () => reconciliationResult("accepted"),
      } as Response;
    });
    const result = await executeReliableSubmit("s", start(), {
      transport: createFetchSubmitTransport(fetchMock as typeof fetch),
      sleep: async () => {},
    });

    expect(result.phase).toBe("accepted");
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/submit"))).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/reconcile"))).toHaveLength(1);
  });

  it("bootstraps a lost submit response from authoritative queued status", async () => {
    sessionCockpitStore.getState().setComposerDraft("l5-ready", L5_EXACT_TEXT);
    const revision = sessionCockpitStore.getState().perSession["l5-ready"].composer.draftRevision;
    const reconcile = vi.fn();
    const outcome = await submitSessionText("l5-ready", L5_EXACT_TEXT, {
      requestId: L5_REQUEST_ID,
      expectedBridgeEpoch: "bridge-epoch-l5",
      source: "composer",
      submittedRevision: revision,
      sleep: async () => {},
      transport: {
        submit: async () => {
          throw new AmbiguousSubmitTransportError("submit response lost");
        },
        reconcile,
      },
      lifecycleTransport: {
        authority: vi.fn(async () => ({ bridgeEpoch: "bridge-epoch-l5" })),
        withdraw: vi.fn(),
        status: vi.fn(async () => ({
          bridgeEpoch: "bridge-epoch-l5",
          submissions: [
            {
              requestId: L5_REQUEST_ID,
              outcome: "found" as const,
              submission: {
                state: "queued" as const,
                submittedAt: "2026-07-17T10:00:00Z",
                updatedAt: "2026-07-17T10:00:01Z",
                acceptedAt: null,
                withdrawable: true,
                detail: null,
              },
            },
          ],
        })),
      },
    });

    expect(outcome).toMatchObject({ status: "started", record: { phase: "queued" } });
    expect(reconcile).not.toHaveBeenCalled();
    expect(sessionCockpitStore.getState().perSession["l5-ready"].queue[0]).toMatchObject({
      requestId: L5_REQUEST_ID,
      text: L5_EXACT_TEXT,
      state: "queued",
    });
    stopSubmissionLifecyclePolling("l5-ready");
  });

  it("reconciles a post-write unknown receipt without resending", async () => {
    const fetchMock = vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () =>
        url.endsWith("/submit")
          ? submitReceipt("unknown")
          : reconciliationResult("accepted"),
    } as Response));
    const result = await executeReliableSubmit("s", start(), {
      transport: createFetchSubmitTransport(fetchMock as typeof fetch),
      sleep: async () => {},
    });

    expect(result.phase).toBe("accepted");
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/submit"))).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/reconcile"))).toHaveLength(1);
  });

  it("never resends an unclassified post-dispatch loss; it reconciles the same id at 1s → 2s", async () => {
    const delays: number[] = [];
    const transport: ReliableSubmitTransport = {
      submit: vi.fn(async () => {
        throw new Error("browser fetch rejected");
      }),
      reconcile: vi
        .fn()
        .mockResolvedValueOnce(reconciliationResult("unresolved"))
        .mockResolvedValueOnce(reconciliationResult("accepted")),
    };
    const result = await executeReliableSubmit("s", start(), {
      transport,
      sleep: async (ms) => {
        delays.push(ms);
      },
    });
    expect(result.phase).toBe("accepted");
    expect(transport.submit).toHaveBeenCalledTimes(1);
    expect(transport.reconcile).toHaveBeenCalledTimes(2);
    expect(transport.reconcile).toHaveBeenNthCalledWith(
      1,
      "s",
      L5_REQUEST_ID,
      "bridge-epoch-l5",
      { signal: expect.any(AbortSignal) },
    );
    expect(delays).toEqual([1_000, 2_000]);
  });

  it("takes a 200 unknown receipt through the capped endgame without ever resending", async () => {
    const delays: number[] = [];
    let wallClock = 0;
    let reconciliationIndex = 0;
    const transport: ReliableSubmitTransport = {
      submit: vi.fn(async () => submitReceipt("unknown")),
      reconcile: vi.fn(
        async () =>
          L5_CAPPED_ENDGAME_RECONCILIATION_FIXTURE[reconciliationIndex++] ??
          reconciliationResult("unresolved"),
      ),
    };
    const result = await executeReliableSubmit("s", start(), {
      transport,
      sleep: async (ms) => {
        delays.push(ms);
        wallClock += ms;
      },
      now: () => wallClock,
    });
    expect(result.phase).toBe("endgame");
    expect(result.reconcileWindowElapsedMs).toBe(120_000);
    expect(delays.slice(0, 4)).toEqual([1_000, 2_000, 5_000, 5_000]);
    expect(delays.at(-1)).toBe(2_000);
    expect(transport.submit).toHaveBeenCalledTimes(1);
    expect(transport.reconcile).toHaveBeenCalledTimes(
      L5_CAPPED_ENDGAME_RECONCILIATION_FIXTURE.length,
    );
  });

  it("aborts a hung initial submit at the wall deadline and reaches endgame without resend", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const transport: ReliableSubmitTransport = {
      submit: vi.fn(() => new Promise<never>(() => {})),
      reconcile: vi.fn(),
    };
    const pending = executeReliableSubmit("s", start(), {
      transport,
      resolutionWindowMs: 100,
    });
    await vi.advanceTimersByTimeAsync(100);
    const result = await pending;
    expect(result.phase).toBe("endgame");
    expect(transport.submit).toHaveBeenCalledTimes(1);
    expect(transport.reconcile).not.toHaveBeenCalled();
  });

  it("aborts a hung reconcile attempt at remaining wall time without resending submit", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const transport: ReliableSubmitTransport = {
      submit: vi.fn(async () => submitReceipt("unknown")),
      reconcile: vi.fn(() => new Promise<never>(() => {})),
    };
    const pending = executeReliableSubmit("s", start(), {
      transport,
      resolutionWindowMs: 100,
      sleep: async () => {},
    });
    await vi.advanceTimersByTimeAsync(100);
    const result = await pending;
    expect(result.phase).toBe("endgame");
    expect(transport.submit).toHaveBeenCalledTimes(1);
    expect(transport.reconcile).toHaveBeenCalledTimes(1);
  });

  it("charges slow submit and reconcile work to wall time rather than scheduled sleeps", async () => {
    let submitWall = 0;
    const lateSubmit = await executeReliableSubmit("s", start(), {
      now: () => submitWall,
      resolutionWindowMs: 100,
      transport: {
        submit: vi.fn(async () => {
          submitWall = 101;
          return submitReceipt("unknown");
        }),
        reconcile: vi.fn(),
      },
    });
    expect(lateSubmit.phase).toBe("endgame");

    let reconcileWall = 0;
    const transport: ReliableSubmitTransport = {
      submit: vi.fn(async () => submitReceipt("unknown")),
      reconcile: vi.fn(async () => {
        reconcileWall += 6_000;
        return reconciliationResult("unresolved");
      }),
    };
    const slowReconcile = await executeReliableSubmit("s", start(), {
      transport,
      now: () => reconcileWall,
      resolutionWindowMs: 10_000,
      sleep: async (ms) => {
        reconcileWall += ms;
      },
    });
    expect(slowReconcile.phase).toBe("endgame");
    expect(transport.submit).toHaveBeenCalledTimes(1);
    expect(transport.reconcile).toHaveBeenCalledTimes(2);
  });
});

describe("store driver + gates", () => {
  it("allows composing before ready but submits only a ready controlled session", () => {
    expect(submissionGate(fromTerminalSessionInfo(L5_READY_SESSION))).toEqual({
      ready: true,
      editable: true,
    });
    expect(submissionGate(fromTerminalSessionInfo(L5_STARTING_SESSION))).toMatchObject({
      ready: false,
      editable: true,
    });
    expect(submissionGate(fromTerminalSessionInfo(L5_RAW_SESSION))).toMatchObject({
      ready: false,
      editable: false,
      reason: expect.stringContaining("raw terminal typing"),
    });
  });

  it.each(["rejected", "unsupported"] as const)(
    "retains the draft and verbatim detail after %s",
    async (acceptance) => {
      const store = sessionCockpitStore.getState();
      store.setComposerDraft("l5-ready", L5_EXACT_TEXT);
      const revision = sessionCockpitStore.getState().perSession["l5-ready"].composer.draftRevision;
      const outcome = await submitSessionText("l5-ready", L5_EXACT_TEXT, {
        requestId: L5_REQUEST_ID,
        expectedBridgeEpoch: "bridge-epoch-l5",
        submittedRevision: revision,
        transport: {
          submit: async () => submitReceipt(acceptance),
          reconcile: vi.fn(),
        },
      });
      expect(outcome).toMatchObject({
        status: "started",
        record: { phase: acceptance, detail: submitReceipt(acceptance).detail },
      });
      expect(sessionCockpitStore.getState().perSession["l5-ready"].composer.draft).toBe(
        L5_EXACT_TEXT,
      );
    },
  );

  it("stores queued history, clears the exact draft revision, and projects a 'yours' queue item", async () => {
    const store = sessionCockpitStore.getState();
    store.setComposerDraft("l5-ready", L5_EXACT_TEXT);
    const revision = sessionCockpitStore.getState().perSession["l5-ready"].composer.draftRevision;
    const outcome = await submitSessionText("l5-ready", L5_EXACT_TEXT, {
      requestId: L5_REQUEST_ID,
      expectedBridgeEpoch: "bridge-epoch-l5",
      source: "composer",
      submittedRevision: revision,
      transport: {
        submit: async () => submitReceipt("queued"),
        reconcile: vi.fn(),
      },
    });
    expect(outcome).toMatchObject({ status: "started", record: { phase: "queued" } });
    const cockpit = sessionCockpitStore.getState().perSession["l5-ready"];
    expect(cockpit.composer.draft).toBe("");
    expect(cockpit.submitHistory).toHaveLength(1);
    expect(cockpit.queue).toEqual([
      expect.objectContaining({
        requestId: L5_REQUEST_ID,
        text: L5_EXACT_TEXT,
        expectedBridgeEpoch: "bridge-epoch-l5",
        state: "queued",
      }),
    ]);
  });

  it("records a background queue receipt without clearing or joining composer pop-back state", async () => {
    const store = sessionCockpitStore.getState();
    store.setComposerDraft("l5-ready", "unrelated visible draft");
    const revision = sessionCockpitStore.getState().perSession["l5-ready"].composer.draftRevision;
    const outcome = await submitSessionText("l5-ready", "leaf context payload", {
      requestId: L5_REQUEST_ID,
      expectedBridgeEpoch: "bridge-epoch-l5",
      source: "leaf-context",
      clearDraftOnAccept: false,
      submittedRevision: revision,
      transport: {
        submit: async () => submitReceipt("queued"),
        reconcile: vi.fn(),
      },
    });
    expect(outcome).toMatchObject({
      status: "started",
      record: {
        phase: "queued",
        source: "leaf-context",
        clearDraftOnAccept: false,
      },
    });
    const cockpit = sessionCockpitStore.getState().perSession["l5-ready"];
    expect(cockpit.composer.draft).toBe("unrelated visible draft");
    expect(cockpit.queue).toEqual([]);
  });

  it("retries a definitive route failure with the occupied id and ORIGINAL text", async () => {
    sessionCockpitStore.getState().setComposerDraft("l5-ready", L5_EXACT_TEXT);
    const first = await submitSessionText("l5-ready", L5_EXACT_TEXT, {
      requestId: L5_REQUEST_ID,
      expectedBridgeEpoch: "bridge-epoch-l5",
      transport: {
        submit: async () => {
          throw new SubmitRouteError({
            httpStatus: 503,
            status: "control-unavailable",
            detail: "bridge restarting",
          });
        },
        reconcile: vi.fn(),
      },
    });
    expect(first).toMatchObject({ status: "started", record: { phase: "route-error" } });
    sessionCockpitStore.getState().setComposerDraft("l5-ready", "edited duplicate");
    const submitted: string[] = [];
    const retried = await retryRouteFailure("l5-ready", L5_REQUEST_ID, "edited duplicate", {
      transport: {
        submit: async (_sessionId, request) => {
          submitted.push(request.text);
          return submitReceipt("immediate");
        },
        reconcile: vi.fn(),
      },
    });
    expect(retried.record?.phase).toBe("accepted");
    expect(retried.notice).toContain("edited text was not sent");
    expect(submitted).toEqual([L5_EXACT_TEXT]);
    // The edited draft has a newer revision, so convergence cannot clobber it.
    expect(sessionCockpitStore.getState().perSession["l5-ready"].composer.draft).toBe(
      "edited duplicate",
    );
  });

  it("releases an unresolved request without discarding or resending its retained draft", () => {
    const store = sessionCockpitStore.getState();
    store.setComposerDraft("l5-ready", L5_EXACT_TEXT);
    const revision = sessionCockpitStore.getState().perSession["l5-ready"].composer.draftRevision;
    store.upsertSubmitRecord("l5-ready", {
      ...startSubmitRecord({
        requestId: L5_REQUEST_ID,
        text: L5_EXACT_TEXT,
        expectedBridgeEpoch: "bridge-epoch-l5",
        submittedRevision: revision,
        at: 100,
      }),
      phase: "endgame",
    });

    expect(releaseSubmitDraft("l5-ready", L5_REQUEST_ID, 200)).toBe(true);
    const cockpit = sessionCockpitStore.getState().perSession["l5-ready"];
    expect(cockpit.composer.draft).toBe(L5_EXACT_TEXT);
    expect(cockpit.submitHistory[0]).toMatchObject({ phase: "released", releasedAt: 200 });
  });
});
