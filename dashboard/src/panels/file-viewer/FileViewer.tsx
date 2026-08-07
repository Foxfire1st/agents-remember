// The File Viewer center tab (full-bleed): a repo selector + a mainline/enclosure scope
// selector drive two Headless Tree explorers (code + onboarding) over the files API; the
// right side is the reusable DualPane. Pairing is bidirectional: opening a code file derives
// its sidecar (forward); opening a sidecar opens its partner code file (reverse), or shows an
// overview placeholder. View-mode (split/single) persists across file switches.
import { memo, useEffect, useRef, useState } from "react";
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
import { DualPane, type SidecarView } from "./DualPane";
import { FileTree } from "./FileTree";
import { usePersistedFlag } from "./usePersistedFlag";

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

function openCodeEntry(
  repo: string,
  scope: Scope,
  currentPath: string | undefined,
  entry: DirEntry,
  setError: (error: string | null) => void,
  setCode: (content: FileContent | null) => void,
  setSidecar: (view: SidecarView) => void,
): void {
  if (entry.kind !== "file" || entry.path === currentPath) return;
  setError(null);
  readFile(repo, scope, entry.path)
    .then((f) => {
      setCode(f);
      return resolveForward(repo, scope, entry.path);
    })
    .then((p) =>
      setSidecar(
        p.status === "found" && p.body != null ? { state: "markdown", body: p.body } : { state: "missing" },
      ),
    )
    .catch((e) => setError(describeError(e)));
}

function openSidecarEntry(
  repo: string,
  scope: Scope,
  entry: DirEntry,
  setError: (error: string | null) => void,
  setCode: (content: FileContent | null) => void,
  setSidecar: (view: SidecarView) => void,
  openCode: (entry: DirEntry) => void,
): void {
  if (entry.kind !== "file") return;
  setError(null);
  resolveReverse(repo, scope, entry.path)
    .then((p) => {
      if (p.kind === "sidecar" && p.exists) {
        openCode({ name: p.codePath.split("/").pop() ?? "", path: p.codePath, kind: "file" });
      } else {
        // An overview/entities/index doc has no code partner — render its OWN markdown instead of an
        // empty placeholder (a route overview must be readable). Fall back to the placeholder only if
        // the body could not be read (binary/unreadable) or there is genuinely no onboarding here.
        setCode(null);
        setSidecar(
          p.kind === "overview" && p.body != null
            ? { state: "markdown", body: p.body }
            : { state: "overview" },
        );
      }
    })
    .catch((e) => setError(describeError(e)));
}

function FileViewerImpl({ active = true }: { active?: boolean }) {
  const [repos, setRepos] = useState<RepoCatalogEntry[]>([]);
  const [repo, setRepo] = useState<string>("");
  const [scope, setScope] = useState<Scope>("mainline");
  const [code, setCode] = useState<FileContent | null>(null);
  const [sidecar, setSidecar] = useState<SidecarView>({ state: "empty" });
  const [split, setSplit] = usePersistedFlag("fileviewer.split", true);
  const [error, setError] = useState<string | null>(null);
  // The Cockpit keeps this layer mounted-but-hidden until its view is selected; the boot catalog
  // read waits for the FIRST real showing. `settled` marks a completed attempt (success OR
  // failure) so later hide/show cycles never re-read — the same once-per-lifetime posture the
  // mount effect had before — while StrictMode's replayed effect simply re-joins the shared
  // in-flight read (data/inflight.ts) instead of firing a second GET.
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

  // Reset the open file when the scope root changes.
  useEffect(() => {
    setCode(null);
    setSidecar({ state: "empty" });
  }, [repo, scope]);

  const current = repos.find((r) => r.repo === repo);
  const scopeItems = current
    ? [
        { id: "mainline", label: current.mainline.label },
        ...current.worktrees.map((w) => ({ id: w.scope, label: w.label })),
      ]
    : [{ id: "mainline", label: "mainline" }];

  const openCode = (entry: DirEntry) =>
    openCodeEntry(repo, scope, code?.path, entry, setError, setCode, setSidecar);
  const openSidecar = (entry: DirEntry) =>
    openSidecarEntry(repo, scope, entry, setError, setCode, setSidecar, openCode);

  return (
    <div className={shell} data-testid="file-viewer">
      <header className={toolbar}>
        <PickList label="Repo" items={repos.map((r) => ({ id: r.repo, label: r.repo }))} value={repo} onChange={setRepo} />
        <PickList label="Scope" items={scopeItems} value={scope} onChange={setScope} />
        <button type="button" className={toggle({ on: split })} aria-pressed={split} onClick={() => setSplit(!split)}>
          {split ? "▣ Split" : "▢ Single"}
        </button>
        {error ? (
          <span className={errBadge} data-testid="files-error">
            {error}
          </span>
        ) : null}
      </header>
      <PanelGroup direction="horizontal" autoSaveId="fileviewer.outer" className={body}>
        <Panel defaultSize={26} minSize={15}>
          <div className={explorer}>
            <div className={treeLabel}>Code</div>
            <FileTree key={`${repo}:${scope}:code`} repo={repo} scope={scope} side="code" onOpen={openCode} />
            <div className={treeLabel}>Onboarding</div>
            <FileTree key={`${repo}:${scope}:onb`} repo={repo} scope={scope} side="onboarding" onOpen={openSidecar} />
          </div>
        </Panel>
        <PanelResizeHandle className={vhandle} />
        <Panel defaultSize={74} minSize={40}>
          <DualPane code={code} sidecar={sidecar} split={split} />
        </Panel>
      </PanelGroup>
    </div>
  );
}

// Memoized (tab-switch CPU): a keep-alive cockpit layer — the shell re-renders on every
// view switch, and the memo gate skips this whole subtree unless `active` (or another prop)
// actually changed; the viewer's own store subscriptions still drive its updates.
export const FileViewer = memo(FileViewerImpl);
