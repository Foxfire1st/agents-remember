import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DetailPanel } from "./DetailPanel";
import { seed } from "./test-utils";

describe("DetailPanel gate respond (task 11)", () => {
  it("renders the gate respond drawer with the full request packet", () => {
    seed("gate-review");
    const { getByTestId, queryByTestId } = render(<DetailPanel selectedId="closeout-005" />);
    expect(getByTestId("gate-review").textContent).toContain("closeout-approval");
    expect(getByTestId("gate-respond-open")).toBeTruthy();
    expect(queryByTestId("gate-approve")).toBeNull();
    fireEvent.click(getByTestId("gate-respond-open"));
    expect(getByTestId("gate-request").textContent).toContain("Changed paths");
  });

  it("does not render the obsolete task-local response box for ask-only attention details", () => {
    seed("blocked");
    const { getByTestId, queryByTestId } = render(<DetailPanel selectedId="plan-002" />);
    expect(queryByTestId("gate-review")).toBeNull();
    expect(queryByTestId("gate-banner")).toBeNull();
    expect(queryByTestId("gate-respond-open")).toBeNull();
    expect(getByTestId("detail-panel").textContent).toContain("persistent worktree");
  });
});
