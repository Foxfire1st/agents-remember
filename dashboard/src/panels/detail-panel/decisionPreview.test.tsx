import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TaskDecisionNode } from "../../types/projection";
import { DecisionPreview } from "./taskReader";

function decision(at: string, text: string, productRecord = false): TaskDecisionNode {
  return { at, decision: text, rationale: "why", product_record: productRecord };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DecisionPreview (rule 6 front page)", () => {
  it("shows five and no link when the log holds five authored decisions", () => {
    const items = [1, 2, 3, 4, 5].map((n) => decision(`2026-10-0${n}T00:00:00+00:00`, `decision ${n}`));
    const view = render(
      <DecisionPreview items={items} repo="r" master="m" title="t" onOpenNotes={vi.fn()} />,
    );
    expect(view.getByText("decision 5")).toBeTruthy();
    expect(view.queryByTestId("decision-log-all-link")).toBeNull();
  });

  it("shows the five newest, newest first, with a link naming the whole count", () => {
    const items = [1, 2, 3, 4, 5, 6, 7].map((n) => decision(`2026-10-0${n}T00:00:00+00:00`, `decision ${n}`));
    const onOpenNotes = vi.fn();
    const view = render(
      <DecisionPreview items={items} repo="r" master="m" title="t" onOpenNotes={onOpenNotes} />,
    );
    const shown = view.getAllByText(/decision \d/);
    expect(shown.map((node) => node.textContent)).toEqual([
      "decision 7",
      "decision 6",
      "decision 5",
      "decision 4",
      "decision 3",
    ]);
    const link = view.getByTestId("decision-log-all-link");
    expect(link.textContent).toContain("all 7 decisions");
    fireEvent.click(link);
    expect(onOpenNotes).toHaveBeenCalledWith({ kind: "decisions", repo: "r", master: "m", title: "t", decisions: items });
  });

  it("excludes product-written records from the five and names both counts", () => {
    const items = [
      decision("2026-10-01T00:00:00+00:00", "decision 1"),
      decision("2026-10-02T00:00:00+00:00", "Finalize task lifecycle.", true),
      decision("2026-10-03T00:00:00+00:00", "decision 2"),
      decision("2026-10-04T00:00:00+00:00", "Removed step S1.", true),
    ];
    const view = render(
      <DecisionPreview items={items} repo="r" master="m" title="t" onOpenNotes={vi.fn()} />,
    );
    expect(view.queryByText("Finalize task lifecycle.")).toBeNull();
    expect(view.queryByText("Removed step S1.")).toBeNull();
    expect(view.getByText("decision 2")).toBeTruthy();
    expect(view.getByTestId("decision-log-all-link").textContent).toContain("all 4 entries, 2 decisions");
  });

  it("shows the link when only lifecycle records are omitted", () => {
    const items = [
      decision("2026-10-01T00:00:00+00:00", "decision 1"),
      decision("2026-10-02T00:00:00+00:00", "Finalize task lifecycle.", true),
    ];
    const view = render(
      <DecisionPreview items={items} repo="r" master="m" title="t" onOpenNotes={vi.fn()} />,
    );
    expect(view.getByTestId("decision-log-all-link").textContent).toContain("all 2 entries, 1 decision");
  });

  it("chooses the five newest by time from an unsorted log, not the array tail", () => {
    // Storage order carries no invariant: the newest decision sits in the middle.
    const items = [
      decision("2026-10-03T00:00:00+00:00", "decision 3"),
      decision("2026-10-07T00:00:00+00:00", "decision 7"),
      decision("2026-10-01T00:00:00+00:00", "decision 1"),
      decision("2026-10-06T00:00:00+00:00", "decision 6"),
      decision("2026-10-02T00:00:00+00:00", "decision 2"),
      decision("2026-10-05T00:00:00+00:00", "decision 5"),
      decision("2026-10-04T00:00:00+00:00", "decision 4"),
    ];
    const view = render(
      <DecisionPreview items={items} repo="r" master="m" title="t" onOpenNotes={vi.fn()} />,
    );
    const shown = view.getAllByText(/decision \d/);
    expect(shown.map((node) => node.textContent)).toEqual([
      "decision 7",
      "decision 6",
      "decision 5",
      "decision 4",
      "decision 3",
    ]);
    expect(view.queryByText("decision 1")).toBeNull();
    expect(view.queryByText("decision 2")).toBeNull();
  });

  it("handles finalizer-shaped input with the newest product record prepended", () => {
    // The unchanged writer returns [new-finalizer-record, ...old-decisions]: the newest
    // authored decision must still win over the array tail.
    const items = [
      decision("2026-10-09T00:00:00+00:00", "Finalize task lifecycle.", true),
      decision("2026-10-01T00:00:00+00:00", "decision 1"),
      decision("2026-10-08T00:00:00+00:00", "decision 8"),
      decision("2026-10-02T00:00:00+00:00", "decision 2"),
    ];
    const view = render(
      <DecisionPreview items={items} repo="r" master="m" title="t" onOpenNotes={vi.fn()} />,
    );
    const shown = view.getAllByText(/decision \d/);
    expect(shown.map((node) => node.textContent)).toEqual(["decision 8", "decision 2", "decision 1"]);
    expect(view.queryByText("Finalize task lifecycle.")).toBeNull();
  });
});
