import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CockpitShell } from "../../cockpit/Cockpit";
import { dashboardStore } from "../../data/store";
import type { NoteContent, NoteEntry } from "../../data/notes";
import type { TaskDocNode, WorkspaceProjection } from "../../types/projection";
import { NotesReaderViewer } from "./NotesReaderViewer";

// Mock the CodeMirror text leaf so a text note renders in jsdom without a live EditorView — the house
// pattern (ChangeSetViewer.test mocks ChangeSetPane; the File Viewer tests never open a file). This is
// the ONLY file-viewer leaf we stub; DualPane's own branching + the shared Markdown render for real.
vi.mock("../file-viewer/FilePane", () => ({
  FilePane: ({ content }: { content: string }) => <pre data-testid="file-pane">{content}</pre>,
}));

const REPO = "agents-remember";
const MASTER = "260703_agent-orchestration";

function entry(path: string, over: Partial<NoteEntry> = {}): NoteEntry {
  return { name: path.split("/").pop() ?? path, path, size: 100, language: "markdown", ...over };
}
function content(path: string, over: Partial<NoteContent> = {}): NoteContent {
  return { repo: REPO, master: MASTER, path, language: "markdown", size: 100, truncated: false, content: `# ${path}`, ...over };
}

// /api/notes/list → the seeded listing; /api/notes/read → the seeded per-path content (404 otherwise).
function stubNotesApi(notes: NoteEntry[], contents: Record<string, NoteContent> = {}, truncated = false) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const params = new URLSearchParams(url.split("?")[1] ?? "");
      if (url.startsWith("/api/notes/list")) {
        const payload = { repo: params.get("repo"), master: params.get("master"), notes, truncated };
        return { ok: true, status: 200, json: async () => payload } as unknown as Response;
      }
      if (url.startsWith("/api/notes/read")) {
        const body = contents[params.get("path") ?? ""];
        if (body) return { ok: true, status: 200, json: async () => body } as unknown as Response;
        return { ok: false, status: 404, json: async () => ({ status: "not-found" }) } as unknown as Response;
      }
      // Unrelated cockpit fetches (e.g. the hidden File Viewer layer's repo catalog) — a `{repos:[]}`
      // shape keeps that panel happy without a network hit.
      return { ok: true, status: 200, json: async () => ({ repos: [] }) } as unknown as Response;
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("NotesReaderViewer rail", () => {
  it("lists the master's notes tree (reports/ included) with the open note highlighted", async () => {
    stubNotesApi(
      [entry("friction-ledger.md"), entry("reports/260703-L1-worker-report.md")],
      { "friction-ledger.md": content("friction-ledger.md") },
    );
    const view = render(
      <NotesReaderViewer repo={REPO} master={MASTER} path="friction-ledger.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    const first = await view.findByTestId("note-rail-1");
    expect(first.textContent).toContain("friction-ledger.md");
    // reports/ subfolder entries are listed, honestly showing their path.
    expect(view.getByText("reports/260703-L1-worker-report.md")).toBeTruthy();
    // The open note is highlighted in the rail; the other row is not.
    expect(first.getAttribute("data-active")).toBe("true");
    expect(view.getByTestId("note-rail-2").getAttribute("data-active")).toBe("false");
  });

  it("moves the highlight to follow the open note when the selection changes", async () => {
    stubNotesApi(
      [entry("a.md"), entry("b.md")],
      { "a.md": content("a.md"), "b.md": content("b.md") },
    );
    const view = render(
      <NotesReaderViewer repo={REPO} master={MASTER} path="a.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    await view.findByTestId("note-rail-1");
    expect(view.getByTestId("note-rail-1").getAttribute("data-active")).toBe("true");
    view.rerender(
      <NotesReaderViewer repo={REPO} master={MASTER} path="b.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    expect(view.getByTestId("note-rail-1").getAttribute("data-active")).toBe("false");
    expect(view.getByTestId("note-rail-2").getAttribute("data-active")).toBe("true");
    await view.findByTestId("sidecar-pane"); // flush the b.md content fetch
  });

  it("asks the parent to switch the open note when a rail row is clicked (switch in place)", async () => {
    stubNotesApi([entry("a.md"), entry("b.md")], { "a.md": content("a.md") });
    const onSelectNote = vi.fn();
    const view = render(
      <NotesReaderViewer repo={REPO} master={MASTER} path="a.md" onSelectNote={onSelectNote} onBack={vi.fn()} />,
    );
    fireEvent.click(await view.findByTestId("note-rail-2"));
    expect(onSelectNote).toHaveBeenCalledWith("b.md");
  });
});

describe("NotesReaderViewer content pane (reuses the File Viewer DualPane)", () => {
  it("renders an opened markdown note formatted (not raw) via the shared Markdown", async () => {
    stubNotesApi([entry("friction-ledger.md")], {
      "friction-ledger.md": content("friction-ledger.md", { content: "The **F-M** row." }),
    });
    const view = render(
      <NotesReaderViewer repo={REPO} master={MASTER} path="friction-ledger.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    await view.findByTestId("sidecar-pane");
    expect((await view.findByText("F-M")).tagName.toLowerCase()).toBe("strong"); // formatted, not raw
    expect(view.queryByText("The **F-M** row.")).toBeNull();
  });

  it("renders a non-markdown text note through the shared file pane (the text fallback)", async () => {
    stubNotesApi([entry("trace.txt", { language: "text" })], {
      "trace.txt": content("trace.txt", { language: "text", content: "line one\nline two" }),
    });
    const view = render(
      <NotesReaderViewer repo={REPO} master={MASTER} path="trace.txt" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    expect((await view.findByTestId("file-pane")).textContent).toBe("line one\nline two");
  });

  it("degrades a binary note to a byte-count placeholder instead of raw bytes", async () => {
    stubNotesApi([entry("scan.png", { language: "text" })], {
      "scan.png": content("scan.png", { language: "binary", size: 2048, content: "" }),
    });
    const view = render(
      <NotesReaderViewer repo={REPO} master={MASTER} path="scan.png" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    expect((await view.findByTestId("pane-placeholder")).textContent).toContain(
      "Binary file — 2,048 bytes (not shown)",
    );
  });

  it("shows the 2-MiB truncation banner for a truncated markdown note (L18 finding 2)", async () => {
    // Markdown is the dominant note type but takes DualPane's partnerless-markdown path, which has
    // no banner (only CodeSide does) -- so without this a truncated markdown note would silently drop
    // the "showing the first 2 MiB" contract. The banner renders here, above the rendered markdown.
    stubNotesApi([entry("big.md")], {
      "big.md": content("big.md", { truncated: true, size: 2_097_162, content: "# big\n\nlots" }),
    });
    const view = render(
      <NotesReaderViewer repo={REPO} master={MASTER} path="big.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    const banner = await view.findByTestId("notes-trunc-banner");
    expect(banner.textContent).toContain("Showing the first 2 MiB of 2,097,162 bytes");
    // The markdown still renders (the banner is ABOVE the pane, not instead of it).
    await view.findByTestId("sidecar-pane");
  });

  it("shows no truncation banner for a normal (untruncated) markdown note", async () => {
    stubNotesApi([entry("small.md")], { "small.md": content("small.md") });
    const view = render(
      <NotesReaderViewer repo={REPO} master={MASTER} path="small.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    await view.findByTestId("sidecar-pane");
    expect(view.queryByTestId("notes-trunc-banner")).toBeNull();
  });

  it("calls onBack when the back control is clicked", async () => {
    stubNotesApi([entry("a.md")], { "a.md": content("a.md") });
    const onBack = vi.fn();
    const view = render(
      <NotesReaderViewer repo={REPO} master={MASTER} path="a.md" onSelectNote={vi.fn()} onBack={onBack} />,
    );
    fireEvent.click(await view.findByTestId("notes-reader-back"));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});

// A minimal projection carrying one selectable master task document so the cockpit sidebar has a row.
function masterDoc(): TaskDocNode {
  return {
    id: "task",
    lifecycleId: undefined,
    repository: REPO,
    title: "Agent Orchestration",
    status: "inProgress",
    kind: "master",
    docPath: `/tasks/${REPO}/${MASTER}/task.json`,
    createdAt: "2026-07-07T09:00:00+00:00",
    stepsDone: 0,
    stepsTotal: 0,
    steps: [],
    objective: "Master objective.",
    requirements: [],
    codeExamples: [],
    decisions: [],
    openQuestions: [],
    references: [],
    subTasks: [],
    sections: [],
  } as unknown as TaskDocNode;
}
function seedMaster() {
  const projection = {
    version: 2,
    generatedAt: "2026-07-07T09:01:00+00:00",
    lifecycles: [],
    enclosures: [],
    providers: [],
    activeWorktreeGroups: [],
    metrics: { lifecycleCount: 0, runningCount: 0, blockedCount: 0, pausedCount: 0, totalTokens: 0, stalenessHistogram: {} },
    analytics: {
      driftSnapshots: [], stalestSidecars: [], setupSummaries: [], setupProgress: [], routeCoverage: [],
      toolReports: [], ledgers: [], taskDocuments: [masterDoc()], series: [], attentionQueue: [], engineProcesses: [],
    },
  } as unknown as WorkspaceProjection;
  dashboardStore.getState().applySnapshot(projection);
}

describe("Cockpit notes reader takeover", () => {
  it("does not show the notes reader initially and keeps the Operations rails", () => {
    stubNotesApi([entry("a.md")]);
    seedMaster();
    const { container } = render(<CockpitShell />);
    expect(container.querySelector('[data-testid="notes-reader-viewer"]')).toBeNull();
    expect(container.querySelector(".rail--left")).not.toBeNull();
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("false");
  });

  it("opens the reader from the notes list and keeps it MOUNTED across back (state survives back/forward, like the File Viewer)", async () => {
    stubNotesApi([entry("a.md"), entry("b.md")], { "a.md": content("a.md"), "b.md": content("b.md") });
    seedMaster();
    const view = render(<CockpitShell />);

    // Select the master in the sidebar, then open a note from its compact Series-notes list.
    fireEvent.click(view.getByText("Agent Orchestration"));
    fireEvent.click(await view.findByTestId("note-open-1"));

    // The takeover shows the notes reader; the railed body is hidden (not unmounted).
    const reader = await view.findByTestId("notes-reader-viewer");
    const railedBody = reader.closest(".shell__body")!.parentElement!.querySelector('[aria-hidden="true"].shell__body');
    expect(railedBody).not.toBeNull();

    // Back hides the takeover but does NOT unmount it — the SAME node persists (its listing/selection
    // survive), exactly like the File Viewer's hidden-not-unmounted layer.
    fireEvent.click(view.getByTestId("notes-reader-back"));
    expect(view.getByTestId("notes-reader-viewer")).toBe(reader);

    // Re-opening surfaces the SAME mounted reader instance (never a fresh remount).
    fireEvent.click(await view.findByTestId("note-open-1"));
    expect(view.getByTestId("notes-reader-viewer")).toBe(reader);
  });
});
