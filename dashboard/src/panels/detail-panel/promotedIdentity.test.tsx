import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DetailPanel } from "./DetailPanel";
import { seedPromotedLeaf } from "./test-utils";

describe("DetailPanel promoted lifecycle identity", () => {
  it("renders the leaf task document without falling back to the master task documents", () => {
    seedPromotedLeaf();
    const { getAllByText, getByText, queryByText } = render(
      <DetailPanel selectedId="01KVW2FE8MQK6QCQQP0J4SEK3C" />,
    );

    expect(getAllByText("16_engine-room-stack-entry-height").length).toBeGreaterThan(0);
    expect(getByText("subTask")).toBeTruthy();
    expect(getByText("Engine Room Stack Entry Height")).toBeTruthy();
    expect(getByText("Keep a single Engine Room enclosure entry visually bounded.")).toBeTruthy();
    expect(getAllByText("S1 — Fix the stack entry height")).toHaveLength(1);
    expect(getByText("Notes")).toBeTruthy();
    expect(getByText("This is the authored leaf task document.")).toBeTruthy();
    expect(queryByText("Lifecycle Finalize Task")).toBeNull();
    expect(queryByText("Close out the lifecycle finalizer.")).toBeNull();
    expect(queryByText("Series Contract")).toBeNull();
    expect(queryByText("schema: ar-series-contract/v1")).toBeNull();
    expect(queryByText("01KVW2FE8MQK6QCQQP0J4SEK3C")).toBeNull();
    expect(queryByText("No task document bound to this task.")).toBeNull();
  });

  it("links an enclosure-opened leaf back to its parent task document", () => {
    seedPromotedLeaf();
    const onOpenLifecycle = vi.fn();
    const { getByTestId } = render(
      <DetailPanel selectedId="01KVW2FE8MQK6QCQQP0J4SEK3C" onOpenLifecycle={onOpenLifecycle} />,
    );

    expect(getByTestId("master-parent-link").textContent).toContain("Browser Dashboard Series");
    fireEvent.click(getByTestId("master-parent-link"));
    expect(onOpenLifecycle).toHaveBeenCalledWith(
      "taskdoc:/tasks/260610_browser-dashboard/task.json",
    );
  });
});
