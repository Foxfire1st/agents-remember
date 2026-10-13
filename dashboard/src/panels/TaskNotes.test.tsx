import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
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

  it("asks for the resolution once per content of the references, not per render", async () => {
    const reference = "notes/friction-ledger.md (F-M — the finding this leaf closes)";
    stubNotesApi([entry("friction-ledger.md")], [[reference, "friction-ledger.md"]]);
    // What each resolution request named, in the order the page asked.
    const asked = (): string[][] =>
      vi
        .mocked(globalThis.fetch)
        .mock.calls.filter(([url]) => String(url).startsWith("/api/notes/resolve"))
        .map(([, init]) => (JSON.parse(String(init?.body)) as { references: string[] }).references);
    const page = (references: string[]) => (
      <TaskNotes repo={REPO} master={MASTER} references={references} onOpenNotes={vi.fn()} />
    );
    const view = render(page([reference]));
    await view.findByTestId("note-ref-1");
    expect(asked()).toEqual([[reference]]);

    // a new list of the same content: no new request, and the link stays
    view.rerender(page([reference]));
    view.rerender(page([reference]));
    await view.findByTestId("notes-root-count");
    expect(asked()).toEqual([[reference]]);
    expect(view.getByTestId("note-ref-1")).toBeTruthy();

    // another content: one more request, which names the new list
    view.rerender(page([reference, "another.md"]));
    await waitFor(() => expect(asked()).toEqual([[reference], [reference, "another.md"]]));

    // an empty text is a reference of its own and no empty list
    view.rerender(page([""]));
    await waitFor(() => expect(asked()).toHaveLength(3));
    expect(asked()[2]).toEqual([""]);
  });

  it("asks nothing again when an empty list becomes a list of one empty text", async () => {
    stubNotesApi([entry("friction-ledger.md")]);
    const asked = (): string[][] =>
      vi
        .mocked(globalThis.fetch)
        .mock.calls.filter(([url]) => String(url).startsWith("/api/notes/resolve"))
        .map(([, init]) => (JSON.parse(String(init?.body)) as { references: string[] }).references);
    const page = (references: string[]) => (
      <TaskNotes repo={REPO} master={MASTER} references={references} onOpenNotes={vi.fn()} />
    );
    const view = render(page([]));
    await view.findByTestId("notes-root-count");
    expect(asked()).toEqual([[]]);

    // the two lists join to the same key, as they always did: the page draws the
    // new reference and sends no second request
    view.rerender(page([""]));
    await view.findByTestId("plain-ref-1");
    expect(asked()).toEqual([[]]);
  });
});

describe("TaskNotes resolution liveness (L150-CODE-01, kept at the L127 sync)", () => {
  // The notes API stub with manual answer control: every fetch parks its answer
  // until the test settles it, so rerenders land while a request is in flight.
  // L127's landed hooks key the resolution on content; these cases pin that the
  // in-flight result still arrives across equal-content rerenders and replays.
  function stubDeferredNotes() {
    const parked: { url: string; answer: (value: Response) => void }[] = [];
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        calls.push(url);
        return new Promise<Response>((answer) => {
          parked.push({ url, answer });
        });
      }),
    );
    return { parked, calls };
  }

  function folderAnswer(total: number): Response {
    return {
      ok: true,
      status: 200,
      json: async () => folderPayload([entry("a.md")], total),
    } as unknown as Response;
  }

  function resolveAnswer(pairs: [string, string | null][]): Response {
    return { ok: true, status: 200, json: async () => resolvePayload(pairs) } as unknown as Response;
  }

  function resolveCalls(calls: string[]): string[] {
    return calls.filter((url) => url.startsWith("/api/notes/resolve"));
  }

  // Settles every parked answer whose URL starts with the prefix, in parked order;
  // other parked answers stay parked.
  async function settle(parked: { url: string; answer: (value: Response) => void }[], prefix: string, value: Response) {
    await act(async () => {
      for (const call of parked.splice(0)) {
        if (call.url.startsWith(prefix)) call.answer(value);
        else parked.push(call);
      }
    });
  }

  // Settles only the latest parked answer for the prefix; earlier ones stay parked.
  async function settleLatest(parked: { url: string; answer: (value: Response) => void }[], prefix: string, value: Response) {
    await act(async () => {
      for (let index = parked.length - 1; index >= 0; index -= 1) {
        if (parked[index].url.startsWith(prefix)) {
          parked.splice(index, 1)[0].answer(value);
          return;
        }
      }
    });
  }

  function renderNotes(references: string[]) {
    return render(<TaskNotes repo={REPO} master={MASTER} references={references} onOpenNotes={vi.fn()} />);
  }

  it("delivers the in-flight result across an equal-content identity rerender", async () => {
    const { parked, calls } = stubDeferredNotes();
    const view = renderNotes(["notes/a.md"]);
    expect(parked).toHaveLength(2);
    // A new array identity with the same content refires nothing: the pending
    // request keeps its only live owner.
    view.rerender(<TaskNotes repo={REPO} master={MASTER} references={["notes/a.md"]} onOpenNotes={vi.fn()} />);
    expect(resolveCalls(calls)).toHaveLength(1);
    await settle(parked, "/api/notes/folder", folderAnswer(7));
    await settle(parked, "/api/notes/resolve", resolveAnswer([["notes/a.md", "a.md"]]));
    expect((await view.findByTestId("notes-root-count")).textContent).toContain("7 entries in the top folder");
    expect(await view.findByTestId("note-ref-1")).toBeTruthy();
    expect(resolveCalls(calls)).toHaveLength(1);
  });

  it("still delivers through a StrictMode setup replay", async () => {
    const { parked, calls } = stubDeferredNotes();
    const view = render(
      <StrictMode>
        <TaskNotes repo={REPO} master={MASTER} references={["notes/a.md"]} onOpenNotes={vi.fn()} />
      </StrictMode>,
    );
    // The replay's cleanup cancels the first owner; the replayed setup re-issues.
    await act(async () => {});
    expect(resolveCalls(calls)).toHaveLength(2);
    await settle(parked, "/api/notes/folder", folderAnswer(7));
    await settle(parked, "/api/notes/resolve", resolveAnswer([["notes/a.md", "a.md"]]));
    expect((await view.findByTestId("notes-root-count")).textContent).toContain("7 entries in the top folder");
    expect(await view.findByTestId("note-ref-1")).toBeTruthy();
  });

  it("lets the latest content win and ignores the stale answer", async () => {
    const { parked, calls } = stubDeferredNotes();
    const view = renderNotes(["notes/a.md"]);
    expect(parked).toHaveLength(2);
    view.rerender(
      <TaskNotes repo={REPO} master={MASTER} references={["notes/a.md", "notes/b.md"]} onOpenNotes={vi.fn()} />,
    );
    expect(resolveCalls(calls)).toHaveLength(2);
    // The latest answer settles first and wins; the stale first answer settles
    // last with different content and must not overwrite it.
    await settleLatest(parked, "/api/notes/folder", folderAnswer(5));
    await settleLatest(parked, "/api/notes/resolve", resolveAnswer([["notes/a.md", "a.md"], ["notes/b.md", "b.md"]]));
    await settle(parked, "/api/notes/folder", folderAnswer(1));
    await settle(parked, "/api/notes/resolve", resolveAnswer([["notes/a.md", "a.md"]]));
    expect((await view.findByTestId("notes-root-count")).textContent).toContain("5 entries in the top folder");
    expect(await view.findByTestId("note-ref-2")).toBeTruthy();
  });

  it("fires no new request for settled content on rerender", async () => {
    const urls = stubNotesApi([entry("friction-ledger.md")], [["notes/friction-ledger.md", "friction-ledger.md"]]);
    const view = renderNotes(["notes/friction-ledger.md"]);
    await view.findByTestId("note-ref-1");
    expect(resolveCalls(urls)).toHaveLength(1);
    view.rerender(<TaskNotes repo={REPO} master={MASTER} references={["notes/friction-ledger.md"]} onOpenNotes={vi.fn()} />);
    // Flush effects and settled fetches deterministically instead of a wall-clock sleep.
    await act(async () => {});
    expect(resolveCalls(urls)).toHaveLength(1);
    expect((await view.findByTestId("notes-root-count")).textContent).toContain("in the top folder");
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
