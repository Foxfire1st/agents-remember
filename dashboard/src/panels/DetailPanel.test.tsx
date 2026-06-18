import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import { DetailPanel } from "./DetailPanel";

function seed(name: string) {
  const projection = GALLERY.find((entry) => entry.name === name)?.projection;
  if (!projection) throw new Error(`fixture not found: ${name}`);
  dashboardStore.getState().applySnapshot(projection);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DetailPanel gate review (6c Part B)", () => {
  it("renders the gate review drawer with a button per decision verb", () => {
    seed("gate-review");
    const { getByTestId } = render(<DetailPanel selectedId="closeout-005" />);
    expect(getByTestId("gate-review").textContent).toContain("closeout-approval");
    expect(getByTestId("gate-approve")).toBeTruthy();
    expect(getByTestId("gate-reject")).toBeTruthy();
  });

  it("POSTs the decision to /api/actions and reports recorded", async () => {
    seed("gate-review");
    const fetchMock = vi.fn().mockResolvedValue({ status: 202 });
    vi.stubGlobal("fetch", fetchMock);
    const { getByTestId } = render(<DetailPanel selectedId="closeout-005" />);
    fireEvent.click(getByTestId("gate-approve"));
    await waitFor(() => expect(getByTestId("gate-status").textContent).toContain("recorded"));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/actions/approve",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("reports no-open-gate on a 409", async () => {
    seed("gate-review");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 409 }));
    const { getByTestId } = render(<DetailPanel selectedId="closeout-005" />);
    fireEvent.click(getByTestId("gate-reject"));
    await waitFor(() => expect(getByTestId("gate-status").textContent).toContain("no open gate"));
  });

  it("falls back to the proto-gate ask banner when there is no durable gate", () => {
    seed("blocked");
    const { getByTestId, queryByTestId } = render(<DetailPanel selectedId="plan-002" />);
    expect(queryByTestId("gate-review")).toBeNull();
    expect(getByTestId("gate-banner").textContent).toContain("Approve the plan?");
  });
});
