// Repair proof for L150-CODE-02, L150-CODE-03 and L150-CODE-06: the extracted
// production dispatch keeps the exact HEAD control flow — a mismatched receipt
// terminates before the result poll, offline task-bound recovery rereads the
// role/document address, and the notify callback is read live at acceptance.
// These drive dispatchRoleAction itself (the pane's only dispatch path) with stub
// writers; the pane wiring passes the live callback ref through.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dispatchRoleAction, type DispatchLiveState, type DispatchWriters } from "./roleChatDispatch";
import type {
  RoleDocumentScope,
  RoleExecutionReceipt,
  RoleLaunchSelection,
  RoleLauncherOptions,
  RoleScopedExecution,
} from "./roleLaunchModel";

type Route = { status: number; body: unknown };
const routes: Record<string, Route[]> = {};
const seen: { url: string; body: unknown }[] = [];

function reply(url: string, status: number, body: unknown) {
  (routes[url] ??= []).push({ status, body });
}

function take(url: string): Route {
  const queue = routes[url] ?? [];
  return queue.length > 1 ? queue.shift()! : queue[0]!;
}

beforeEach(() => {
  for (const key of Object.keys(routes)) delete routes[key];
  seen.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      seen.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const next = take(url);
      const requestBody = init?.body ? JSON.parse(String(init.body)) : undefined;
      const body = typeof next?.body === "function"
        ? (next.body as (request: unknown) => unknown)(requestBody)
        : next?.body;
      return new Response(JSON.stringify(body ?? {}), { status: next?.status ?? 404 });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const TASK_SELECTION: RoleLaunchSelection = {
  role: "manager",
  sprintDocumentRef: { repository: "repo", path: "sprint/task.json" },
  masterDocumentRef: { repository: "repo", path: "master/task.json" },
};

const REQUEST_SELECTION: RoleLaunchSelection = { role: "architect" };

function paseoReceipt(requestId: string, over: Record<string, unknown> = {}): RoleExecutionReceipt {
  return {
    status: "accepted",
    requestId,
    execution: { kind: "paseo-agent", agentId: "codex" },
    ...over,
  } as RoleExecutionReceipt;
}

function liveFor(selection: RoleLaunchSelection, over: Partial<DispatchLiveState> = {}): DispatchLiveState {
  return {
    active: true,
    busy: false,
    options: {} as RoleLauncherOptions,
    currentScopedExecution: undefined,
    activeRequestsRef: { current: {} },
    currentSelectionRef: { current: selection },
    notify: undefined,
    ...over,
  };
}

function writers(): DispatchWriters & {
  remembered: { scope: RoleDocumentScope; value: RoleExecutionReceipt }[];
  errors: unknown[];
} {
  const remembered: { scope: RoleDocumentScope; value: RoleExecutionReceipt }[] = [];
  const errors: unknown[] = [];
  // The result poll the dispatch drives: a real HTTP read against the stub that
  // folds the answer with the production identity rule (a constrained poll rejects
  // a foreign receipt; an unconstrained one adopts what the address returns).
  const poll: DispatchWriters["fetchExecutionResult"] = async (scope, _reportNotFound, requestId) => {
    const response = await fetch("/api/role-launch/result", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...scope, ...(requestId ? { requestId } : {}) }),
    });
    if (response.status === 404) return undefined;
    const value = (await response.json()) as RoleExecutionReceipt;
    if (requestId && value.requestId !== requestId) {
      errors.push({ scope, message: "The role result did not match the selected request." });
      return undefined;
    }
    remembered.push({ scope, value });
    return value;
  };
  return {
    remembered,
    errors,
    updateActiveRequest: vi.fn(),
    rememberExecution: vi.fn((scope: RoleDocumentScope, value: RoleExecutionReceipt) => {
      remembered.push({ scope, value });
    }),
    setBusyScope: vi.fn(),
    setLaunchRequestId: vi.fn(),
    setExecutionErrorState: vi.fn((state) => {
      errors.push(typeof state === "function" ? "updater" : state);
    }),
    fetchExecutionResult: vi.fn(poll),
    requestOptionsRefresh: vi.fn(),
  };
}

function resultBodies(): Record<string, unknown>[] {
  return seen.filter((call) => call.url === "/api/role-launch/result").map((call) => call.body as Record<string, unknown>);
}

describe("L150-CODE-02: a mismatched dispatch receipt terminates the dispatch", () => {
  it("sets the mismatch error and issues no immediate result poll", async () => {
    const live = liveFor(REQUEST_SELECTION);
    const run = writers();
    reply("/api/role-launch/dispatch", 200, { status: "accepted", requestId: "foreign-id" });
    await dispatchRoleAction(live, run, "start", REQUEST_SELECTION);
    // The foreign receipt is neither adopted nor polled: the error names the mismatch.
    expect(run.errors).toEqual([
      null,
      { scope: expect.anything(), message: "The role execution receipt did not match this request." },
    ]);
    expect(run.remembered).toEqual([]);
    expect(run.fetchExecutionResult).not.toHaveBeenCalled();
    expect(resultBodies()).toEqual([]);
    // The run still settles: busy cleared and the options reread at the end.
    expect(run.setBusyScope).toHaveBeenCalledWith(null);
    expect(run.requestOptionsRefresh).toHaveBeenCalledTimes(1);
  });

  it("still adopts, notifies and polls a matching receipt (positive control)", async () => {
    const notify = vi.fn();
    const live = liveFor(REQUEST_SELECTION, { notifyRef: { current: notify } });
    const run = writers();
    reply("/api/role-launch/dispatch", 200, (request: unknown) =>
      paseoReceipt((request as Record<string, string>)["requestId"]));
    await dispatchRoleAction(live, run, "start", REQUEST_SELECTION);
    expect(run.remembered).toHaveLength(1);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(run.fetchExecutionResult).toHaveBeenCalledTimes(1);
    expect(run.errors).toEqual([null, null]);
  });
});

describe("L150-CODE-03: offline task-bound recovery rereads the role/document address", () => {
  const PRIOR: RoleExecutionReceipt = {
    status: "completed",
    requestId: "prior-valid-receipt",
  } as RoleExecutionReceipt;

  async function offlineDispatch(
    action: "start" | "revive",
    resultStatus: number,
    resultBody: unknown,
    execution: RoleScopedExecution | undefined,
  ) {
    const live = liveFor(TASK_SELECTION, { currentScopedExecution: execution });
    const run = writers();
    // A refused transport: the dispatch fetch itself rejects.
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("offline"));
    reply("/api/role-launch/result", resultStatus, resultBody);
    await dispatchRoleAction(live, run, action, TASK_SELECTION);
    return run;
  }

  const revivable = { status: "completed", canRevive: true } as RoleScopedExecution;
  const offlineMessage =
    "The role launch request did not return. Its state is unknown; refresh the AR result before retrying.";

  it.each([["start"], ["revive"]] as ["start" | "revive"][])("%s recovers the prior receipt", async (action) => {
    const run = await offlineDispatch(action, 200, PRIOR, action === "revive" ? revivable : undefined);
    // The poll carries no request-id constraint, so the prior receipt is adopted
    // with no mismatch error.
    expect(resultBodies()).toHaveLength(1);
    expect(resultBodies()[0]).not.toHaveProperty("requestId");
    expect(run.remembered.map((entry) => entry.value)).toContainEqual(PRIOR);
    expect(run.errors).toEqual([null, { scope: expect.anything(), message: offlineMessage }]);
  });

  it.each([["start"], ["revive"]] as ["start" | "revive"][])("%s reports no mismatch on an absent result", async (action) => {
    const run = await offlineDispatch(action, 404, {}, action === "revive" ? revivable : undefined);
    expect(resultBodies()).toHaveLength(1);
    expect(resultBodies()[0]).not.toHaveProperty("requestId");
    expect(run.errors).toEqual([null, { scope: expect.anything(), message: offlineMessage }]);
  });

  it("keeps the request-scoped offline poll pinned to its own request", async () => {
    const live = liveFor(REQUEST_SELECTION);
    const run = writers();
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("offline"));
    reply("/api/role-launch/result", 404, {});
    await dispatchRoleAction(live, run, "start", REQUEST_SELECTION);
    const sent = resultBodies()[0];
    expect(typeof sent["requestId"]).toBe("string");
  });
});

describe("L150-CODE-06: the dispatch notifies the current callback at acceptance", () => {
  // The dispatch answer parks until the test settles it, so the callback can be
  // replaced or removed while the answer is pending.
  function stubDeferredDispatch() {
    const parked: { answer: (value: Response) => void }[] = [];
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      seen.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (String(input) === "/api/role-launch/dispatch") {
        return new Promise<Response>((answer) => {
          parked.push({ answer });
        });
      }
      return new Response(JSON.stringify({}), { status: 404 });
    });
    return parked;
  }

  async function startWithCallback(notifyRef: { current: ((receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void) | undefined }) {
    const live = liveFor(REQUEST_SELECTION, { notifyRef });
    const run = writers();
    const parked = stubDeferredDispatch();
    const done = dispatchRoleAction(live, run, "start", REQUEST_SELECTION);
    // Wait for the dispatch request itself, not a wall-clock sleep.
    await vi.waitFor(() => {
      expect(seen.some((call) => call.url === "/api/role-launch/dispatch")).toBe(true);
    });
    return { parked, done, run };
  }

  function dispatchAnswer(parked: { answer: (value: Response) => void }[], requestId: string, over: Record<string, unknown> = {}) {
    parked.splice(0).forEach((call) =>
      call.answer(new Response(JSON.stringify(paseoReceipt(requestId, over)), { status: 200 })));
  }

  async function sentRequestId(): Promise<string> {
    // Wait for the dispatch request itself, not a wall-clock sleep.
    await vi.waitFor(() => {
      expect(seen.some((call) => call.url === "/api/role-launch/dispatch")).toBe(true);
    });
    const dispatches = seen.filter((call) => call.url === "/api/role-launch/dispatch");
    return (dispatches[dispatches.length - 1]?.body as Record<string, string>)["requestId"];
  }

  it("calls only the replaced callback", async () => {
    const before = vi.fn();
    const notifyRef = { current: before as ((receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void) | undefined };
    const { parked, done } = await startWithCallback(notifyRef);
    const after = vi.fn();
    notifyRef.current = after;
    dispatchAnswer(parked, await sentRequestId());
    await done;
    expect(after).toHaveBeenCalledTimes(1);
    expect(before).not.toHaveBeenCalled();
  });

  it("stays silent when the callback is removed mid-flight", async () => {
    const before = vi.fn();
    const notifyRef = { current: before as ((receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void) | undefined };
    const { parked, done } = await startWithCallback(notifyRef);
    notifyRef.current = undefined;
    dispatchAnswer(parked, await sentRequestId());
    await done;
    expect(before).not.toHaveBeenCalled();
  });

  it("still runs the unchanged callback (positive control)", async () => {
    const notify = vi.fn();
    const { parked, done } = await startWithCallback({ current: notify });
    dispatchAnswer(parked, await sentRequestId());
    await done;
    expect(notify).toHaveBeenCalledTimes(1);
  });

  it("never notifies a wrong, rejected, or hostless receipt", async () => {
    const notify = vi.fn();
    const ref = { current: notify as ((receipt: RoleExecutionReceipt, scope: RoleDocumentScope) => void) | undefined };
    // Wrong request id: the dispatch terminates before any notification.
    {
      const { parked, done } = await startWithCallback(ref);
      dispatchAnswer(parked, "foreign-id");
      await done;
    }
    // Rejected status: no usable host target.
    {
      const { parked, done } = await startWithCallback(ref);
      dispatchAnswer(parked, await sentRequestId(), { status: "rejected" });
      await done;
    }
    // Unusable host: no paseo agent behind the receipt.
    {
      const { parked, done } = await startWithCallback(ref);
      dispatchAnswer(parked, await sentRequestId(), { execution: { kind: "other-host", agentId: "codex" } });
      await done;
    }
    expect(notify).not.toHaveBeenCalled();
  });
});
