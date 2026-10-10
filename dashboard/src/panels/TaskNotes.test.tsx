import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FolderEntry } from "../data/notes";
import type { TaskReports } from "../data/taskReports";
import { TaskNotes } from "./TaskNotes";

// L126: TaskNotes is the compact ENTRY SURFACE — the document's references (resolved by one
// request), the task's recorded role reports, and one Notes line with a link. No list is drawn.
const REPO = "agents-remember";
const MASTER = "260703_agent-orchestration";
const DOC = `${MASTER}/09_notes-dashboard.json`;

function entry(path: string, over: Partial<FolderEntry> = {}): FolderEntry {
  return {
    name: path.split("/").pop() ?? path,
    path,
    kind: "file",
    size: 100,
    language: "markdown",
    ...over,
  };
}

function folderPayload(entries: FolderEntry[], total: number) {
  return {
    repo: REPO,
    master: MASTER,
    path: "",
    entries,
    total,
    offset: 0,
    limit: 200,
    remaining: 0,
    exists: true,
    reason: null,
  };
}

function resolvePayload(pairs: [string, string | null][]) {
  return {
    repo: REPO,
    master: MASTER,
    resolutions: pairs.map(([reference, path]) => ({ reference, path })),
  };
}

// The notes API stub: /api/notes/folder answers the seeded top folder, /api/notes/resolve
// answers the seeded resolutions, /api/role-launch/reports answers the seeded report list.
function stubNotesApi(
  entries: FolderEntry[] = [],
  resolutions: [string, string | null][] = [],
  reports: TaskReports | null = null,
) {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      urls.push(url);
      if (url.startsWith("/api/notes/folder")) {
        return { ok: true, status: 200, json: async () => folderPayload(entries, entries.length) } as unknown as Response;
      }
      if (url.startsWith("/api/notes/resolve")) {
        return { ok: true, status: 200, json: async () => resolvePayload(resolutions) } as unknown as Response;
      }
      if (url.startsWith("/api/role-launch/reports")) {
        const payload = reports ?? { repo: REPO, master: MASTER, doc: DOC, reports: [], total: 0, explorer: null };
        return { ok: true, status: 200, json: async () => payload } as unknown as Response;
      }
      return { ok: false, status: 404, json: async () => ({ status: "not-found" }) } as unknown as Response;
    }),
  );
  return urls;
}

function reportRow(requestId: string, over: object = {}) {
  return {
    role: "worker",
    requestId,
    createdAt: "2026-10-01T00:00:00+00:00",
    status: "completed",
    path: `task-reports/${requestId}.md`,
    canonicalPath: `/coord/${requestId}.md`,
    available: true,
    ...over,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("TaskNotes entry surface", () => {
  it("shows one Notes line with the top-folder count and a link, and draws no list", async () => {
    const urls = stubNotesApi([entry("friction-ledger.md"), entry("reports/x.md")]);
    const onOpenNotes = vi.fn();
    const view = render(<TaskNotes repo={REPO} master={MASTER} references={[]} onOpenNotes={onOpenNotes} />);
    expect((await view.findByTestId("notes-root-count")).textContent).toContain("2 entries in the top folder");
    fireEvent.click(view.getByTestId("notes-root-link"));
    expect(onOpenNotes).toHaveBeenCalledWith({ kind: "notes", repo: REPO, master: MASTER, path: "", folder: true });
    // No list rows are drawn, and only the two page requests fire (folder + resolve).
    expect(view.queryByText("friction-ledger.md")).toBeNull();
    expect(urls.filter((url) => url.startsWith("/api/notes/"))).toHaveLength(2);
  });

  it("shows no Notes line when the master has no notes folder", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/notes/folder")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              repo: REPO, master: MASTER, path: "", entries: [], total: 0, offset: 0,
              limit: 200, remaining: 0, exists: false, reason: "this master has no notes folder",
            }),
          } as unknown as Response;
        }
        if (url.startsWith("/api/notes/resolve")) {
          return { ok: true, status: 200, json: async () => resolvePayload([]) } as unknown as Response;
        }
        return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
      }),
    );
    const view = render(<TaskNotes repo={REPO} master={MASTER} references={["notes/a.md"]} onOpenNotes={vi.fn()} />);
    // The resolve answer arrives (the reference renders, unresolved) with no Notes line.
    expect(await view.findByText("notes/a.md")).toBeTruthy();
    expect(view.queryByTestId("notes-root-link")).toBeNull();
    expect(view.queryByTestId("notes-root-count")).toBeNull();
  });

  it("renders no notes surface when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("unreachable"))));
    const view = render(
      <TaskNotes repo={REPO} master={MASTER} references={["notes/friction-ledger.md"]} onOpenNotes={vi.fn()} />,
    );
    // The reference stays plain text; no Notes line.
    expect(await view.findByText("notes/friction-ledger.md")).toBeTruthy();
    expect(view.queryByTestId("note-ref-1")).toBeNull();
    expect(view.queryByTestId("notes-root-link")).toBeNull();
  });
});

describe("TaskNotes reference resolution", () => {
  it("turns a reference naming an existing notes file into a link that opens the reader on it", async () => {
    stubNotesApi([entry("friction-ledger.md")], [
      ["notes/friction-ledger.md (F-M — the finding this leaf closes)", "friction-ledger.md"],
    ]);
    const onOpenNotes = vi.fn();
    const view = render(
      <TaskNotes
        repo={REPO}
        master={MASTER}
        references={["notes/friction-ledger.md (F-M — the finding this leaf closes)"]}
        onOpenNotes={onOpenNotes}
      />,
    );
    const link = await view.findByTestId("note-ref-1");
    expect(link.tagName.toLowerCase()).toBe("button");
    fireEvent.click(link);
    expect(onOpenNotes).toHaveBeenCalledWith({
      kind: "notes",
      repo: REPO,
      master: MASTER,
      path: "friction-ledger.md",
    });
  });

  it("keeps a non-matching reference as plain text", async () => {
    stubNotesApi([entry("friction-ledger.md")], [
      ["mcp/src/agents_remember/serving/files.py (the confinement idiom to reuse)", null],
    ]);
    const view = render(
      <TaskNotes
        repo={REPO}
        master={MASTER}
        references={["mcp/src/agents_remember/serving/files.py (the confinement idiom to reuse)"]}
        onOpenNotes={vi.fn()}
      />,
    );
    await view.findByTestId("notes-root-count"); // the folder answer arrived — resolution is settled
    expect(view.queryByTestId("note-ref-1")).toBeNull();
    expect(view.getByText(/the confinement idiom to reuse/)).toBeTruthy();
  });
});

describe("TaskNotes recorded reports", () => {
  it("lists the task's recorded reports newest first and opens one in the reader", async () => {
    stubNotesApi([entry("a.md")], [], {
      repo: REPO,
      master: MASTER,
      doc: DOC,
      reports: [reportRow("bbb"), reportRow("aaa")],
      total: 2,
      explorer: null,
    });
    const onOpenNotes = vi.fn();
    const view = render(
      <TaskNotes repo={REPO} master={MASTER} docPath={DOC} references={[]} onOpenNotes={onOpenNotes} />,
    );
    const first = await view.findByTestId("task-report-bbb");
    expect(first.textContent).toContain("worker");
    fireEvent.click(first);
    expect(onOpenNotes).toHaveBeenCalledWith({
      kind: "task-report",
      repo: REPO,
      master: MASTER,
      role: "worker",
      requestId: "bbb",
      taskRepository: REPO,
      taskPath: DOC,
    });
    expect(view.queryByTestId("task-reports-explorer-link")).toBeNull();
  });

  it("shows the explorer link only when more reports exist than the rows show", async () => {
    stubNotesApi([entry("a.md")], [], {
      repo: REPO,
      master: MASTER,
      doc: DOC,
      reports: [reportRow("bbb")],
      total: 21,
      explorer: "reports/role-launch",
    });
    const onOpenNotes = vi.fn();
    const view = render(
      <TaskNotes repo={REPO} master={MASTER} docPath={DOC} references={[]} onOpenNotes={onOpenNotes} />,
    );
    const link = await view.findByTestId("task-reports-explorer-link");
    expect(link.textContent).toContain("21");
    fireEvent.click(link);
    expect(onOpenNotes).toHaveBeenCalledWith({
      kind: "notes",
      repo: REPO,
      master: MASTER,
      path: "reports/role-launch",
      folder: true,
    });
  });

  it("says the reports could not be listed and keeps the Notes link", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/notes/folder")) {
          return { ok: true, status: 200, json: async () => folderPayload([entry("a.md")], 1) } as unknown as Response;
        }
        if (url.startsWith("/api/notes/resolve")) {
          return { ok: true, status: 200, json: async () => resolvePayload([]) } as unknown as Response;
        }
        return { ok: false, status: 409, json: async () => ({ status: "report-records-unreadable" }) } as unknown as Response;
      }),
    );
    const view = render(
      <TaskNotes repo={REPO} master={MASTER} docPath={DOC} references={[]} onOpenNotes={vi.fn()} />,
    );
    expect(await view.findByTestId("task-reports-failed")).toBeTruthy();
    expect(view.getByTestId("notes-root-link")).toBeTruthy();
  });

  it("shows no report list on a master page", async () => {
    const urls = stubNotesApi([entry("a.md")]);
    const view = render(<TaskNotes repo={REPO} master={MASTER} references={[]} onOpenNotes={vi.fn()} />);
    await view.findByTestId("notes-root-count");
    expect(view.queryByTestId("task-reports-empty")).toBeNull();
    expect(urls.some((url) => url.startsWith("/api/role-launch/reports"))).toBe(false);
  });
});
