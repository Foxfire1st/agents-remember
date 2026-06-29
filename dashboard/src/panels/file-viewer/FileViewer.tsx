// The File Viewer center tab (full-bleed): a repo selector + a mainline/enclosure scope
// selector drive two Headless Tree explorers (code + onboarding) over the L1 files API; the
// right side is the reusable DualPane. Pairing is bidirectional: opening a code file derives
// its sidecar (forward); opening a sidecar opens its partner code file (reverse), or shows an
// overview placeholder. View-mode (split/single) persists across file switches.
import { useEffect, useState } from "react";
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

export function FileViewer() {
  const [repos, setRepos] = useState<RepoCatalogEntry[]>([]);
  const [repo, setRepo] = useState<string>("");
  const [scope, setScope] = useState<Scope>("mainline");
  const [code, setCode] = useState<FileContent | null>(null);
  const [sidecar, setSidecar] = useState<SidecarView>({ state: "empty" });
  const [split, setSplit] = usePersistedFlag("fileviewer.split", true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchRepos()
      .then((cat) => {
        if (!alive) return;
        setRepos(cat.repos);
        const first = cat.repos[0];
        if (first) setRepo((r) => r || first.repo);
      })
      .catch((e) => {
        if (alive) setError(describeError(e));
      });
    return () => {
      alive = false;
    };
  }, []);

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

  function openCode(entry: DirEntry) {
    if (entry.kind !== "file" || entry.path === code?.path) return;
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

  function openSidecar(entry: DirEntry) {
    if (entry.kind !== "file") return;
    setError(null);
    resolveReverse(repo, scope, entry.path)
      .then((p) => {
        if (p.kind === "sidecar" && p.exists) {
          openCode({ name: p.codePath.split("/").pop() ?? "", path: p.codePath, kind: "file" });
        } else {
          setCode(null);
          setSidecar({ state: "overview" });
        }
      })
      .catch((e) => setError(describeError(e)));
  }

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
