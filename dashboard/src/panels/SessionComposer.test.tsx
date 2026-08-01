import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sessionCockpitStore } from "../data/sessionCockpitStore";
import { writeKeymapPreferences } from "../data/keymap/preferences";
import { fromTerminalSessionInfo, sessionStore } from "../data/sessions";
import { dashboardStore } from "../data/store";
import { startSubmitRecord } from "../data/submitMachine";
import {
  clearSubmissionAuthorityCache,
  pollSubmissionLifecycleOnce,
  type SubmissionLifecycleTransport,
  type SubmissionStatusBatchWire,
} from "../data/submissionLifecycleClient";
import { catalogRow } from "../test/fixtures/catalogRows";
import { lifecycleWithGate } from "../test/fixtures/wire";
import {
  L5_ALREADY_DELIVERED_RACE_FIXTURE,
  L5_MULTI_QUEUE_FIXTURE,
  L5_POP_BACK_SUPERSESSION_FIXTURE,
} from "../test/fixtures/submitScenarios";
import { SessionComposer, type SessionComposerHandle } from "./SessionComposer";

const readySession = (id = "composer-ready") =>
  fromTerminalSessionInfo(catalogRow({ id, label: id, controlState: "ready" }));

function receipt(requestId: string, acceptance: "immediate" | "queued" = "immediate") {
  return {
    ok: true,
    json: async () => ({
      requestId,
      acceptance,
      submittedAt: "2026-07-17T10:00:00Z",
      vendorCorrelationId: null,
      acceptedAt: acceptance === "immediate" ? "2026-07-17T10:00:01Z" : null,
      detail: null,
      bridgeEpoch: "bridge-epoch-l5",
    }),
  } as Response;
}

beforeEach(() => {
  window.localStorage.clear();
  writeKeymapPreferences({});
  sessionCockpitStore.setState({ perSession: {} });
  sessionStore.getState().hydrate([]);
  dashboardStore.setState({ lifecycles: {} });
  clearSubmissionAuthorityCache();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SessionComposer (FEUI-L5)", () => {
  it("reconfigures same-tab profile/binding writes without rebuilding or revising the draft", async () => {
    const session = readySession("live-keymap-write");
    sessionStore.getState().hydrate([session]);
    const ref = createRef<SessionComposerHandle>();
    const { getByTestId } = render(<SessionComposer ref={ref} session={session} />);
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "draft survives rebind"));
    await waitFor(() => expect(ref.current?.getDraft()).toBe("draft survives rebind"));
    const editorNode = getByTestId("session-composer-editor").querySelector(".cm-content");
    const revision =
      sessionCockpitStore.getState().perSession[session.id]?.composer.draftRevision;

    act(() => {
      writeKeymapPreferences({
        composerProfile: "vim",
        bindings: { "focus.nextRegion": "K", "focus.prevRegion": "L" },
      });
    });

    await waitFor(() =>
      expect(getByTestId("session-composer-editor").getAttribute("data-composer-profile")).toBe(
        "vim",
      ),
    );
    expect(getByTestId("session-composer-editor").querySelector(".cm-content")).toBe(editorNode);
    expect(ref.current?.getDraft()).toBe("draft survives rebind");
    expect(sessionCockpitStore.getState().perSession[session.id]?.composer).toEqual({
      draft: "draft survives rebind",
      draftRevision: revision,
    });
  });

  it("submits the exact multiline/non-ASCII draft through /submit and clears only on acceptance", async () => {
    const session = readySession();
    sessionStore.getState().hydrate([session]);
    const calls: Array<{
      url: string;
      body: { requestId: string; text: string };
    }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/submission-authority")) {
          return {
            ok: true,
            json: async () => ({ bridgeEpoch: "bridge-epoch-l5" }),
          } as Response;
        }
        const body = JSON.parse(String(init?.body)) as {
          requestId: string;
          text: string;
        };
        calls.push({ url: String(url), body });
        return receipt(body.requestId);
      }),
    );
    const { getByTestId } = render(<SessionComposer session={session} />);
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "  α\nβ  "));
    fireEvent.click(getByTestId("session-composer-send"));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      url: `/api/terminal/${session.id}/submit`,
      body: {
        requestId: expect.any(String),
        text: "  α\nβ  ",
        expectedBridgeEpoch: "bridge-epoch-l5",
      },
    });
    await waitFor(() =>
      expect(sessionCockpitStore.getState().perSession[session.id]?.composer.draft).toBe(""),
    );
    expect(getByTestId("session-composer-status").textContent).toContain("delivered");
  });

  it("keeps a newer human revision when an older async submit resolves", async () => {
    const session = readySession("revision-guard");
    sessionStore.getState().hydrate([session]);
    let release: (response: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/submission-authority")) {
          return {
            ok: true,
            json: async () => ({ bridgeEpoch: "bridge-epoch-l5" }),
          } as Response;
        }
        const body = JSON.parse(String(init?.body)) as { requestId: string };
        return new Promise<Response>((resolve) => {
          release = () => resolve(receipt(body.requestId));
        });
      }),
    );
    const { getByTestId } = render(<SessionComposer session={session} />);
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "old draft"));
    fireEvent.click(getByTestId("session-composer-send"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().perSession[session.id]?.submitHistory[0]?.phase).toBe(
        "sending",
      ),
    );
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "new human edit"));
    release({} as Response);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().perSession[session.id]?.submitHistory[0]?.phase).toBe(
        "accepted",
      ),
    );
    expect(sessionCockpitStore.getState().perSession[session.id]?.composer.draft).toBe(
      "new human edit",
    );
  });

  it("preserves drafts per session and opens slash-to-palette only at line start", async () => {
    const first = readySession("draft-one");
    const second = readySession("draft-two");
    sessionStore.getState().hydrate([first, second]);
    const openPalette = vi.fn();
    const ref = createRef<SessionComposerHandle>();
    const { container, rerender } = render(
      <SessionComposer ref={ref} session={first} onSlashAtLineStart={openPalette} />,
    );
    act(() => sessionCockpitStore.getState().setComposerDraft(first.id, "first draft"));
    await waitFor(() => expect(ref.current?.getDraft()).toBe("first draft"));
    rerender(<SessionComposer ref={ref} session={second} onSlashAtLineStart={openPalette} />);
    act(() => sessionCockpitStore.getState().setComposerDraft(second.id, "second draft"));
    await waitFor(() => expect(ref.current?.getDraft()).toBe("second draft"));
    rerender(<SessionComposer ref={ref} session={first} onSlashAtLineStart={openPalette} />);
    await waitFor(() => expect(ref.current?.getDraft()).toBe("first draft"));

    act(() => sessionCockpitStore.getState().setComposerDraft(first.id, ""));
    const content = container.querySelector(".cm-content");
    expect(content).not.toBeNull();
    fireEvent.keyDown(content!, { key: "/" });
    expect(openPalette).toHaveBeenCalledTimes(1);
  });

  it("Alt+Up restores exact text only after authoritative withdrawal", async () => {
    const session = readySession("queue-pop");
    sessionStore.getState().hydrate([session]);
    sessionCockpitStore.getState().enqueueSubmit(session.id, L5_POP_BACK_SUPERSESSION_FIXTURE);
    sessionCockpitStore.getState().upsertSubmitRecord(session.id, {
      ...startSubmitRecord({
        requestId: L5_POP_BACK_SUPERSESSION_FIXTURE.requestId,
        text: L5_POP_BACK_SUPERSESSION_FIXTURE.text,
        expectedBridgeEpoch: L5_POP_BACK_SUPERSESSION_FIXTURE.expectedBridgeEpoch,
        submittedRevision: 0,
        at: 3,
      }),
      phase: "queued",
      serverLifecycleState: "queued",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: true,
            json: async () => ({
              requestId: L5_POP_BACK_SUPERSESSION_FIXTURE.requestId,
              outcome: "withdrawn",
              state: "withdrawn",
              withdrawnAt: "2026-07-17T10:00:03Z",
              detail: null,
            }),
          }) as Response,
      ),
    );
    const ref = createRef<SessionComposerHandle>();
    const { getByTestId } = render(<SessionComposer ref={ref} session={session} />);
    act(() => ref.current?.popBack());
    await waitFor(() =>
      expect(ref.current?.getDraft()).toBe(L5_POP_BACK_SUPERSESSION_FIXTURE.text),
    );
    expect(sessionCockpitStore.getState().perSession[session.id]?.queue).toEqual([]);
    expect(sessionCockpitStore.getState().perSession[session.id]?.submitHistory[0]).toMatchObject({
      phase: "withdrawn",
      restoredAt: expect.any(Number),
    });
    expect(getByTestId("session-composer-status").textContent).toContain(
      "restored for editing under a new requestId",
    );
  });

  it("keeps a newer draft visible and exposes exact withdrawn-text recovery", async () => {
    const session = readySession("queue-pop-revision-race");
    sessionStore.getState().hydrate([session]);
    sessionCockpitStore.getState().enqueueSubmit(session.id, L5_POP_BACK_SUPERSESSION_FIXTURE);
    sessionCockpitStore.getState().upsertSubmitRecord(session.id, {
      ...startSubmitRecord({
        requestId: L5_POP_BACK_SUPERSESSION_FIXTURE.requestId,
        text: L5_POP_BACK_SUPERSESSION_FIXTURE.text,
        expectedBridgeEpoch: L5_POP_BACK_SUPERSESSION_FIXTURE.expectedBridgeEpoch,
        submittedRevision: 0,
        at: 3,
      }),
      phase: "queued",
      serverLifecycleState: "queued",
    });
    let release: (response: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Promise<Response>((resolve) => {
            release = resolve;
          }),
      ),
    );

    const ref = createRef<SessionComposerHandle>();
    const { getByTestId } = render(<SessionComposer ref={ref} session={session} />);
    act(() => ref.current?.popBack());
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "new human edit"));
    release({
      ok: true,
      json: async () => ({
        requestId: L5_POP_BACK_SUPERSESSION_FIXTURE.requestId,
        outcome: "withdrawn",
        state: "withdrawn",
        withdrawnAt: "2026-07-17T10:00:03Z",
        detail: null,
      }),
    } as Response);

    await waitFor(() => expect(getByTestId("withdrawn-recovery")).toBeTruthy());
    expect(ref.current?.getDraft()).toBe("new human edit");
    expect(getByTestId("withdrawn-recovery-text").textContent).toBe(
      L5_POP_BACK_SUPERSESSION_FIXTURE.text,
    );
    fireEvent.click(getByTestId("withdrawn-recovery-replace"));
    await waitFor(() =>
      expect(ref.current?.getDraft()).toBe(L5_POP_BACK_SUPERSESSION_FIXTURE.text),
    );
    expect(sessionCockpitStore.getState().perSession[session.id]?.withdrawal).toBeUndefined();
  });

  it("keeps the current draft only through the explicit recovery-dismiss action", async () => {
    const session = readySession("queue-pop-keep-current");
    sessionStore.getState().hydrate([session]);
    sessionCockpitStore.getState().enqueueSubmit(session.id, L5_POP_BACK_SUPERSESSION_FIXTURE);
    sessionCockpitStore.getState().upsertSubmitRecord(session.id, {
      ...startSubmitRecord({
        requestId: L5_POP_BACK_SUPERSESSION_FIXTURE.requestId,
        text: L5_POP_BACK_SUPERSESSION_FIXTURE.text,
        expectedBridgeEpoch: L5_POP_BACK_SUPERSESSION_FIXTURE.expectedBridgeEpoch,
        submittedRevision: 0,
        at: 3,
      }),
      phase: "queued",
      serverLifecycleState: "queued",
    });
    let release: (response: Response) => void = () => {};
    const fetchMock = vi.fn(
      async () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const ref = createRef<SessionComposerHandle>();
    const { getByTestId } = render(<SessionComposer ref={ref} session={session} />);
    act(() => ref.current?.popBack());
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "keep newer draft"));
    release({
      ok: true,
      json: async () => ({
        requestId: L5_POP_BACK_SUPERSESSION_FIXTURE.requestId,
        outcome: "withdrawn",
        state: "withdrawn",
        withdrawnAt: "2026-07-17T10:00:03Z",
        detail: null,
      }),
    } as Response);

    await waitFor(() => expect(getByTestId("withdrawn-recovery-keep-current")).toBeTruthy());
    fireEvent.click(getByTestId("withdrawn-recovery-keep-current"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().perSession[session.id]?.withdrawal).toBeUndefined(),
    );
    expect(ref.current?.getDraft()).toBe("keep newer draft");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getByTestId("session-composer-status").textContent).toContain(
      "current draft kept; withdrawn text dismissed",
    );
  });

  it("renders terminal not-found as explicitly non-withdrawable and not restored", () => {
    const session = readySession("queue-not-found");
    sessionStore.getState().hydrate([session]);
    sessionCockpitStore.getState().upsertSubmitRecord(session.id, {
      ...startSubmitRecord({
        requestId: "not-found-request",
        text: "unretained text",
        expectedBridgeEpoch: "bridge-epoch-l5",
        submittedRevision: 0,
        at: 3,
      }),
      phase: "not-found",
      detail: "submission is not retained by this authority",
    });

    const { getByTestId } = render(<SessionComposer session={session} />);
    const status = getByTestId("session-composer-status");
    expect(status.textContent).toContain("submission not retained by this authority");
    expect(status.textContent).toContain("withdrawal unavailable");
    expect(status.textContent).toContain("draft was not restored");
    expect(status.textContent).not.toContain("withdrawable");
  });

  it("keeps QueuePreview and visible unknown copy monotonic after an older queued poll", async () => {
    const session = readySession("queued-after-unknown");
    const requestId = "queued-after-unknown-request";
    const text = "possible send must stay visible";
    const epoch = "bridge-epoch-l5";
    sessionStore.getState().hydrate([session]);
    sessionCockpitStore.getState().enqueueSubmit(session.id, {
      requestId,
      text,
      preview: text,
      queuedAt: 3,
      expectedBridgeEpoch: epoch,
      state: "queued",
    });
    sessionCockpitStore.getState().upsertSubmitRecord(session.id, {
      ...startSubmitRecord({
        requestId,
        text,
        expectedBridgeEpoch: epoch,
        submittedRevision: 0,
        at: 3,
      }),
      phase: "queued",
      serverLifecycleState: "queued",
    });

    let releaseStatus: (value: SubmissionStatusBatchWire) => void = () => {};
    const delayedStatus = new Promise<SubmissionStatusBatchWire>((resolve) => {
      releaseStatus = resolve;
    });
    const pollTransport: SubmissionLifecycleTransport = {
      authority: vi.fn(async () => ({ bridgeEpoch: epoch })),
      status: vi.fn(async () => delayedStatus),
      withdraw: vi.fn(async () => {
        throw new Error("not used");
      }),
    };
    const poll = pollSubmissionLifecycleOnce(session.id, pollTransport, 4);
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: true,
            json: async () => ({
              requestId,
              outcome: "not-withdrawable",
              state: "unknown",
              withdrawnAt: null,
              detail: "delivery is unresolved",
            }),
          }) as Response,
      ),
    );

    const ref = createRef<SessionComposerHandle>();
    const { getByTestId, queryByTestId } = render(
      <SessionComposer ref={ref} session={session} />,
    );
    expect(getByTestId("queue-preview")).toBeTruthy();
    act(() => ref.current?.popBack());
    await waitFor(() =>
      expect(getByTestId("session-composer-status").textContent).toContain(
        "delivery unknown — cannot restore",
      ),
    );
    expect(queryByTestId("queue-preview")).toBeNull();

    releaseStatus({
      bridgeEpoch: epoch,
      submissions: [
        {
          requestId,
          outcome: "found",
          submission: {
            state: "queued",
            submittedAt: "2026-07-17T10:00:00Z",
            updatedAt: "2026-07-17T10:00:01Z",
            acceptedAt: null,
            withdrawable: true,
            detail: null,
          },
        },
      ],
    });
    await act(async () => {
      await poll;
    });

    const status = getByTestId("session-composer-status").textContent ?? "";
    expect(queryByTestId("queue-preview")).toBeNull();
    expect(status).toContain("ambiguous · reconciling the same requestId");
    expect(status).not.toContain("queued · withdrawable");
  });

  it("renders every authority-confirmed queued item as a 'yours' block + delivery row", () => {
    const session = readySession("multi-queue");
    sessionStore.getState().hydrate([session]);
    for (const item of L5_MULTI_QUEUE_FIXTURE) {
      sessionCockpitStore.getState().enqueueSubmit(session.id, item);
      // The withdrawable queue surface requires the authority lifecycle's own queued word.
      sessionCockpitStore.getState().upsertSubmitRecord(session.id, {
        ...startSubmitRecord({
          requestId: item.requestId,
          text: item.text,
          expectedBridgeEpoch: item.expectedBridgeEpoch,
          submittedRevision: 0,
          at: item.queuedAt,
        }),
        phase: "queued",
        serverLifecycleState: "queued",
      });
    }
    const { getAllByTestId, getByTestId } = render(<SessionComposer session={session} />);
    expect(getByTestId("queue-preview").textContent).toContain("2 queued · yours");
    expect(getAllByTestId("queued-user-block")).toHaveLength(2);
    expect(getAllByTestId("queue-preview-item")[1].textContent).toContain(
      "queued · withdrawable before dispatch",
    );
  });

  it("hides queue entries the authority has not confirmed pre-dispatch queued", () => {
    const session = readySession("unconfirmed-queue");
    sessionStore.getState().hydrate([session]);
    sessionCockpitStore.getState().enqueueSubmit(session.id, L5_MULTI_QUEUE_FIXTURE[0]);
    const { queryByTestId } = render(<SessionComposer session={session} />);
    expect(queryByTestId("queue-preview")).toBeNull();
  });

  it("acknowledges a boot-deferred send honestly instead of echoing the gate line (260721 D3)", async () => {
    const session = fromTerminalSessionInfo(
      catalogRow({ id: "boot-defer", label: "boot-defer", controlState: "starting" }),
    );
    sessionStore.getState().hydrate([session]);
    const ref = createRef<SessionComposerHandle>();
    const { getByTestId } = render(<SessionComposer ref={ref} session={session} />);
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "hello claude"));
    act(() => ref.current?.submit());
    // The press registers: the status names the still-connecting control and the kept draft,
    // while the standing gate line keeps the blocker reason — never a duplicate of it.
    await waitFor(() =>
      expect(getByTestId("session-composer-status").textContent).toContain(
        "connecting… · composer draft unchanged",
      ),
    );
    expect(getByTestId("session-composer-gate").textContent).toContain(
      "native control is not ready yet",
    );
    // Nothing was submitted; the draft survives for the send that follows the connect.
    expect(sessionCockpitStore.getState().perSession[session.id]?.composer.draft).toBe(
      "hello claude",
    );
    expect(sessionCockpitStore.getState().perSession[session.id]?.submitHistory ?? []).toEqual([]);
  });

  it("keeps the server's reason on a hard block (failed control is not a connecting state)", async () => {
    const session = fromTerminalSessionInfo(
      catalogRow({ id: "hard-block", label: "hard-block", controlState: "failed" }),
    );
    sessionStore.getState().hydrate([session]);
    const ref = createRef<SessionComposerHandle>();
    const { getByTestId } = render(<SessionComposer ref={ref} session={session} />);
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "hello claude"));
    act(() => ref.current?.submit());
    await waitFor(() =>
      expect(getByTestId("session-composer-status").textContent).toContain(
        "native control failed — inspect the session evidence",
      ),
    );
    expect(getByTestId("session-composer-status").textContent).not.toContain("connecting…");
  });

  it("shows no withdrawable claim on a bare queued receipt and settles delivering on the dispatching poll", async () => {
    const session = readySession("queued-dispatch-grace");
    sessionStore.getState().hydrate([session]);
    let releaseStatus: () => void = () => {};
    let statusState = "dispatching";
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/submission-authority")) {
        return {
          ok: true,
          json: async () => ({ bridgeEpoch: "bridge-epoch-l5" }),
        } as Response;
      }
      if (url.endsWith("/submission-status")) {
        const body = JSON.parse(String(init?.body)) as { requestIds: string[] };
        return new Promise<Response>((resolve) => {
          releaseStatus = () =>
            resolve({
              ok: true,
              json: async () => ({
                bridgeEpoch: "bridge-epoch-l5",
                submissions: body.requestIds.map((requestId) => ({
                  requestId,
                  outcome: "found",
                  submission: {
                    state: statusState,
                    submittedAt: "2026-07-17T10:00:00Z",
                    updatedAt: "2026-07-17T10:00:01Z",
                    acceptedAt: null,
                    withdrawable: false,
                    detail: null,
                  },
                })),
              }),
            } as Response);
        });
      }
      const body = JSON.parse(String(init?.body)) as { requestId: string };
      return receipt(body.requestId, "queued");
    });
    vi.stubGlobal("fetch", fetchMock);
    const { getByTestId, queryByTestId } = render(<SessionComposer session={session} />);
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "hello claude"));
    fireEvent.click(getByTestId("session-composer-send"));

    // Bare queued receipt: the draft is released (the draft-release commit point) but no withdrawable claim
    // and no queue preview — under the dispatch grace the record is already dispatching-head,
    // so the claim would be a lie on every send.
    await waitFor(() =>
      expect(getByTestId("session-composer-status").textContent).toContain("queued"),
    );
    const receiptStatus = getByTestId("session-composer-status").textContent ?? "";
    expect(receiptStatus).toContain("draft released");
    expect(receiptStatus).not.toContain("withdrawable");
    expect(queryByTestId("queue-preview")).toBeNull();
    expect(sessionCockpitStore.getState().perSession[session.id]?.composer.draft).toBe("");

    // The first lifecycle poll reports dispatching: the composer settles on delivering… with
    // the draft still cleared and no queue surface ever shown.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).endsWith("/submission-status")),
      ).toBe(true),
    );
    act(() => releaseStatus());
    await waitFor(() =>
      expect(getByTestId("session-composer-status").textContent).toContain("delivering…"),
    );
    expect(queryByTestId("queue-preview")).toBeNull();
    expect(sessionCockpitStore.getState().perSession[session.id]?.composer.draft).toBe("");
    expect(sessionCockpitStore.getState().perSession[session.id]?.queue).toEqual([]);

    // Dispatching is not terminal: polling continues, and the delivered upgrade on a later poll
    // settles the composer on the server's terminal word instead of "delivering…" forever.
    await waitFor(
      () =>
        expect(
          fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/submission-status")),
        ).toHaveLength(2),
      { timeout: 3_000 },
    );
    statusState = "delivered";
    act(() => releaseStatus());
    await waitFor(() =>
      expect(getByTestId("session-composer-status").textContent).toContain(
        "delivered · draft released",
      ),
    );
    expect(queryByTestId("queue-preview")).toBeNull();
    expect(sessionCockpitStore.getState().perSession[session.id]?.queue).toEqual([]);
  });

  it("ignores background receipt status beside an unrelated visible composer draft", () => {
    const session = readySession("background-status");
    sessionStore.getState().hydrate([session]);
    sessionCockpitStore.getState().setComposerDraft(session.id, "keep this visible draft");
    sessionCockpitStore.getState().upsertSubmitRecord(session.id, {
      ...startSubmitRecord({
        requestId: "leaf-context-record",
        text: "background context",
        expectedBridgeEpoch: "bridge-epoch-l5",
        source: "leaf-context",
        clearDraftOnAccept: false,
        submittedRevision: 0,
        at: 1,
      }),
      phase: "accepted",
    });
    const { queryByTestId } = render(<SessionComposer session={session} />);
    expect(sessionCockpitStore.getState().perSession[session.id].composer.draft).toBe(
      "keep this visible draft",
    );
    expect(queryByTestId("session-composer-status")).toBeNull();
    expect(sessionCockpitStore.getState().perSession[session.id].queue).toEqual([]);
  });

  it("fails pop-back loudly when observed acceptance says the message already delivered", async () => {
    const session = readySession("already-delivered");
    sessionStore.getState().hydrate([session]);
    sessionCockpitStore.getState().upsertSubmitRecord(session.id, {
      ...startSubmitRecord({
        requestId: L5_ALREADY_DELIVERED_RACE_FIXTURE.requestId,
        text: L5_ALREADY_DELIVERED_RACE_FIXTURE.text,
        expectedBridgeEpoch: "bridge-epoch-l5",
        submittedRevision: L5_ALREADY_DELIVERED_RACE_FIXTURE.submittedRevision,
        at: L5_ALREADY_DELIVERED_RACE_FIXTURE.acceptedAt,
      }),
      phase: "accepted",
    });
    const ref = createRef<SessionComposerHandle>();
    const { getByTestId } = render(<SessionComposer ref={ref} session={session} />);
    act(() => ref.current?.popBack());
    await waitFor(() =>
      expect(getByTestId("session-composer-status").textContent).toContain("already delivered"),
    );
    expect(ref.current?.getDraft()).toBe("");
  });

  it("does not submit Ctrl+Enter while IME composition owns the editor", async () => {
    const session = readySession("ime-guard");
    sessionStore.getState().hydrate([session]);
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as { requestId: string };
      return receipt(body.requestId);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<SessionComposer session={session} />);
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "変換中"));
    const content = container.querySelector(".cm-content")!;
    fireEvent.compositionStart(content);
    fireEvent.keyDown(content, {
      key: "Enter",
      ctrlKey: true,
      isComposing: true,
    });
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.compositionEnd(content);
  });

  it("makes answer-mode a built-in gate route and locks duplicate sends, never /submit", async () => {
    const session = {
      ...readySession("answer-mode"),
      lifecycleId: "lc-answer-mode",
      controlPendingInteraction: {
        interactionId: "ix-answer-mode",
        kind: "input",
        prompt: "Which branch?",
        choices: [],
      },
    };
    sessionStore.getState().hydrate([session]);
    dashboardStore.setState({
      lifecycles: {
        "lc-answer-mode": lifecycleWithGate(
          { id: "lc-answer-mode" },
          {
            id: "gate-answer-mode",
            kind: "agent-question",
            state: "open",
            decisions: [],
            ts: "2026-07-17T09:00:00Z",
            packet: {
              adapterInteraction: {
                sessionId: session.id,
                interactionId: "ix-answer-mode",
              },
            },
          },
        ),
      },
    });
    let release: (response: Response) => void = () => {};
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      expect(url).toBe("/api/actions/approve");
      expect(init?.method).toBe("POST");
      return new Promise<Response>((resolve) => (release = resolve));
    });
    vi.stubGlobal("fetch", fetchMock);
    const { getByTestId } = render(<SessionComposer session={session} />);
    act(() => sessionCockpitStore.getState().setComposerDraft(session.id, "exact answer"));
    fireEvent.click(getByTestId("session-composer-send"));
    fireEvent.click(getByTestId("session-composer-send"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/actions/approve");
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toMatchObject({
      target: "lc-answer-mode",
      gateId: "gate-answer-mode",
      note: "exact answer",
    });
    expect(getByTestId("session-composer-send").textContent).toBe("send answer");
    expect(getByTestId("session-composer-send").getAttribute("disabled")).not.toBeNull();
    release({ status: 202, text: async () => "" } as Response);
    await waitFor(() =>
      expect(getByTestId("session-composer-answer-mode").textContent).toContain("answered"),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("disables native submission on a raw terminal while naming raw typing as the supported path", () => {
    const raw = fromTerminalSessionInfo(
      catalogRow({
        id: "raw",
        kind: "terminal",
        harness: undefined,
        controlState: undefined,
      }),
    );
    sessionStore.getState().hydrate([raw]);
    const { getByTestId } = render(<SessionComposer session={raw} />);
    expect(getByTestId("session-composer-gate").textContent).toContain("raw terminal typing");
    expect(getByTestId("session-composer-send").getAttribute("disabled")).not.toBeNull();
  });
});
