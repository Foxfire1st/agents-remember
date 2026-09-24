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

// ── ICR-R33: a landed master's leaves, and the bound that keeps the list a list ──────────────────
// The delivered defect: a leaf row was admitted ONLY while its worktree existed on disk, so a master
// whose leaves had all closed out rendered as a bare row and its finished work was unreachable.
describe("LifecycleList landed leaves under their master (R33)", () => {
  // The one shape these cases exercise: a master doc, its series index, the leaf docs, and the
  // enclosures the projection stats. `worktreeExists: false` is a CLOSED leaf — closeout removed the
  // worktrees — which is exactly the state the old rule read as "not there".
  function landedMasterProjection(options: {
    masterTitle: string;
    masterPath: string;
    leaves: { id: string; name: string; status: string; live: boolean }[];
  }) {
    const dir = options.masterPath.replace(/\/task\.json$/, "");
    const slug = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    return projection({
      lifecycles: [],
      enclosures: options.leaves
        .filter((leaf) => leaf.live)
        .map((leaf) =>
          enclosure({
            enclosure: `/contracts/${leaf.id}`,
            lifecycleId: "",
            leafId: `${leaf.id}_${slug(leaf.name)}`,
            taskRoot: dir,
          }),
        ),
      analytics: {
        ...EMPTY_ANALYTICS,
        taskDocuments: [
          taskDoc({ kind: "master", title: options.masterTitle, docPath: options.masterPath }),
          ...options.leaves.map((leaf) =>
            taskDoc({
              id: leaf.id,
              title: leaf.name,
              status: leaf.status,
              lifecycleId: undefined,
              docPath: `${dir}/${leaf.id}_${slug(leaf.name)}.json`,
            }),
          ),
        ],
        series: [
          seriesNode({
            seriesId: dir.split("/").pop() ?? "master",
            title: options.masterTitle,
            docPath: options.masterPath,
            subTasks: options.leaves.map((leaf) => ({
              number: leaf.id,
              name: leaf.name,
              file: `${leaf.id}_${slug(leaf.name)}.md`,
              status: leaf.status,
              scope: "",
              createdAt: "2026-06-21T08:00:00+00:00",
            })),
          }),
        ],
      },
    });
  }

  it("holds a fully landed master closed by default and reaches its leaf when opened (R33.1/R33.4)", () => {
    const onSelect = vi.fn();
    seed(
      landedMasterProjection({
        masterTitle: "Landed Master",
        masterPath: "/tasks/master-a/task.json",
        leaves: [{ id: "01", name: "Landed Leaf", status: "Completed", live: false }],
      }),
    );

    const view = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    // The default: the master alone. A list that opened every landed leaf would be one row per task
    // document, so the landed leaf is NOT forced open — and the header counts what is carried.
    expect(view.getByText("Tasks · 1")).toBeTruthy();
    expect(view.queryByText("01. Landed Leaf")).toBeNull();
    const toggle = view.getByRole("button", { name: "Expand Landed Master tasks" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    // The reader's own open reaches it: the row appears UNDER the master, marked as landed.
    fireEvent.click(toggle);
    const leaf = view.getByText("01. Landed Leaf").closest("[role='option']");
    expect(leaf?.getAttribute("data-landed")).toBe("true");
    expect(leaf?.getAttribute("data-depth")).toBe("1");
    expect(leaf?.getAttribute("data-parent-key")).toBe("taskdoc:/tasks/master-a/task.json");
    expect(view.getByText("Tasks · 2")).toBeTruthy();

    // …and it selects the task document — the landed leaf's durable identity, which is what the
    // reader then opens its committed change-set from.
    fireEvent.click(view.getByText("01. Landed Leaf"));
    expect(onSelect).toHaveBeenCalledWith("taskdoc:/tasks/master-a/01_landed-leaf.json");
  });

  it("shows a master's landed leaves beside its live ones without a click (R33.1)", () => {
    // The master under review still has work in flight. It is open as it always was, so its landed
    // leaves belong beside the live ones rather than behind a disclosure the reader must find.
    const onSelect = vi.fn();
    seed(
      landedMasterProjection({
        masterTitle: "Mixed Master",
        masterPath: "/tasks/master-m/task.json",
        leaves: [
          { id: "01", name: "Landed Leaf", status: "Completed", live: false },
          { id: "02", name: "Live Leaf", status: "inProgress", live: true },
        ],
      }),
    );

    const view = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    expect(view.getByText("Tasks · 3")).toBeTruthy();
    const landed = view.getByText("01. Landed Leaf").closest("[role='option']");
    const live = view.getByText("02. Live Leaf").closest("[role='option']");
    expect(landed?.getAttribute("data-landed")).toBe("true");
    expect(landed?.getAttribute("data-depth")).toBe("1");
    expect(live?.getAttribute("data-landed")).toBeNull();
    // The master row tells the reader how much landed work sits behind it before they open anything.
    expect(view.getByText("Mixed Master").closest("[role='option']")?.textContent).toContain(
      "1 landed",
    );
  });

  it("never closes a row that carries OTHER rows, even when its own work has all landed (R33.4)", () => {
    // THE GUARD THE WHOLE MECHANISM RESTS ON. The default-collapse rule closes a master whose only
    // children are its own landed leaves — but a row that also carries other ROWS (the masters it
    // commands) must stay open, or closing it hides another master's work behind a disclosure.
    //
    // This is not hypothetical: an earlier revision of this rule closed the live projection's
    // 260713_improved-agentic-system row (orchestration tier, no live worktree of its own, 38 direct
    // children) and hid its default-render subtree of 161 rows — including the very master this leaf
    // was written for. Deleting the exclusion from `markAutoCollapsed` makes this case the only
    // delivered thing that notices.
    seed(
      projection({
        lifecycles: [],
        enclosures: [
          // The commander's own landed work: its worktrees are gone, as a landed leaf's are.
          ...[
            ["01", "01_commander-leaf-01"],
            ["02", "02_commander-leaf-02"],
          ].map(([id, slug]) =>
            enclosure({
              enclosure: `/contracts/cmd-${id}`,
              lifecycleId: "",
              leafId: slug,
              taskRoot: "/tasks/cmd",
              cleanup: "completed",
              codeWorktreeExists: false,
              memoryWorktreeExists: false,
            }),
          ),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              id: "CMDR",
              kind: "master",
              title: "Commander",
              docPath: "/tasks/cmd/task.json",
              orchestrates: ["commanded"],
            }),
            taskDoc({
              id: "CMD",
              kind: "master",
              title: "Commanded Master",
              docPath: "/tasks/commanded/task.json",
            }),
            taskDoc({
              id: "01",
              status: "Completed",
              lifecycleId: undefined,
              title: "Commander Leaf 01",
              docPath: "/tasks/cmd/01_commander-leaf-01.json",
            }),
            taskDoc({
              id: "02",
              status: "Completed",
              lifecycleId: undefined,
              title: "Commander Leaf 02",
              docPath: "/tasks/cmd/02_commander-leaf-02.json",
            }),
            taskDoc({
              id: "01",
              status: "Completed",
              lifecycleId: undefined,
              title: "Commanded Leaf 01",
              docPath: "/tasks/commanded/01_commanded-leaf-01.json",
            }),
          ],
          series: [
            seriesNode({
              seriesId: "cmd",
              title: "Commander",
              docPath: "/tasks/cmd/task.json",
              subTasks: ["01", "02"].map((id) => ({
                number: id,
                name: `Commander Leaf ${id}`,
                file: `${id}_commander-leaf-${id}.md`,
                status: "Completed",
                scope: "",
                createdAt: "2026-06-21T08:00:00+00:00",
              })),
            }),
            seriesNode({
              seriesId: "commanded",
              title: "Commanded Master",
              docPath: "/tasks/commanded/task.json",
              subTasks: [
                {
                  number: "01",
                  name: "Commanded Leaf 01",
                  file: "01_commanded-leaf-01.md",
                  status: "Completed",
                  scope: "",
                  createdAt: "2026-06-21T09:00:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const view = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);

    // The commander stays OPEN although every one of its own leaves has landed…
    const commander = view.getByText("Commander").closest("[role='option']");
    expect(commander?.getAttribute("data-auto-collapsed")).toBeNull();
    expect(
      view.getByRole("button", { name: "Collapse Commander tasks" }).getAttribute("aria-expanded"),
    ).toBe("true");
    // …because the master it commands is reachable without a click, nested under it.
    const commanded = view.getByText("Commanded Master").closest("[role='option']");
    expect(commanded?.getAttribute("data-depth")).toBe("1");
    expect(commanded?.getAttribute("data-parent-key")).toBe("taskdoc:/tasks/cmd/task.json");
    // The commander's OWN landed leaves are shown beside it, for the same reason.
    expect(
      view.getByText("01. Commander Leaf 01").closest("[role='option']")?.getAttribute("data-landed"),
    ).toBe("true");
    // The rule stays targeted: the COMMANDED master, whose only children are its own landed leaves,
    // is still closed by default — its leaf is not forced open, and it remains reachable.
    expect(commanded?.getAttribute("data-auto-collapsed")).toBe("true");
    expect(view.queryByText("01. Commanded Leaf 01")).toBeNull();
    expect(view.getByText("Tasks · 4")).toBeTruthy();
  });

  it("counts the task entries it carries, not the projection's documents (R33.4)", () => {
    // THREE masters and NINE documents. The closed master's four landed leaves are carried by the
    // list but not opened by it, so the header stays a number a reader can interpret — the entries
    // the list carries — and the header says so in its own tooltip.
    seed(
      projection({
        lifecycles: [],
        enclosures: [
          // The live master's leaf: its worktree exists, so it renders exactly as it always did.
          enclosure({
            enclosure: "/contracts/live-01",
            lifecycleId: "",
            leafId: "01_live-leaf",
            taskRoot: "/tasks/live",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({ kind: "master", title: "Closed Master", docPath: "/tasks/closed/task.json" }),
            ...["01", "02", "03", "04"].map((id) =>
              taskDoc({
                id,
                status: "Completed",
                title: `Closed Leaf ${id}`,
                lifecycleId: undefined,
                docPath: `/tasks/closed/${id}_closed-leaf.json`,
              }),
            ),
            taskDoc({ kind: "master", title: "Live Master", docPath: "/tasks/live/task.json" }),
            taskDoc({
              id: "01",
              status: "inProgress",
              title: "Live Leaf",
              lifecycleId: undefined,
              docPath: "/tasks/live/01_live-leaf.json",
            }),
            taskDoc({ kind: "master", title: "Empty Master", docPath: "/tasks/empty/task.json" }),
          ],
          series: [
            seriesNode({
              seriesId: "closed",
              title: "Closed Master",
              docPath: "/tasks/closed/task.json",
              subTasks: ["01", "02", "03", "04"].map((id) => ({
                number: id,
                name: `Closed Leaf ${id}`,
                file: `${id}_closed-leaf.md`,
                status: "Completed",
                scope: "",
                createdAt: "2026-06-21T08:00:00+00:00",
              })),
            }),
            seriesNode({
              seriesId: "live",
              title: "Live Master",
              docPath: "/tasks/live/task.json",
              subTasks: [
                {
                  number: "01",
                  name: "Live Leaf",
                  file: "01_live-leaf.md",
                  status: "inProgress",
                  scope: "",
                  createdAt: "2026-06-21T09:00:00+00:00",
                },
              ],
            }),
          ],
        },
      }),
    );

    const view = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);

    // 3 masters + 1 live leaf. The closed master's four landed leaves are carried, not opened.
    expect(view.getByText("Tasks · 4")).toBeTruthy();
    expect(view.getByText("01. Live Leaf")).toBeTruthy();
    expect(view.queryByText("01. Closed Leaf 01")).toBeNull();
    // The header says what it counts — and says it TRUE for this very render, where a parent is open
    // without the reader having clicked anything (the live master's landed leaves are not in play
    // here, but the sentence has to hold for the fresh render the reader is looking at).
    const header = view.getByText("Tasks · 4");
    expect(header.getAttribute("title")).toContain("task entries this list carries");
    expect(header.getAttribute("title")).toContain(
      "including the parents open by default because they still hold live work",
    );
  });

  it("keeps a landed leaf that no series index resolves out of the list (R33.4)", () => {
    // The boundary the nesting rule must not lose: a landed leaf whose master index lists no ref for
    // it has no parent row to be nested under. It is NOT floated to the top of the list — an
    // unreachable row is not made reachable by inventing a place for it.
    seed(
      projection({
        lifecycles: [],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({ kind: "master", title: "Index-less Master", docPath: "/tasks/m/task.json" }),
            taskDoc({
              id: "07",
              status: "Completed",
              title: "Unindexed Landed Leaf",
              lifecycleId: undefined,
              docPath: "/tasks/m/07_unindexed-leaf.json",
            }),
          ],
          series: [seriesNode({ seriesId: "m", title: "Index-less Master", docPath: "/tasks/m/task.json" })],
        },
      }),
    );

    const view = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    expect(view.getByText("Tasks · 1")).toBeTruthy();
    expect(view.queryByText("Unindexed Landed Leaf")).toBeNull();
  });
});

