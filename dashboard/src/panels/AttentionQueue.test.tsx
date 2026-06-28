import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { postAttentionDismiss } from "../data/actions";
import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import type { AttentionItem, TaskDocNode } from "../types/projection";
import { AttentionQueue } from "./AttentionQueue";

vi.mock("../data/actions", () => ({ postAttentionDismiss: vi.fn() }));

// §9 (slice 5f S6/S3): a pre-contract blocked start is a server-computed attention item. The queue
// renders it generically by severity, so the same alarm the agent raises in chat reaches the cockpit.
const blockedStart: AttentionItem = {
  id: "blocked-start:v12-feat-ar",
  kind: "blocked-start",
  severity: "warn",
  lane: "worktree",
  title: "Worktree start blocked",
  detail: "no exact ledger mapping for selected code base commit",
};

const actionableDrift: AttentionItem = {
  id: "actionable-drift:agents-remember:ar/260610-browser-dashboard",
  kind: "actionable-drift",
  severity: "warn",
  lane: "repo",
  title: "1 actionable drift in agents-remember",
  detail: "branch ar/260610-browser-dashboard · memory /memory/ar-agents-remember",
};

const taskDoc: TaskDocNode = {
  id: "19",
  lifecycleId: "LC19",
  repository: "agents-remember",
  title: "Gate interaction polish",
  status: "inProgress",
  kind: "subTask",
  stepsDone: 1,
  stepsTotal: 4,
  docPath: "/tasks/agents-remember/260610_browser-dashboard/19_gate-interaction-polish.json",
  steps: [],
  objective: "Make gate responses human-usable.",
  requirements: [],
  codeExamples: [],
  decisions: [],
  openQuestions: [],
  references: [],
  subTasks: [],
  sections: [],
};

function seed(queue: AttentionItem[], docs: TaskDocNode[] = []) {
  const base = GALLERY.find((entry) => entry.name === "engine-fleet")?.projection;
  if (!base?.analytics) throw new Error("fixture missing analytics");
  dashboardStore.getState().applySnapshot({
    ...base,
    analytics: { ...base.analytics, taskDocuments: docs, attentionQueue: queue },
  });
}

afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
  vi.clearAllMocks();
});

describe("AttentionQueue blocked-start alarm parity (5f S3)", () => {
  it("renders a §9 blocked-start attention item", () => {
    seed([blockedStart]);
    const { getAllByTestId, getByText } = render(<AttentionQueue onSelect={() => {}} />);
    const items = getAllByTestId("attn-item");
    expect(items.some((el) => el.textContent?.includes("Worktree start blocked"))).toBe(true);
    expect(getByText("no exact ledger mapping for selected code base commit")).not.toBeNull();
  });

  it("renders lifecycle attention through the bound task document when available", () => {
    seed(
      [
        {
          id: "blocked-gate:LC19",
          kind: "blocked-gate",
          severity: "warn",
          lane: "lifecycle",
          title: "Gate - closeout-approval",
          detail: "awaiting your decision",
          lifecycleId: "LC19",
          gateId: "G19",
        },
      ],
      [taskDoc],
    );
    const { getByText } = render(<AttentionQueue onSelect={() => {}} />);
    expect(getByText("Task 19: Gate interaction polish")).toBeTruthy();
    expect(getByText("Gate - closeout-approval · awaiting your decision")).toBeTruthy();
  });
});

describe("AttentionQueue lifecycle-scoped dismiss (leaf-28 S5.2)", () => {
  it("dismisses a single item by its id and kind", async () => {
    vi.mocked(postAttentionDismiss).mockResolvedValue("dismissed");
    seed([
      {
        id: "awaiting-developer:LC19",
        kind: "awaiting-developer",
        severity: "info",
        lane: "lifecycle",
        title: "Turn complete — your move",
        lifecycleId: "LC19",
      },
    ]);

    const { getByTestId } = render(<AttentionQueue onSelect={() => {}} />);
    fireEvent.click(getByTestId("attn-dismiss"));

    await waitFor(() =>
      expect(postAttentionDismiss).toHaveBeenCalledWith({
        itemId: "awaiting-developer:LC19",
        kind: "awaiting-developer",
        lifecycleId: "LC19",
        gateId: undefined,
      }),
    );
  });

  it("dismisses actionable drift without a lifecycle target and hides it immediately", async () => {
    vi.mocked(postAttentionDismiss).mockResolvedValue("dismissed");
    seed([actionableDrift]);

    const view = render(<AttentionQueue onSelect={() => {}} />);
    fireEvent.click(view.getByTestId("attn-dismiss"));

    expect(view.queryByText("1 actionable drift in agents-remember")).toBeNull();
    await waitFor(() =>
      expect(postAttentionDismiss).toHaveBeenCalledWith({
        itemId: "actionable-drift:agents-remember:ar/260610-browser-dashboard",
        kind: "actionable-drift",
        lifecycleId: null,
        gateId: undefined,
      }),
    );
  });

  it("Clear all dismisses dismissible listed items, not worktree alarms", async () => {
    vi.mocked(postAttentionDismiss).mockResolvedValue("dismissed");
    seed([
      {
        id: "gate:G19",
        kind: "gate-open",
        severity: "warn",
        lane: "lifecycle",
        title: "Gate - closeout-approval",
        lifecycleId: "LC19",
        gateId: "G19",
      },
      {
        id: "stale-session:LC7",
        kind: "stale-session",
        severity: "info",
        lane: "lifecycle",
        title: "Session gone quiet",
        lifecycleId: "LC7",
      },
      actionableDrift,
      blockedStart,
    ]);

    const { getByTestId } = render(<AttentionQueue onSelect={() => {}} />);
    fireEvent.click(getByTestId("attn-clear"));

    await waitFor(() => expect(postAttentionDismiss).toHaveBeenCalledTimes(3));
    expect(postAttentionDismiss).toHaveBeenCalledWith({
      itemId: "gate:G19",
      kind: "gate-open",
      lifecycleId: "LC19",
      gateId: "G19",
    });
    expect(postAttentionDismiss).toHaveBeenCalledWith({
      itemId: "stale-session:LC7",
      kind: "stale-session",
      lifecycleId: "LC7",
      gateId: undefined,
    });
    expect(postAttentionDismiss).toHaveBeenCalledWith({
      itemId: "actionable-drift:agents-remember:ar/260610-browser-dashboard",
      kind: "actionable-drift",
      lifecycleId: null,
      gateId: undefined,
    });
  });

  it("does not offer dismiss or Open controls on a non-lifecycle item", () => {
    seed([blockedStart]);
    const { queryByTestId, queryByText } = render(<AttentionQueue onSelect={() => {}} />);
    expect(queryByTestId("attn-dismiss")).toBeNull();
    expect(queryByTestId("attn-clear")).toBeNull();
    expect(queryByText("Open")).toBeNull();
  });
});
