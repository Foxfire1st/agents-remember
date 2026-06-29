// The Change-Set Viewer screen (L4). Opened as a task-scoped takeover from a DetailPanel button
// (Cockpit clears it via `onBack`, restoring the railed Operations view). Layout:
//   column 1  two rows — changed CODE files / changed ONBOARDING files (counts + status)
//   column 2  the selected file's diff (ChangeSetPane, always visible once a row is picked)
//   column 3  the code<->sidecar partner, opened from a "split" affordance
// A `scope` (one active enclosure) drives the full per-file diff via the L3 file-diff endpoint; a
// `master` drives the ACCUMULATED series summary (counts + leafCount), where per-file diffs are not
// available (the accumulation spans leaves, so there is no single scope to diff against — L3).
import { useEffect, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { css } from "../../../styled-system/css";
import {
  type ChangedFile,
  type FileDiff,
  type MasterChangeset,
  type TaskChangeset,
  fileDiff,
  masterChangeset,
  masterFileDiff,
  taskChangeset,
} from "../../data/changeset";
import { FilesApiError } from "../../data/files";
import { ChangeSetPane } from "./ChangeSetPane";

export interface ChangeSetTarget {
  repo: string;
  scope?: string; // one active enclosure (full diff)
  master?: string; // a series master (accumulated summary)
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
  _hover: { background: "bg" },
  "&[data-active=true]": { background: "bg" },
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

export function ChangeSetViewer({ repo, scope, master, onBack }: ChangeSetTarget & { onBack: () => void }) {
  const [data, setData] = useState<TaskChangeset | MasterChangeset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<{ kind: "code" | "memory"; path: string; hasSidecar?: boolean } | null>(null);
  const [diff, setDiff] = useState<FileDiff | null>(null);
  const [partner, setPartner] = useState<FileDiff | null>(null);
  const isMaster = Boolean(master);

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    setActive(null);
    setDiff(null);
    setPartner(null);
    const req = master ? masterChangeset(repo, master) : taskChangeset(repo, scope ?? "");
    void req.then(
      (d) => live && setData(d),
      (e: unknown) => live && setError(e instanceof FilesApiError ? `${e.code} (${e.httpStatus})` : "Failed to load change-set"),
    );
    return () => {
      live = false;
    };
  }, [repo, scope, master]);

  const partnerOf = (kind: "code" | "memory", path: string, hasSidecar?: boolean) =>
    kind === "code"
      ? hasSidecar
        ? { kind: "memory" as const, path: `onboarding/${path}.md` }
        : null
      : (() => {
          const code = partnerCodePath(path);
          return code ? { kind: "code" as const, path: code } : null;
        })();

  // Master mode diffs the NET series range (master base -> tip) via `master`; a leaf diffs its
  // enclosure `scope`. Both feed the same MergeView.
  const loadDiff = (kind: "code" | "memory", path: string) =>
    master ? masterFileDiff(repo, master, kind, path) : fileDiff(repo, scope ?? "", kind, path);

  const open = (kind: "code" | "memory", file: Row, withPartner = false) => {
    if (!isMaster && !scope) return;
    setActive({ kind, path: file.path, hasSidecar: file.hasSidecar });
    setDiff(null);
    setPartner(null);
    void loadDiff(kind, file.path).then(setDiff, () => setDiff(null));
    const partnerRef = withPartner ? partnerOf(kind, file.path, file.hasSidecar) : null;
    if (partnerRef) void loadDiff(partnerRef.kind, partnerRef.path).then(setPartner, () => setPartner(null));
  };

  const counters = data?.counters;

  return (
    <div className={screen} data-testid="changeset-viewer">
      <header className={header}>
        <button type="button" className={back} onClick={onBack} data-testid="changeset-back">
          ← back
        </button>
        <span className={title}>
          change-set · {isMaster ? `series ${master}` : scope} {isMaster ? "· net since series start" : ""}
        </span>
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
              <div className={placeholder} data-testid="pane-placeholder">
                Select a changed file
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
