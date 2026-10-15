import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CockpitShell } from '../../cockpit/Cockpit';
import { dashboardStore } from '../../data/store';
import type { FolderEntry, NoteContent } from '../../data/notes';
import { metricsFor } from '../../types/projection';
import type { TaskDocNode, WorkspaceProjection } from '../../types/projection';
import { NotesReaderViewer } from './NotesReaderViewer';
import { block, decisionReading, productRecords } from '../detail-panel/pageStyles';

// Mock the CodeMirror text leaf so a text note renders in jsdom without a live EditorView — the house
// pattern (ChangeSetViewer.test mocks ChangeSetPane; the File Viewer tests never open a file). This is
// the ONLY file-viewer leaf we stub; DualPane's own branching + the shared Markdown render for real.
vi.mock('../file-viewer/FilePane', () => ({
  FilePane: ({ content }: { content: string }) => <pre data-testid="file-pane">{content}</pre>,
}));

const REPO = 'agents-remember';
const MASTER = '260703_agent-orchestration';

function entry(path: string, over: Partial<FolderEntry> = {}): FolderEntry {
  const isDir = path.endsWith('/');
  const clean = isDir ? path.slice(0, -1) : path;
  return {
    name: clean.split('/').pop() ?? clean,
    path: clean,
    kind: isDir ? 'dir' : 'file',
    size: isDir ? null : 100,
    language: isDir ? '' : 'markdown',
    ...over,
  };
}
function content(path: string, over: Partial<NoteContent> = {}): NoteContent {
  return { repo: REPO, master: MASTER, path, language: "markdown", size: 100, truncated: false, content: `# ${path}`, ...over };
}

// /api/notes/folder → the seeded level in parts of two (so the next-part row is exercised);
// /api/notes/read → the seeded per-path content (404 otherwise); /api/notes/resolve and
// /api/role-launch/reports answer empty so task surfaces stay inert.
// opts.gone answers exists:false with the given reason for those folders; opts.vanish
// answers exists:false from the given offset on (a folder disappearing mid-pagination);
// opts.pageSize overrides the part size.
function stubNotesApi(
  folders: Record<string, FolderEntry[]>,
  contents: Record<string, NoteContent> = {},
  opts: { gone?: Record<string, string>; vanish?: { folder: string; fromOffset: number; reason: string }; pageSize?: number } = {},
) {
  const urls: string[] = [];
  const pageSize = opts.pageSize ?? 2;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      urls.push(url);
      const params = new URLSearchParams(url.split("?")[1] ?? "");
      if (url.startsWith("/api/task-document")) {
        const docPath = params.get("path") ?? "";
        const doc =
          dashboardStore.getState().analytics?.taskDocuments.find((item) => item.docPath === docPath) ??
          masterDoc();
        return { ok: true, status: 200, json: async () => doc } as unknown as Response;
      }
      if (url.startsWith("/api/notes/folder")) {
        const folder = params.get("path") ?? "";
        const offset = Number(params.get("offset") ?? "0");
        const goneReason = opts.gone?.[folder];
        if (goneReason !== undefined) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              repo: REPO, master: MASTER, path: folder, entries: [], total: 0, offset,
              limit: 200, remaining: 0, exists: false, reason: goneReason,
            }),
          } as unknown as Response;
        }
        if (opts.vanish && opts.vanish.folder === folder && offset >= opts.vanish.fromOffset) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              repo: REPO, master: MASTER, path: folder, entries: [], total: 0, offset,
              limit: 200, remaining: 0, exists: false, reason: opts.vanish!.reason,
            }),
          } as unknown as Response;
        }
        const all = folders[folder] ?? [];
        const page = all.slice(offset, offset + pageSize);
        return {
          ok: true,
          status: 200,
          json: async () => ({
            repo: REPO,
            master: MASTER,
            path: folder,
            entries: page,
            total: all.length,
            offset,
            limit: 200,
            remaining: Math.max(0, all.length - offset - page.length),
            exists: true,
            reason: null,
          }),
        } as unknown as Response;
      }
      if (url.startsWith("/api/notes/read")) {
        const body = contents[params.get("path") ?? ""];
        if (body) return { ok: true, status: 200, json: async () => body } as unknown as Response;
        return { ok: false, status: 404, json: async () => ({ status: "not-found" }) } as unknown as Response;
      }
      if (url.startsWith("/api/notes/resolve")) {
        return { ok: true, status: 200, json: async () => ({ repo: REPO, master: MASTER, resolutions: [] }) } as unknown as Response;
      }
      if (url.startsWith("/api/role-launch/reports")) {
        return { ok: true, status: 200, json: async () => ({ repo: REPO, master: MASTER, doc: "", reports: [], total: 0, explorer: null }) } as unknown as Response;
      }
      // Unrelated cockpit fetches (e.g. the hidden File Viewer layer's repo catalog) — a `{repos:[]}`
      // shape keeps that panel happy without a network hit.
      return { ok: true, status: 200, json: async () => ({ repos: [] }) } as unknown as Response;
    }),
  );
  return urls;
}

function stubRequirementsApi(path: string, contentBody: string) {
  const fetch = vi.fn(async (url: string) => {
    const params = new URLSearchParams(url.split("?")[1] ?? "");
    if (url.startsWith("/api/requirements/list")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          repo: REPO,
          master: MASTER,
          document: params.get("document"),
          registered: true,
          requirements: [
            { name: path, path, address: `requirements/${path}`, size: 100, sha256: "abc" },
          ],
        }),
      } as unknown as Response;
    }
    if (url.startsWith("/api/requirements/read")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          repo: REPO,
          master: MASTER,
          document: params.get("document"),
          name: path,
          path,
          address: `requirements/${path}`,
          size: contentBody.length,
          sha256: "abc",
          content: contentBody,
        }),
      } as unknown as Response;
    }
    return { ok: false, status: 404, json: async () => ({ status: "not-found" }) } as unknown as Response;
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('NotesReaderViewer tree rail (the shared ExplorerTree)', () => {
  it("shows one level at a time with the open note highlighted", async () => {
    stubNotesApi(
      { "": [entry("friction-ledger.md"), entry("reports/")] },
      { "friction-ledger.md": content("friction-ledger.md") },
    );
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="friction-ledger.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    const row = await view.findByTitle("friction-ledger.md");
    expect(row.textContent).toContain("friction-ledger.md");
    // The nested level is not fetched until its folder opens: no second-level row, one folder call.
    expect(view.queryByTitle("260703-L1-worker-report.md")).toBeNull();
    expect(row.getAttribute("aria-selected")).toBe("true");
    expect(view.getByTitle("reports").getAttribute("aria-selected")).toBe("false");
  });

  it("moves the highlight to follow the open note when the selection changes", async () => {
    stubNotesApi(
      { "": [entry("a.md"), entry("b.md")] },
      { "a.md": content("a.md"), "b.md": content("b.md") },
    );
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="a.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    await view.findByTitle("a.md");
    expect(view.getByTitle("a.md").getAttribute("aria-selected")).toBe("true");
    view.rerender(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="b.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    expect(view.getByTitle("a.md").getAttribute("aria-selected")).toBe("false");
    expect(view.getByTitle("b.md").getAttribute("aria-selected")).toBe("true");
    await view.findByTestId("sidecar-pane"); // flush the b.md content fetch
  });

  it("asks the parent to switch the open note when a file row is clicked (switch in place)", async () => {
    stubNotesApi({ "": [entry("a.md"), entry("b.md")] }, { "a.md": content("a.md") });
    const onSelectNote = vi.fn();
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="a.md" onSelectNote={onSelectNote} onBack={vi.fn()} />,
    );
    fireEvent.click(await view.findByTitle("b.md"));
    expect(onSelectNote).toHaveBeenCalledWith("b.md");
  });

  it("opens a closed folder on click and shows its children", async () => {
    stubNotesApi(
      {
        "": [entry("reports/")],
        reports: [entry("reports/r.md")],
      },
      { "reports/r.md": content("reports/r.md") },
    );
    const onSelectNote = vi.fn();
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="reports/r.md" onSelectNote={onSelectNote} onBack={vi.fn()} />,
    );
    // The folders on the way to the open note are open and the note is marked.
    const row = await view.findByTitle("r.md");
    expect(row.getAttribute("aria-selected")).toBe("true");
    fireEvent.click(row);
    expect(onSelectNote).toHaveBeenCalledWith("reports/r.md");
  });

  it("keeps folders closed until opened, except on the way to the open note", async () => {
    stubNotesApi({ "": [entry("a.md"), entry("reports/")], reports: [entry("reports/r.md")] });
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="a.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    await view.findByTitle("a.md");
    // reports/ is present but its children are not loaded until it opens.
    expect(view.getByTitle("reports")).toBeTruthy();
    expect(view.queryByTitle("r.md")).toBeNull();
  });

  it("shows a row that loads the next part of a large folder", async () => {
    stubNotesApi({ "": [entry("a.md"), entry("b.md"), entry("c.md")] });
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="a.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    await view.findByTitle("b.md");
    expect(view.queryByTitle("c.md")).toBeNull();
    const more = await view.findByTestId("notes-more");
    expect(more.textContent).toContain("1 more");
    fireEvent.click(more);
    expect(await view.findByTitle("c.md")).toBeTruthy();
    expect(view.queryByTestId("notes-more")).toBeNull();
  });

  it("opens a note below the old depth cap with its ancestor folders open", async () => {
    const deep = "reports/deep/d2/note.md";
    stubNotesApi(
      {
        "": [entry("reports/")],
        reports: [entry("reports/deep/")],
        "reports/deep": [entry("reports/deep/d2/")],
        "reports/deep/d2": [entry(deep)],
      },
      { [deep]: content(deep) },
    );
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path={deep} onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    const row = await view.findByTitle("note.md");
    expect(row.getAttribute("aria-selected")).toBe("true");
    await view.findByTestId("sidecar-pane"); // the deep note reads through the same read route
  });

  it("opens at a folder with the tree and a selection placeholder, reading nothing", async () => {
    const urls = stubNotesApi({ "": [entry("a.md")] });
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} folder onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    await view.findByTitle("a.md");
    expect(view.getByTestId("note-status").textContent).toContain("Select a note from the tree.");
    expect(urls.some((url) => url.startsWith("/api/notes/read"))).toBe(false);
  });

  it('marks an open note living beyond the first part, loading only its folder', async () => {
    const notes = ['n0.md', 'n1.md', 'n2.md', 'n3.md', 'n4.md'].map((name) => entry(name));
    const urls = stubNotesApi(
      { '': [entry('unrelated/'), ...notes] },
      { 'n4.md': content('n4.md') },
    );
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="n4.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    // The note's row is loaded and marked even though it lives in the third part.
    const row = await view.findByTitle('n4.md');
    expect(row.getAttribute('aria-selected')).toBe('true');
    // Only the open note's own folder was paged through; the unrelated folder stayed closed.
    const folderUrls = urls.filter((url) => url.startsWith('/api/notes/folder'));
    expect(folderUrls).toHaveLength(3);
    expect(
      folderUrls.every(
        (url) => url.includes('path=&') || (url.includes('path=') && !url.includes('unrelated')),
      ),
    ).toBe(true);
    expect(view.queryByTitle('unrelated')).toBeTruthy(); // the folder row exists, unopened
    expect(view.queryByTitle('inner.md')).toBeNull();
  });

  it("empties a vanished folder and shows the server reason, keeping the rest usable", async () => {
    stubNotesApi(
      { "": [entry("gone/"), entry("ok/")], ok: [entry("ok/kept.md")] },
      { "ok/kept.md": content("ok/kept.md") },
      { gone: { gone: "that folder is not there" } },
    );
    const onSelectNote = vi.fn();
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="gone/old.md" onSelectNote={onSelectNote} onBack={vi.fn()} />,
    );
    // The vanished folder shows the server's reason and no stale rows.
    const alert = await view.findByRole("alert");
    expect(alert.textContent).toContain("that folder is not there");
    // A sibling folder still opens and its note still selects.
    fireEvent.click(await view.findByTitle("ok"));
    const kept = await view.findByTitle("kept.md");
    fireEvent.click(kept);
    expect(onSelectNote).toHaveBeenCalledWith("ok/kept.md");
  });

  it("clears stale rows when a folder vanishes before its next part", async () => {
    const notes = ["n0.md", "n1.md", "n2.md"].map((name) => entry(name));
    stubNotesApi(
      { "": notes },
      {},
      { vanish: { folder: "", fromOffset: 2, reason: "that folder cannot be read" } },
    );
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} folder onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    await view.findByTitle("n1.md");
    fireEvent.click(await view.findByTestId("notes-more"));
    // The 200-scale stale rows are gone and the server reason is shown instead.
    const alert = await view.findByRole("alert");
    expect(alert.textContent).toContain("that folder cannot be read");
    expect(view.queryByTitle("n0.md")).toBeNull();
    expect(view.queryByTitle("n1.md")).toBeNull();
  });

  it.each([40, 70])("evicts least-recently-used folders past the stated cap (%i visited)", async (count) => {
    // Every folder holds one note except the last, which holds 250 (so only it has a
    // next-part row); the root lists all folders in one part.
    const folders: Record<string, FolderEntry[]> = { "": [] };
    for (let index = 0; index < count; index++) {
      const name = `f${String(index).padStart(2, "0")}`;
      folders[""]!.push(entry(`${name}/`));
      folders[name] =
        index === count - 1
          ? Array.from({ length: 250 }, (_, note) => entry(`${name}/n${String(note).padStart(3, "0")}.md`))
          : [entry(`${name}/only.md`)];
    }
    const last = `f${String(count - 1).padStart(2, "0")}`;
    const urls = stubNotesApi(folders, {}, { pageSize: 200 });
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} folder onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    // Visit every folder in order; each expansion fetches exactly once.
    for (let index = 0; index < count; index++) {
      fireEvent.click(await view.findByTitle(`f${String(index).padStart(2, "0")}`));
    }
    const folderFetches = (name: string) =>
      urls.filter((url) => url.startsWith("/api/notes/folder") && url.includes(`path=${name}`)).length;
    expect(folderFetches("f00")).toBe(1);
    // Paging the last folder bumps the revision, so the tree re-asks every loaded
    // parent: folders evicted past the cap of 32 refetch on demand, proving bounded
    // reclamation instead of unbounded retention.
    fireEvent.click(await view.findByTestId("notes-more"));
    await view.findByTitle(`${last}/n249.md`.split("/").pop()!);
    expect(folderFetches("f00")).toBe(2);
  });

  it("never evicts the folders on the way to the open note", async () => {
    const folders: Record<string, FolderEntry[]> = { "": [entry("keep/")] };
    for (let index = 0; index < 40; index++) folders[""]!.push(entry(`f${String(index).padStart(2, "0")}/`));
    folders["keep"] = [entry("keep/open.md")];
    const urls = stubNotesApi(folders, { "keep/open.md": content("keep/open.md") }, { pageSize: 200 });
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="keep/open.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    await view.findByTitle("open.md");
    for (let index = 0; index < 40; index++) {
      fireEvent.click(await view.findByTitle(`f${String(index).padStart(2, "0")}`));
    }
    // keep/ survived 40 further visits: collapsing and reopening it does not refetch.
    fireEvent.click(view.getByTitle("keep")); // collapse
    fireEvent.click(view.getByTitle("keep")); // expand again
    await view.findByTitle("open.md");
    const keepFetches = urls.filter(
      (url) => url.startsWith("/api/notes/folder") && url.includes("path=keep"),
    ).length;
    expect(keepFetches).toBe(1);
  });

  it("refetches nothing on an unrelated rerender and still loads on demand", async () => {
    const urls = stubNotesApi(
      { "": [entry("a.md"), entry("reports/")], reports: [entry("reports/r.md")] },
      { "a.md": content("a.md") },
    );
    const onSelectNote = vi.fn();
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="a.md" onSelectNote={onSelectNote} onBack={vi.fn()} />,
    );
    await view.findByTitle("a.md");
    const loaded = urls.filter((url) => url.startsWith("/api/notes/folder")).length;
    expect(loaded).toBeGreaterThan(0);
    // The same props again: the loaded folders answer from the cache, no second fetch.
    view.rerender(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="a.md" onSelectNote={onSelectNote} onBack={vi.fn()} />,
    );
    // Flush effects and settled fetches deterministically instead of a wall-clock sleep.
    await act(async () => {});
    expect(urls.filter((url) => url.startsWith("/api/notes/folder"))).toHaveLength(loaded);
    // Demand still loads: opening the closed folder fetches exactly its level.
    fireEvent.click(view.getByTitle("reports"));
    expect(await view.findByTitle("r.md")).toBeTruthy();
    expect(
      urls.filter((url) => url.startsWith("/api/notes/folder") && url.includes("path=reports")).length,
    ).toBe(1);
  });
});

describe("NotesReaderViewer content pane (reuses the File Viewer DualPane)", () => {
  it("renders an opened markdown note formatted (not raw) via the shared Markdown", async () => {
    stubNotesApi({ "": [entry("friction-ledger.md")] }, {
      "friction-ledger.md": content("friction-ledger.md", { content: "The **F-M** row." }),
    });
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="friction-ledger.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    await view.findByTestId("sidecar-pane");
    expect((await view.findByText("F-M")).tagName.toLowerCase()).toBe("strong"); // formatted, not raw
    expect(view.queryByText("The **F-M** row.")).toBeNull();
  });

  it("renders a non-markdown text note through the shared file pane (the text fallback)", async () => {
    stubNotesApi({ "": [entry("trace.txt", { language: "text" })] }, {
      "trace.txt": content("trace.txt", { language: "text", content: "line one\nline two" }),
    });
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="trace.txt" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    expect((await view.findByTestId("file-pane")).textContent).toBe("line one\nline two");
  });

  it("degrades a binary note to a byte-count placeholder instead of raw bytes", async () => {
    stubNotesApi({ "": [entry("scan.png", { language: "text" })] }, {
      "scan.png": content("scan.png", { language: "binary", size: 2048, content: "" }),
    });
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="scan.png" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    expect((await view.findByTestId("pane-placeholder")).textContent).toContain(
      "Binary file — 2,048 bytes (not shown)",
    );
  });

  it("shows the 2-MiB truncation banner for a truncated markdown note (L18 finding 2)", async () => {
    // Markdown is the dominant note type but takes DualPane's partnerless-markdown path, which has
    // no banner (only CodeSide does) -- so without this a truncated markdown note would silently drop
    // the "showing the first 2 MiB" contract. The banner renders here, above the rendered markdown.
    stubNotesApi({ "": [entry("big.md")] }, {
      "big.md": content("big.md", { truncated: true, size: 2_097_162, content: "# big\n\nlots" }),
    });
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="big.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    const banner = await view.findByTestId("notes-trunc-banner");
    expect(banner.textContent).toContain("Showing the first 2 MiB of 2,097,162 bytes");
    // The markdown still renders (the banner is ABOVE the pane, not instead of it).
    await view.findByTestId("sidecar-pane");
  });

  it("shows no truncation banner for a normal (untruncated) markdown note", async () => {
    stubNotesApi({ "": [entry("small.md")] }, { "small.md": content("small.md") });
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="small.md" onSelectNote={vi.fn()} onBack={vi.fn()} />,
    );
    await view.findByTestId("sidecar-pane");
    expect(view.queryByTestId("notes-trunc-banner")).toBeNull();
  });

  it("calls onBack when the back control is clicked", async () => {
    stubNotesApi({ "": [entry("a.md")] }, { "a.md": content("a.md") });
    const onBack = vi.fn();
    const view = render(
      <NotesReaderViewer kind="notes" repo={REPO} master={MASTER} path="a.md" onSelectNote={vi.fn()} onBack={onBack} />,
    );
    fireEvent.click(await view.findByTestId("notes-reader-back"));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});

describe('requirements artifact reader', () => {
  it('shows the explicit Requirements root and renders the exact selected packet', async () => {
    const path = 'CCR-R23-v1.md';
    const document = `${MASTER}/23_leaf.json`;
    const fetch = stubRequirementsApi(path, '# CCR-R23\n\nRegistered root.');
    const view = render(
      <NotesReaderViewer
        kind="requirements"
        repo={REPO}
        master={MASTER}
        document={document}
        path={path}
        onSelectNote={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    expect((await view.findByTestId("notes-reader-viewer")).getAttribute("data-artifact-kind")).toBe('requirements');
    expect(view.getByText(`requirements · ${MASTER}`)).toBeTruthy();
    expect(view.getByTestId('notes-reader-open').textContent).toBe(`requirements/${path}`);
    expect((await view.findByText('CCR-R23')).tagName.toLowerCase()).toBe('h1');
    const urls = (fetch.mock.calls as unknown as string[][]).map((call) => call[0]);
    expect(urls).toContain(
      `/api/requirements/read?repo=${REPO}&master=${MASTER}&document=${encodeURIComponent(document)}&path=${path}`,
    );
  });
});

// A minimal projection carrying one selectable master task document so the cockpit sidebar has a row.
//
// Typed, not `as unknown as`. Both literals below carried a double cast, which is worse here than
// on the shared fixture: this is a HAND-WRITTEN object, so the cast was the only thing standing
// between it and the mirror, and it made the seed immune to the mirror entirely — adding a
// required field to `Analytics` failed fifteen files and not this one. A seed that cannot fail
// when the contract moves is a seed that stops describing the contract the day it does.
function masterDoc(): TaskDocNode {
  return {
    id: 'task',
    lifecycleId: undefined,
    repository: REPO,
    title: 'Agent Orchestration',
    status: 'inProgress',
    kind: 'master',
    form: 'master',
    type: '',
    headerNotes: [],
    statusNoteTruncated: false,
    docPath: `/tasks/${REPO}/${MASTER}/task.json`,
    bodyRevision: '',
    createdAt: '2026-07-07T09:00:00+00:00',
    stepsDone: 0,
    stepsTotal: 0,
    steps: [],
    objective: 'Master objective.',
    requirements: [],
    codeExamples: [],
    decisions: [],
    openQuestions: [],
    references: [],
    orchestrates: [],
    executionWaves: [],
    subTasks: [],
    seats: [],
    sections: [],
  };
}
function seedMaster() {
  const projection = {
    version: 2,
    generatedAt: "2026-07-07T09:01:00+00:00",
    lifecycles: [],
    enclosures: [],
    providers: [],
    activeWorktreeGroups: [],
    metrics: metricsFor([]),
    analytics: {
      driftSnapshots: [], stalestSidecars: [], setupSummaries: [], setupProgress: [], routeCoverage: [],
      toolReports: [], ledgers: [], taskDocuments: [masterDoc()], series: [], attentionQueue: [], engineProcesses: [],
    },
  } satisfies WorkspaceProjection;
  dashboardStore.getState().applySnapshot(projection);
}

describe("Cockpit notes reader takeover", () => {
  it("does not show the notes reader initially and keeps the Operations rails", () => {
    stubNotesApi({ "": [entry("a.md")] });
    seedMaster();
    const { container } = render(<CockpitShell initialView="operations" />);
    expect(container.querySelector('[data-testid="notes-reader-viewer"]')).toBeNull();
    expect(container.querySelector(".rail--left")).not.toBeNull();
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("false");
  });

  it("opens the reader from the Notes line and keeps it MOUNTED across back (state survives back/forward, like the File Viewer)", async () => {
    stubNotesApi({ "": [entry("a.md"), entry("b.md")] }, { "a.md": content("a.md"), "b.md": content("b.md") });
    seedMaster();
    const view = render(<CockpitShell initialView="operations" />);

    // Select the master in the sidebar, then open the notes explorer from its Notes line.
    fireEvent.click(view.getByText("Agent Orchestration"));
    fireEvent.click(await view.findByTestId("notes-root-link"));

    // The takeover shows the notes reader; the railed body is hidden (not unmounted).
    const reader = await view.findByTestId("notes-reader-viewer");
    const railedBody = reader.closest(".shell__body")!.parentElement!.querySelector('[aria-hidden="true"].shell__body');
    expect(railedBody).not.toBeNull();

    // A tree row opens its note in place.
    fireEvent.click(await view.findByTitle("a.md"));
    await view.findByTestId("sidecar-pane");

    // Back hides the takeover but does NOT unmount it — the SAME node persists (its tree/selection
    // survive), exactly like the File Viewer's hidden-not-unmounted layer.
    fireEvent.click(view.getByTestId("notes-reader-back"));
    expect(view.getByTestId("notes-reader-viewer")).toBe(reader);

    // Re-opening surfaces the SAME mounted reader instance (never a fresh remount).
    fireEvent.click(await view.findByTestId("notes-root-link"));
    expect(view.getByTestId("notes-reader-viewer")).toBe(reader);
  });
});

describe("role report in the existing reader", () => {
  it("renders the supplied report with the shared pane, no listing, and its existing back control", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const onBack = vi.fn();
    const view = render(<NotesReaderViewer kind="role-report" report={{ path: "role.md", language: "markdown", content: "The **report**.", size: 15, truncated: false }} onBack={onBack} />);
    expect((await view.findByText("report")).tagName.toLowerCase()).toBe("strong");
    expect(view.queryByTestId("notes-tree")).toBeNull();
    expect(view.container.querySelectorAll('[data-testid="dual-pane"]')).toHaveLength(1);
    expect(view.getByTestId("notes-reader-open").textContent).toBe("role.md");
    expect(view.getByText("Report")).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(view.getByTestId("notes-reader-back"));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});

it("retains the supplied role report body, name and truncation notice in the shared pane", async () => {
  const view = render(<NotesReaderViewer kind="role-report" report={{ path: "written-role.md", language: "markdown", content: "A **bounded** report.", size: 2097216, truncated: true }} onBack={vi.fn()} />);
  expect((await view.findByText("bounded")).tagName.toLowerCase()).toBe("strong");
  expect(view.getByTestId("notes-reader-open").textContent).toBe("written-role.md");
  expect(view.getByTestId("notes-trunc-banner").textContent).toBe("Showing the first 2 MiB of 2,097,216 bytes");
  expect(view.container.querySelectorAll('[data-testid="dual-pane"]')).toHaveLength(1);
  expect(view.getByTestId("sidecar-pane").textContent).toBe("A bounded report.");
});

describe('decision whole-log page (MIK-R128 rule 13, evidence 10)', () => {
  const decisions = [
    {
      at: '2026-10-01T00:00:00+00:00',
      decision: 'first **decision**',
      rationale: 'why **one**',
      product_record: false,
    },
    { at: "2026-10-02T00:00:00+00:00", decision: "Finalize task lifecycle.", rationale: "record", product_record: true },
    { at: "2026-10-03T00:00:00+00:00", decision: "latest decision", rationale: "why three", product_record: false },
    {
      at: '2026-10-04T00:00:00+00:00',
      decision: 'Removed step S1.',
      rationale: 'kept record',
      product_record: true,
    },
  ];
  it('shares numbered task-page rows with short time, formatted text and reason, preserving records behind the muted end line', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const onBack = vi.fn();
    const input = [...decisions].reverse(); // newest product record was prepended
    const original = JSON.stringify(input);
    const view = render(
      <NotesReaderViewer
        kind="decisions"
        repo={REPO}
        master={MASTER}
        title="leaf"
        decisions={input}
        onSelectNote={vi.fn()}
        onBack={onBack}
      />,
    );
    const log = await view.findByTestId('decision-log-whole');
    for (const className of `${block} ${decisionReading}`.split(' '))
      expect(log.classList.contains(className)).toBe(true);
    const rows = view.getAllByTestId('decision-row');
    expect(rows).toHaveLength(2);
    expect(
      rows.map((row) => row.querySelector('[data-testid="decision-number"]')?.textContent),
    ).toEqual(['#2', '#1']);
    expect(
      rows.map((row) => row.querySelector('[data-testid="decision-time"]')?.textContent),
    ).toEqual(['2026-10-03 00:00', '2026-10-01 00:00']);
    expect(rows[0].textContent).toContain('latest decision');
    expect(rows[0].querySelector('[data-testid="decision-rationale"]')?.textContent).toBe(
      'why three',
    );
    expect(rows[1].querySelector('[data-testid="decision-text"] strong')?.textContent).toBe(
      'decision',
    );
    expect(rows[1].querySelector('[data-testid="decision-rationale"] strong')?.textContent).toBe(
      'one',
    );
    const records = view.getByTestId('decision-product-records');
    for (const className of productRecords.split(' '))
      expect(records.classList.contains(className)).toBe(true);
    const endLine = records.querySelector('summary')!;
    expect(endLine.textContent).toBe('2 product records');
    expect(log.lastElementChild).toBe(records);
    expect(records.hasAttribute('open')).toBe(false);
    fireEvent.click(endLine);
    expect(records.hasAttribute('open')).toBe(true);
    expect(records.textContent).toContain('Finalize task lifecycle.');
    expect(records.textContent).toContain('record');
    expect(records.textContent).toContain('Removed step S1.');
    expect(records.querySelectorAll('[data-testid="decision-row"]')).toHaveLength(0);
    expect(JSON.stringify(input)).toBe(original);
    expect(fetch).not.toHaveBeenCalled();
    expect(view.getByTestId('notes-reader-open').textContent).toBe('decisions/');
    fireEvent.click(view.getByTestId('notes-reader-back'));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it.each(
    [
      [],
      decisions.filter((entry) => entry.product_record),
      decisions.filter((entry) => !entry.product_record),
    ].map((input) => ({ input })),
  )(
    'handles empty, records-only and authored-only logs without inventing decisions',
    async ({ input }) => {
      vi.stubGlobal('fetch', vi.fn());
      const view = render(
        <NotesReaderViewer
          kind="decisions"
          repo={REPO}
          master={MASTER}
          title="leaf"
          decisions={input}
          onSelectNote={vi.fn()}
          onBack={vi.fn()}
        />,
      );
      await view.findByTestId('decision-log-whole');
      expect(view.queryAllByTestId('decision-row')).toHaveLength(
        input.filter((entry) => !entry.product_record).length,
      );
      expect(view.queryByTestId('decision-product-records') !== null).toBe(
        input.some((entry) => entry.product_record),
      );
    },
  );
});

describe("task report target", () => {
  // The role scope the report is recorded under: the viewer is given it and the request names it.
  const REPORT_ROLE = "worker";

  it("fetches the recorded report for its role scope and renders it", async () => {
    const fetch = vi.fn(async (url: string, init?: { body?: string }) => {
      if (url === "/api/role-launch/report") {
        const body = JSON.parse(typeof init?.body === "string" ? init.body : "{}");
        expect(body).toEqual({
          role: REPORT_ROLE,
          taskDocumentRef: { repository: REPO, path: `${MASTER}/09_x.json` },
          requestId: "req-1",
        });
        return {
          ok: true,
          status: 200,
          json: async () => ({ path: "req-1.md", language: "markdown", content: "The **report**.", size: 15, truncated: false }),
        } as unknown as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
    });
    vi.stubGlobal("fetch", fetch);
    const view = render(
      <NotesReaderViewer
        kind="task-report"
        repo={REPO}
        master={MASTER}
        role={REPORT_ROLE}
        requestId="req-1"
        taskRepository={REPO}
        taskPath={`${MASTER}/09_x.json`}
        onSelectNote={vi.fn()}
        onBack={vi.fn()}
      />,
    );
    expect((await view.findByText("report")).tagName.toLowerCase()).toBe("strong");
    expect(view.getByTestId("notes-reader-open").textContent).toBe("req-1");
  });
});
