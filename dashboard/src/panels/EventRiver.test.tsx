import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { dashboardStore } from "../data/store";
import type { ObserverEvent } from "../types/event";
import { EventRiver } from "./EventRiver";

afterEach(cleanup);
beforeEach(() => dashboardStore.setState({ events: [] }));

function ev(partial: Partial<ObserverEvent> & { kind: string }): ObserverEvent {
  return {
    schema: "ar-observer-event/v1",
    id: partial.id ?? `e-${partial.kind}-${Math.random().toString(36).slice(2)}`,
    ts: "2026-06-23T10:11:12+00:00",
    trust: "observed",
    actor: "model",
    ...partial,
  } as ObserverEvent;
}

// 07b v1: read.packet is the river's one per-kind treatment — "Read: <basename>", the read's repo in
// the meta, and the full path(s) on hover. Everything else stays generic.
describe("EventRiver read.packet row (07b)", () => {
  it("renders 'Read: <basename>' with the repo, and the full path on hover", () => {
    const full = "mcp/src/agents_remember/kernel/coordination_context/contracts.py";
    dashboardStore.setState({
      events: [
        ev({
          kind: "read.packet",
          lifecycleId: "lc-1",
          data: {
            repoId: "agents-remember",
            files: [{ path: full, lines: "full", status: "found", bytes: 2270 }],
          },
        }),
      ],
    });
    const { getByText } = render(<EventRiver />);
    const label = getByText("Read: contracts.py");
    expect(label.getAttribute("title")).toBe(full);
    const item = label.closest("[data-testid='river-item']");
    expect(item?.textContent).toContain("agents-remember");
  });

  it("summarizes a multi-file batch as '+N more' with every path on hover", () => {
    dashboardStore.setState({
      events: [
        ev({
          kind: "read.packet",
          data: {
            repoId: "agents-remember",
            files: [{ path: "a/one.py" }, { path: "b/two.py" }, { path: "c/three.py" }],
          },
        }),
      ],
    });
    const { getByText } = render(<EventRiver />);
    const label = getByText("Read: one.py +2 more");
    const title = label.getAttribute("title") ?? "";
    expect(title).toContain("a/one.py");
    expect(title).toContain("b/two.py");
    expect(title).toContain("c/three.py");
  });

  it("renders other kinds generically (no read treatment)", () => {
    dashboardStore.setState({ events: [ev({ kind: "tool.completed", lifecycleId: "lc-1" })] });
    const { getByText } = render(<EventRiver />);
    expect(getByText("tool.completed")).not.toBeNull();
  });
});
