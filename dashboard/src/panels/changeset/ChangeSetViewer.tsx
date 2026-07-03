// The Change-Set Viewer screen (L4). Opened as a task-scoped takeover from a DetailPanel button
// (Cockpit clears it via `onBack`, restoring the railed Operations view). Layout:
//   column 1  two rows — changed CODE files / changed ONBOARDING files (counts + status)
//   column 2  the selected file's diff (ChangeSetPane, always visible once a row is picked)
//   column 3  the code<->sidecar partner, opened from a "split" affordance
// The target selects the range (precedence leaf > master > scope), all rendered the same way and all
// inspectable per file: a `scope` (one active enclosure) = base->worktree; a `master` = the series
// NET base->tip; a `leaf` (+ `mode`) = that leaf's committed (base->code_commit) or working
// (HEAD->worktree, uncommitted) delta — the L4a doc-reader views, which need no live enclosure.
import { useEffect, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { css } from "../../../styled-system/css";
import {
  type ChangedFile,
  type FileDiff,
  type LeafMode,
  type MasterChangeset,
  type TaskChangeset,
  fileDiff,
  leafChangeset,
  leafFileDiff,
  masterChangeset,
  masterFileDiff,
  taskChangeset,
} from "../../data/changeset";
import { FilesApiError } from "../../data/files";
import { EmptyStateBackdrop } from "../EmptyStateBackdrop";
import { ChangeSetPane } from "./ChangeSetPane";

export interface ChangeSetTarget {
  repo: string;
  scope?: string; // one active enclosure (full base->worktree diff)
  master?: string; // a series master (net base->tip); also QUALIFIES a `leaf`
  leaf?: string; // a single leaf (committed/working), resolved by leaf-id; needs `master` + `mode`
  mode?: LeafMode; // committed = landed delta (base->code_commit), working = uncommitted delta (live)
}

const screen = css({
  height: "100%",
  minHeight: "0",
  display: "flex",
  flexDirection: "column",
  background: "bg",
});
const header = css({
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  gap: "0.8rem",
  paddingInline: "0.8rem",
  paddingBlock: "0.4rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  background: "bgPanel",
});
const back = css({
  fontFamily: "mono",
  fontSize: "0.74rem",
  color: "amber",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.5rem",
  paddingBlock: "0.15rem",
  cursor: "pointer",
  _hover: { borderColor: "amber" },
});
const title = css({ fontSize: "0.82rem", color: "ink", fontWeight: "600" });
const counterRow = css({ display: "flex", gap: "0.8rem", marginLeft: "auto", fontSize: "0.74rem", fontFamily: "mono" });
const ins = css({ color: "mint" });
const del = css({ color: "amber" });
const colList = css({ height: "100%", minHeight: "0", display: "flex", flexDirection: "column", background: "bgPanel" });
const section = css({ flex: "1", minHeight: "0", overflow: "auto" });
const sectionHead = css({
  position: "sticky",
  top: "0",
  background: "bg",
  paddingInline: "0.6rem",
  paddingBlock: "0.25rem",
  fontSize: "0.68rem",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  color: "amber",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
});
const row = css({
  display: "flex",
  alignItems: "center",
  gap: "0.3rem",
  paddingInline: "0.6rem",
  // Match the File Viewer tree's selected/hover amber wash — the old `background: bg` active state was
  // indistinguishable from the panel, so the selected file looked unselected.
  _hover: { background: "color-mix(in oklab, var(--amber) 12%, transparent)" },
  "&[data-active=true]": { background: "color-mix(in oklab, var(--amber) 20%, transparent)" },
});
const rowMain = css({
  flex: "1",
  minWidth: "0",
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  textAlign: "left",
  paddingBlock: "0.2rem",
  fontSize: "0.74rem",
  fontFamily: "mono",
  color: "ink",
  background: "transparent",
  border: "0",
  cursor: "pointer",
  _hover: { color: "amber" },
  _disabled: { cursor: "default" },
});
const pathText = css({ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
const statusChip = css({ width: "1.1em", textAlign: "center", color: "cyan" });
const counts = css({ marginLeft: "auto", display: "flex", gap: "0.4rem" });
const sidecarBtn = css({
  fontSize: "0.64rem",
  color: "cyan",
  border: "1px solid token(colors.grid)",
  borderRadius: "2px",
  paddingInline: "0.25rem",
  background: "transparent",
  cursor: "pointer",
  _hover: { borderColor: "cyan" },
});
const handle = css({ width: "3px", flexShrink: "0", background: "grid", cursor: "col-resize", _hover: { background: "amber" } });
const placeholder = css({ height: "100%", display: "grid", placeItems: "center", padding: "1rem", color: "muted", fontSize: "0.8rem", textAlign: "center" });
// EmptyStateBackdrop's flex:1 canvas needs a flex-column host to fill the diff Panel.
const emptyHost = css({ height: "100%", minHeight: "0", display: "flex", flexDirection: "column" });

type Row = ChangedFile & { leafCount?: number };

function Counts({ file }: { file: Row }) {
  return (
    <span className={counts}>
      <span className={ins}>+{file.insertions ?? "·"}</span>
      <span className={del}>−{file.deletions ?? "·"}</span>
    </span>
  );
}

// A memory path under onboarding/ for a 1:1 sidecar -> its partner code path (strip onboarding/ + .md).
function partnerCodePath(memPath: string): string | null {
  if (!memPath.startsWith("onboarding/") || !memPath.endsWith(".md")) return null;
  const base = memPath.slice("onboarding/".length, -3);
  if (base.endsWith("/overview") || base.endsWith("/entities") || base.endsWith(".index")) return null;
  return base;
}

export function ChangeSetViewer({ repo, scope, master, leaf, mode, onBack }: ChangeSetTarget & { onBack: () => void }) {
  const [data, setData] = useState<TaskChangeset | MasterChangeset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<{ kind: "code" | "memory"; path: string; hasSidecar?: boolean } | null>(null);
  const [diff, setDiff] = useState<FileDiff | null>(null);
  const [partner, setPartner] = useState<FileDiff | null>(null);
  // Selection precedence leaf > master > scope: a `leaf` is one leaf's committed/working delta
  // (qualified by `master`); `master` alone is the series net; otherwise an enclosure `scope`.
  const isLeaf = Boolean(leaf);
  const isSeries = Boolean(master) && !leaf;

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    setActive(null);
    setDiff(null);
    setPartner(null);
    const req = leaf
      ? leafChangeset(repo, master ?? "", leaf, mode ?? "committed")
      : master
        ? masterChangeset(repo, master)
        : taskChangeset(repo, scope ?? "");
    void req.then(
      (d) => live && setData(d),
      (e: unknown) => live && setError(e instanceof FilesApiError ? `${e.code} (${e.httpStatus})` : "Failed to load change-set"),
    );
    return () => {
      live = false;
    };
  }, [repo, scope, master, leaf, mode]);

  // L4a: the WORKING view is the LIVE uncommitted delta, so it must not be a frozen snapshot taken
  // when the button was clicked. Poll the change-set on an interval so a file edited *after* opening
  // appears in the list (and the counters track), AND re-fetch the file currently open in the diff
  // column so an edit to the file you are LOOKING AT updates in place. The open-diff re-fetch is cheap
  // and non-disruptive: CodeMirror only rebuilds when the before/after content actually changed, so an
  // unchanged poll is a no-op (no flicker / scroll-reset) — it only re-renders when that file is the
  // one edited, which is exactly when you want it to. Only `working` polls — committed/series/scope
  // are immutable snapshots of committed state. (A server push would need a worktree watcher + SSE; a
  // client interval is self-contained and enough on localhost.)
  useEffect(() => {
    if (mode !== "working" || !leaf) return;
    const m = master ?? "";
    const id = setInterval(() => {
      void leafChangeset(repo, m, leaf, "working").then(
        (d) => setData(d),
        () => {}, // a transient fetch error (e.g. mid-git-op) keeps the last good list
      );
      if (active) {
        void leafFileDiff(repo, m, leaf, active.kind, active.path, "working").then(
          (d) => setDiff(d),
          () => {},
        );
      }
    }, 2500);
    return () => clearInterval(id);
  }, [mode, leaf, repo, master, active]);

  const partnerOf = (kind: "code" | "memory", path: string, hasSidecar?: boolean) =>
    kind === "code"
      ? hasSidecar
        ? { kind: "memory" as const, path: `onboarding/${path}.md` }
        : null
      : (() => {
          const code = partnerCodePath(path);
          return code ? { kind: "code" as const, path: code } : null;
        })();

  // Each selector diffs its own range, all into the same MergeView: a leaf its committed/working
  // range, `master` the NET series range (base -> tip), an enclosure `scope` its base -> worktree.
  const loadDiff = (kind: "code" | "memory", path: string) =>
    leaf
      ? leafFileDiff(repo, master ?? "", leaf, kind, path, mode ?? "committed")
      : master
        ? masterFileDiff(repo, master, kind, path)
        : fileDiff(repo, scope ?? "", kind, path);

  const open = (kind: "code" | "memory", file: Row, withPartner = false) => {
    if (!leaf && !master && !scope) return;
    setActive({ kind, path: file.path, hasSidecar: file.hasSidecar });
    setDiff(null);
    setPartner(null);
    void loadDiff(kind, file.path).then(setDiff, () => setDiff(null));
    const partnerRef = withPartner ? partnerOf(kind, file.path, file.hasSidecar) : null;
    if (partnerRef) void loadDiff(partnerRef.kind, partnerRef.path).then(setPartner, () => setPartner(null));
  };

  const counters = data?.counters;
  const headerLabel = isLeaf
    ? mode === "working"
      ? `working · ${leaf} · uncommitted`
      : `committed · ${leaf}`
    : isSeries
      ? `series ${master} · net since series start`
      : (scope ?? "");

  return (
    <div className={screen} data-testid="changeset-viewer">
      <header className={header}>
        <button type="button" className={back} onClick={onBack} data-testid="changeset-back">
          ← back
        </button>
        <span className={title}>change-set · {headerLabel}</span>
        {counters ? (
          <span className={counterRow} data-testid="changeset-counters">
            <span>
              code <span className={ins}>+{counters.code.insertions}</span>{" "}
              <span className={del}>−{counters.code.deletions}</span> ({counters.code.files})
            </span>
            <span>
              memory <span className={ins}>+{counters.memory.insertions}</span>{" "}
              <span className={del}>−{counters.memory.deletions}</span> ({counters.memory.files})
            </span>
          </span>
        ) : null}
      </header>

      {error ? (
        <div className={placeholder} data-testid="pane-placeholder">
          {error}
        </div>
      ) : (
        <PanelGroup direction="horizontal" autoSaveId="changeset.outer" className={css({ flex: "1", minHeight: "0" })}>
          <Panel defaultSize={26} minSize={16}>
            <div className={colList}>
              <div className={section}>
                <div className={sectionHead}>changed code ({data?.code.length ?? 0})</div>
                {(data?.code ?? []).map((f) => (
                  <div key={f.path} className={row} data-active={active?.kind === "code" && active.path === f.path}>
                    <button type="button" className={rowMain} onClick={() => open("code", f)}>
                      <span className={statusChip}>{f.status}</span>
                      <span className={pathText}>{f.path}</span>
                      <Counts file={f} />
                    </button>
                    {f.hasSidecar ? (
                      <button
                        type="button"
                        className={sidecarBtn}
                        title="open with its sidecar (3rd column)"
                        onClick={() => open("code", f, true)}
                        data-testid="changeset-open-sidecar"
                      >
                        ◇
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
              <div className={section}>
                <div className={sectionHead}>changed onboarding ({data?.memory.length ?? 0})</div>
                {(data?.memory ?? []).map((f) => (
                  <div key={f.path} className={row} data-active={active?.kind === "memory" && active.path === f.path}>
                    <button type="button" className={rowMain} onClick={() => open("memory", f)}>
                      <span className={statusChip}>{f.status}</span>
                      <span className={pathText}>{f.path}</span>
                      <Counts file={f} />
                    </button>
                    {partnerOf("memory", f.path) ? (
                      <button
                        type="button"
                        className={sidecarBtn}
                        title="open with its partner code file (3rd column)"
                        onClick={() => open("memory", f, true)}
                      >
                        ↔
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          </Panel>
          <PanelResizeHandle className={handle} />
          <Panel minSize={20}>
            {diff ? (
              <ChangeSetPane diff={diff} keyPrefix="changeset.main" />
            ) : (
              // No file picked yet: the same faint boomerang backdrop the File Viewer / Operations use.
              <div className={emptyHost}>
                {/* Brighter than the shared 0.14 default — the siege-tank clip reads darker; matches DualPane. */}
                <EmptyStateBackdrop src="/assets/sc2-siege-tank-boomerang.mp4" opacity={0.18}>
                  Select a changed file
                </EmptyStateBackdrop>
              </div>
            )}
          </Panel>
          {partner ? (
            <>
              <PanelResizeHandle className={handle} />
              <Panel minSize={20}>
                <ChangeSetPane diff={partner} keyPrefix="changeset.partner" />
              </Panel>
            </>
          ) : null}
        </PanelGroup>
      )}
    </div>
  );
}
