import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  form: "task",
  type: "",
  headerNotes: [],
  statusNoteTruncated: false,
  stepsDone: 1,
  stepsTotal: 4,
  docPath: "/tasks/agents-remember/260610_browser-dashboard/19_gate-interaction-polish.json",
  bodyRevision: "rev-fixture",
  createdAt: "2026-06-23T10:00:00+00:00",
  steps: [],
  objective: "Make gate responses human-usable.",
  requirements: [],
  codeExamples: [],
  decisions: [],
  openQuestions: [],
  references: [],
  subTasks: [],
  sections: [],
  orchestrates: [],
  seats: [],
  executionWaves: [],
};

function seed(queue: AttentionItem[], docs: TaskDocNode[] = []) {
  const base = GALLERY.find((entry) => entry.name === "engine-fleet")?.projection;
  if (!base?.analytics) throw new Error("fixture missing analytics");
  dashboardStore.getState().applySnapshot({
    ...base,
    analytics: { ...base.analytics, taskDocuments: docs, attentionQueue: queue },
  });
}

// The queue is a bell; its entries are in the layer the bell opens.
function renderOpen(onSelect: (lifecycleId: string) => void = () => {}) {
  const view = render(<AttentionQueue onSelect={onSelect} />);
  fireEvent.click(view.getByTestId("attention-bell"));
  return view;
}

const quietSession = (lifecycleId: string): AttentionItem => ({
  id: `stale-session:${lifecycleId}`,
  kind: "stale-session",
  severity: "info",
  lane: "lifecycle",
  title: "Session gone quiet",
  lifecycleId,
});

afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
  vi.clearAllMocks();
});

describe("AttentionQueue blocked-start alarm parity (5f S3)", () => {
  it("renders a §9 blocked-start attention item", () => {
    seed([blockedStart]);
    const { getAllByTestId, getByRole } = renderOpen();
    const items = getAllByTestId("attn-item");
    // The cause is one line: the title, then the detail. The lane stands where a task would.
    expect(items[0].textContent).toContain(
      "Worktree start blocked · no exact ledger mapping for selected code base commit",
    );
    expect(items[0].textContent).toContain("worktree");
    expect(getByRole("img", { name: "Severity: warn" })).toBeTruthy();
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
    const onSelect = vi.fn();
    const { getByText, getByTestId, queryByTestId } = renderOpen(onSelect);
    expect(getByText("Gate - closeout-approval · awaiting your decision")).toBeTruthy();
    // The task is the entry's second line and its link: it opens the entry's lifecycle, as
    // "Open" did, and closes the layer.
    const link = getByText("19 Gate interaction polish");
    expect(link.tagName).toBe("BUTTON");
    fireEvent.click(link);
    expect(onSelect).toHaveBeenCalledWith("LC19");
    expect(queryByTestId("attention-queue")).toBeNull();
    expect(getByTestId("attention-bell").getAttribute("aria-expanded")).toBe("false");
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

    const { getByTestId } = renderOpen();
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

    const view = renderOpen();
    fireEvent.click(view.getByTestId("attn-dismiss"));

    expect(view.queryByText(/1 actionable drift in agents-remember/)).toBeNull();
    // The entry goes, the layer stays and says that nothing waits; the bell loses its number.
    expect(view.getByTestId("attention-queue").textContent).toContain("nothing waiting");
    expect(view.getByTestId("attention-bell").getAttribute("aria-label")).toBe(
      "Attention: nothing waiting",
    );
    expect(view.getByTestId("attention-bell").textContent).toBe("");
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

    const { getByTestId, getAllByTestId } = renderOpen();
    fireEvent.click(getByTestId("attn-clear"));

    // What cannot be dismissed stays listed in the open layer.
    expect(getAllByTestId("attn-item")).toHaveLength(1);
    expect(getAllByTestId("attn-item")[0].textContent).toContain("Worktree start blocked");
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
    const { queryByTestId } = renderOpen();
    expect(queryByTestId("attn-dismiss")).toBeNull();
    expect(queryByTestId("attn-clear")).toBeNull();
    expect(queryByTestId("attn-open")).toBeNull();
  });
});

describe("AttentionQueue bell (MIK-R144 rule 3a)", () => {
  it("is a bell without a number while nothing waits, and its layer says so", () => {
    seed([]);
    const view = render(<AttentionQueue onSelect={() => {}} />);
    const bell = view.getByTestId("attention-bell");
    expect(bell.getAttribute("aria-label")).toBe("Attention: nothing waiting");
    expect(bell.textContent).toBe("");
    // The entries are no part of the column until the bell is asked.
    expect(view.queryByTestId("attention-queue")).toBeNull();

    fireEvent.click(bell);
    expect(view.getByTestId("attention-queue").textContent).toBe("Attention · nothing waiting");
    expect(view.queryByTestId("attn-item")).toBeNull();
  });

  it("carries the number that waits and the most pressing kind, and opens the entries", () => {
    seed([
      quietSession("LC7"),
      { ...blockedStart, id: "provider-down:cgc", kind: "provider-down", severity: "alarm" },
      quietSession("LC8"),
    ]);
    const view = render(<AttentionQueue onSelect={() => {}} />);
    const bell = view.getByTestId("attention-bell");
    expect(bell.getAttribute("aria-label")).toBe("Attention: 3 waiting");
    expect(bell.textContent).toBe("3");
    expect(bell.querySelector("[title]")?.getAttribute("title")).toBe(
      "Attention: 3 waiting (1 alarm, 2 information)",
    );
    // The colour is the most pressing entry's, whatever its place in the queue.
    expect(bell.getAttribute("data-tone")).toBe("alarm");
    expect(bell.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(bell);
    expect(bell.getAttribute("aria-expanded")).toBe("true");
    const layer = view.getByRole("dialog", { name: "Attention" });
    expect(layer.textContent).toContain("Attention · 3 waiting");
    // The server's order is kept.
    expect(view.getAllByTestId("attn-severity").map((mark) => mark.getAttribute("aria-label"))).toEqual([
      "Severity: info",
      "Severity: alarm",
      "Severity: info",
    ]);
  });

  it("opens an entry's lifecycle also where no task document names it, and closes by Escape", () => {
    const onSelect = vi.fn();
    seed([quietSession("LC7")]);
    const view = renderOpen(onSelect);

    fireEvent.keyDown(view.getByRole("dialog", { name: "Attention" }), { key: "Escape" });
    expect(view.queryByTestId("attention-queue")).toBeNull();
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(view.getByTestId("attention-bell"));
    fireEvent.click(view.getByTestId("attn-open"));
    expect(onSelect).toHaveBeenCalledWith("LC7");
  });

  it("closes by a press outside the layer and keeps what waits", async () => {
    seed([quietSession("LC7")]);
    const view = renderOpen(() => {});
    // An open layer lays a sheet over the rest of the page, the bell included: a press anywhere
    // outside the layer lands on it. (That a press at the bell's place closes the layer too is
    // tried in a browser: notes/run3/operations-sidebar-proposal/comparison/behave.json.)
    const sheet = view.baseElement.querySelector("[data-testid='underlay']");
    if (!sheet) throw new Error("an open layer has no sheet under it");
    await userEvent.click(sheet);
    expect(view.queryByTestId("attention-queue")).toBeNull();
    expect(view.getByTestId("attention-bell").getAttribute("aria-expanded")).toBe("false");
    expect(view.getByTestId("attention-bell").textContent).toBe("1");
    expect(postAttentionDismiss).not.toHaveBeenCalled();
  });
});
