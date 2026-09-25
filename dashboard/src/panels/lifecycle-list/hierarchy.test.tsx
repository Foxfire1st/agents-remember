import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LifecycleList } from "./LifecycleList";
import {
  EMPTY_ANALYTICS,
  collapsibleHierarchyProjection,
  enclosure,
  installLifecycleListCleanup,
  lifecycle,
  projection,
  seed,
  seriesNode,
  taskDoc,
} from "./test-utils";

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
    expect(row?.textContent).toContain("0/1 · 1 discarded");
    expect(row?.textContent).not.toContain("1/1");
  });

  it("renders the orchestration tier above its commanded masters with the V4 treatment (L14)", () => {
    // An orchestration task is a master doc carrying `orchestrates`.
    // It renders gold-tier at depth 0; a master it names nests one step with the purple tier; that
    // master's leaves keep today's rendering one step further; an uncommanded master is unchanged.
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

    // Gold tier: the orchestration row, top-level, chevron badge rendered.
    const sprintRow = getByText("SPRINT 02 · rollout").closest("[role='option']");
    expect(sprintRow?.getAttribute("data-tier")).toBe("orchestration");
    expect(sprintRow?.getAttribute("data-depth")).toBe("0");
    expect(sprintRow?.querySelector("[data-rank-tier='orchestration']")).not.toBeNull();

    // Purple tier: the commanded master nests under the orchestration row at 22px.
    const masterRow = getByText("Browser Dashboard Series").closest("[role='option']");
    expect(masterRow?.getAttribute("data-tier")).toBe("management");
    expect(masterRow?.getAttribute("data-depth")).toBe("1");
    expect(masterRow?.getAttribute("data-parent-key")).toBe("taskdoc:/tasks/sprint-02/task.json");
    expect((masterRow as HTMLElement).style.marginLeft).toBe("22px");
    expect(masterRow?.querySelector("[data-rank-tier='management']")).not.toBeNull();

    // Leaves keep today's rendering one step further (depth 2, one 22px margin step + nested look).
    const leafRow = getByText("15. Parallel Leaf Enclosure Workflow").closest("[role='option']");
    expect(leafRow?.getAttribute("data-depth")).toBe("2");
    expect(leafRow?.getAttribute("data-tier")).toBeNull();
    expect((leafRow as HTMLElement).style.marginLeft).toBe("22px");
    expect(leafRow?.querySelector("[data-rank-tier]")).toBeNull();

    // The uncommanded master is untouched: top-level, no tier, no badge, no margin.
    const freeRow = getByText("Free Standing Series").closest("[role='option']");
    expect(freeRow?.getAttribute("data-tier")).toBeNull();
    expect(freeRow?.getAttribute("data-depth")).toBe("0");
    expect((freeRow as HTMLElement).style.marginLeft).toBe("");
    expect(freeRow?.querySelector("[data-rank-tier]")).toBeNull();
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
    expect(getByText("01. Leaf A1").closest("[role='option']")?.querySelector("button")).toBeNull();
    expect(getByText("01. Leaf B1").closest("[role='option']")?.querySelector("button")).toBeNull();
    expect(getByText("Empty Master").closest("[role='option']")?.querySelector("button")).toBeNull();
  });

  it("keeps sprint and master collapse independent without changing selection or BY PHASE", () => {
    const onSelect = vi.fn();
    seed(collapsibleHierarchyProjection());
    const selectedLeaf = "taskdoc:/tasks/master-a/01_leaf-a1.json";
    const view = render(<LifecycleList selectedId={selectedLeaf} onSelect={onSelect} />);

    expect(view.getByText("01. Leaf A1").closest("[role='option']")?.getAttribute("aria-selected"))
      .toBe("true");
    const masterToggle = view.getByRole("button", { name: "Collapse Master A tasks" });
    expect(masterToggle.tagName).toBe("BUTTON");
    expect(masterToggle.tabIndex).toBe(0);
    fireEvent.keyDown(masterToggle, { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(masterToggle);
    expect(view.queryByText("01. Leaf A1")).toBeNull();
    expect(view.getByText("01. Leaf B1")).toBeTruthy();
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(view.getByRole("button", { name: "Collapse Sprint 02 tasks" }));
    expect(view.queryByText("Master A")).toBeNull();
    expect(view.queryByText("Master B")).toBeNull();
    expect(view.getByText("Tasks · 6")).toBeTruthy();
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(view.getByRole("button", { name: "Expand Sprint 02 tasks" }));
    expect(view.getByText("Master A")).toBeTruthy();
    expect(view.getByText("Master B")).toBeTruthy();
    expect(view.queryByText("01. Leaf A1")).toBeNull();
    expect(view.getByText("01. Leaf B1")).toBeTruthy();
    expect(view.getByRole("button", { name: "Expand Master A tasks" }).getAttribute("aria-expanded"))
      .toBe("false");

    fireEvent.click(view.getByText("BY PHASE"));
    expect(view.getByText("01. Leaf A1")).toBeTruthy();
    expect(view.getByText("01. Leaf B1")).toBeTruthy();
    expect(view.getByText("01. Leaf A1").closest("[role='option']")?.getAttribute("aria-selected"))
      .toBe("true");
    expect(view.queryByRole("button", { name: /^(Collapse|Expand) .* tasks$/ })).toBeNull();
    expect(view.getByText("Tasks · 6")).toBeTruthy();
    expect(onSelect).not.toHaveBeenCalled();
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
    expect(second.queryByText("01. Leaf A1")).toBeNull();
    expect(second.getByText("01. Leaf B1")).toBeTruthy();
  });

  it("renders NO orchestration row or insignia in a flat run (D3 regression)", () => {
    // No doc carries `orchestrates` ⇒ the list is byte-identical to the pre-tier rendering:
    // masters top-level, leaves one nested step, zero tier attributes, zero badges.
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
    expect(container.querySelector("[data-rank-tier]")).toBeNull();
    const masterRow = getByText("Browser Dashboard Series").closest("[role='option']");
    expect(masterRow?.getAttribute("data-depth")).toBe("0");
    expect((masterRow as HTMLElement).style.marginLeft).toBe("");
    const leafRow = getByText("15. Parallel Leaf Enclosure Workflow").closest("[role='option']");
    expect(leafRow?.getAttribute("data-depth")).toBe("1");
    expect((leafRow as HTMLElement).style.marginLeft).toBe("");
  });

  it("exposes the full long task title and row context on title hover", () => {
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
            gate: {
              id: "gate-1",
              kind: "plan-approval",
              state: "open",
              decisions: ["approve", "revise"],
              packet: {},
              evidenceRefs: [],
              ts: "2026-06-24T06:00:40+00:00",
            },
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
    expect(row?.className).toContain("min-w_0");
    expect(row?.className).toContain("max-w_100%");
    expect(row?.lastElementChild?.className).toContain("tov_ellipsis");
    expect(row?.lastElementChild?.className).not.toContain("ml_auto");
    expect(title.className).toContain("flex_1_1_0");
    expect(title.getAttribute("title")).toContain(`Title: ${longTitle}`);
    expect(title.getAttribute("title")).toContain("Lifecycle: 01KVWK7Z8PQZ7BV9T6QPXFHM3B");
    expect(title.getAttribute("title")).toContain("State: blocked");
    expect(title.getAttribute("title")).toContain("Phase: reframe-research");
    expect(title.getAttribute("title")).toContain("Repo: agents-remember");
    expect(title.getAttribute("title")).toContain("Gate: plan-approval");
    expect(title.getAttribute("title")).toContain(
      "Current step: Constrain Tasks panel row title layout",
    );
  });
});
