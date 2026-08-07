import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { announcerStore } from "../data/announcer";
import { capabilityCatalogStore } from "../data/capabilityCatalog";
import { hydrateTerminalSessionsFromCatalog } from "../data/catalogPoll";
import { sessionCockpitStore } from "../data/sessionCockpitStore";
import { lifecycleNoticeStore } from "../data/sessionLifecycle";
import { ptyHarvestStore } from "../data/ptyHarvest";
import {
  fromTerminalSessionInfo,
  registerConnection,
  sendToSession,
  sessionStore,
} from "../data/sessions";
import {
  readSubmissionAuthority,
  type SubmissionLifecycleTransport,
  type WithdrawalResultWire,
} from "../data/submissionLifecycleClient";
import { withdrawLastQueuedSubmission } from "../data/submissionWithdrawal";
import { startSubmitRecord } from "../data/submitMachine";
import { catalogRow } from "../test/fixtures/catalogRows";
import {
  openTerminalSession,
  type TerminalConnection,
} from "../data/terminal";
import {
  COCKPIT_SCENARIOS,
  installCockpitScenarioFetch,
  resetCockpitScenario,
} from "./cockpitScenarios";

const fleet = COCKPIT_SCENARIOS.find(
  (scenario) => scenario.kind === "fleet-12",
)!;
const REUSED_SESSION = "scenario-reused-session";
const OLD_REQUEST = "old-scenario-request";
const NEW_REQUEST = "new-scenario-request";
const reusedSessionScenario = {
  ...fleet,
  name: "sessions-reused-authority-test",
  rows: [catalogRow({ id: REUSED_SESSION, label: "new-authority-row" })],
};

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

function authorityTransport(onRead: () => void): SubmissionLifecycleTransport {
  return {
    authority: async () => {
      onRead();
      return { bridgeEpoch: "scenario-reset-epoch" };
    },
    status: async () => ({
      bridgeEpoch: "scenario-reset-epoch",
      submissions: [],
    }),
    // The return type is written out on purpose. An object literal in an ASYNC arrow's concise
    // body loses excess-property checking — the literal is compared to the inferred `Promise<…>`
    // rather than checked fresh against the contextual one — so this result carried a
    // `bridgeEpoch` that `WithdrawalResultWire` does not declare (nor does the server's
    // `extra="forbid"` model; it was copied from the `/submit` receipt, which does carry one).
    // With the annotation, a re-added field fails `tsc -b` right here.
    withdraw: async (_sessionId, _epoch, requestId): Promise<WithdrawalResultWire> => ({
      requestId,
      outcome: "not-found",
      state: null,
      withdrawnAt: null,
      detail: null,
    }),
  };
}

function seedQueued(sessionId: string, requestId: string, text: string): void {
  const store = sessionCockpitStore.getState();
  store.upsertSubmitRecord(sessionId, {
    ...startSubmitRecord({
      requestId,
      text,
      expectedBridgeEpoch: "scenario-epoch",
      submittedRevision: 0,
      at: 1,
    }),
    phase: "queued",
    serverLifecycleState: "queued",
  });
  store.enqueueSubmit(sessionId, {
    requestId,
    text,
    preview: text,
    queuedAt: 1,
    expectedBridgeEpoch: "scenario-epoch",
    state: "queued",
  });
}

beforeEach(() => {
  resetCockpitScenario(fleet);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the scenario server answers only what the daemon could answer", () => {
  // Two routes whose response types live in UNMARKED modules — `data/harnessCatalog.ts` and
  // `data/submissionLifecycleClient.ts` — and are therefore not in `wireFixtureGuard.ts`'s
  // vocabulary. Both had a field the server cannot send: a `control` on every catalog row, and a
  // `bridgeEpoch` on the withdrawal result. The `satisfies`/return-type pins beside each fixture
  // catch a field added to a fresh literal; these two assertions catch the rest.

  it("serves harness catalog rows with exactly `DetectedHarness`'s three fields", async () => {
    const restore = installCockpitScenarioFetch(fleet);
    try {
      const body = (await (await window.fetch("/api/harnesses")).json()) as {
        harnesses: Array<Record<string, unknown>>;
      };
      expect(body.harnesses.length).toBeGreaterThan(0);
      for (const row of body.harnesses) {
        expect(Object.keys(row).sort()).toEqual(["detected", "id", "name"]);
      }
    } finally {
      restore();
    }
  });

  it("withdraws with exactly the fields `WithdrawalResultWire` declares", async () => {
    const result = await authorityTransport(() => {}).withdraw("s", "e", "req-1");
    expect(Object.keys(result).sort()).toEqual([
      "detail",
      "outcome",
      "requestId",
      "state",
      "withdrawnAt",
    ]);
  });
});

describe("cockpit scenario authority boundary", () => {
  it("models raw and harness opens as explicit accepted HTTP authority", async () => {
    const restore = installCockpitScenarioFetch(fleet);
    try {
      const raw = await openTerminalSession(
        "bench-raw",
        "terminal",
        "",
        undefined,
        { label: "Terminal" },
      );
      expect(raw).toMatchObject({
        outcome: "opened",
        session: {
          id: "bench-raw",
          label: "Terminal",
          kind: "terminal",
          status: "running",
        },
      });
      if (raw.outcome !== "opened") throw new Error(raw.detail);
      expect(raw.session.harness).toBeUndefined();
      expect(raw.session.controlState).toBeUndefined();

      const harness = await openTerminalSession(
        "bench-harness",
        "harness",
        "",
        "codex",
        { label: "Codex", model: "gpt-5.6-sol", effort: "xhigh" },
      );
      expect(harness).toMatchObject({
        outcome: "opened",
        session: {
          id: "bench-harness",
          label: "Codex",
          kind: "harness",
          harness: "codex",
          controlState: "starting",
          resolvedModel: "gpt-5.6-sol",
          resolvedEffort: "xhigh",
        },
      });

      const catalog = await window.fetch("/api/terminal/sessions");
      const body = (await catalog.json()) as {
        sessions: Array<Record<string, unknown>>;
      };
      expect(body.sessions).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: "bench-raw",
            kind: "terminal",
          }),
          expect.objectContaining({
            id: "bench-harness",
            kind: "harness",
            harness: "codex",
          }),
        ]),
      );
      expect(body.sessions.find((row) => row.id === "bench-raw")).not.toHaveProperty(
        "harness",
      );
    } finally {
      restore();
    }
  });

  it("clears every transient Sessions store while preserving only declared user preferences", async () => {
    sessionStore
      .getState()
      .hydrate([
        fromTerminalSessionInfo(
          catalogRow({ id: "old-session", label: "old-session" }),
        ),
      ]);
    sessionCockpitStore
      .getState()
      .setComposerDraft("old-session", "must disappear");
    sessionCockpitStore.setState({
      focusedSessionId: "old-session",
      layout: { railCollapsed: true, inspectorCollapsed: true },
      paletteOpen: true,
      orchestrationTreeView: true,
    });
    capabilityCatalogStore.setState({
      perHarness: { claude: { fetchState: "loading" } },
    });
    announcerStore.setState({
      polite: { text: "old polite", seq: 9 },
      assertive: { text: "old assertive", seq: 8 },
    });
    lifecycleNoticeStore.getState().recordResidual({
      sessionId: "old-session",
      label: "old-session",
      kind: "terminate",
      detail: "old residual",
      at: 1,
    });
    ptyHarvestStore.getState().recordBell("old-session", 1);

    let authorityReads = 0;
    const transport = authorityTransport(() => {
      authorityReads += 1;
    });
    await readSubmissionAuthority("old-session", transport);
    await readSubmissionAuthority("old-session", transport);
    expect(authorityReads).toBe(1); // prove the cache was populated before reset.

    const queuedSend = vi.fn();
    sendToSession("old-session", "queued input must not cross fixtures");
    resetCockpitScenario(fleet);
    registerConnection("old-session", {
      sendInput: queuedSend,
    } as unknown as TerminalConnection);

    expect(
      sessionStore.getState().sessions.map((session) => session.id),
    ).toEqual(fleet.rows.map((row) => row.id));
    expect(sessionStore.getState().activeId).toBe(fleet.rows[0]?.id);
    expect(sessionCockpitStore.getState()).toMatchObject({
      focusedSessionId: fleet.rows[0]?.id,
      layout: { railCollapsed: false, inspectorCollapsed: false },
      paletteOpen: false,
      orchestrationTreeView: true,
      perSession: {},
    });
    expect(capabilityCatalogStore.getState().perHarness).toEqual({});
    expect(announcerStore.getState()).toMatchObject({
      polite: { text: "", seq: 0 },
      assertive: { text: "", seq: 0 },
    });
    expect(lifecycleNoticeStore.getState()).toMatchObject({
      residuals: [],
      cleanupOutcome: null,
      cleanupFailure: null,
      sweptRetire: {},
    });
    expect(ptyHarvestStore.getState().bySession).toEqual({});
    expect(queuedSend).not.toHaveBeenCalled();

    await readSubmissionAuthority("old-session", transport);
    expect(authorityReads).toBe(2); // module-level submission authority cache was cleared too.
    registerConnection("old-session", null);
  });

  it("revokes an old authority read without satisfying or overwriting a new same-id read", async () => {
    const oldAuthority = deferred<{ bridgeEpoch: string }>();
    const oldReadTransport = authorityTransport(() => {});
    oldReadTransport.authority = vi.fn(() => oldAuthority.promise);
    const oldRead = readSubmissionAuthority(REUSED_SESSION, oldReadTransport);
    expect(oldReadTransport.authority).toHaveBeenCalledTimes(1);

    resetCockpitScenario(reusedSessionScenario);
    const newReadTransport = authorityTransport(() => {});
    newReadTransport.authority = vi.fn(async () => ({
      bridgeEpoch: "new-scenario-epoch",
    }));
    await expect(
      readSubmissionAuthority(REUSED_SESSION, newReadTransport),
    ).resolves.toEqual({
      bridgeEpoch: "new-scenario-epoch",
    });
    expect(newReadTransport.authority).toHaveBeenCalledTimes(1);

    oldAuthority.resolve({ bridgeEpoch: "old-scenario-epoch" });
    await expect(oldRead).resolves.toEqual({
      bridgeEpoch: "old-scenario-epoch",
    });
    await expect(
      readSubmissionAuthority(REUSED_SESSION, newReadTransport),
    ).resolves.toEqual({
      bridgeEpoch: "new-scenario-epoch",
    });
    expect(newReadTransport.authority).toHaveBeenCalledTimes(1);
  });

  it("keeps an old withdrawal settlement away from a newer same-id owner", async () => {
    resetCockpitScenario(reusedSessionScenario);
    seedQueued(REUSED_SESSION, OLD_REQUEST, "old authority draft");
    const oldResult = deferred<WithdrawalResultWire>();
    const oldTransport = authorityTransport(() => {});
    oldTransport.withdraw = vi.fn(() => oldResult.promise);
    const oldWithdrawal = withdrawLastQueuedSubmission(REUSED_SESSION, {
      transport: oldTransport,
      now: () => 10,
    });

    resetCockpitScenario(reusedSessionScenario);
    expect(sessionCockpitStore.getState().perSession).toEqual({});
    seedQueued(REUSED_SESSION, NEW_REQUEST, "new authority draft");
    const newResult = deferred<WithdrawalResultWire>();
    const newTransport = authorityTransport(() => {});
    newTransport.withdraw = vi.fn(() => newResult.promise);
    const newWithdrawal = withdrawLastQueuedSubmission(REUSED_SESSION, {
      transport: newTransport,
      now: () => 20,
    });

    oldResult.resolve({
      requestId: OLD_REQUEST,
      outcome: "withdrawn",
      state: "withdrawn",
      withdrawnAt: "2026-07-18T00:00:00Z",
      detail: "old authority result",
    });
    await expect(oldWithdrawal).resolves.toContain("authority changed");

    const joinedNewWithdrawal = withdrawLastQueuedSubmission(REUSED_SESSION, {
      transport: authorityTransport(() => {}),
    });
    expect(joinedNewWithdrawal).toBe(newWithdrawal);
    expect(
      sessionStore.getState().sessions.map((session) => session.id),
    ).toEqual([REUSED_SESSION]);
    expect(
      sessionCockpitStore.getState().perSession[REUSED_SESSION],
    ).toMatchObject({
      composer: { draft: "", draftRevision: 0 },
      queue: [{ requestId: NEW_REQUEST, text: "new authority draft" }],
      submitHistory: [{ requestId: NEW_REQUEST }],
      withdrawal: {
        phase: "pending",
        requestId: NEW_REQUEST,
        text: "new authority draft",
      },
    });

    newResult.resolve({
      requestId: NEW_REQUEST,
      outcome: "withdrawn",
      state: "withdrawn",
      withdrawnAt: "2026-07-18T00:00:01Z",
      detail: "new authority result",
    });
    await expect(newWithdrawal).resolves.toContain("restored for editing");
    const settled = sessionCockpitStore.getState().perSession[REUSED_SESSION];
    expect(settled.composer).toEqual({
      draft: "new authority draft",
      draftRevision: 1,
    });
    expect(settled.queue).toEqual([]);
    expect(settled.submitHistory).toHaveLength(1);
    expect(settled.submitHistory[0]).toMatchObject({
      requestId: NEW_REQUEST,
      phase: "withdrawn",
    });
  });

  it("revokes an old catalog hydrate before it can replace new rows or poll health", async () => {
    const oldCatalog = deferred<Response>();
    const fetchMock = vi.fn(() => oldCatalog.promise);
    vi.stubGlobal("fetch", fetchMock);
    const oldHydration = hydrateTerminalSessionsFromCatalog(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resetCockpitScenario(reusedSessionScenario);
    const newPollHealth = { lastBeatAt: 123, missedBeats: 2, healthy: true };
    sessionCockpitStore.setState({ pollHealth: newPollHealth });
    oldCatalog.resolve(
      new Response(
        JSON.stringify({ sessions: [catalogRow({ id: "old-authority-row" })] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(oldHydration).resolves.toBe(false);
    expect(
      sessionStore.getState().sessions.map((session) => session.id),
    ).toEqual([REUSED_SESSION]);
    expect(sessionStore.getState().activeId).toBe(REUSED_SESSION);
    expect(sessionCockpitStore.getState().pollHealth).toEqual(newPollHealth);
  });
});
