import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { postGateDecision } from "../data/actions";
import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import type { AttentionItem, TaskDocNode } from "../types/projection";
import { AttentionQueue } from "./AttentionQueue";

vi.mock("../data/actions", () => ({ postGateDecision: vi.fn() }));

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

afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
  vi.clearAllMocks();
});

describe("AttentionQueue blocked-start alarm parity (5f S3)", () => {
  it("renders a §9 blocked-start attention item", () => {
    const base = GALLERY.find((entry) => entry.name === "engine-fleet")?.projection;
    if (!base?.analytics) throw new Error("fixture missing analytics");
    dashboardStore.getState().applySnapshot({
      ...base,
      analytics: { ...base.analytics, attentionQueue: [blockedStart] },
    });

    const { getAllByTestId, getByText } = render(<AttentionQueue onSelect={() => {}} />);
    const items = getAllByTestId("attn-item");
    expect(items.some((el) => el.textContent?.includes("Worktree start blocked"))).toBe(true);
    expect(getByText("no exact ledger mapping for selected code base commit")).not.toBeNull();
  });

  it("renders lifecycle attention through the bound task document when available", () => {
    const base = GALLERY.find((entry) => entry.name === "engine-fleet")?.projection;
    if (!base?.analytics) throw new Error("fixture missing analytics");
    dashboardStore.getState().applySnapshot({
      ...base,
      analytics: {
        ...base.analytics,
        taskDocuments: [taskDoc],
        attentionQueue: [
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
      },
    });

    const { getByText } = render(<AttentionQueue onSelect={() => {}} />);
    expect(getByText("Task 19: Gate interaction polish")).toBeTruthy();
    expect(getByText("Gate - closeout-approval · awaiting your decision")).toBeTruthy();
  });

  it("clears queued gate items through cancel decisions", async () => {
    vi.mocked(postGateDecision).mockResolvedValue("recorded");
    const base = GALLERY.find((entry) => entry.name === "engine-fleet")?.projection;
    if (!base?.analytics) throw new Error("fixture missing analytics");
    dashboardStore.getState().applySnapshot({
      ...base,
      analytics: {
        ...base.analytics,
        attentionQueue: [
          {
            id: "gate:G19",
            kind: "gate-open",
            severity: "warn",
            lane: "lifecycle",
            title: "Gate - closeout-approval",
            detail: "awaiting your decision",
            lifecycleId: "LC19",
            gateId: "G19",
          },
        ],
      },
    });

    const { getByTestId } = render(<AttentionQueue onSelect={() => {}} />);
    fireEvent.click(getByTestId("attn-clear"));

    await waitFor(() =>
      expect(postGateDecision).toHaveBeenCalledWith("LC19", "cancel", {
        gateId: "G19",
        note: "Cleared from attention queue.",
      }),
    );
  });
});
