// Lifecycle flows (260715-FEUI-L6 R5): terminate keeps the stop residual, bulk cleanup keeps
// the route's honest outcome, and the residual copy is INFORMATIONAL — never a failure word.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  cleanupOutcomeCopy,
  retireResidualCopy,
  terminateConfirmCopy,
  terminateResidualCopy,
} from "../panels/session-cockpit/lifecycleCopy";
import { fromTerminalSessionInfo, sessionStore } from "./sessions";
import {
  endLandedDetailed,
  endSessionDetailed,
  lifecycleNoticeStore,
  startRetireResidualSweep,
  terminateSessionDetailed,
} from "./sessionLifecycle";
import {
  L6_CONTROLLED_WORKING,
  L6_RETIRED_WITH_STOP_ERROR,
  L6_TERMINATE_RESPONSE_WITH_RESIDUAL,
} from "../test/fixtures/catalogRows";

beforeEach(() => {
  lifecycleNoticeStore.setState({ residuals: [], cleanupOutcome: null, sweptRetire: {} });
  sessionStore.getState().hydrate([]);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("terminateSessionDetailed", () => {
  it("keeps controlStopDetail from the terminate response instead of discarding the body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => L6_TERMINATE_RESPONSE_WITH_RESIDUAL,
      })) as unknown as typeof fetch,
    );
    const outcome = await terminateSessionDetailed("l6-controlled");
    expect(outcome).toEqual({ ok: true, controlStopDetail: "control command queue is stopped" });
  });

  it("a clean terminate carries no residual", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ session: "x", status: "terminated" }),
      })) as unknown as typeof fetch,
    );
    expect(await terminateSessionDetailed("x")).toEqual({ ok: true, controlStopDetail: undefined });
  });

  it("a FAILED terminate POST keeps the server's words verbatim (review finding 4)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 502,
        text: async () => "bridge host unavailable",
      })) as unknown as typeof fetch,
    );
    expect(await terminateSessionDetailed("x")).toEqual({
      ok: false,
      error: "bridge host unavailable",
    });
  });
});

describe("retire-residual sweep (review F1, sev-3 — focus-independent capture)", () => {
  it("captures retireControlStopError for an UNFOCUSED tombstoned row, exactly once", () => {
    const release = startRetireResidualSweep();
    const retired = fromTerminalSessionInfo(L6_RETIRED_WITH_STOP_ERROR);
    sessionStore.getState().hydrate([retired]); // nothing focused this row — data-layer capture
    let residuals = lifecycleNoticeStore.getState().residuals;
    expect(residuals).toHaveLength(1);
    expect(residuals[0]).toMatchObject({
      sessionId: "l6-retired-residual",
      kind: "retire",
      detail: "control command queue is stopped",
    });
    // The catalog keeps serving the row every beat — the sweep must not duplicate it.
    sessionStore.getState().hydrate([retired]);
    expect(lifecycleNoticeStore.getState().residuals).toHaveLength(1);
    // A dismissal stays dismissed across later beats (the swept-set remembers).
    const entry = lifecycleNoticeStore.getState().residuals[0];
    lifecycleNoticeStore.getState().dismissResidual(entry.sessionId, entry.at);
    sessionStore.getState().hydrate([retired]);
    residuals = lifecycleNoticeStore.getState().residuals;
    expect(residuals).toHaveLength(0);
    release();
  });

  it("rows already in the store when the sweep starts are captured too (reload path)", () => {
    sessionStore.getState().hydrate([fromTerminalSessionInfo(L6_RETIRED_WITH_STOP_ERROR)]);
    const release = startRetireResidualSweep();
    expect(lifecycleNoticeStore.getState().residuals).toHaveLength(1);
    release();
  });
});

describe("endSessionDetailed (the cockpit terminate flow)", () => {
  it("tombstones the row and records the stop residual as an informational notice", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => L6_TERMINATE_RESPONSE_WITH_RESIDUAL,
      })) as unknown as typeof fetch,
    );
    const session = fromTerminalSessionInfo(L6_CONTROLLED_WORKING);
    sessionStore.getState().hydrate([session]);
    const outcome = await endSessionDetailed(session);
    expect(outcome.ok).toBe(true);
    expect(sessionStore.getState().sessions.find((s) => s.id === session.id)).toBeUndefined();
    const residuals = lifecycleNoticeStore.getState().residuals;
    expect(residuals).toHaveLength(1);
    expect(residuals[0]).toMatchObject({
      sessionId: "l6-controlled",
      kind: "terminate",
      detail: "control command queue is stopped",
    });
  });
});

describe("endLandedDetailed (bulk cleanup honesty)", () => {
  it("records the route's own closed + skipped outcome — skips never vanish", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("landed-cleanup")) {
          return {
            ok: true,
            json: async () => ({
              closed: 1,
              skipped: 1,
              closedSessions: ["landed-a"],
              skippedSessions: [{ session: "landed-b", reason: "status:running" }],
            }),
          } as Response;
        }
        return { ok: true, json: async () => ({ sessions: [] }) } as Response;
      }),
    );
    const rows = [
      fromTerminalSessionInfo({ ...L6_CONTROLLED_WORKING, id: "landed-a", status: "landed" }),
      fromTerminalSessionInfo({ ...L6_CONTROLLED_WORKING, id: "landed-b", status: "landed" }),
    ];
    sessionStore.getState().hydrate(rows);
    const result = await endLandedDetailed(rows);
    expect(result?.closed).toBe(1);
    expect(lifecycleNoticeStore.getState().cleanupOutcome).toMatchObject({
      closed: 1,
      skipped: 1,
    });
    expect(cleanupOutcomeCopy(lifecycleNoticeStore.getState().cleanupOutcome!)).toBe(
      "ended 1 · skipped 1 (landed-b: status:running)",
    );
  });
});

describe("lifecycleCopy honesty rules", () => {
  it("the terminate confirm NAMES session, leaf, and state", () => {
    const session = fromTerminalSessionInfo(L6_CONTROLLED_WORKING);
    const copy = terminateConfirmCopy(session);
    expect(copy).toContain("worker-l6-controlled");
    expect(copy).toContain("leaf 06_pty-stage-interactions-lifecycle");
    expect(copy).toContain("state working");
  });

  it("stop residuals are informational lines — the word 'failed' never appears", () => {
    for (const copy of [
      terminateResidualCopy("seat-x", "control command queue is stopped"),
      retireResidualCopy("seat-x", "control command queue is stopped"),
    ]) {
      expect(copy).toContain("informational");
      expect(copy).toContain("control command queue is stopped");
      expect(copy.toLowerCase()).not.toContain("fail");
    }
  });
});
