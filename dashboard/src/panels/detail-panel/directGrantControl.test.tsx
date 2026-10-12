import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DetailPanel } from "./DetailPanel";
import { seedTaskDocuments, taskDoc } from "./test-utils";

afterEach(() => {
  vi.unstubAllGlobals();
});

function seedGrantedLeaf() {
  const master = taskDoc({
    lifecycleId: undefined,
    kind: "master",
    title: "Direct master",
    docPath: "/tasks/repo-a/direct-master/task.json",
    objective: "Master objective.",
    subTasks: [
      {
        number: "L1",
        name: "Direct leaf",
        file: "leaf.md",
        status: "planning",
        scope: "",
      },
    ],
  });
  const leaf = taskDoc({
    id: "L1",
    lifecycleId: undefined,
    kind: "subTask",
    title: "Direct leaf",
    docPath: "/tasks/repo-a/direct-master/leaf.json",
    objective: "Leaf objective.",
    directGrant: { grantedBy: "developer", grantedAt: "2026-10-06T02:00:00+00:00" },
  });
  seedTaskDocuments([master, leaf]);
}

function seedUngrantedLeaf() {
  const master = taskDoc({
    lifecycleId: undefined,
    kind: "master",
    title: "Direct master",
    docPath: "/tasks/repo-a/direct-master/task.json",
    objective: "Master objective.",
    subTasks: [
      {
        number: "L1",
        name: "Direct leaf",
        file: "leaf.md",
        status: "planning",
        scope: "",
      },
    ],
  });
  const leaf = taskDoc({
    id: "L1",
    lifecycleId: undefined,
    kind: "subTask",
    title: "Direct leaf",
    docPath: "/tasks/repo-a/direct-master/leaf.json",
    objective: "Leaf objective.",
  });
  seedTaskDocuments([master, leaf]);
}

describe("DetailPanel direct-execution grant control (MIK-R94 rules 2, 4)", () => {
  it("shows the grant badge with the giver on a granted leaf row", () => {
    seedGrantedLeaf();

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/task.json" />);

    expect(screen.getByText("Direct · developer")).toBeTruthy();
    expect(screen.getByTestId("subtask-open-direct-1").textContent).toContain("Take back");
  });

  it("offers the control without a badge on an ungranted leaf row", () => {
    seedUngrantedLeaf();

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/task.json" />);

    expect(screen.queryByText(/Direct ·/)).toBeNull();
    expect(screen.getByTestId("subtask-open-direct-1").textContent).toContain("Make direct");
  });

  it("gives the grant through the route and shows the server's words", async () => {
    seedUngrantedLeaf();
    const grantPayload = JSON.stringify({
      ok: true,
      operation: "direct_execution_grant",
      state: "granted",
      summary: "repo-a:direct-master/leaf.json has its direct-execution grant",
      changed: true,
      grant: { grantedBy: "developer", grantedAt: "2026-10-06T02:00:00+00:00" },
    });
    const fetch = vi
      .fn()
      .mockImplementation(() => Promise.resolve(new Response(grantPayload, { status: 200 })));
    vi.stubGlobal("fetch", fetch);

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/task.json" />);
    fireEvent.click(screen.getByTestId("subtask-open-direct-1"));

    await waitFor(() =>
      expect(screen.getByTestId("subtask-open-direct-note-1").textContent).toContain(
        "has its direct-execution grant",
      ),
    );
    expect(fetch).toHaveBeenCalledWith("/api/direct-execution-grant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repository: "repo-a",
        path: "direct-master/leaf.json",
        action: "give",
      }),
    });
    expect(screen.getByTestId("subtask-open-direct-1").textContent).toContain("Take back");
  });

  it("shows the refusal words when the route refuses", async () => {
    seedUngrantedLeaf();
    const refusalPayload = JSON.stringify({
      ok: false,
      operation: "direct_execution_grant",
      state: "refused",
      status: "direct-grant-leaf-started",
      detail: "the grant is refused: the leaf has started (status=inProgress).",
      changed: false,
      grant: null,
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(() => Promise.resolve(new Response(refusalPayload, { status: 200 }))),
    );

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/task.json" />);
    fireEvent.click(screen.getByTestId("subtask-open-direct-1"));

    await waitFor(() =>
      expect(screen.getByTestId("subtask-open-direct-note-1").textContent).toContain(
        "the leaf has started",
      ),
    );
    expect(screen.getByTestId("subtask-open-direct-1").textContent).toContain("Make direct");
  });

  it("follows a live projection change after mount", async () => {
    seedGrantedLeaf();
    const view = render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/task.json" />);
    expect(screen.getByText("Direct · developer")).toBeTruthy();
    expect(screen.getByTestId("subtask-open-direct-1").textContent).toContain("Take back");

    seedUngrantedLeaf();
    view.rerender(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/task.json" />);
    await waitFor(() => expect(screen.queryByText(/Direct ·/)).toBeNull());
    expect(screen.getByTestId("subtask-open-direct-1").textContent).toContain("Make direct");

    seedGrantedLeaf();
    view.rerender(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/task.json" />);
    await waitFor(() => expect(screen.getByText("Direct · developer")).toBeTruthy());
    expect(screen.getByTestId("subtask-open-direct-1").textContent).toContain("Take back");
  });

  it("keeps the actual grant after a refused revoke", async () => {
    seedGrantedLeaf();
    const refusalPayload = JSON.stringify({
      ok: false,
      operation: "direct_execution_grant",
      state: "refused",
      status: "direct-grant-leaf-started",
      detail: "the grant is refused: the leaf has started.",
      changed: false,
      grant: null,
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(() => Promise.resolve(new Response(refusalPayload, { status: 200 }))),
    );

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/task.json" />);
    fireEvent.click(screen.getByTestId("subtask-open-direct-1"));

    await waitFor(() =>
      expect(screen.getByTestId("subtask-open-direct-note-1").textContent).toContain(
        "the leaf has started",
      ),
    );
    // The served refusal carries no grant, but the control keeps the actual one.
    expect(screen.getByText("Direct · developer")).toBeTruthy();
    expect(screen.getByTestId("subtask-open-direct-1").textContent).toContain("Take back");
  });

  it("does not transfer grant state across selection changes", async () => {
    seedGrantedLeaf();
    const view = render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/task.json" />);
    expect(screen.getByText("Direct · developer")).toBeTruthy();

    seedUngrantedLeaf();
    view.rerender(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);
    await waitFor(() =>
      expect(screen.getByTestId("taskdoc-direct-direct-0").textContent).toContain("Make direct"),
    );
    expect(screen.queryByText(/Direct ·/)).toBeNull();
  });

  it("shows the giver and control on the directly selected leaf", async () => {
    seedGrantedLeaf();
    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);

    expect(screen.getByText("Direct · developer")).toBeTruthy();
    expect(screen.getByTestId("taskdoc-direct-direct-0").textContent).toContain("Take back");
  });

  it("offers give without a badge on the directly selected ungranted leaf", async () => {
    seedUngrantedLeaf();
    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);

    expect(screen.queryByText(/Direct ·/)).toBeNull();
    expect(screen.getByTestId("taskdoc-direct-direct-0").textContent).toContain("Make direct");
  });
});
