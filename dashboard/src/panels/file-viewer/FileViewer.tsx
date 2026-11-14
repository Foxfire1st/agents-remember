// The File Viewer center tab (full-bleed): a repo selector + a mainline/enclosure scope
// selector drive two Headless Tree explorers (code + onboarding) over the files API; the
// right side is the reusable DualPane. Pairing is bidirectional: opening a code file derives
// its sidecar (forward); opening a sidecar opens its partner code file (reverse), or shows an
// overview placeholder. View-mode (split/single) persists across file switches.
//
// A Markdown file of the repository opens RENDERED, with one Rendered/Source switch in the
// header; a picture or video opens as itself; a document's links open in this viewer. Back and
// forward lead through the files opened here, each at the place the reader had scrolled to.
import { memo, useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from "react";
import { Button, ListBox, ListBoxItem, Popover, Select, SelectValue } from "react-aria-components";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { css, cva } from "../../../styled-system/css";
import {
  FilesApiError,
  fetchRepos,
  readFile,
  resolveForward,
  resolveReverse,
  type DirEntry,
  type FileContent,
  type RepoCatalogEntry,
  type Scope,
} from "../../data/files";
import { fileMediaUrl } from "../../data/media";
import { documentAnchor } from "../../grammar/documentMarkdown";
import type { DocumentContext } from "../../grammar/DocumentMarkdown";
import { SidebarToggle, sidebarHandleProps, sidebarPanelProps, useSidebar } from "../../grammar/SidebarToggle";
import { usePublishPageRepository } from "../../tray/shownChat";
import { DualPane, type SidecarView } from "./DualPane";
import { FileTree } from "./FileTree";
import { usePersistedFlag } from "./usePersistedFlag";
import {
  keepScroll,
  releaseBodies,
  type OpenEntry,
  type ViewerHistory,
} from "./viewerHistory";

const shell = css({ display: "flex", flexDirection: "column", height: "100%", minHeight: "0", gap: "0.5rem" });
const toolbar = css({ display: "flex", alignItems: "center", gap: "0.6rem", flexShrink: 0, flexWrap: "wrap" });
const body = css({ flex: "1", minHeight: "0" });
const explorer = css({
  display: "flex",
  flexDirection: "column",
  height: "100%",
  minHeight: "0",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  overflow: "hidden",
});
const treeLabel = css({
  flexShrink: 0,
  fontSize: "0.7rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "amber",
  paddingInline: "0.5rem",
  paddingBlock: "0.25rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  background: "bg",
});
const vhandle = css({ width: "4px", flexShrink: 0, background: "grid", cursor: "col-resize", _hover: { background: "amber" } });
const errBadge = css({ color: "alarm", fontSize: "0.75rem", marginLeft: "auto" });
const toggle = cva({
  base: {
    font: "inherit",
    fontSize: "0.74rem",
    paddingInline: "0.5rem",
    paddingBlock: "0.15rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    cursor: "pointer",
    background: "transparent",
  },
  variants: { on: { true: { color: "amber", borderColor: "amber" }, false: { color: "muted", borderColor: "grid" } } },
});
const navBtn = css({
  font: "inherit",
  fontSize: "0.8rem",
  lineHeight: "1",
  paddingInline: "0.45rem",
  paddingBlock: "0.2rem",
  color: "ink",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _disabled: { color: "muted", cursor: "default", opacity: "0.5" },
  _hover: { borderColor: "amber" },
});
const selLabel = css({ fontSize: "0.7rem", color: "muted" });
const selectRow = css({ display: "flex", alignItems: "center", gap: "0.3rem" });
const selectBtn = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  font: "inherit",
  fontSize: "0.78rem",
  color: "ink",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.5rem",
  paddingBlock: "0.2rem",
  cursor: "pointer",
  _hover: { borderColor: "muted" },
});
const popover = css({
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.2rem",
  maxHeight: "50vh",
  overflow: "auto",
});
const listbox = css({ display: "flex", flexDirection: "column", outline: "none" });
const option = css({
  fontSize: "0.78rem",
  color: "ink",
  paddingInline: "0.5rem",
  paddingBlock: "0.2rem",
  borderRadius: "2px",
  cursor: "pointer",
  _selected: { color: "amber" },
  "&[data-focused]": { background: "color-mix(in oklab, var(--amber) 16%, transparent)", outline: "none" },
});

function describeError(e: unknown): string {
  return e instanceof FilesApiError ? e.code : "request failed";
}

function PickList({
  label,
  items,
  value,
  onChange,
}: {
  label: string;
  items: { id: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <Select
      aria-label={label}
      selectedKey={value || null}
      onSelectionChange={(k) => onChange(String(k))}
      className={selectRow}
    >
      <span className={selLabel}>{label}</span>
      <Button className={selectBtn}>
        <SelectValue />
        <span aria-hidden>▾</span>
      </Button>
      <Popover className={popover}>
        <ListBox className={listbox}>
          {items.map((it) => (
            <ListBoxItem key={it.id} id={it.id} className={option}>
              {it.label}
            </ListBoxItem>
          ))}
        </ListBox>
      </Popover>
    </Select>
  );
}

// One file opened in the viewer, as the history keeps it: its content, its sidecar and the
// place the reader had scrolled to. A document opened by a link also names its anchor.
interface OpenedCode {
  content: FileContent;
  sidecar: SidecarView;
}

function sidecarView(pairing: { status?: string; body?: string | null }): SidecarView {
  return pairing.status === "found" && pairing.body != null
    ? { state: "markdown", body: pairing.body }
    : { state: "missing" };
}

async function loadCode(repo: string, scope: Scope, path: string): Promise<OpenedCode> {
  const content = await readFile(repo, scope, path);
  const pairing = await resolveForward(repo, scope, content.path);
  return { content, sidecar: sidecarView(pairing) };
}

async function loadSidecarDocument(repo: string, scope: Scope, path: string): Promise<SidecarView> {
  const pairing = await resolveReverse(repo, scope, path);
  if (pairing.kind !== "overview" || pairing.body == null) return { state: "overview" };
  return { state: "markdown", body: pairing.body };
}

function openedEntry(opened: OpenedCode, anchor?: string): OpenEntry {
  return {
    kind: "code",
    content: opened.content,
    sidecar: opened.sidecar,
    rendered: opened.content.language === "markdown",
    scrollTop: 0,
    anchor,
  };
}

// The boot catalog read waits for the FIRST real showing. `settled` marks a completed attempt
// (success OR failure) so later hide/show cycles never re-read — the same once-per-lifetime
// posture the mount effect had before — while StrictMode's replayed effect simply re-joins the
// shared in-flight read (data/inflight.ts) instead of firing a second GET.
function useRepoCatalog(active: boolean) {
  const [repos, setRepos] = useState<RepoCatalogEntry[]>([]);
  const [repo, setRepo] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const settledRef = useRef(false);

  useEffect(() => {
    if (!active || settledRef.current) return;
    let alive = true;
    fetchRepos()
      .then((cat) => {
        if (!alive) return;
        settledRef.current = true;
        // Defensive: a catalog response without `repos` (unexpected server shape, generic test
        // stubs) must degrade to the empty list — `undefined` here put every later render's
        // `repos.find` into a crash loop.
        const list = cat.repos ?? [];
        setRepos(list);
        const first = list[0];
        if (first) setRepo((r) => r || first.repo);
      })
      .catch((e) => {
        if (!alive) return;
        settledRef.current = true;
        setError(describeError(e));
      });
    return () => {
      alive = false;
    };
  }, [active]);

  return { repos, repo, setRepo, error, setError };
}

function scrollHost(host: HTMLDivElement | null): HTMLElement | null {
  if (!host) return null;
  return (
    host.querySelector<HTMLElement>(".cm-scroller") ??
    host.querySelector<HTMLElement>("[data-pane-scroll='true']")
  );
}

// Back and forward lead through the entries opened here. The scrolling surface for the open
// entry is the rendered document or media box, or CodeMirror's own scroller for source.
function useViewerHistory(paneRef: RefObject<HTMLDivElement | null>, repo: string, scope: Scope) {
  const [nav, setNav] = useState<ViewerHistory>({ entries: [], index: -1 });

  useEffect(() => {
    setNav({ entries: [], index: -1 });
  }, [repo, scope]);

  const currentScroll = () => scrollHost(paneRef.current)?.scrollTop ?? 0;

  function push(next: OpenEntry): void {
    const scrollTop = currentScroll();
    setNav((state) => {
      const kept = keepScroll(state, scrollTop);
      const entries = kept.entries.slice(0, kept.index + 1);
      entries.push(next);
      return releaseBodies({ entries, index: entries.length - 1 });
    });
  }

  function back(): void {
    const scrollTop = currentScroll();
    setNav((state) => {
      if (state.index <= 0) return state;
      const kept = keepScroll(state, scrollTop);
      return releaseBodies({ entries: kept.entries, index: kept.index - 1 });
    });
  }

  function forward(): void {
    const scrollTop = currentScroll();
    setNav((state) => {
      if (state.index < 0 || state.index >= state.entries.length - 1) return state;
      const kept = keepScroll(state, scrollTop);
      return releaseBodies({ entries: kept.entries, index: kept.index + 1 });
    });
  }

  function setRendered(value: boolean): void {
    setNav((state) => {
      const current = state.entries[state.index];
      if (!current || current.kind !== "code") return state;
      const entries = [...state.entries];
      entries[state.index] = { ...current, rendered: value };
      return releaseBodies({ ...state, entries });
    });
  }

  function replaceCurrent(next: OpenEntry): void {
    setNav((state) => {
      if (state.index < 0) return state;
      const entries = [...state.entries];
      entries[state.index] = next;
      return releaseBodies({ ...state, entries });
    });
  }

  const entry = nav.index >= 0 ? nav.entries[nav.index] : null;
  return { nav, entry, push, back, forward, setRendered, replaceCurrent };
}

// A rendered document's link opens the named repository file in this viewer. A media file and a
// source file both come back from the same read; Markdown opens rendered.
function useFileOpeners(
  repo: string,
  scope: Scope,
  push: (entry: OpenEntry) => void,
  setError: Dispatch<SetStateAction<string | null>>,
) {
  function openLinkedFile(path: string, anchor?: string): void {
    setError(null);
    loadCode(repo, scope, path)
      .then((opened) => push(openedEntry(opened, anchor)))
      .catch((e) => setError(describeError(e)));
  }

  function openCode(dirEntry: DirEntry): void {
    if (dirEntry.kind !== "file") return;
    setError(null);
    loadCode(repo, scope, dirEntry.path)
      .then((opened) => push(openedEntry(opened)))
      .catch((e) => setError(describeError(e)));
  }

  function openSidecar(dirEntry: DirEntry): void {
    if (dirEntry.kind !== "file") return;
    setError(null);
    resolveReverse(repo, scope, dirEntry.path)
      .then(async (pairing) => {
        if (pairing.kind === "sidecar" && pairing.exists) {
          push(openedEntry(await loadCode(repo, scope, pairing.codePath)));
          return;
        }
        push({ kind: "document", path: dirEntry.path, sidecar: await loadSidecarDocument(repo, scope, dirEntry.path), scrollTop: 0 });
      })
      .catch((e) => setError(describeError(e)));
  }

  return { openLinkedFile, openCode, openSidecar };
}

// Back and forward bring each entry back at the place the reader had scrolled to, and a link's
// heading is scrolled into view once the entry is drawn.
function useScrollRestore(
  paneRef: RefObject<HTMLDivElement | null>,
  nav: ViewerHistory,
  entry: OpenEntry | null,
): void {
  const index = nav.index;
  useEffect(() => {
    if (index < 0 || !entry) return;
    const frame = requestAnimationFrame(() => {
      const host = paneRef.current;
      if (!host) return;
      // A saved position is the reader's place; the entry's link anchor is only the first
      // arrival. Restoring scrollTop first is what keeps a later visit to a linked document
      // from jumping back to its heading, and a saved zero is a position too: the scrollKnown
      // flag separates "first arrival at this anchor" from "history saved this entry".
      if (entry.scrollKnown || entry.scrollTop > 0) {
        const scroller = scrollHost(host);
        if (scroller) scroller.scrollTop = entry.scrollTop;
        return;
      }
      if (entry.anchor) {
        const found = documentAnchor(host, entry.anchor);
        if (found) {
          found.scrollIntoView({ block: "start" });
          return;
        }
      }
      const scroller = scrollHost(host);
      if (scroller) scroller.scrollTop = 0;
    });
    return () => cancelAnimationFrame(frame);
  }, [index, entry, paneRef]);
}

function scopeItemsFor(current: RepoCatalogEntry | undefined): { id: string; label: string }[] {
  if (!current) return [{ id: "mainline", label: "mainline" }];
  return [
    { id: "mainline", label: current.mainline.label },
    ...current.worktrees.map((w) => ({ id: w.scope, label: w.label })),
  ];
}

function RenderedToggle({
  isMarkdown,
  rendered,
  setRendered,
}: {
  isMarkdown: boolean;
  rendered: boolean;
  setRendered: (value: boolean) => void;
}) {
  if (!isMarkdown) return null;
  return (
    <span className={selectRow} role="group" aria-label="Rendered or source">
      <button
        type="button"
        className={toggle({ on: rendered })}
        aria-pressed={rendered}
        onClick={() => setRendered(true)}
        data-testid="file-viewer-rendered"
      >
        Rendered
      </button>
      <button
        type="button"
        className={toggle({ on: !rendered })}
        aria-pressed={!rendered}
        onClick={() => setRendered(false)}
        data-testid="file-viewer-source"
      >
        Source
      </button>
    </span>
  );
}

function shownFile(repos: RepoCatalogEntry[], repo: string, entry: OpenEntry | null) {
  const code = entry?.kind === "code" ? entry.content : null;
  const rendered = entry?.kind === "code" ? entry.rendered : false;
  const sidecar: SidecarView = entry?.sidecar ?? { state: "empty" };
  const current = repos.find((r) => r.repo === repo);
  return { code, rendered, sidecar, isMarkdown: code?.language === "markdown", scopeItems: scopeItemsFor(current) };
}

function documentContextFor(
  code: FileContent | null,
  repo: string,
  scope: Scope,
  openFile: (path: string, anchor?: string) => void,
): DocumentContext | undefined {
  if (!code || code.language !== "markdown") return undefined;
  return { repo, scope, path: code.path, openFile };
}

function ViewerToolbar({
  explorerSide,
  nav,
  back,
  forward,
  repos,
  repo,
  setRepo,
  scopeItems,
  scope,
  setScope,
  isMarkdown,
  rendered,
  setRendered,
  split,
  setSplit,
  error,
}: {
  explorerSide: ReturnType<typeof useSidebar>;
  nav: ViewerHistory;
  back: () => void;
  forward: () => void;
  repos: RepoCatalogEntry[];
  repo: string;
  setRepo: (repo: string) => void;
  scopeItems: { id: string; label: string }[];
  scope: Scope;
  setScope: (scope: Scope) => void;
  isMarkdown: boolean;
  rendered: boolean;
  setRendered: (value: boolean) => void;
  split: boolean;
  setSplit: (value: boolean) => void;
  error: string | null;
}) {
  return (
    <div className={toolbar}>
      {/* MIK-R89 v2 rule 8: the explorer's shared toggle, first on the line above it. */}
      <SidebarToggle side="left" open={explorerSide.open} onToggle={explorerSide.toggle} name="Explorer sidebar" testId="sidebar-toggle-files-left" />
      <button
        type="button"
        className={navBtn}
        onClick={back}
        disabled={nav.index <= 0}
        aria-label="Back"
        title="Back"
        data-testid="file-viewer-back"
      >
        ←
      </button>
      <button
        type="button"
        className={navBtn}
        onClick={forward}
        disabled={nav.index < 0 || nav.index >= nav.entries.length - 1}
        aria-label="Forward"
        title="Forward"
        data-testid="file-viewer-forward"
      >
        →
      </button>
      <PickList label="Repo" items={repos.map((r) => ({ id: r.repo, label: r.repo }))} value={repo} onChange={setRepo} />
      <PickList label="Scope" items={scopeItems} value={scope} onChange={setScope} />
      <RenderedToggle isMarkdown={isMarkdown} rendered={rendered} setRendered={setRendered} />
      <button type="button" className={toggle({ on: split })} aria-pressed={split} onClick={() => setSplit(!split)}>
        {split ? "▣ Split" : "▢ Single"}
      </button>
      {error ? (
        <span className={errBadge} data-testid="files-error">
          {error}
        </span>
      ) : null}
    </div>
  );
}

function ViewerBody({
  explorerSide,
  repo,
  scope,
  openCode,
  openSidecar,
  code,
  sidecar,
  split,
  mediaUrl,
  rendered,
  documentContext,
  paneRef,
}: {
  explorerSide: ReturnType<typeof useSidebar>;
  repo: string;
  scope: Scope;
  openCode: (entry: DirEntry) => void;
  openSidecar: (entry: DirEntry) => void;
  code: FileContent | null;
  sidecar: SidecarView;
  split: boolean;
  mediaUrl: string | null;
  rendered: boolean;
  documentContext: DocumentContext | undefined;
  paneRef: RefObject<HTMLDivElement | null>;
}) {
  return (
    <PanelGroup direction="horizontal" autoSaveId="fileviewer.outer" className={body}>
      <Panel defaultSize={26} minSize={15} {...sidebarPanelProps(explorerSide.open)}>
        <div className={explorer}>
          <div className={treeLabel}>Code</div>
          <FileTree key={`${repo}:${scope}:code`} repo={repo} scope={scope} side="code" onOpen={openCode} />
          <div className={treeLabel}>Onboarding</div>
          <FileTree key={`${repo}:${scope}:onb`} repo={repo} scope={scope} side="onboarding" onOpen={openSidecar} />
        </div>
      </Panel>
      <PanelResizeHandle className={vhandle} {...sidebarHandleProps(explorerSide.open)} />
      <Panel defaultSize={74} minSize={40}>
        <div ref={paneRef} className={css({ height: "100%", minHeight: "0" })}>
          <DualPane
            code={code}
            sidecar={sidecar}
            split={split}
            repository={repo || undefined}
            mediaUrl={mediaUrl}
            rendered={rendered}
            document={documentContext}
          />
        </div>
      </Panel>
    </PanelGroup>
  );
}

function FileViewerImpl({ active = true }: { active?: boolean }) {
  const { repos, repo, setRepo, error, setError } = useRepoCatalog(active);
  const [scope, setScope] = useState<Scope>("mainline");
  const [split, setSplit] = usePersistedFlag("fileviewer.split", true);
  const paneRef = useRef<HTMLDivElement>(null);
  const explorerSide = useSidebar("files", "left");
  const { nav, entry, push, back, forward, setRendered, replaceCurrent } = useViewerHistory(paneRef, repo, scope);
  // The tray files a message to a chat without a task under the shown page's repository.
  usePublishPageRepository("File Viewer", repo || null, active);
  const { openLinkedFile, openCode, openSidecar } = useFileOpeners(repo, scope, push, setError);
  useScrollRestore(paneRef, nav, entry);
  useEffect(() => {
    if (!entry?.bodyReleased) return;
    let alive = true;
    const current = entry;
    const load = current.kind === "code"
      ? loadCode(repo, scope, current.content.path).then((opened) => ({
          ...current,
          content: opened.content,
          sidecar: opened.sidecar,
          bodyReleased: false,
        }))
      : loadSidecarDocument(repo, scope, current.path).then((sidecar) => ({
          ...current,
          sidecar,
          bodyReleased: false,
        }));
    load
      .then((next) => {
        if (alive) replaceCurrent(next);
      })
      .catch((e) => {
        if (alive) setError(describeError(e));
      });
    return () => {
      alive = false;
    };
  }, [entry?.bodyReleased, entry?.kind, entry, repo, scope, replaceCurrent, setError]);
  const shown = shownFile(repos, repo, entry);

  return (
    <div className={shell} data-testid="file-viewer">
      <ViewerToolbar
        explorerSide={explorerSide}
        nav={nav}
        back={back}
        forward={forward}
        repos={repos}
        repo={repo}
        setRepo={setRepo}
        scopeItems={shown.scopeItems}
        scope={scope}
        setScope={setScope}
        isMarkdown={Boolean(shown.isMarkdown)}
        rendered={shown.rendered}
        setRendered={setRendered}
        split={split}
        setSplit={setSplit}
        error={error}
      />
      <ViewerBody
        explorerSide={explorerSide}
        repo={repo}
        scope={scope}
        openCode={openCode}
        openSidecar={openSidecar}
        code={shown.code}
        sidecar={shown.sidecar}
        split={split}
        mediaUrl={shown.code ? fileMediaUrl(repo, scope, shown.code.path) : null}
        rendered={shown.rendered}
        documentContext={documentContextFor(shown.code, repo, scope, openLinkedFile)}
        paneRef={paneRef}
      />
    </div>
  );
}

// Memoized (tab-switch CPU): a keep-alive cockpit layer — the shell re-renders on every
// view switch, and the memo gate skips this whole subtree unless `active` (or another prop)
// actually changed; the viewer's own store subscriptions still drive its updates.
export const FileViewer = memo(FileViewerImpl);
