import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSelectionCapture } from "../data/selection";
import { sessionCockpitStore } from "../data/sessionCockpitStore";
import { createSession, sessionStore } from "../data/sessions";
import { dashboardStore } from "../data/store";
import {
  keepWaitingForSubmit,
  retryRouteFailure,
  submitSessionText,
  waitForSubmissionReady,
} from "../data/submitClient";
import {
  startSubmitRecord,
  type SubmitPhase,
  type SubmitRecord,
} from "../data/submitMachine";
import { fetchHarnesses } from "../data/terminal";
import { SERVED, taskDoc } from "../test/fixtures/wire";
import { HighlightComposer } from "./HighlightComposer";

vi.mock("../data/selection", () => ({ useSelectionCapture: vi.fn() }));
vi.mock("../data/sessions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/sessions")>();
  return { ...actual, createSession: vi.fn() };
});
vi.mock("../data/submitClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/submitClient")>();
  return {
    ...actual,
    submitSessionText: vi.fn(),
    waitForSubmissionReady: vi.fn(),
    retryRouteFailure: vi.fn(),
    keepWaitingForSubmit: vi.fn(),
  };
});
vi.mock("../data/terminal", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/terminal")>();
  return { ...actual, fetchHarnesses: vi.fn() };
});

const SELECTION = {
  text: "a blocked finding",
  rect: { left: 10, top: 10, width: 40, height: 14 } as DOMRect,
};
const clear = vi.fn();
const LEAF_TASK = { repository: "repo", path: "master/L8.json" };
const HARNESSES = [
  { id: "claude", name: "Claude Code", detected: true },
  { id: "codex", name: "Codex", detected: true },
  { id: "pi", name: "Pi.dev", detected: false },
];

function record(
  text: string,
  phase: SubmitPhase = "accepted",
  requestId = "highlight-request",
  detail?: string,
): SubmitRecord {
  return {
    ...startSubmitRecord({
      requestId,
      text,
      expectedBridgeEpoch: "bridge-epoch-l5",
      submittedRevision: 0,
      at: 1,
    }),
    phase,
    detail,
    ...(phase === "route-error"
      ? {
          routeFailure: {
            httpStatus: 503,
            status: "control-unavailable",
            detail: detail ?? "bridge restarting",
          },
        }
      : {}),
  };
}

beforeEach(() => {
  vi.mocked(useSelectionCapture).mockReturnValue({
    selection: SELECTION,
    clear,
  });
  vi.mocked(createSession).mockResolvedValue({
    outcome: "opened",
    httpStatus: 200,
    responseBody: {},
    session: {
      id: "created-id",
      label: "Claude Code 1",
      kind: "harness",
      harness: "claude",
      status: "running",
    },
  });
  vi.mocked(fetchHarnesses).mockResolvedValue(HARNESSES);
  vi.mocked(waitForSubmissionReady).mockResolvedValue({
    ready: true,
    editable: true,
  });
  vi.mocked(submitSessionText).mockImplementation(async (_id, text) => ({
    status: "started",
    record: record(text),
  }));
  vi.mocked(retryRouteFailure).mockImplementation(
    async (_id, _requestId, text) => ({
      record: record(text),
    }),
  );
  vi.mocked(keepWaitingForSubmit).mockImplementation(async (_id, requestId) =>
    record("retained", "accepted", requestId),
  );
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
  sessionCockpitStore.setState({ focusedSessionId: null });
  dashboardStore.setState({
    analytics: {
      ...SERVED.analytics,
      taskDocuments: [taskDoc({
        repository: "repo",
        id: "L8",
        kind: "subTask",
        docPath: "/coordination/tasks/repo/master/L8.json",
      })],
    },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
  sessionCockpitStore.setState({ focusedSessionId: null });
});

describe("HighlightComposer reliable-submit disposition (FEUI-L5)", () => {
  it("keeps the pre-projection task-document snapshot stable", async () => {
    dashboardStore.setState({ analytics: null });
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    try {
      const { findByTestId } = render(<HighlightComposer />);
      expect(await findByTestId("highlight-add-to-chat")).not.toBeNull();
      expect(consoleError.mock.calls.flat().join(" ")).not.toContain(
        "The result of getSnapshot should be cached",
      );
    } finally {
      consoleError.mockRestore();
    }
  });

  it("renders nothing until there is a selection", () => {
    vi.mocked(useSelectionCapture).mockReturnValue({ selection: null, clear });
    const { queryByTestId } = render(<HighlightComposer />);
    expect(queryByTestId("highlight-composer")).toBeNull();
  });

  it("a selection raises an explicit-action pill, then opens the composer", async () => {
    const { findByTestId, queryByTestId } = render(<HighlightComposer />);
    expect(await findByTestId("highlight-add-to-chat")).not.toBeNull();
    expect(submitSessionText).not.toHaveBeenCalled();
    expect(queryByTestId("highlight-send")).toBeNull();
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    expect(await findByTestId("highlight-send")).not.toBeNull();
  });

  it("offers detected harnesses only — never a terminal target — and states text-only scope", async () => {
    const { findByTestId, getByText, queryByTestId } = render(
      <HighlightComposer />,
    );
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    expect(await findByTestId("highlight-target-c:claude")).not.toBeNull();
    expect(await findByTestId("highlight-target-c:codex")).not.toBeNull();
    expect(queryByTestId("highlight-target-c:pi")).toBeNull();
    expect(queryByTestId("highlight-target-c:terminal")).toBeNull();
    expect(getByText("text only · attachments unavailable")).not.toBeNull();
  });

  it("Ctrl+Enter creates the default harness, waits for ready, and submits one exact package", async () => {
    const onSent = vi.fn();
    const { findByTestId, getByRole } = render(
      <HighlightComposer onSent={onSent} />,
    );
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    await findByTestId("highlight-target-c:claude");
    fireEvent.change(getByRole("textbox"), { target: { value: "  note α  " } });
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter", ctrlKey: true });
    await waitFor(() =>
      expect(createSession).toHaveBeenCalledWith(
        "Claude Code",
        "harness",
        "claude",
      ),
    );
    expect(waitForSubmissionReady).toHaveBeenCalledWith("created-id");
    await waitFor(() =>
      expect(submitSessionText).toHaveBeenCalledWith(
        "created-id",
        "  note α  \n\n--- from the dashboard ---\na blocked finding",
        { source: "highlight", clearDraftOnAccept: false },
      ),
    );
    expect(clear).toHaveBeenCalled();
    expect(onSent).toHaveBeenCalledWith("created-id");
  });

  it("passes the selected lifecycle when creating a native-control chat", async () => {
    const { findByTestId, getByRole } = render(
      <HighlightComposer selectedLifecycleId="LC1" />,
    );
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter", ctrlKey: true });
    await waitFor(() =>
      expect(createSession).toHaveBeenCalledWith(
        "Claude Code",
        "harness",
        "claude",
        "LC1",
      ),
    );
  });

  it("surfaces a failed create without waiting for readiness or submitting", async () => {
    vi.mocked(createSession).mockResolvedValueOnce({
      outcome: "failed",
      failure: "network",
      detail: "network failure — the open POST did not answer",
      httpStatus: null,
      responseStatus: null,
    });
    const { findByTestId, getByRole } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter", ctrlKey: true });

    expect((await findByTestId("highlight-status")).textContent).toContain(
      "session open network",
    );
    expect(waitForSubmissionReady).not.toHaveBeenCalled();
    expect(submitSessionText).not.toHaveBeenCalled();
  });

  it("uses the explicitly selected detected harness", async () => {
    const { findByTestId, getByRole } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    fireEvent.click(await findByTestId("highlight-target-c:codex"));
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter", ctrlKey: true });
    await waitFor(() =>
      expect(createSession).toHaveBeenCalledWith("Codex", "harness", "codex"),
    );
  });

  it("submits to an open harness and filters targets by lifecycle", async () => {
    sessionStore.getState().hydrate([
      {
        id: "s1",
        label: "Claude LC1",
        kind: "harness",
        lifecycleId: "LC1",
        status: "running",
        controlState: "ready",
      },
      {
        id: "s2",
        label: "Claude LC2",
        kind: "harness",
        lifecycleId: "LC2",
        status: "running",
        controlState: "ready",
      },
      { id: "t1", label: "Terminal", kind: "terminal", status: "running" },
    ]);
    const onSent = vi.fn();
    const { findByTestId, getByRole, queryByTestId } = render(
      <HighlightComposer selectedLifecycleId="LC1" onSent={onSent} />,
    );
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    expect(await findByTestId("highlight-target-s:s1")).not.toBeNull();
    expect(queryByTestId("highlight-target-s:s2")).toBeNull();
    expect(queryByTestId("highlight-target-s:t1")).toBeNull();
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter", ctrlKey: true });
    await waitFor(() =>
      expect(submitSessionText).toHaveBeenCalledWith("s1", expect.any(String), {
        source: "highlight",
        clearDraftOnAccept: false,
      }),
    );
    expect(sessionStore.getState().activeId).toBe("s1");
    expect(clear).toHaveBeenCalled();
    expect(onSent).toHaveBeenCalledWith("s1");
  });

  it("commits an existing queued target to the active route only after acceptance", async () => {
    sessionStore.getState().hydrate(
      [
        {
          id: "previous",
          label: "Current chat",
          kind: "harness",
          status: "running",
          controlState: "ready",
        },
        {
          id: "target",
          label: "Target chat",
          kind: "harness",
          status: "running",
          controlState: "ready",
        },
      ],
      "previous",
    );
    sessionCockpitStore.setState({ focusedSessionId: "previous" });
    vi.mocked(submitSessionText).mockImplementationOnce(async (_id, text) => ({
      status: "started",
      record: record(text, "queued", "queued-existing"),
    }));
    const onSent = vi.fn();
    const { findByTestId } = render(<HighlightComposer onSent={onSent} />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    fireEvent.click(await findByTestId("highlight-target-s:target"));
    fireEvent.click(await findByTestId("highlight-send"));

    await waitFor(() => expect(onSent).toHaveBeenCalledWith("target"));
    expect(sessionStore.getState().activeId).toBe("target");
    expect(clear).toHaveBeenCalledTimes(1);
  });

  it.each([
    {
      name: "rejected",
      result: (text: string) => ({
        status: "started" as const,
        record: record(text, "rejected", "existing-rejected", "queue full"),
      }),
    },
    {
      name: "blocked",
      result: () => ({
        status: "blocked" as const,
        reason: "control route unavailable",
      }),
    },
    {
      name: "route-error",
      result: (text: string) => ({
        status: "started" as const,
        record: record(
          text,
          "route-error",
          "existing-route-error",
          "bridge restarting",
        ),
      }),
    },
    {
      name: "unresolved endgame",
      result: (text: string) => ({
        status: "started" as const,
        record: record(text, "endgame", "existing-endgame", "still unresolved"),
      }),
    },
  ])(
    "a $name existing-target outcome preserves the prior route, focus, view, and callback",
    async ({ result }) => {
      sessionStore.getState().hydrate(
        [
          {
            id: "previous",
            label: "Current chat",
            kind: "harness",
            status: "running",
            controlState: "ready",
          },
          {
            id: "target",
            label: "Target chat",
            kind: "harness",
            status: "running",
            controlState: "ready",
          },
        ],
        "previous",
      );
      sessionCockpitStore.setState({ focusedSessionId: "previous" });
      vi.mocked(submitSessionText).mockImplementationOnce(async (_id, text) =>
        result(text),
      );
      let view = "files";
      const onSent = vi.fn(() => {
        view = "chats";
      });
      const { findByTestId } = render(<HighlightComposer onSent={onSent} />);
      fireEvent.click(await findByTestId("highlight-add-to-chat"));
      fireEvent.click(await findByTestId("highlight-target-s:target"));
      fireEvent.click(await findByTestId("highlight-send"));

      await findByTestId("highlight-status");
      expect(sessionStore.getState().activeId).toBe("previous");
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("previous");
      expect(view).toBe("files");
      expect(onSent).not.toHaveBeenCalled();
      expect(clear).not.toHaveBeenCalled();
    },
  );

  it("direct leaf pill click submits through /submit; selection alone never acts", async () => {
    const leafKey = "repo/master/L8";
    vi.mocked(useSelectionCapture).mockReturnValue({
      selection: { ...SELECTION, leafKey },
      clear,
    });
    sessionStore.getState().hydrate([
      {
        id: "leaf-chat",
        label: "Claude Code 1",
        kind: "harness",
        taskDocumentRef: LEAF_TASK,
        status: "running",
        controlState: "ready",
      },
    ]);
    const onSent = vi.fn();
    const { findByTestId, queryByTestId } = render(
      <HighlightComposer
        viewedLeafKey={leafKey}
        leafChatActive
        onSent={onSent}
      />,
    );
    const pill = await findByTestId("highlight-add-to-chat");
    expect(submitSessionText).not.toHaveBeenCalled();
    fireEvent.click(pill);
    await waitFor(() =>
      expect(submitSessionText).toHaveBeenCalledWith(
        "leaf-chat",
        expect.any(String),
        {
          source: "highlight",
          clearDraftOnAccept: false,
        },
      ),
    );
    expect(queryByTestId("highlight-send")).toBeNull();
    expect(clear).toHaveBeenCalled();
    expect(onSent).toHaveBeenCalledWith("leaf-chat");
  });

  it("keeps a rejected direct submit visible with the verbatim detail", async () => {
    const leafKey = "repo/master/L8";
    vi.mocked(useSelectionCapture).mockReturnValue({
      selection: { ...SELECTION, leafKey },
      clear,
    });
    sessionStore.getState().hydrate([
      {
        id: "leaf-chat",
        label: "Claude Code 1",
        kind: "harness",
        taskDocumentRef: LEAF_TASK,
        status: "running",
        controlState: "ready",
      },
    ]);
    vi.mocked(submitSessionText).mockImplementationOnce(async (_id, text) => ({
      status: "started",
      record: record(text, "rejected", "direct-rejected", "queue full: 8/8"),
    }));
    const onSent = vi.fn();
    const { findByTestId } = render(
      <HighlightComposer
        viewedLeafKey={leafKey}
        leafChatActive
        onSent={onSent}
      />,
    );
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    expect((await findByTestId("highlight-status")).textContent).toContain(
      "queue full: 8/8",
    );
    expect(await findByTestId("highlight-send")).not.toBeNull();
    expect(clear).not.toHaveBeenCalled();
    expect(onSent).not.toHaveBeenCalled();
  });

  it("retries a route failure with the same requestId and reuses the created session", async () => {
    vi.mocked(submitSessionText).mockImplementationOnce(async (_id, text) => ({
      status: "started",
      record: record(
        text,
        "route-error",
        "occupied-request",
        "bridge restarting",
      ),
    }));
    vi.mocked(retryRouteFailure).mockImplementationOnce(
      async (_id, requestId, text) => ({
        record: record(text, "accepted", requestId),
      }),
    );
    const { findByTestId } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    fireEvent.click(await findByTestId("highlight-send"));
    const retry = await findByTestId("highlight-send");
    await waitFor(() => expect(retry.textContent).toBe("Retry same id"));
    fireEvent.click(retry);
    await waitFor(() =>
      expect(retryRouteFailure).toHaveBeenCalledWith(
        "created-id",
        "occupied-request",
        expect.any(String),
      ),
    );
    expect(createSession).toHaveBeenCalledTimes(1);
    expect(clear).toHaveBeenCalled();
  });

  it("renders the capped reconciliation endgame and keep-waits on the same id", async () => {
    vi.mocked(submitSessionText).mockImplementationOnce(async (_id, text) => ({
      status: "started",
      record: record(text, "endgame", "unresolved-request", "still unresolved"),
    }));
    const { findByTestId, findByText } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    fireEvent.click(await findByTestId("highlight-send"));
    const keep = await findByText("keep waiting");
    fireEvent.click(keep);
    await waitFor(() =>
      expect(keepWaitingForSubmit).toHaveBeenCalledWith(
        "created-id",
        "unresolved-request",
      ),
    );
    expect(clear).toHaveBeenCalled();
  });

  it("guards duplicate Ctrl+Enter in one tick and never creates two sessions", async () => {
    let release: (() => void) | undefined;
    vi.mocked(waitForSubmissionReady).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve({ ready: true, editable: true });
        }),
    );
    const { findByTestId, getByRole } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    const box = getByRole("textbox");
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(createSession).toHaveBeenCalledTimes(1));
    release?.();
    await waitFor(() => expect(submitSessionText).toHaveBeenCalledTimes(1));
  });

  it("does not intercept pasted images and exposes no image/terminal delivery path", async () => {
    const { findByTestId, getByRole, queryByTestId } = render(
      <HighlightComposer />,
    );
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    const file = new File([new Uint8Array([1])], "shot.png", {
      type: "image/png",
    });
    fireEvent.paste(getByRole("textbox"), { clipboardData: { files: [file] } });
    expect(queryByTestId("highlight-image")).toBeNull();
    expect(queryByTestId("highlight-target-c:terminal")).toBeNull();
  });
});
