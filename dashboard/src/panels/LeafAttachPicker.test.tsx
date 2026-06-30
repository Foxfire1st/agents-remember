import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TaskTreeNode } from "../data/taskIdentity";
import { LeafAttachPicker } from "./LeafAttachPicker";

afterEach(cleanup);

// A nested tree: Operations Integration (master) → { L5 leaf, Engine Room (NESTED master) → E1 leaf }.
// Proves the picker drills arbitrary nesting — "a master that is the leaf of another master".
const TREE: TaskTreeNode[] = [
  {
    key: "ops",
    title: "Operations Integration",
    master: "ops",
    children: [
      { key: "repo/ops/L5", title: "Sidebar chat", leafKey: "repo/ops/L5", children: [] },
      {
        key: "engine",
        title: "Engine Room",
        master: "engine",
        children: [
          { key: "repo/engine/E1", title: "Canvas motion", leafKey: "repo/engine/E1", children: [] },
        ],
      },
    ],
  },
];

const TID = "attach-leaf-picker";

describe("LeafAttachPicker drill-down", () => {
  it("lists top-level masters first, then drills into a master to reveal its leaves and nested masters", () => {
    const onPick = vi.fn();
    const { getByTestId, queryByTestId, getAllByTestId } = render(
      <LeafAttachPicker tree={TREE} onPick={onPick} />,
    );

    // Top level: the master row, no leaf rows yet.
    fireEvent.click(getByTestId(TID));
    expect(getByTestId(`${TID}-master`).getAttribute("data-master")).toBe("ops");
    expect(queryByTestId(`${TID}-leaf`)).toBeNull();

    // Drill into the master: its leaf AND its nested master appear, with a back row.
    fireEvent.click(getByTestId(`${TID}-master`));
    expect(getByTestId(`${TID}-back`)).not.toBeNull();
    expect(getByTestId(`${TID}-leaf`).getAttribute("data-leaf-key")).toBe("repo/ops/L5");
    const masters = getAllByTestId(`${TID}-master`);
    expect(masters.some((node) => node.getAttribute("data-master") === "engine")).toBe(true);
  });

  it("attaches a leaf nested under a sub-master (arbitrary depth)", () => {
    const onPick = vi.fn();
    const { getByTestId, getAllByTestId } = render(<LeafAttachPicker tree={TREE} onPick={onPick} />);

    fireEvent.click(getByTestId(TID)); // open → top level
    fireEvent.click(getByTestId(`${TID}-master`)); // drill into Operations Integration
    const engine = getAllByTestId(`${TID}-master`).find(
      (node) => node.getAttribute("data-master") === "engine",
    );
    fireEvent.click(engine as HTMLElement); // drill into the NESTED master
    fireEvent.click(getByTestId(`${TID}-leaf`)); // its only leaf

    expect(onPick).toHaveBeenCalledWith("repo/engine/E1");
  });

  it("pre-drills to the in-context master so its leaves show immediately on open", () => {
    const onPick = vi.fn();
    const { getByTestId } = render(
      <LeafAttachPicker tree={TREE} onPick={onPick} contextMaster="engine" />,
    );

    fireEvent.click(getByTestId(TID)); // opens already drilled into Engine Room
    expect(getByTestId(`${TID}-back`).textContent).toContain("Engine Room");
    expect(getByTestId(`${TID}-leaf`).getAttribute("data-leaf-key")).toBe("repo/engine/E1");
  });

  it("walks back up out of a drilled master", () => {
    const onPick = vi.fn();
    const { getByTestId, queryByTestId } = render(<LeafAttachPicker tree={TREE} onPick={onPick} />);

    fireEvent.click(getByTestId(TID));
    fireEvent.click(getByTestId(`${TID}-master`)); // drill in
    fireEvent.click(getByTestId(`${TID}-back`)); // back to top
    expect(queryByTestId(`${TID}-back`)).toBeNull();
    expect(getByTestId(`${TID}-master`).getAttribute("data-master")).toBe("ops");
  });
});
