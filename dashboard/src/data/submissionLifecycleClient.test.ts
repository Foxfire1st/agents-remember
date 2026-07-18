import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sessionCockpitStore } from "./sessionCockpitStore";
import {
  applySubmissionLifecycle,
  BridgeEpochMismatchError,
  createFetchSubmissionLifecycleTransport,
  dismissWithdrawnRecovery,
  ensureSubmissionLifecyclePolling,
  HIDDEN_STATUS_POLL_MS,
  pollSubmissionLifecycleOnce,
  resetSubmissionLifecycleClientForDev,
  restoreWithdrawnRecovery,
  STATUS_FAILURE_BACKOFF_MS,
  stopSubmissionLifecyclePolling,
  VISIBLE_STATUS_POLL_MS,
  withdrawLastQueuedSubmission,
  type SubmissionLifecycleTransport,
  type SubmissionStatusBatchWire,
  type WithdrawalResultWire,
} from "./submissionLifecycleClient";
import { latestActiveSubmit, startSubmitRecord } from "./submitMachine";
import type { SubmissionLifecycleState } from "../types/harnessCapabilities";

const SESSION = "seat / lifecycle";
const REQUEST = "request-lifecycle-1";
const EPOCH = "bridge-epoch-1";
const TEXT = "exact\nqueued text";

function found(
  state: SubmissionLifecycleState,
  requestId = REQUEST,
): SubmissionStatusBatchWire {
  return {
    bridgeEpoch: EPOCH,
    submissions: [
      {
        requestId,
        outcome: "found",
        submission: {
          state,
          submittedAt: "2026-07-17T10:00:00Z",
          updatedAt: "2026-07-17T10:00:01Z",
          acceptedAt: null,
          withdrawable: state === "queued",
          detail: null,
        },
      },
    ],
  };
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: Error) => void;
} {
  let resolve: (value: T) => void = () => {};
  let reject: (error: Error) => void = () => {};
  const promise = new Promise<T>((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, resolve, reject };
}

function notFound(requestId = REQUEST): SubmissionStatusBatchWire {
  return {
    bridgeEpoch: EPOCH,
    submissions: [{ requestId, outcome: "not-found" }],
  };
}

function withdrawn(requestId = REQUEST): WithdrawalResultWire {
  return {
    requestId,
    outcome: "withdrawn",
    state: "withdrawn",
    withdrawnAt: "2026-07-17T10:00:02Z",
    detail: null,
  };
}

function notWithdrawable(
  state: SubmissionLifecycleState,
  requestId = REQUEST,
): WithdrawalResultWire {
  return {
    requestId,
    outcome: "not-withdrawable",
    state,
    withdrawnAt: null,
    detail: null,
  };
}

function seedQueued(requestId = REQUEST, text = TEXT, queuedAt = 1): void {
  const store = sessionCockpitStore.getState();
  store.upsertSubmitRecord(SESSION, {
    ...startSubmitRecord({
      requestId,
      text,
      expectedBridgeEpoch: EPOCH,
      submittedRevision: 0,
      at: queuedAt,
    }),
    phase: "queued",
    serverLifecycleState: "queued",
  });
  store.enqueueSubmit(SESSION, {
    requestId,
    text,
    preview: text.replace("\n", " ↵ "),
    queuedAt,
    expectedBridgeEpoch: EPOCH,
    state: "queued",
  });
}

function transport(
  overrides: Partial<SubmissionLifecycleTransport> = {},
): SubmissionLifecycleTransport {
  return {
    authority: vi.fn(async () => ({ bridgeEpoch: EPOCH })),
    status: vi.fn(async () => found("queued")),
    withdraw: vi.fn(async () => withdrawn()),
    ...overrides,
  };
}

beforeEach(() => {
  sessionCockpitStore.setState({ perSession: {} });
  resetSubmissionLifecycleClientForDev();
});

afterEach(() => {
  stopSubmissionLifecyclePolling(SESSION);
  vi.useRealTimers();
});

describe("submission lifecycle transport", () => {
  it("uses private raw-free epoch-bound routes and caller supplied ids", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ bridgeEpoch: EPOCH }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => found("queued"),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => withdrawn(),
      } as Response);
    const client = createFetchSubmissionLifecycleTransport(fetchMock as typeof fetch);

    expect(await client.authority(SESSION)).toEqual({ bridgeEpoch: EPOCH });
    expect(await client.status(SESSION, EPOCH, [REQUEST])).toEqual(found("queued"));
    expect(await client.withdraw(SESSION, EPOCH, REQUEST)).toEqual(withdrawn());

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/terminal/seat%20%2F%20lifecycle/submission-authority",
      "/api/terminal/seat%20%2F%20lifecycle/submission-status",
      "/api/terminal/seat%20%2F%20lifecycle/withdraw",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      expectedBridgeEpoch: EPOCH,
      requestIds: [REQUEST],
    });
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({
      expectedBridgeEpoch: EPOCH,
      requestId: REQUEST,
    });
  });

  it("removes QueuePreview projection as soon as authority reports dispatching", async () => {
    seedQueued();
    await pollSubmissionLifecycleOnce(
      SESSION,
      transport({ status: vi.fn(async () => found("dispatching")) }),
      10,
    );
    const cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.queue).toEqual([]);
    expect(cockpit.submitHistory[0]).toMatchObject({
      phase: "delivering",
      serverLifecycleState: "dispatching",
      updatedAt: 10,
    });
  });

  it("polls immediately then at the visible cadence and stops after dispatch", async () => {
    vi.useFakeTimers();
    seedQueued();
    const status = vi
      .fn()
      .mockResolvedValueOnce(found("queued"))
      .mockResolvedValueOnce(found("dispatching"));
    ensureSubmissionLifecyclePolling(SESSION, transport({ status }));

    await vi.advanceTimersByTimeAsync(0);
    expect(status).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(VISIBLE_STATUS_POLL_MS - 1);
    expect(status).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(status).toHaveBeenCalledTimes(2);
    expect(sessionCockpitStore.getState().perSession[SESSION].queue).toEqual([]);
    expect(HIDDEN_STATUS_POLL_MS).toBe(2_500);
    expect(STATUS_FAILURE_BACKOFF_MS).toEqual([1_000, 2_000, 5_000]);
  });

  it("keeps an old poll completion from applying to or deleting a new same-id poller", async () => {
    vi.useFakeTimers();
    seedQueued();
    const oldStatusResult = deferred<SubmissionStatusBatchWire>();
    const oldStatus = vi.fn(() => oldStatusResult.promise);
    ensureSubmissionLifecyclePolling(SESSION, transport({ status: oldStatus }));
    await vi.advanceTimersByTimeAsync(0);
    expect(oldStatus).toHaveBeenCalledTimes(1);

    resetSubmissionLifecycleClientForDev();
    sessionCockpitStore.setState({ perSession: {} });
    seedQueued();
    const newStatus = vi
      .fn()
      .mockResolvedValueOnce(found("queued"))
      .mockResolvedValueOnce(found("dispatching"));
    ensureSubmissionLifecyclePolling(SESSION, transport({ status: newStatus }));
    await vi.advanceTimersByTimeAsync(0);
    expect(newStatus).toHaveBeenCalledTimes(1);

    oldStatusResult.resolve(found("dispatching"));
    await Promise.resolve();
    await Promise.resolve();
    expect(sessionCockpitStore.getState().perSession[SESSION]).toMatchObject({
      queue: [{ requestId: REQUEST }],
      submitHistory: [{ requestId: REQUEST, phase: "queued" }],
    });

    await vi.advanceTimersByTimeAsync(VISIBLE_STATUS_POLL_MS);
    expect(newStatus).toHaveBeenCalledTimes(2);
    expect(sessionCockpitStore.getState().perSession[SESSION]).toMatchObject({
      queue: [],
      submitHistory: [{ requestId: REQUEST, phase: "delivering" }],
    });
  });

  it("uses the hidden cadence and 1s then 2s transport backoff without changing queue truth", async () => {
    vi.useFakeTimers();
    seedQueued();
    const originalVisibility = document.visibilityState;
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    const status = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockRejectedValueOnce(new Error("still offline"))
      .mockResolvedValueOnce(found("queued"))
      .mockResolvedValueOnce(found("dispatching"));
    try {
      ensureSubmissionLifecyclePolling(SESSION, transport({ status }));
      await vi.advanceTimersByTimeAsync(0);
      expect(status).toHaveBeenCalledTimes(1);
      expect(sessionCockpitStore.getState().perSession[SESSION].queue).toHaveLength(1);

      await vi.advanceTimersByTimeAsync(999);
      expect(status).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(1);
      expect(status).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(1_999);
      expect(status).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(1);
      expect(status).toHaveBeenCalledTimes(3);

      await vi.advanceTimersByTimeAsync(HIDDEN_STATUS_POLL_MS);
      expect(status).toHaveBeenCalledTimes(4);
      expect(sessionCockpitStore.getState().perSession[SESSION].queue).toEqual([]);
    } finally {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        value: originalVisibility,
      });
    }
  });
});

describe("atomic Alt+Up withdrawal", () => {
  it("does not mutate locally before withdrawn and restores the immutable text exactly once", async () => {
    seedQueued();
    let release: (value: WithdrawalResultWire) => void = () => {};
    const deferred = new Promise<WithdrawalResultWire>((resolve) => {
      release = resolve;
    });
    const client = transport({ withdraw: vi.fn(async () => deferred) });

    const pending = withdrawLastQueuedSubmission(SESSION, {
      transport: client,
      now: () => 20,
    });
    expect(sessionCockpitStore.getState().perSession[SESSION].queue).toHaveLength(1);
    expect(sessionCockpitStore.getState().perSession[SESSION].composer.draft).toBe("");
    release(withdrawn());
    await expect(pending).resolves.toContain("restored for editing");

    const cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.queue).toEqual([]);
    expect(cockpit.composer).toEqual({ draft: TEXT, draftRevision: 1 });
    expect(cockpit.submitHistory[0]).toMatchObject({
      phase: "withdrawn",
      restoredAt: 20,
    });
    await withdrawLastQueuedSubmission(SESSION, {
      transport: client,
      now: () => 30,
    });
    expect(sessionCockpitStore.getState().perSession[SESSION].composer.draftRevision).toBe(1);
    expect(client.withdraw).toHaveBeenCalledTimes(1);
  });

  it("converges a lost withdraw response through status before restoring", async () => {
    seedQueued();
    const client = transport({
      withdraw: vi.fn(async () => {
        throw new Error("response lost");
      }),
      status: vi.fn(async () => found("withdrawn")),
    });
    await expect(
      withdrawLastQueuedSubmission(SESSION, {
        transport: client,
        now: () => 40,
      }),
    ).resolves.toContain("restored for editing");
    expect(sessionCockpitStore.getState().perSession[SESSION].composer.draft).toBe(TEXT);
  });

  it("repeats the same idempotent withdrawal when lost-response status is still queued", async () => {
    seedQueued();
    const withdraw = vi
      .fn()
      .mockRejectedValueOnce(new Error("response lost"))
      .mockResolvedValueOnce(withdrawn());
    const client = transport({
      withdraw,
      status: vi.fn(async () => found("queued")),
    });
    await expect(
      withdrawLastQueuedSubmission(SESSION, {
        transport: client,
        now: () => 45,
      }),
    ).resolves.toContain("restored for editing");
    expect(withdraw).toHaveBeenCalledTimes(2);
    expect(withdraw.mock.calls[0].slice(0, 3)).toEqual(withdraw.mock.calls[1].slice(0, 3));
  });

  it("persists first-response-loss intent until a later poll authoritatively withdraws", async () => {
    seedQueued();
    const status = vi
      .fn()
      .mockRejectedValueOnce(new Error("status unavailable"))
      .mockResolvedValueOnce(found("withdrawn"));
    const client = transport({
      withdraw: vi.fn(async () => {
        throw new Error("withdraw response lost");
      }),
      status,
    });

    await expect(
      withdrawLastQueuedSubmission(SESSION, {
        transport: client,
        now: () => 50,
      }),
    ).resolves.toContain("status is unavailable");
    expect(sessionCockpitStore.getState().perSession[SESSION].withdrawal).toMatchObject({
      phase: "pending",
      requestId: REQUEST,
      text: TEXT,
      draftRevision: 0,
    });

    // Pending intent is itself a bounded poll target even if queue projection compacts it away.
    sessionCockpitStore.getState().dequeueSubmit(SESSION, REQUEST);
    await expect(pollSubmissionLifecycleOnce(SESSION, client, 51)).resolves.toBe(0);
    const cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(status).toHaveBeenLastCalledWith(SESSION, EPOCH, [REQUEST]);
    expect(cockpit.composer).toEqual({ draft: TEXT, draftRevision: 1 });
    expect(cockpit.withdrawal).toBeUndefined();
    expect(cockpit.submitHistory[0]).toMatchObject({
      phase: "withdrawn",
      restoredAt: 51,
    });
  });

  it("persists intent when both repeated withdrawal responses are lost", async () => {
    seedQueued();
    const withdraw = vi
      .fn()
      .mockRejectedValueOnce(new Error("first response lost"))
      .mockRejectedValueOnce(new Error("repeat response lost"));
    const status = vi
      .fn()
      .mockResolvedValueOnce(found("queued"))
      .mockResolvedValueOnce(found("withdrawn"));
    const client = transport({ withdraw, status });

    await expect(
      withdrawLastQueuedSubmission(SESSION, {
        transport: client,
        now: () => 60,
      }),
    ).resolves.toContain("status is unavailable");
    expect(withdraw).toHaveBeenCalledTimes(2);
    expect(sessionCockpitStore.getState().perSession[SESSION].withdrawal).toMatchObject({
      phase: "pending",
      requestId: REQUEST,
      draftRevision: 0,
    });

    await pollSubmissionLifecycleOnce(SESSION, client, 61);
    const cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.composer.draft).toBe(TEXT);
    expect(cockpit.withdrawal).toBeUndefined();
    expect(cockpit.submitHistory[0].restoredAt).toBe(61);
  });

  it.each(["direct withdrawal", "lost-response status", "poll"] as const)(
    "projects authoritative not-found from %s into one terminal non-withdrawable record",
    async (ingress) => {
      seedQueued();
      if (ingress === "poll") {
        await pollSubmissionLifecycleOnce(
          SESSION,
          transport({ status: vi.fn(async () => notFound()) }),
          65,
        );
      } else {
        const client = transport({
          withdraw:
            ingress === "direct withdrawal"
              ? vi.fn(
                  async (): Promise<WithdrawalResultWire> => ({
                    requestId: REQUEST,
                    outcome: "not-found",
                    state: null,
                    withdrawnAt: null,
                    detail: null,
                  }),
                )
              : vi.fn(async () => {
                  throw new Error("withdraw response lost");
                }),
          status: vi.fn(async () => notFound()),
        });
        await expect(
          withdrawLastQueuedSubmission(SESSION, {
            transport: client,
            now: () => 65,
          }),
        ).resolves.toContain("not retained");
      }

      const cockpit = sessionCockpitStore.getState().perSession[SESSION];
      expect(cockpit.queue).toEqual([]);
      expect(cockpit.withdrawal).toBeUndefined();
      expect(cockpit.submitHistory[0]).toMatchObject({
        phase: "not-found",
        serverLifecycleState: undefined,
        lifecycleObservationVersion: 1,
      });
      expect(cockpit.submitHistory[0].detail).toContain("withdrawal is unavailable");
      expect(latestActiveSubmit(cockpit.submitHistory)).toBeUndefined();
    },
  );

  it.each(["dispatching", "unknown"] as const)(
    "preserves newer non-withdrawable %s when an older queued poll settles later",
    async (state) => {
      seedQueued();
      const delayedStatus = deferred<SubmissionStatusBatchWire>();
      const poll = pollSubmissionLifecycleOnce(
        SESSION,
        transport({ status: vi.fn(async () => delayedStatus.promise) }),
        66,
      );

      const notice = await withdrawLastQueuedSubmission(SESSION, {
        transport: transport({ withdraw: vi.fn(async () => notWithdrawable(state)) }),
        now: () => 67,
      });
      delayedStatus.resolve(found("queued"));
      await expect(poll).resolves.toBe(0);

      const cockpit = sessionCockpitStore.getState().perSession[SESSION];
      expect(cockpit.queue).toEqual([]);
      expect(cockpit.withdrawal).toBeUndefined();
      expect(cockpit.submitHistory[0]).toMatchObject({
        phase: state === "dispatching" ? "delivering" : "ambiguous",
        serverLifecycleState: state,
        lifecycleObservationVersion: 1,
      });
      expect(notice).toContain(state === "dispatching" ? "already dispatching" : "unknown");
      expect(latestActiveSubmit(cockpit.submitHistory)).toBe(
        state === "unknown" ? cockpit.submitHistory[0] : undefined,
      );
    },
  );

  it("settles the exact withdrawal transaction when a stronger unknown poll wins first", async () => {
    seedQueued();
    const delayedWithdrawal = deferred<WithdrawalResultWire>();
    const withdrawing = withdrawLastQueuedSubmission(SESSION, {
      transport: transport({ withdraw: vi.fn(async () => delayedWithdrawal.promise) }),
      now: () => 67,
    });
    await expect(
      pollSubmissionLifecycleOnce(
        SESSION,
        transport({ status: vi.fn(async () => found("unknown")) }),
        68,
      ),
    ).resolves.toBe(1);
    expect(sessionCockpitStore.getState().perSession[SESSION].withdrawal).toMatchObject({
      phase: "pending",
      requestId: REQUEST,
    });

    delayedWithdrawal.resolve(notWithdrawable("dispatching"));
    await expect(withdrawing).resolves.toContain("delivery unknown");
    const cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.withdrawal).toBeUndefined();
    expect(cockpit.queue).toEqual([]);
    expect(cockpit.submitHistory[0]).toMatchObject({
      phase: "ambiguous",
      serverLifecycleState: "unknown",
      lifecycleObservationVersion: 1,
    });
  });

  it.each([
    { olderState: "queued" as const, localState: "not-found" as const },
    { olderState: "queued" as const, localState: "generation-lost" as const },
    { olderState: "dispatching" as const, localState: "not-found" as const },
    { olderState: "dispatching" as const, localState: "generation-lost" as const },
  ])(
    "folds older $olderState after newer $localState without reviving QueuePreview",
    async ({ olderState, localState }) => {
      seedQueued();
      const delayedStatus = deferred<SubmissionStatusBatchWire>();
      const poll = pollSubmissionLifecycleOnce(
        SESSION,
        transport({ status: vi.fn(async () => delayedStatus.promise) }),
        68,
      );
      const localTransport =
        localState === "not-found"
          ? transport({
              withdraw: vi.fn(async () => ({
                requestId: REQUEST,
                outcome: "not-found" as const,
                state: null,
                withdrawnAt: null,
                detail: null,
              })),
            })
          : transport({
              withdraw: vi.fn(async () => {
                throw new BridgeEpochMismatchError(EPOCH, "epoch-2", "runner replaced");
              }),
            });
      await withdrawLastQueuedSubmission(SESSION, { transport: localTransport, now: () => 69 });

      delayedStatus.resolve(found(olderState));
      await expect(poll).resolves.toBe(0);
      const cockpit = sessionCockpitStore.getState().perSession[SESSION];
      expect(cockpit.queue).toEqual([]);
      expect(cockpit.withdrawal).toBeUndefined();
      if (olderState === "queued") {
        expect(cockpit.submitHistory[0]).toMatchObject({
          phase: localState,
          serverLifecycleState: undefined,
          lifecycleObservationVersion: 1,
        });
        expect(latestActiveSubmit(cockpit.submitHistory)).toBeUndefined();
      } else {
        expect(cockpit.submitHistory[0]).toMatchObject({
          phase: "ambiguous",
          serverLifecycleState: "unknown",
          detail: expect.stringContaining("possible send"),
          lifecycleObservationVersion: 2,
        });
        expect(latestActiveSubmit(cockpit.submitHistory)).toBe(cockpit.submitHistory[0]);
      }
    },
  );

  it.each([
    { name: "unchanged revision", newerDraft: null },
    { name: "changed revision", newerDraft: "newer human draft during epoch race" },
  ])(
    "consumes the issued withdrawal intent exactly once when epoch settles first ($name)",
    async ({ newerDraft }) => {
      seedQueued();
      const delayedWithdrawal = deferred<WithdrawalResultWire>();
      const withdraw = vi.fn(async () => delayedWithdrawal.promise);
      const withdrawing = withdrawLastQueuedSubmission(SESSION, {
        transport: transport({ withdraw }),
        now: () => 70,
      });
      if (newerDraft) sessionCockpitStore.getState().setComposerDraft(SESSION, newerDraft);

      await expect(
        pollSubmissionLifecycleOnce(
          SESSION,
          transport({
            status: vi.fn(async () => {
              throw new BridgeEpochMismatchError(EPOCH, "epoch-2", "epoch settled first");
            }),
          }),
          71,
        ),
      ).resolves.toBe(1);
      expect(sessionCockpitStore.getState().perSession[SESSION].withdrawal).toMatchObject({
        phase: "pending",
        requestId: REQUEST,
        text: TEXT,
        draftRevision: 0,
      });

      delayedWithdrawal.resolve(withdrawn());
      const notice = await withdrawing;
      const cockpit = sessionCockpitStore.getState().perSession[SESSION];
      expect(withdraw).toHaveBeenCalledTimes(1);
      expect(cockpit.queue).toEqual([]);
      expect(cockpit.submitHistory[0]).toMatchObject({
        phase: "withdrawn",
        serverLifecycleState: "withdrawn",
        lifecycleObservationVersion: 2,
      });
      if (newerDraft) {
        expect(notice).toContain("newer draft preserved");
        expect(cockpit.composer.draft).toBe(newerDraft);
        expect(cockpit.submitHistory[0].restoredAt).toBeUndefined();
        expect(cockpit.withdrawal).toEqual({
          phase: "recovery",
          requestId: REQUEST,
          text: TEXT,
          withdrawnAt: 70,
        });
      } else {
        expect(notice).toContain("restored for editing");
        expect(cockpit.composer).toEqual({ draft: TEXT, draftRevision: 1 });
        expect(cockpit.submitHistory[0].restoredAt).toBe(70);
        expect(cockpit.withdrawal).toBeUndefined();
        await withdrawLastQueuedSubmission(SESSION, {
          transport: transport({ withdraw }),
          now: () => 72,
        });
        expect(withdraw).toHaveBeenCalledTimes(1);
        expect(sessionCockpitStore.getState().perSession[SESSION].composer.draftRevision).toBe(1);
      }
    },
  );

  it.each(["not-found", "generation-lost"] as const)(
    "admits stronger delivered evidence after intermediate %s",
    async (localState) => {
      seedQueued();
      const delayedStatus = deferred<SubmissionStatusBatchWire>();
      const poll = pollSubmissionLifecycleOnce(
        SESSION,
        transport({ status: vi.fn(async () => delayedStatus.promise) }),
        73,
      );
      const localTransport =
        localState === "not-found"
          ? transport({
              withdraw: vi.fn(async () => ({
                requestId: REQUEST,
                outcome: "not-found" as const,
                state: null,
                withdrawnAt: null,
                detail: null,
              })),
            })
          : transport({
              withdraw: vi.fn(async () => {
                throw new BridgeEpochMismatchError(EPOCH, "epoch-2", "runner replaced");
              }),
            });
      await withdrawLastQueuedSubmission(SESSION, { transport: localTransport, now: () => 74 });

      delayedStatus.resolve(found("delivered"));
      await expect(poll).resolves.toBe(0);
      const cockpit = sessionCockpitStore.getState().perSession[SESSION];
      expect(cockpit.queue).toEqual([]);
      expect(cockpit.submitHistory[0]).toMatchObject({
        phase: "accepted",
        serverLifecycleState: "delivered",
        lifecycleObservationVersion: 2,
      });
      expect(latestActiveSubmit(cockpit.submitHistory)).toBeUndefined();
    },
  );

  it("preserves exact recovery when an older poll later reports not-found, even after history eviction", async () => {
    seedQueued();
    let releaseStatus: (value: SubmissionStatusBatchWire) => void = () => {};
    const delayedStatus = new Promise<SubmissionStatusBatchWire>((resolve) => {
      releaseStatus = resolve;
    });
    const poll = pollSubmissionLifecycleOnce(
      SESSION,
      transport({ status: vi.fn(async () => delayedStatus) }),
      75,
    );

    let releaseWithdrawal: (value: WithdrawalResultWire) => void = () => {};
    const delayedWithdrawal = new Promise<WithdrawalResultWire>((resolve) => {
      releaseWithdrawal = resolve;
    });
    const withdrawing = withdrawLastQueuedSubmission(SESSION, {
      transport: transport({ withdraw: vi.fn(async () => delayedWithdrawal) }),
      now: () => 76,
    });
    sessionCockpitStore.getState().setComposerDraft(SESSION, "newer human draft");
    releaseWithdrawal(withdrawn());
    await expect(withdrawing).resolves.toContain("newer draft preserved");

    const beforeEviction = sessionCockpitStore.getState();
    const cockpitBeforeEviction = beforeEviction.perSession[SESSION];
    sessionCockpitStore.setState({
      perSession: {
        ...beforeEviction.perSession,
        [SESSION]: { ...cockpitBeforeEviction, submitHistory: [] },
      },
    });
    releaseStatus(notFound());
    await expect(poll).resolves.toBe(0);

    const cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.composer.draft).toBe("newer human draft");
    expect(cockpit.withdrawal).toEqual({
      phase: "recovery",
      requestId: REQUEST,
      text: TEXT,
      withdrawnAt: 76,
    });
    expect(cockpit.submitHistory).toEqual([]);
  });

  it("preserves auto-restored withdrawn truth when an older poll later reports not-found", async () => {
    seedQueued();
    let releaseStatus: (value: SubmissionStatusBatchWire) => void = () => {};
    const delayedStatus = new Promise<SubmissionStatusBatchWire>((resolve) => {
      releaseStatus = resolve;
    });
    const poll = pollSubmissionLifecycleOnce(
      SESSION,
      transport({ status: vi.fn(async () => delayedStatus) }),
      76,
    );

    await expect(
      withdrawLastQueuedSubmission(SESSION, {
        transport: transport({ withdraw: vi.fn(async () => withdrawn()) }),
        now: () => 77,
      }),
    ).resolves.toContain("restored for editing");
    releaseStatus(notFound());
    await expect(poll).resolves.toBe(0);

    const cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.composer.draft).toBe(TEXT);
    expect(cockpit.submitHistory[0]).toMatchObject({
      phase: "withdrawn",
      serverLifecycleState: "withdrawn",
      restoredAt: 77,
    });
  });

  it("preserves auto-restored withdrawn truth when an older poll later reports epoch mismatch", async () => {
    seedQueued();
    let rejectStatus: (error: Error) => void = () => {};
    const delayedStatus = new Promise<SubmissionStatusBatchWire>((_resolve, reject) => {
      rejectStatus = reject;
    });
    const poll = pollSubmissionLifecycleOnce(
      SESSION,
      transport({ status: vi.fn(async () => delayedStatus) }),
      77,
    );

    await expect(
      withdrawLastQueuedSubmission(SESSION, {
        transport: transport({ withdraw: vi.fn(async () => withdrawn()) }),
        now: () => 78,
      }),
    ).resolves.toContain("restored for editing");
    const withdrawnVersion =
      sessionCockpitStore.getState().perSession[SESSION].submitHistory[0]
        .lifecycleObservationVersion;
    expect(withdrawnVersion).toBeGreaterThan(0);

    rejectStatus(new BridgeEpochMismatchError(EPOCH, "epoch-2", "older poll epoch"));
    await expect(poll).resolves.toBe(0);
    const cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.composer.draft).toBe(TEXT);
    expect(cockpit.withdrawal).toBeUndefined();
    expect(cockpit.submitHistory[0]).toMatchObject({
      phase: "withdrawn",
      serverLifecycleState: "withdrawn",
      restoredAt: 78,
      lifecycleObservationVersion: withdrawnVersion,
    });
  });

  it("preserves exact recovery when an older poll later reports epoch mismatch", async () => {
    seedQueued();
    let rejectStatus: (error: Error) => void = () => {};
    const delayedStatus = new Promise<SubmissionStatusBatchWire>((_resolve, reject) => {
      rejectStatus = reject;
    });
    const poll = pollSubmissionLifecycleOnce(
      SESSION,
      transport({ status: vi.fn(async () => delayedStatus) }),
      78,
    );

    let releaseWithdrawal: (value: WithdrawalResultWire) => void = () => {};
    const delayedWithdrawal = new Promise<WithdrawalResultWire>((resolve) => {
      releaseWithdrawal = resolve;
    });
    const withdrawing = withdrawLastQueuedSubmission(SESSION, {
      transport: transport({ withdraw: vi.fn(async () => delayedWithdrawal) }),
      now: () => 79,
    });
    sessionCockpitStore.getState().setComposerDraft(SESSION, "newer draft survives epoch");
    releaseWithdrawal(withdrawn());
    await expect(withdrawing).resolves.toContain("newer draft preserved");

    rejectStatus(new BridgeEpochMismatchError(EPOCH, "epoch-2", "older poll epoch"));
    await expect(poll).resolves.toBe(0);
    const cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.composer.draft).toBe("newer draft survives epoch");
    expect(cockpit.withdrawal).toMatchObject({
      phase: "recovery",
      requestId: REQUEST,
      text: TEXT,
    });
    expect(cockpit.submitHistory[0]).toMatchObject({
      phase: "withdrawn",
      serverLifecycleState: "withdrawn",
    });
  });

  it("preserves withdrawn truth when an older poll later reports queued", async () => {
    seedQueued();
    let releaseStatus: (value: SubmissionStatusBatchWire) => void = () => {};
    const delayedStatus = new Promise<SubmissionStatusBatchWire>((resolve) => {
      releaseStatus = resolve;
    });
    const poll = pollSubmissionLifecycleOnce(
      SESSION,
      transport({ status: vi.fn(async () => delayedStatus) }),
      79,
    );
    await withdrawLastQueuedSubmission(SESSION, {
      transport: transport({ withdraw: vi.fn(async () => withdrawn()) }),
      now: () => 80,
    });
    const withdrawnVersion =
      sessionCockpitStore.getState().perSession[SESSION].submitHistory[0]
        .lifecycleObservationVersion;

    releaseStatus(found("queued"));
    await expect(poll).resolves.toBe(0);
    expect(sessionCockpitStore.getState().perSession[SESSION].submitHistory[0]).toMatchObject({
      phase: "withdrawn",
      serverLifecycleState: "withdrawn",
      lifecycleObservationVersion: withdrawnVersion,
    });
  });

  it("still generation-loses a live queued record on epoch mismatch", async () => {
    seedQueued();
    await expect(
      pollSubmissionLifecycleOnce(
        SESSION,
        transport({
          status: vi.fn(async () => {
            throw new BridgeEpochMismatchError(EPOCH, "epoch-2", "runner replaced");
          }),
        }),
        81,
      ),
    ).resolves.toBe(0);
    const cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.queue).toEqual([]);
    expect(cockpit.submitHistory[0]).toMatchObject({
      phase: "generation-lost",
      serverLifecycleState: undefined,
      lifecycleObservationVersion: 1,
    });
  });

  it("dismisses only the exact recovery choice and then lets Alt+Up target a later queued item", async () => {
    seedQueued();
    let releaseWithdrawal: (value: WithdrawalResultWire) => void = () => {};
    const delayedWithdrawal = new Promise<WithdrawalResultWire>((resolve) => {
      releaseWithdrawal = resolve;
    });
    const recovering = withdrawLastQueuedSubmission(SESSION, {
      transport: transport({ withdraw: vi.fn(async () => delayedWithdrawal) }),
      now: () => 85,
    });
    sessionCockpitStore.getState().setComposerDraft(SESSION, "keep this newer draft");
    releaseWithdrawal(withdrawn());
    await recovering;

    const laterRequest = "request-lifecycle-later";
    const laterText = "later queued text";
    seedQueued(laterRequest, laterText, 86);
    const laterWithdraw = vi.fn(async (_sessionId, _epoch, requestId: string) =>
      withdrawn(requestId),
    );
    const laterClient = transport({ withdraw: laterWithdraw });
    await expect(
      withdrawLastQueuedSubmission(SESSION, {
        transport: laterClient,
        now: () => 87,
      }),
    ).resolves.toContain("choose replace or keep current draft");
    expect(laterWithdraw).not.toHaveBeenCalled();

    const renderedRevision =
      sessionCockpitStore.getState().perSession[SESSION].composer.draftRevision;
    expect(dismissWithdrawnRecovery(SESSION, laterRequest, renderedRevision, 88)).toContain(
      "no matching",
    );
    expect(dismissWithdrawnRecovery(SESSION, REQUEST, renderedRevision - 1, 88)).toContain(
      "draft changed again",
    );
    expect(sessionCockpitStore.getState().perSession[SESSION].withdrawal).toMatchObject({
      phase: "recovery",
      requestId: REQUEST,
    });

    expect(dismissWithdrawnRecovery(SESSION, REQUEST, renderedRevision, 89)).toContain(
      "current draft kept",
    );
    let cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.composer.draft).toBe("keep this newer draft");
    expect(cockpit.withdrawal).toBeUndefined();
    expect(cockpit.submitHistory.find((record) => record.requestId === REQUEST)).toMatchObject({
      phase: "withdrawn",
      recoveryDismissedAt: 89,
      detail: expect.stringContaining("current draft kept"),
    });
    expect(laterWithdraw).not.toHaveBeenCalled();

    await expect(
      withdrawLastQueuedSubmission(SESSION, {
        transport: laterClient,
        now: () => 90,
      }),
    ).resolves.toContain("restored for editing");
    expect(laterWithdraw).toHaveBeenCalledTimes(1);
    expect(laterWithdraw).toHaveBeenCalledWith(SESSION, EPOCH, laterRequest);
    cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.queue).toEqual([]);
    expect(cockpit.composer.draft).toBe(laterText);
  });

  it("preserves a newer draft and requires a revision-guarded explicit recovery", async () => {
    seedQueued();
    let release: (value: WithdrawalResultWire) => void = () => {};
    const deferred = new Promise<WithdrawalResultWire>((resolve) => {
      release = resolve;
    });
    const client = transport({ withdraw: vi.fn(async () => deferred) });

    const pending = withdrawLastQueuedSubmission(SESSION, {
      transport: client,
      now: () => 70,
    });
    sessionCockpitStore.getState().setComposerDraft(SESSION, "new human edit");
    release(withdrawn());
    await expect(pending).resolves.toContain("newer draft preserved");

    let cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.composer).toEqual({
      draft: "new human edit",
      draftRevision: 1,
    });
    expect(cockpit.withdrawal).toEqual({
      phase: "recovery",
      requestId: REQUEST,
      text: TEXT,
      withdrawnAt: 70,
    });
    expect(cockpit.submitHistory[0]).toMatchObject({ phase: "withdrawn" });
    expect(cockpit.submitHistory[0].restoredAt).toBeUndefined();

    expect(restoreWithdrawnRecovery(SESSION, REQUEST, 0, 71)).toContain("draft changed again");
    expect(sessionCockpitStore.getState().perSession[SESSION].composer.draft).toBe(
      "new human edit",
    );
    expect(restoreWithdrawnRecovery(SESSION, REQUEST, 1, 72)).toContain("restored for editing");
    cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.composer).toEqual({ draft: TEXT, draftRevision: 2 });
    expect(cockpit.withdrawal).toBeUndefined();
    expect(cockpit.submitHistory[0].restoredAt).toBe(72);
  });

  it("never restores without pending intent and never regresses terminal withdrawn to queued", async () => {
    seedQueued();
    await pollSubmissionLifecycleOnce(
      SESSION,
      transport({ status: vi.fn(async () => found("withdrawn")) }),
      80,
    );
    const afterWithdrawn = sessionCockpitStore.getState().perSession[SESSION];
    expect(afterWithdrawn.composer.draft).toBe("");
    expect(afterWithdrawn.submitHistory[0].phase).toBe("withdrawn");

    applySubmissionLifecycle(SESSION, REQUEST, "queued", "late stale poll", 81);
    const afterStale = sessionCockpitStore.getState().perSession[SESSION];
    expect(afterStale.submitHistory[0]).toMatchObject({
      phase: "withdrawn",
      serverLifecycleState: "withdrawn",
      updatedAt: 80,
    });
    expect(afterStale.queue).toEqual([]);
  });

  it("never restores after dispatch wins or the runner generation changes", async () => {
    seedQueued();
    const dispatchWon = transport({
      withdraw: vi.fn(
        async (): Promise<WithdrawalResultWire> => ({
          requestId: REQUEST,
          outcome: "not-withdrawable",
          state: "dispatching",
          withdrawnAt: null,
          detail: null,
        }),
      ),
    });
    await expect(
      withdrawLastQueuedSubmission(SESSION, { transport: dispatchWon }),
    ).resolves.toContain("already dispatching");
    expect(sessionCockpitStore.getState().perSession[SESSION].composer.draft).toBe("");

    sessionCockpitStore.setState({ perSession: {} });
    seedQueued();
    const replaced = transport({
      withdraw: vi.fn(async () => {
        throw new BridgeEpochMismatchError(EPOCH, "epoch-2", "runner replaced");
      }),
    });
    await expect(withdrawLastQueuedSubmission(SESSION, { transport: replaced })).resolves.toContain(
      "generation changed",
    );
    const cockpit = sessionCockpitStore.getState().perSession[SESSION];
    expect(cockpit.composer.draft).toBe("");
    expect(cockpit.queue).toEqual([]);
    expect(cockpit.submitHistory[0].phase).toBe("generation-lost");
  });
});
