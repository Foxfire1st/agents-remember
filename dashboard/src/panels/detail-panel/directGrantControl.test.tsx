import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DetailPanel } from "./DetailPanel";
import { seedTaskDocuments, taskDoc } from "./test-utils";

afterEach(() => {
  vi.unstubAllGlobals();
});

function seedGrantedLeaf(over: object = {}) {
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
    status: "planning",
    directGrant: { grantedBy: "developer", grantedAt: "2026-10-06T02:00:00+00:00" },
    ...over,
  });
  seedTaskDocuments([master, leaf]);
}

function seedUngrantedLeaf(over: object = {}) {
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
    status: "planning",
    ...over,
  });
  seedTaskDocuments([master, leaf]);
}

function grantPayload() {
  return JSON.stringify({
    ok: true,
    operation: "direct_execution_grant",
    state: "granted",
    summary: "repo-a:direct-master/leaf.json has its direct-execution grant",
    changed: true,
    grant: { grantedBy: "developer", grantedAt: "2026-10-06T02:00:00+00:00" },
  });
}

function refusalPayload() {
  return JSON.stringify({
    ok: false,
    operation: "direct_execution_grant",
    state: "refused",
    status: "direct-grant-leaf-started",
    detail: "the grant is refused: the leaf has started (status=inProgress).",
    changed: false,
    grant: null,
  });
}

function taskSwitch() {
  return screen.getByRole("switch", { name: "Direct execution" }) as HTMLButtonElement;
}

describe("DetailPanel direct-execution grant switch (MIK-R150 rules 9 to 13)", () => {
  it("draws nothing of the grant on a master's rows", async () => {
    seedGrantedLeaf();
    const view = render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/task.json" />);
    await view.findByText("Direct master");
    // No row control, no row mark: rule 9 removed the control and the badge.
    expect(view.container.querySelectorAll('[data-testid^="subtask-open-direct"]').length).toBe(0);
    expect(screen.queryByText("Make direct")).toBeNull();
    expect(screen.queryByText(/Direct ·/)).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("shows an off switch with no words on an ungranted planning task", async () => {
    seedUngrantedLeaf();
    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);

    const control = taskSwitch();
    expect(control.getAttribute("aria-checked")).toBe("false");
    expect(screen.queryByText(/given by/)).toBeNull();
    // One control only: no second grant button beside the switch.
    expect(screen.queryAllByRole("switch")).toHaveLength(1);
  });

  it("shows an on switch with the giver's words on a granted planning task", async () => {
    seedGrantedLeaf();
    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);

    const control = taskSwitch();
    expect(control.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText("given by developer")).toBeTruthy();
  });

  it("shows bare words and no switch on a granted task that is not planning", async () => {
    seedGrantedLeaf({ status: "inProgress" });
    const view = render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);
    await view.findByText("Direct leaf");

    expect(screen.getByText("Direct · given by developer")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("shows nothing of the grant on an ungranted task that is not planning", async () => {
    seedUngrantedLeaf({ status: "completed" });
    const view = render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);
    await view.findByText("Direct leaf");

    expect(screen.queryByTestId("taskdoc-direct-line")).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("gives the grant through the route and shows the server's words", async () => {
    seedUngrantedLeaf();
    const fetch = vi
      .fn()
      .mockImplementation(() => Promise.resolve(new Response(grantPayload(), { status: 200 })));
    vi.stubGlobal("fetch", fetch);

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);
    fireEvent.click(taskSwitch());

    await waitFor(() => expect(screen.getByText("given by developer")).toBeTruthy());
    expect(fetch).toHaveBeenCalledWith("/api/direct-execution-grant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repository: "repo-a",
        path: "direct-master/leaf.json",
        action: "give",
      }),
    });
    expect(taskSwitch().getAttribute("aria-checked")).toBe("true");
  });

  it("disables the switch while the request runs and keeps its position", async () => {
    seedUngrantedLeaf();
    let answer: ((value: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; })),
    );

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);
    fireEvent.click(taskSwitch());

    await waitFor(() => expect(taskSwitch().disabled).toBe(true));
    // Still off while pending: the position does not move before the answer.
    expect(taskSwitch().getAttribute("aria-checked")).toBe("false");
    await waitFor(() =>
      answer?.(new Response(grantPayload(), { status: 200 })),
    );
    await waitFor(() => expect(taskSwitch().getAttribute("aria-checked")).toBe("true"));
  });

  it("shows the refusal words and keeps the real state when the route refuses", async () => {
    seedUngrantedLeaf();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.resolve(new Response(refusalPayload(), { status: 200 }))),
    );

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);
    fireEvent.click(taskSwitch());

    await waitFor(() =>
      expect(screen.getByTestId("taskdoc-direct-note").textContent).toContain("the leaf has started"),
    );
    expect(taskSwitch().getAttribute("aria-checked")).toBe("false");
  });

  it("follows a live projection change after mount", async () => {
    seedGrantedLeaf();
    const view = render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);
    expect(taskSwitch().getAttribute("aria-checked")).toBe("true");

    seedUngrantedLeaf();
    view.rerender(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);
    await waitFor(() => expect(taskSwitch().getAttribute("aria-checked")).toBe("false"));

    seedGrantedLeaf();
    view.rerender(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);
    await waitFor(() => expect(taskSwitch().getAttribute("aria-checked")).toBe("true"));
  });

  it("keeps the actual grant after a refused revoke", async () => {
    seedGrantedLeaf();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.resolve(new Response(refusalPayload(), { status: 200 }))),
    );

    render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf.json" />);
    fireEvent.click(taskSwitch());

    await waitFor(() =>
      expect(screen.getByTestId("taskdoc-direct-note").textContent).toContain("the leaf has started"),
    );
    // The served refusal carries no grant, but the switch keeps the actual one.
    expect(screen.getByText("given by developer")).toBeTruthy();
    expect(taskSwitch().getAttribute("aria-checked")).toBe("true");
  });

  it("does not transfer grant state across selection changes", async () => {
    // L150-S7-TEST-01 repair: the old test re-rendered the SAME selectedId with
    // new seed data and never changed the selection. This one really changes
    // selectedId: leaf one carries a grant and a refusal notice, leaf two is
    // untouched. Rule 13's reset must clear both the grant and the notice.
    const master = taskDoc({
      lifecycleId: undefined,
      kind: "master",
      title: "Direct master",
      docPath: "/tasks/repo-a/direct-master/task.json",
      objective: "Master objective.",
      subTasks: [
        { number: "L1", name: "Direct leaf one", file: "leaf1.md", status: "planning", scope: "" },
        { number: "L2", name: "Direct leaf two", file: "leaf2.md", status: "planning", scope: "" },
      ],
    });
    const leafOne = taskDoc({
      id: "L1",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Direct leaf one",
      docPath: "/tasks/repo-a/direct-master/leaf1.json",
      objective: "Leaf one objective.",
      status: "planning",
      directGrant: { grantedBy: "developer", grantedAt: "2026-10-06T02:00:00+00:00" },
    });
    const leafTwo = taskDoc({
      id: "L2",
      lifecycleId: undefined,
      kind: "subTask",
      title: "Direct leaf two",
      docPath: "/tasks/repo-a/direct-master/leaf2.json",
      objective: "Leaf two objective.",
      status: "planning",
    });
    seedTaskDocuments([master, leafOne, leafTwo]);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.resolve(new Response(refusalPayload(), { status: 200 }))),
    );

    const view = render(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf1.json" />);
    await view.findByText("Direct leaf one");
    expect(taskSwitch().getAttribute("aria-checked")).toBe("true");

    // A refused revoke leaves a notice on leaf one's line.
    fireEvent.click(taskSwitch());
    await waitFor(() =>
      expect(screen.getByTestId("taskdoc-direct-note").textContent).toContain("the leaf has started"),
    );

    // A real selection change onto the untouched leaf two.
    view.rerender(<DetailPanel selectedId="taskdoc:/tasks/repo-a/direct-master/leaf2.json" />);
    await view.findByText("Direct leaf two");
    await waitFor(() => expect(taskSwitch().getAttribute("aria-checked")).toBe("false"));
    expect(screen.queryByText(/given by/)).toBeNull();
    // Leaf one's notice does not follow the selection.
    expect(screen.queryByTestId("taskdoc-direct-note")).toBeNull();
  });
});
