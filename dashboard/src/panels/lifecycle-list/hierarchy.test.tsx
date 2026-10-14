import { act, fireEvent, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { WorkspaceProjection } from "../../types/projection";
import { LifecycleList } from "./LifecycleList";
import {
  EMPTY_ANALYTICS,
  collapsibleHierarchyProjection,
  enclosure,
  installLifecycleListCleanup,
  lifecycle,
  projection,
  rowHover,
  rowOf,
  seed,
  standInForCssEscape,
  seriesNode,
  taskDoc,
} from "./test-utils";

// The indent of a row, in rem: the padding of the line that fills it.
function indentOf(row: Element | null): number {
  return Number.parseFloat((row?.firstElementChild as HTMLElement).style.paddingLeft);
}

installLifecycleListCleanup();

describe("LifecycleList task labels — hierarchy and phase grouping", () => {
  it("reports discarded planning work without adding it to completed progress", () => {
    seed(
      projection({
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              id: "MASTER",
              kind: "master",
              title: "Audited planning master",
              docPath: "/tasks/master/task.json",
              subTasks: [
                {
                  number: "1",
                  name: "Live planning leaf",
                  file: "01_live.md",
                  status: "planning",
                  scope: "",
                },
              ],
              discardedCount: 1,
            }),
          ],
        },
      }),
    );

    const { getByText } = render(
      <LifecycleList selectedId={null} onSelect={vi.fn()} />,
    );
    const row = getByText("Audited planning master").closest("[role='option']");
    // The row carries the figure; what was discarded is in its hover.
    expect(row?.textContent).toContain("0/1");
    expect(row?.textContent).not.toContain("1/1");
    expect(rowHover(row)).toContain("Tasks completed: 0 of 1, 1 discarded");
  });

  it("leaves abandoned rows out of the series figure and names them beside it", () => {
    const leaf = (number: string, status: string) => ({
      number,
      name: `Leaf ${number}`,
      file: "",
      status,
      scope: "",
    });
    seed(
      projection({
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              id: "COMPLETE",
              kind: "master",
              title: "Master with an abandoned leaf",
              docPath: "/tasks/complete/task.json",
              subTasks: [leaf("1", "Completed"), leaf("2", "Completed"), leaf("3", "abandoned")],
            }),
            taskDoc({
              id: "DROPPED",
              kind: "master",
              title: "Master whose leaves were all abandoned",
              docPath: "/tasks/dropped/task.json",
              subTasks: [leaf("1", "abandoned"), leaf("2", "abandoned")],
            }),
          ],
          series: [
            seriesNode({
              seriesId: "folder-series",
              title: "Series without a master document",
              docPath: "/tasks/folder-series/task.json",
              doneCount: 5,
              totalCount: 5,
              abandonedCount: 1,
              discardedCount: 1,
            }),
          ],
        },
      }),
    );

    const { getByText } = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    const figure = (title: string) => getByText(title).closest("[role='option']")?.textContent;
    const hover = (title: string) => rowHover(getByText(title).closest("[role='option']"));
    // Every row that will run is Completed, so the master reads complete; its hover names the
    // abandoned leaf beside the figure.
    expect(figure("Master with an abandoned leaf")).toContain("2/2");
    expect(hover("Master with an abandoned leaf")).toContain("Tasks completed: 2 of 2, 1 abandoned");
    // Nothing will run: no figure on the row, and the hover still says what was abandoned.
    expect(figure("Master whose leaves were all abandoned")).not.toContain("0/");
    expect(hover("Master whose leaves were all abandoned")).toContain("2 abandoned");
    expect(figure("Series without a master document")).toContain("5/5");
    expect(hover("Series without a master document")).toContain(
      "Tasks completed: 5 of 5, 1 abandoned, 1 discarded",
    );
  });

  it("renders the orchestration tier above its commanded masters by place and weight (L14, MIK-R144)", () => {
    // An orchestration task is a master doc carrying `orchestrates`.
    // It stands at depth 0; a master it names nests one step; that master's leaves one step
    // further; an uncommanded master is top-level. A sprint and a master carry a heavy title and
    // no rank mark.
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [],
        enclosures: [
          enclosure({
            enclosure: "/contracts/15",
            lifecycleId: "",
            leafId: "15_parallel-leaf-enclosure-workflow",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              id: "SPRINT-02",
              kind: "master",
              title: "SPRINT 02 · rollout",
              docPath: "/tasks/sprint-02/task.json",
              orchestrates: ["260610_browser-dashboard"],
              createdAt: "2026-06-19T09:00:00+00:00",
            }),
            taskDoc({
              kind: "master",
              title: "Browser Dashboard Series",
              docPath: "/tasks/260610_browser-dashboard/task.json",
              createdAt: "2026-06-20T08:00:00+00:00",
            }),
            taskDoc({
              kind: "master",
              title: "Free Standing Series",
              docPath: "/tasks/260620_free-standing/task.json",
              createdAt: "2026-06-21T08:00:00+00:00",
            }),
            taskDoc({
              id: "15",
              title: "Parallel Leaf Enclosure Workflow",
              docPath: "/tasks/260610_browser-dashboard/15_parallel-leaf-enclosure-workflow.json",
              createdAt: "2026-06-22T08:00:00+00:00",
            }),
          ],
          series: [
            seriesNode({
              seriesId: "260610_browser-dashboard",
              subTasks: [
                {
                  number: "15",
                  name: "Parallel Leaf Enclosure Workflow",
                  file: "15_parallel-leaf-enclosure-workflow.md",
                  status: "inProgress",
                  scope: "",
                  createdAt: "2026-06-20T09:00:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const { getByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    // The orchestration row: top-level, heavy title, no rank mark.
    const sprintTitle = getByText("SPRINT 02 · rollout");
    const sprintRow = sprintTitle.closest("[role='option']");
    expect(sprintRow?.getAttribute("data-tier")).toBe("orchestration");
    expect(sprintRow?.getAttribute("data-depth")).toBe("0");
    expect(sprintTitle.className).toContain("fw_600");
    expect(sprintRow?.querySelector("svg")).toBeNull();

    // The commanded master nests under the orchestration row, one step of the indent in.
    const masterTitle = getByText("Browser Dashboard Series");
    const masterRow = masterTitle.closest("[role='option']");
    expect(masterRow?.getAttribute("data-tier")).toBe("management");
    expect(masterRow?.getAttribute("data-depth")).toBe("1");
    expect(masterRow?.getAttribute("data-parent-key")).toBe("taskdoc:/tasks/sprint-02/task.json");
    expect(masterTitle.className).toContain("fw_600");
    expect(masterRow?.querySelector("svg")).toBeNull();
    const step = indentOf(masterRow) - indentOf(sprintRow);
    expect(step).toBeGreaterThan(0);

    // A leaf stands one step further, with its id before its title and a title of normal weight.
    const leafTitle = getByText("Parallel Leaf Enclosure Workflow");
    const leafRow = leafTitle.closest("[role='option']");
    expect(leafRow?.getAttribute("data-depth")).toBe("2");
    expect(leafRow?.getAttribute("data-tier")).toBeNull();
    expect(leafTitle.previousElementSibling?.textContent).toBe("15");
    expect(leafTitle.className).not.toContain("fw_600");
    expect(indentOf(leafRow) - indentOf(masterRow)).toBeCloseTo(step, 3);

    // The uncommanded master is top-level and carries no tier.
    const freeRow = getByText("Free Standing Series").closest("[role='option']");
    expect(freeRow?.getAttribute("data-tier")).toBeNull();
    expect(freeRow?.getAttribute("data-depth")).toBe("0");
    expect(indentOf(freeRow)).toBeCloseTo(indentOf(sprintRow), 3);
  });

  it("defaults hierarchy disclosures to expanded and renders controls only for parents", () => {
    seed(collapsibleHierarchyProjection());

    const { getByRole, getByText } = render(
      <LifecycleList selectedId={null} onSelect={vi.fn()} />,
    );

    expect(getByText("Tasks · 6")).toBeTruthy();
    expect(getByRole("button", { name: "Collapse Sprint 02 tasks" }).getAttribute("aria-expanded"))
      .toBe("true");
    expect(getByRole("button", { name: "Collapse Master A tasks" }).getAttribute("aria-expanded"))
      .toBe("true");
    expect(getByRole("button", { name: "Collapse Master B tasks" }).getAttribute("aria-expanded"))
      .toBe("true");
    expect(getByText("Leaf A1").closest("[role='option']")?.querySelector("button")).toBeNull();
    expect(getByText("Leaf B1").closest("[role='option']")?.querySelector("button")).toBeNull();
    expect(getByText("Empty Master").closest("[role='option']")?.querySelector("button")).toBeNull();
  });

  it("keeps sprint and master collapse independent without changing selection or BY PHASE", () => {
    const onSelect = vi.fn();
    seed(collapsibleHierarchyProjection());
    // The chosen document stands outside the sprint: what holds the chosen document does not
    // close (the next test), so collapsing is tried on parents that do not hold it.
    const selected = "taskdoc:/tasks/empty-master/task.json";
    const view = render(<LifecycleList selectedId={selected} onSelect={onSelect} />);
    const isChosen = () =>
      view.getByText("Empty Master").closest("[role='option']")?.getAttribute("aria-selected");

    expect(isChosen()).toBe("true");
    const masterToggle = view.getByRole("button", { name: "Collapse Master A tasks" });
    expect(masterToggle.tagName).toBe("BUTTON");
    expect(masterToggle.tabIndex).toBe(0);
    fireEvent.keyDown(masterToggle, { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(masterToggle);
    expect(view.queryByText("Leaf A1")).toBeNull();
    expect(view.getByText("Leaf B1")).toBeTruthy();
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(view.getByRole("button", { name: "Collapse Sprint 02 tasks" }));
    expect(view.queryByText("Master A")).toBeNull();
    expect(view.queryByText("Master B")).toBeNull();
    expect(view.getByText("Tasks · 6")).toBeTruthy();
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(view.getByRole("button", { name: "Expand Sprint 02 tasks" }));
    expect(view.getByText("Master A")).toBeTruthy();
    expect(view.getByText("Master B")).toBeTruthy();
    expect(view.queryByText("Leaf A1")).toBeNull();
    expect(view.getByText("Leaf B1")).toBeTruthy();
    expect(view.getByRole("button", { name: "Expand Master A tasks" }).getAttribute("aria-expanded"))
      .toBe("false");

    fireEvent.click(view.getByText("BY PHASE"));
    expect(view.getByText("Leaf A1")).toBeTruthy();
    expect(view.getByText("Leaf B1")).toBeTruthy();
    expect(isChosen()).toBe("true");
    expect(view.queryByRole("button", { name: /^(Collapse|Expand) .* tasks$/ })).toBeNull();
    expect(view.getByText("Tasks · 6")).toBeTruthy();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("keeps the tree open down to the chosen document", () => {
    seed(collapsibleHierarchyProjection());
    const leafA = "taskdoc:/tasks/master-a/01_leaf-a1.json";
    const view = render(<LifecycleList selectedId={leafA} onSelect={vi.fn()} />);

    // The master and the sprint above the chosen leaf do not close, and the try is not remembered.
    const masterToggle = view.getByRole("button", { name: "Collapse Master A tasks" });
    expect(masterToggle.getAttribute("title")).toBe("Stays open: the chosen document is inside");
    fireEvent.click(masterToggle);
    fireEvent.click(view.getByRole("button", { name: "Collapse Sprint 02 tasks" }));
    expect(view.getByText("Leaf A1")).toBeTruthy();
    expect(window.localStorage.getItem("operations.tasks.collapsed.v1")).toBeNull();

    // A master the developer closed opens again while the chosen document is inside it.
    fireEvent.click(view.getByRole("button", { name: "Collapse Master B tasks" }));
    expect(view.queryByText("Leaf B1")).toBeNull();
    view.rerender(
      <LifecycleList selectedId="taskdoc:/tasks/master-b/01_leaf-b1.json" onSelect={vi.fn()} />,
    );
    expect(rowOf(view.getByText("Leaf B1")).getAttribute("aria-selected")).toBe("true");
    // The choice moved away: Master A closes now, Master B is closed again as it was left.
    fireEvent.click(view.getByRole("button", { name: "Collapse Master A tasks" }));
    expect(view.queryByText("Leaf A1")).toBeNull();
    view.rerender(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    expect(view.queryByText("Leaf B1")).toBeNull();
  });

  it("opens and closes a parent row with the right and left arrow keys", () => {
    seed(collapsibleHierarchyProjection());
    const view = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    const master = rowOf(view.getByText("Master A"));

    fireEvent.keyDown(master, { key: "ArrowLeft" });
    expect(view.queryByText("Leaf A1")).toBeNull();
    // A closed row stays closed on a second left arrow; the right arrow opens it.
    fireEvent.keyDown(rowOf(view.getByText("Master A")), { key: "ArrowLeft" });
    expect(view.queryByText("Leaf A1")).toBeNull();
    fireEvent.keyDown(rowOf(view.getByText("Master A")), { key: "ArrowRight" });
    expect(view.getByText("Leaf A1")).toBeTruthy();
    // A row without children gives the two keys no meaning.
    fireEvent.keyDown(rowOf(view.getByText("Leaf A1")), { key: "ArrowLeft" });
    expect(view.getByText("Leaf A1")).toBeTruthy();
    expect(view.getByText("Master A")).toBeTruthy();
    // With a modifier the keys are the browser's (Alt+Left is "back"): the row does not take them.
    for (const modifier of ["altKey", "ctrlKey", "metaKey", "shiftKey"]) {
      const taken = !fireEvent.keyDown(rowOf(view.getByText("Master A")), {
        key: "ArrowLeft",
        [modifier]: true,
      });
      expect(taken, modifier).toBe(false);
      expect(view.getByText("Leaf A1")).toBeTruthy();
    }
  });

  it("finds a row by typing what it shows", async () => {
    seed(collapsibleHierarchyProjection());
    standInForCssEscape();
    // One search a list: what was typed before is part of the next search for a moment.
    const typed = async (text: string) => {
      const view = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
      act(() => rowOf(view.getByText("Sprint 02")).focus());
      await userEvent.keyboard(text);
      const found = document.activeElement?.querySelector("[data-part='title']")?.textContent;
      view.unmount();
      return found;
    };

    // A master by its title; a task by its id as the row writes it, then its title.
    expect(await typed("emp")).toBe("Empty Master");
    expect(await typed("01 leaf b")).toBe("Leaf B1");
  });

  it("still finds a task that stands alone by typing its title", async () => {
    // Two root documents that no master numbers. Their rows show an id ("doc") before the title,
    // and typing the title found them before the id had a cell of its own.
    seed(
      projection({
        lifecycles: [],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "light",
              title: "Another Root Task",
              docPath: "/tasks/repo-a/other/task.json",
              createdAt: "2026-06-19T09:00:00+00:00",
              status: "planning",
            }),
            taskDoc({
              kind: "light",
              title: "Standalone Planning Task",
              docPath: "/tasks/repo-a/plan/task.json",
              createdAt: "2026-06-20T09:00:00+00:00",
              status: "planning",
            }),
          ],
        },
      }),
    );
    standInForCssEscape();
    const view = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    const other = rowOf(view.getByText("Another Root Task"));
    expect(other.textContent).toContain("doc");
    act(() => other.focus());

    await userEvent.keyboard("sta");

    expect(document.activeElement).toBe(rowOf(view.getByText("Standalone Planning Task")));
  });

  // A master with four tasks that have a worktree: one waits, one is completed, one is in work,
  // one was abandoned, created in this order. No lifecycle reports, so the documents speak.
  function needProjection() {
    const leaves = [
      ["1", "First waits", "planning"],
      ["2", "Second is done", "Completed"],
      ["3", "Third works", "inProgress"],
      ["4", "Fourth was dropped", "abandoned"],
    ];
    return projection({
      enclosures: leaves.map(([number]) =>
        enclosure({
          enclosure: `/contracts/need-${number}`,
          lifecycleId: "",
          leafId: `0${number}_leaf`,
          taskRoot: "/tasks/need",
        }),
      ),
      analytics: {
        ...EMPTY_ANALYTICS,
        taskDocuments: [
          taskDoc({
            id: "NEED",
            kind: "master",
            title: "Need Master",
            docPath: "/tasks/need/task.json",
            createdAt: "2026-06-20T08:00:00+00:00",
          }),
          ...leaves.map(([number, title, status]) =>
            taskDoc({
              id: `NEED-L${number}`,
              title,
              status,
              docPath: `/tasks/need/0${number}_leaf.json`,
              createdAt: `2026-06-2${number}T08:00:00+00:00`,
            }),
          ),
        ],
        series: [
          seriesNode({
            seriesId: "need",
            title: "Need Master",
            docPath: "/tasks/need/task.json",
            subTasks: leaves.map(([number, name, status]) => ({
              number,
              name,
              file: `0${number}_leaf.md`,
              status,
              scope: "",
            })),
          }),
        ],
      },
    });
  }
  // The lines of the column as they read, the caret of a parent and of a line included.
  const optionTexts = (container: HTMLElement) =>
    [...container.querySelectorAll("[role='option']")].map((row) => row.textContent);

  it("orders the tasks of a master by need and keeps the completed behind one line", () => {
    const onSelect = vi.fn();
    seed(needProjection());
    const { container, getByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    // In work before waiting, whatever was created first; a task shows its id inside its master.
    expect(optionTexts(container)).toEqual([
      "▾Need Masterin work",
      "L3Third worksin work",
      "L1First waitswaiting",
      "▸1 completed, 1 abandoned",
    ]);
    expect(getByText("Tasks · 5")).toBeTruthy();

    // The line opens in place and is no document: nothing is chosen. Its name says in words
    // what its caret shows.
    const line = () => rowOf(getByText("1 completed, 1 abandoned"));
    expect(line().getAttribute("aria-label")).toBe("1 completed, 1 abandoned, hidden");
    fireEvent.click(getByText("1 completed, 1 abandoned"));
    expect(line().getAttribute("aria-label")).toBe("1 completed, 1 abandoned, shown");
    expect(optionTexts(container).slice(3)).toEqual([
      "▾1 completed, 1 abandoned",
      "L2Second is donecompleted",
      "L4Fourth was droppedabandoned",
    ]);
    expect(rowOf(getByText("Second is done")).getAttribute("data-depth")).toBe("2");
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(getByText("1 completed, 1 abandoned"));
    expect(optionTexts(container)).toHaveLength(4);
  });

  it("opens the line of completed ones by itself while the chosen document is behind it", () => {
    seed(needProjection());
    const view = render(
      <LifecycleList selectedId="taskdoc:/tasks/need/02_leaf.json" onSelect={vi.fn()} />,
    );
    expect(rowOf(view.getByText("Second is done")).getAttribute("aria-selected")).toBe("true");
    // It stays open while it holds the choice.
    fireEvent.click(view.getByText("1 completed, 1 abandoned"));
    expect(view.getByText("Second is done")).toBeTruthy();
    view.rerender(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    expect(view.queryByText("Second is done")).toBeNull();
  });

  it("groups BY PHASE in the order of the work, with plain words and whole ids", () => {
    seed(needProjection());
    const { container, getByText } = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    fireEvent.click(getByText("BY PHASE"));

    // A document without a lifecycle stands under its status, in the task page's words; each
    // group carries its count. No line of completed ones: every row is listed.
    expect([...container.querySelectorAll("header")].map((label) => label.textContent)).toEqual([
      "in work2",
      "waiting1",
      "completed1",
      "abandoned1",
    ]);
    expect(optionTexts(container)).toEqual([
      "Need Masterin work",
      "NEED-L3Third worksin work",
      "NEED-L1First waitswaiting",
      "NEED-L2Second is donecompleted",
      "NEED-L4Fourth was droppedabandoned",
    ]);
  });

  // One attention entry on the lifecycle of the leaf of Master B.
  function entryProjection(carried: boolean): WorkspaceProjection {
    const base = collapsibleHierarchyProjection();
    return {
      ...base,
      lifecycles: [lifecycle({ id: "LC-B1", repoId: "agents-remember" })],
      analytics: {
        ...(base.analytics ?? EMPTY_ANALYTICS),
        taskDocuments: (base.analytics?.taskDocuments ?? []).map((doc) =>
          carried && doc.title === "Leaf B1" ? { ...doc, lifecycleId: "LC-B1" } : doc,
        ),
        attentionQueue: [
          {
            id: "stale-session:LC-B1",
            kind: "stale-session",
            severity: "info",
            lane: "lifecycle",
            title: "Session gone quiet",
            lifecycleId: "LC-B1",
          },
        ],
      },
    };
  }
  // jsdom has no scrollIntoView: stand in for it, and take the stand-in away again. The row that
  // is brought into view takes the focus, so CSS.escape is stood in for too.
  async function withScrollStandIn(run: (shown: ReturnType<typeof vi.fn>) => Promise<void>) {
    const shown = vi.fn();
    Element.prototype.scrollIntoView = shown;
    standInForCssEscape();
    try {
      await run(shown);
    } finally {
      Reflect.deleteProperty(Element.prototype, "scrollIntoView");
    }
  }
  // The shell stores what the bell's layer opens as the lifecycle's own selection
  // (Cockpit.tsx `open`): the list is handed this, never the bare id.
  const chosenLifecycle = "lifecycle:LC-B1";

  it("chooses and shows the task of an attention entry that the bell's layer opens", async () => {
    seed(entryProjection(true));
    const onSelect = vi.fn();
    await withScrollStandIn(async (shown) => {
      const view = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
      fireEvent.click(view.getByRole("button", { name: "Collapse Master B tasks" }));
      expect(view.queryByText("Leaf B1")).toBeNull();

      // The entry's link opens the lifecycle, as "Open" did.
      fireEvent.click(view.getByTestId("attention-bell"));
      fireEvent.click(view.getByText("01 Leaf B1"));
      expect(onSelect).toHaveBeenCalledWith("LC-B1");
      expect(shown).not.toHaveBeenCalled();

      // The choice arrives: the task's row is marked as the chosen one, its closed master opens
      // over it, and the row is brought into view once and takes the keyboard's focus.
      view.rerender(<LifecycleList selectedId={chosenLifecycle} onSelect={onSelect} />);
      const row = rowOf(view.getByText("Leaf B1"));
      expect(row.hasAttribute("data-chosen")).toBe(true);
      await waitFor(() => expect(shown).toHaveBeenCalledTimes(1));
      expect(shown.mock.instances[0]).toBe(row);
      expect(document.activeElement).toBe(row);

      // The list's own selection is still the lifecycle, so a click on the marked row chooses
      // the task's document, as a click on that row always did.
      expect(row.getAttribute("aria-selected")).toBe("false");
      fireEvent.click(view.getByText("Leaf B1"));
      expect(onSelect).toHaveBeenLastCalledWith("taskdoc:/tasks/master-b/01_leaf-b1.json");

      // A later render of the same choice moves nothing again.
      view.rerender(<LifecycleList selectedId={chosenLifecycle} onSelect={vi.fn()} />);
      expect(shown).toHaveBeenCalledTimes(1);
    });
  });

  it("moves nothing for an entry whose lifecycle has no row, also when a row comes later", async () => {
    seed(entryProjection(false));
    const onSelect = vi.fn();
    await withScrollStandIn(async (shown) => {
      const view = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
      fireEvent.click(view.getByTestId("attention-bell"));
      fireEvent.click(view.getByText("lifecycle LC-B1"));
      expect(onSelect).toHaveBeenCalledWith("LC-B1");
      view.rerender(<LifecycleList selectedId={chosenLifecycle} onSelect={onSelect} />);
      expect(view.container.querySelector("[data-chosen]")).toBeNull();

      // A document that carries the lifecycle arrives later: its row is marked, the tree stays.
      act(() => seed(entryProjection(true)));
      await waitFor(() =>
        expect(rowOf(view.getByText("Leaf B1")).hasAttribute("data-chosen")).toBe(true),
      );
      expect(shown).not.toHaveBeenCalled();
    });
  });

  it("persists stable sprint and master keys across remounts", () => {
    seed(collapsibleHierarchyProjection());
    const first = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);

    fireEvent.click(first.getByRole("button", { name: "Collapse Master A tasks" }));
    fireEvent.click(first.getByRole("button", { name: "Collapse Sprint 02 tasks" }));
    expect(JSON.parse(window.localStorage.getItem("operations.tasks.collapsed.v1") ?? "[]"))
      .toEqual([
        "taskdoc:/tasks/master-a/task.json",
        "taskdoc:/tasks/sprint-02/task.json",
      ]);
    first.unmount();

    const second = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    expect(second.getByRole("button", { name: "Expand Sprint 02 tasks" }).getAttribute("aria-expanded"))
      .toBe("false");
    fireEvent.click(second.getByRole("button", { name: "Expand Sprint 02 tasks" }));
    expect(second.getByRole("button", { name: "Expand Master A tasks" }).getAttribute("aria-expanded"))
      .toBe("false");
    expect(second.queryByText("Leaf A1")).toBeNull();
    expect(second.getByText("Leaf B1")).toBeTruthy();
  });

  it("renders NO orchestration row or tier in a flat run (D3 regression)", () => {
    // No doc carries `orchestrates` ⇒ masters top-level, leaves one nested step, zero tier
    // attributes.
    const onSelect = vi.fn();
    seed(
      projection({
        lifecycles: [],
        enclosures: [
          enclosure({
            enclosure: "/contracts/15",
            lifecycleId: "",
            leafId: "15_parallel-leaf-enclosure-workflow",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              kind: "master",
              title: "Browser Dashboard Series",
              docPath: "/tasks/260610_browser-dashboard/task.json",
            }),
            taskDoc({
              id: "15",
              title: "Parallel Leaf Enclosure Workflow",
              docPath: "/tasks/260610_browser-dashboard/15_parallel-leaf-enclosure-workflow.json",
            }),
          ],
          series: [
            seriesNode({
              seriesId: "260610_browser-dashboard",
              subTasks: [
                {
                  number: "15",
                  name: "Parallel Leaf Enclosure Workflow",
                  file: "15_parallel-leaf-enclosure-workflow.md",
                  status: "inProgress",
                  scope: "",
                  createdAt: "2026-06-20T09:00:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const { container, getByText } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);
    expect(container.querySelector("[data-tier]")).toBeNull();
    const masterRow = getByText("Browser Dashboard Series").closest("[role='option']");
    expect(masterRow?.getAttribute("data-depth")).toBe("0");
    const leafRow = getByText("Parallel Leaf Enclosure Workflow").closest("[role='option']");
    expect(leafRow?.getAttribute("data-depth")).toBe("1");
    expect(indentOf(leafRow)).toBeGreaterThan(indentOf(masterRow));
  });

  it("exposes the full long task title and row context on the row's hover", () => {
    const longTitle =
      "Operations task reader row title that is intentionally long enough to require ellipsis in the left rail";
    seed(
      projection({
        lifecycles: [
          lifecycle({
            id: "01KVWK7Z8PQZ7BV9T6QPXFHM3B",
            state: "blocked",
            phase: "reframe-research",
            repoId: "agents-remember",
          }),
        ],
        enclosures: [
          enclosure({
            enclosure: "/contracts/long-title",
            lifecycleId: "01KVWK7Z8PQZ7BV9T6QPXFHM3B",
            leafId: "01",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              lifecycleId: "01KVWK7Z8PQZ7BV9T6QPXFHM3B",
              title: longTitle,
              currentStep: "Constrain Tasks panel row title layout",
              stepsDone: 1,
              stepsTotal: 4,
            }),
          ],
        },
      }),
    );

    const { getByText } = render(<LifecycleList selectedId={null} onSelect={() => {}} />);

    const title = getByText(longTitle);
    const row = title.closest("[role='option']");
    // Nothing leaves the column: the row and its line may shrink, the title gives way first and
    // is cut at its end, the word is cut at its own bound.
    expect(row?.className).toContain("min-w_0");
    expect(row?.firstElementChild?.className).toContain("min-w_0");
    expect(title.className).toContain("flex_1_1_0");
    expect(title.className).toContain("tov_ellipsis");
    const word = row?.querySelector("[data-testid='task-word']");
    expect(word?.textContent).toBe("blocked");
    expect(word?.className).toContain("tov_ellipsis");
    expect(word?.className).toContain("max-w_15ch");
    // The hover of the whole row says everything the line has no room for.
    const hover = rowHover(row).split("\n");
    expect(hover[0]).toBe(`doc. ${longTitle}`);
    expect(hover).toContain("State: blocked");
    expect(hover).toContain("Kind: task");
    expect(hover).toContain("Document status: inProgress");
    expect(hover).toContain("Steps done: 1 of 4");
    expect(hover).toContain("Current step: Constrain Tasks panel row title layout");
    expect(hover.some((line) => line.startsWith(
      "Lifecycle: 01KVWK7Z8PQZ7BV9T6QPXFHM3B · blocked · phase reframe-research",
    ))).toBe(true);
    expect(hover).toContain("Worktree: on disk");
    expect(hover).toContain("Repo: agents-remember");
  });
});
