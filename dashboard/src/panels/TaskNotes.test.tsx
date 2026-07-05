import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { NoteContent, NoteEntry } from "../data/notes";
import { TaskNotes } from "./TaskNotes";

const REPO = "agents-remember";
const MASTER = "260703_agent-orchestration";

function entry(path: string, over: Partial<NoteEntry> = {}): NoteEntry {
  return { name: path.split("/").pop() ?? path, path, size: 100, language: "markdown", ...over };
}

function content(path: string, over: Partial<NoteContent> = {}): NoteContent {
  return {
    repo: REPO,
    master: MASTER,
    path,
    language: "markdown",
    size: 100,
    truncated: false,
    content: `# ${path}`,
    ...over,
  };
}

// The notes API stub: /api/notes/list answers the seeded listing; /api/notes/read answers the
// seeded per-path content (404 not-found otherwise) — the serving status idiom, not a mock shape.
function stubNotesApi(
  notes: NoteEntry[],
  contents: Record<string, NoteContent> = {},
  truncated = false,
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const params = new URLSearchParams(url.split("?")[1] ?? "");
      if (url.startsWith("/api/notes/list")) {
        const payload = { repo: params.get("repo"), master: params.get("master"), notes, truncated };
        return { ok: true, status: 200, json: async () => payload } as unknown as Response;
      }
      const body = contents[params.get("path") ?? ""];
      if (body) return { ok: true, status: 200, json: async () => body } as unknown as Response;
      return {
        ok: false,
        status: 404,
        json: async () => ({ status: "not-found" }),
      } as unknown as Response;
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("TaskNotes list + reader", () => {
  it("lists the series notes including reports/ subfolder entries", async () => {
    stubNotesApi([entry("friction-ledger.md"), entry("reports/260703-L1-worker-report.md")]);
    const { findByTestId, getByText } = render(
      <TaskNotes repo={REPO} master={MASTER} references={[]} />,
    );
    expect((await findByTestId("note-open-1")).textContent).toContain("friction-ledger.md");
    expect(getByText("Series notes")).toBeTruthy();
    expect(getByText("reports/260703-L1-worker-report.md")).toBeTruthy();
  });

  it("opens a note as formatted markdown (not raw) and closes it again", async () => {
    stubNotesApi(
      [entry("friction-ledger.md")],
      { "friction-ledger.md": content("friction-ledger.md", { content: "The **F-M** row." }) },
    );
    const view = render(<TaskNotes repo={REPO} master={MASTER} references={[]} />);
    fireEvent.click(await view.findByTestId("note-open-1"));
    await view.findByTestId("note-view");
    expect((await view.findByText("F-M")).tagName.toLowerCase()).toBe("strong"); // formatted, not raw
    expect(view.queryByText("The **F-M** row.")).toBeNull();

    fireEvent.click(view.getByTestId("note-close"));
    expect(view.queryByTestId("note-view")).toBeNull();
  });

  it("degrades a binary note to a placeholder instead of raw bytes", async () => {
    stubNotesApi(
      [entry("scan.png", { language: "text" })],
      { "scan.png": content("scan.png", { language: "binary", size: 2048, content: "" }) },
    );
    const view = render(<TaskNotes repo={REPO} master={MASTER} references={[]} />);
    fireEvent.click(await view.findByTestId("note-open-1"));
    expect((await view.findByTestId("note-view")).textContent).toContain(
      "Binary file — 2,048 bytes (not shown)",
    );
  });

  it("renders a non-markdown text note preformatted", async () => {
    stubNotesApi(
      [entry("trace.txt", { language: "text" })],
      { "trace.txt": content("trace.txt", { language: "text", content: "line one\nline two" }) },
    );
    const view = render(<TaskNotes repo={REPO} master={MASTER} references={[]} />);
    fireEvent.click(await view.findByTestId("note-open-1"));
    const box = await view.findByTestId("note-view");
    expect(box.querySelector("pre")?.textContent).toBe("line one\nline two");
  });

  it("says when the server's depth cap pruned the listing", async () => {
    stubNotesApi([entry("a.md")], {}, true);
    const view = render(<TaskNotes repo={REPO} master={MASTER} references={[]} />);
    await view.findByTestId("note-open-1");
    expect(view.getByText(/beyond the list cap/)).toBeTruthy();
  });

  it("renders no notes surface when the API is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("unreachable"))),
    );
    const view = render(
      <TaskNotes repo={REPO} master={MASTER} references={["notes/friction-ledger.md"]} />,
    );
    // The reference stays plain text; nothing to list, nothing to open.
    expect(await view.findByText("notes/friction-ledger.md")).toBeTruthy();
    expect(view.queryByTestId("note-ref-1")).toBeNull();
    expect(view.queryByText("Series notes")).toBeNull();
  });
});

describe("TaskNotes reference resolution", () => {
  it("turns a reference naming an existing notes file into an openable link", async () => {
    stubNotesApi(
      [entry("friction-ledger.md")],
      { "friction-ledger.md": content("friction-ledger.md", { content: "ledger body" }) },
    );
    const view = render(
      <TaskNotes
        repo={REPO}
        master={MASTER}
        references={["notes/friction-ledger.md (F-M — the finding this leaf closes)"]}
      />,
    );
    const link = await view.findByTestId("note-ref-1");
    expect(link.tagName.toLowerCase()).toBe("button");
    fireEvent.click(link);
    expect((await view.findByTestId("note-view")).textContent).toContain("ledger body");
  });

  it("keeps a non-matching reference as plain text", async () => {
    stubNotesApi([entry("friction-ledger.md")]);
    const view = render(
      <TaskNotes
        repo={REPO}
        master={MASTER}
        references={["mcp/src/agents_remember/serving/files.py (the confinement idiom to reuse)"]}
      />,
    );
    await view.findByTestId("note-open-1"); // the listing has arrived — resolution is settled
    expect(view.queryByTestId("note-ref-1")).toBeNull();
    expect(view.getByText(/the confinement idiom to reuse/)).toBeTruthy();
  });
});
