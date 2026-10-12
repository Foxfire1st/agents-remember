// The Notes Reader screen: the coordination-notes reading experience rebuilt on the SAME
// full-view pattern the Change-Set / File Viewer use — a task-scoped takeover whose LEFT RAIL is
// the master's notes tree, with the open note highlighted, and whose content pane renders the
// opened note by REUSING the File Viewer's `DualPane` primitive:
//   - a markdown note takes DualPane's partnerless-markdown path (code=null, sidecar=markdown) — the
//     exact path the File Viewer uses to render a partnerless route overview full-pane;
//   - a text note takes DualPane's CodeSide (read-only CodeMirror), same as any code file;
//   - a binary note degrades to DualPane's byte-count placeholder.
// There is no second bespoke reader: the layout is the ChangeSetViewer rail+pane+back idiom and the
// content is `DualPane`. The view is CONTROLLED — the open `path` and rail `onSelectNote` are lifted
// to CockpitShell (like the File Viewer's persisted state) so selection survives back/forward.
//
// MIK-R126: the rail is the SHARED `ExplorerTree` (the File viewer's tree), fed one folder at a
// time — folders stay closed until opened, except the folders on the way to the open note, and a
// folder answered in parts shows a row that loads the next part. No second tree is written.
import { memo, useEffect, useState, type ReactNode } from "react";
import { ancestorsOf, isMoreRow, useNotesFolderCache } from "./useNotesFolderCache";
import type { RoleReportContent } from "../../data/roleReport";
import { readRoleReport } from "../../data/roleReport";
import type { LauncherRole } from "../../cockpit/roleLaunchModel";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { css } from "../../../styled-system/css";
import type { FileContent } from "../../data/files";
import { readNote, type NoteContent } from "../../data/notes";
import {
  listRequirements,
  readRequirement,
  type RequirementContent,
} from "../../data/requirements";
import type { TaskArtifactReaderTarget } from "../../data/taskArtifacts";
import type { TaskDecisionNode } from "../../types/projection";
import { orderDecisionsNewestFirst } from "../detail-panel/model";
import { ExplorerTree, type ExplorerRow } from "../../grammar/ExplorerTree";
import { DualPane, type SidecarView } from "../file-viewer/DualPane";
import { Markdown } from "../../grammar/Markdown";

export type { TaskArtifactReaderTarget } from "../../data/taskArtifacts";

// Screen chrome — the ChangeSetViewer takeover idiom (flex column · sticky back header · rail+pane).
const screen = css({ height: "100%", minHeight: "0", display: "flex", flexDirection: "column", background: "bg" });
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
const openPath = css({ marginLeft: "auto", fontSize: "0.74rem", fontFamily: "mono", color: "cyan", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "50%" });
// Rail — the ChangeSetViewer changed-files column idiom (sticky section head + amber-wash active row).
const railCol = css({ height: "100%", minHeight: "0", display: "flex", flexDirection: "column", background: "bgPanel" });
const railHead = css({
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
const railRow = css({
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  width: "100%",
  textAlign: "left",
  font: "inherit",
  fontSize: "0.74rem",
  fontFamily: "mono",
  color: "ink",
  background: "transparent",
  border: "0",
  paddingInline: "0.6rem",
  paddingBlock: "0.2rem",
  cursor: "pointer",
  _hover: { background: "color-mix(in oklab, var(--amber) 12%, transparent)", color: "amber" },
  "&[data-active=true]": { background: "color-mix(in oklab, var(--amber) 20%, transparent)", color: "cyan" },
});
const handle = css({ width: "3px", flexShrink: "0", background: "grid", cursor: "col-resize", _hover: { background: "amber" } });
const placeholder = css({ height: "100%", display: "grid", placeItems: "center", padding: "1rem", color: "muted", fontSize: "0.8rem", textAlign: "center" });
// Content-pane fill + the truncation banner (mirrors DualPane's own `fill`/`banner`): a truncated
// MARKDOWN note takes DualPane's partnerless-markdown path, which has no banner (only CodeSide does),
// so a >2-MiB markdown note -- the dominant note type -- would silently drop its "showing the first
// 2 MiB" contract. We render the same banner here, above the pane, for that case.
const paneFill = css({ height: "100%", minHeight: "0", display: "flex", flexDirection: "column" });
const paneBody = css({ flex: "1", minHeight: "0" });
const truncBanner = css({
  flexShrink: 0,
  paddingInline: "0.6rem",
  paddingBlock: "0.2rem",
  fontSize: "0.72rem",
  color: "amber",
  background: "bgPanel",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
});
const mutedDecision = css({ opacity: "0.55" });

// A fetched note mapped to DualPane's props: markdown renders full-pane as a partnerless markdown doc
// (the File Viewer's overview treatment); other text / binary render through CodeSide.
type ArtifactContent = RoleReportContent | NoteContent | (RequirementContent & { language: "markdown"; truncated: false });

function noteAsFileContent(note: ArtifactContent): FileContent {
  // CodeSide reads only language/size/truncated/content; scope/onboarding are inert for a note.
  return {
    scope: "",
    path: note.path,
    language: note.language,
    size: note.size,
    truncated: note.truncated,
    content: note.content,
    onboarding: { status: "missing" },
  };
}

function dualPaneProps(note: ArtifactContent): { code: FileContent | null; sidecar: SidecarView } {
  return note.language === "markdown"
    ? { code: null, sidecar: { state: "markdown", body: note.content } }
    : { code: noteAsFileContent(note), sidecar: { state: "empty" } };
}

function NotesTree({
  repo,
  master,
  current,
  onSelectNote,
}: {
  repo: string;
  master: string;
  current: string | undefined;
  onSelectNote: (path: string) => void;
}) {
  const { loadChildren, openEntry, revision } = useNotesFolderCache(repo, master, current, onSelectNote);
  // Ancestor folders of the open note, deepest last; the tree opens exactly these, so
  // folders stay closed until opened except on the way to the open note.
  const ancestors = current ? ancestorsOf(current) : [];
  return (
    <div className={railCol}>
      <div className={railHead}>notes ({master})</div>
      <ExplorerTree
        label={`notes of ${master}`}
        testid="notes-tree"
        root={{ name: master, path: "", kind: "dir" }}
        loadChildren={loadChildren}
        onOpen={openEntry}
        current={current}
        ancestors={ancestors}
        revision={String(revision)}
        rowAttributes={(entry: ExplorerRow) => ({
          "data-testid": isMoreRow(entry) ? "notes-more" : undefined,
          title: isMoreRow(entry) ? "load the next part of this folder" : entry.path,
        })}
      />
    </div>
  );
}

function RequirementsRail({
  repo,
  master,
  document,
  activePath,
  onSelectNote,
}: {
  repo: string;
  master: string;
  document: string;
  activePath: string;
  onSelectNote: (path: string) => void;
}) {
  const [entries, setEntries] = useState<{ path: string; name: string }[] | null>(null);
  useEffect(() => {
    let live = true;
    setEntries(null);
    void listRequirements(repo, master, document).then(
      (data) => {
        if (live) setEntries(data.requirements.map((entry) => ({ path: entry.path, name: entry.name })));
      },
      () => {},
    );
    return () => {
      live = false;
    };
  }, [document, master, repo]);
  return (
    <div className={railCol}>
      <div className={railHead}>requirements ({(entries ?? []).length})</div>
      {(entries ?? []).map((entry: { path: string; name: string }, index: number) => (
        <button
          key={entry.path}
          type="button"
          className={railRow}
          data-active={entry.path === activePath}
          data-testid={`note-rail-${index + 1}`}
          title={entry.path}
          onClick={() => onSelectNote(entry.path)}
        >
          {entry.name}
        </button>
      ))}
    </div>
  );
}

function NotePane({ failed, note, kind }: {
  failed: boolean;
  note: ArtifactContent | null;
  kind: TaskArtifactReaderTarget["kind"] | "role-report";
}) {
  if (failed) {
    return (
      <div className={placeholder} data-testid="note-status">
        Could not load this {kind === "notes" ? "note" : "requirement"}.
      </div>
    );
  }
  if (note === null) {
    return (
      <div className={placeholder} data-testid="note-status">
        Select a note from the tree.
      </div>
    );
  }
  return (
    <div className={paneFill}>
      {note.truncated && note.language === "markdown" ? (
        <div className={truncBanner} data-testid="notes-trunc-banner">
          Showing the first 2 MiB of {note.size.toLocaleString()} bytes
        </div>
      ) : null}
      <div className={paneBody}>
        <DualPane {...dualPaneProps(note)} split={false} />
      </div>
    </div>
  );
}

function DecisionsPane({ decisions }: { decisions: TaskDecisionNode[] }) {
  // The whole log on a page of its own, newest first by recorded time (not storage
  // order); product-written records muted.
  const newestFirst = orderDecisionsNewestFirst(decisions);
  return (
    <div className={paneFill}>
      <div className={paneBody} style={{ overflow: "auto" }}>
        <ul data-testid="decision-log-whole">
          {newestFirst.map((item) => (
            <li key={`${item.at}:${item.decision}`} className={item.product_record ? mutedDecision : undefined}>
              <div>
                <Markdown inline>{item.decision}</Markdown>
              </div>
              <div>
                {item.at} — <Markdown inline>{item.rationale}</Markdown>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function useArtifactContent(
  repo: string,
  master: string,
  document: string | undefined,
  path: string | undefined,
): { failed: boolean; content: ArtifactContent | null } {
  const [content, setContent] = useState<ArtifactContent | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    setContent(null);
    setFailed(false);
    if (path === undefined) return () => {};
    const request =
      document === undefined
        ? readNote(repo, master, path)
        : readRequirement(repo, master, document, path).then((data) => ({
            ...data,
            language: "markdown" as const,
            truncated: false as const,
          }));
    void request.then(
      (data) => live && setContent(data),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [document, master, path, repo]);
  return { failed, content };
}

function useTaskReportContent(
  role: string,
  requestId: string,
  taskRepository: string,
  taskPath: string,
): { failed: boolean; content: RoleReportContent | null } {
  const [content, setContent] = useState<RoleReportContent | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    setContent(null);
    setFailed(false);
    // Row roles come from the product's own recorded starts, so they name a launcher role.
    void readRoleReport({ role: role as LauncherRole, taskDocumentRef: { repository: taskRepository, path: taskPath } }, requestId).then(
      (data) => live && setContent(data),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [requestId, role, taskPath, taskRepository]);
  return { failed, content };
}

function NotesTargetReader({
  repo,
  master,
  path,
  folder,
  onSelectNote,
  onBack,
}: {
  repo: string;
  master: string;
  path: string | undefined;
  folder: boolean;
  onSelectNote: (path: string) => void;
  onBack: () => void;
}) {
  const { failed, content } = useArtifactContent(repo, master, undefined, folder ? undefined : path);
  return (
    <ReaderLayout
      kind="notes"
      heading={master}
      path={path ?? ""}
      onBack={onBack}
      failed={failed}
      content={content}
      rail={<NotesTree key={`${repo}\0${master}`} repo={repo} master={master} current={path} onSelectNote={onSelectNote} />}
    />
  );
}

function RequirementsTargetReader({
  repo,
  master,
  document,
  path,
  onSelectNote,
  onBack,
}: {
  repo: string;
  master: string;
  document: string;
  path: string;
  onSelectNote: (path: string) => void;
  onBack: () => void;
}) {
  const { failed, content } = useArtifactContent(repo, master, document, path);
  return (
    <ReaderLayout
      kind="requirements"
      heading={master}
      path={path}
      onBack={onBack}
      failed={failed}
      content={content}
      rail={
        <RequirementsRail
          repo={repo}
          master={master}
          document={document}
          activePath={path}
          onSelectNote={onSelectNote}
        />
      }
    />
  );
}

function TaskReportTargetReader({
  role,
  requestId,
  taskRepository,
  taskPath,
  onBack,
}: {
  role: string;
  requestId: string;
  taskRepository: string;
  taskPath: string;
  onBack: () => void;
}) {
  const { failed, content } = useTaskReportContent(role, requestId, taskRepository, taskPath);
  return (
    <ReaderLayout
      kind="role-report"
      heading="Report"
      path={requestId}
      onBack={onBack}
      failed={failed}
      content={content}
    />
  );
}

function NotesReaderViewerImpl(
  props: TaskArtifactReaderTarget & { onSelectNote: (path: string) => void; onBack: () => void },
) {
  const { onSelectNote, onBack } = props;
  switch (props.kind) {
    case "decisions":
      return (
        <ReaderLayout
          kind="decisions"
          heading={props.title}
          path=""
          onBack={onBack}
          failed={false}
          content={null}
          decisions={props.decisions}
        />
      );
    case "task-report":
      return (
        <TaskReportTargetReader
          role={props.role}
          requestId={props.requestId}
          taskRepository={props.taskRepository}
          taskPath={props.taskPath}
          onBack={onBack}
        />
      );
    case "requirements":
      return (
        <RequirementsTargetReader
          repo={props.repo}
          master={props.master}
          document={props.document}
          path={props.path}
          onSelectNote={onSelectNote}
          onBack={onBack}
        />
      );
    case "notes":
      return (
        <NotesTargetReader
          repo={props.repo}
          master={props.master}
          path={props.path}
          folder={props.folder === true}
          onSelectNote={onSelectNote}
          onBack={onBack}
        />
      );
  }
}

function ReaderLayout({ kind, heading, path, onBack, failed, content, rail, decisions }: {
  kind: TaskArtifactReaderTarget["kind"] | "role-report";
  heading: string;
  path: string;
  onBack: () => void;
  failed: boolean;
  content: ArtifactContent | null;
  rail?: ReactNode;
  decisions?: TaskDecisionNode[];
}) {
  return (
    <div className={screen} data-testid="notes-reader-viewer" data-artifact-kind={kind}>
      <header className={header}>
        <button type="button" className={back} onClick={onBack} data-testid="notes-reader-back">← back</button>
        <span className={title}>{kind === "role-report" ? "Report" : kind + " · " + heading}</span>
        <span className={openPath} data-testid="notes-reader-open">{kind === "role-report" ? path : kind + "/" + path}</span>
      </header>
      <PanelGroup direction="horizontal" autoSaveId="notesreader.outer" className={css({ flex: "1", minHeight: "0" })}>
        {rail ? <><Panel defaultSize={26} minSize={16}>{rail}</Panel><PanelResizeHandle className={handle} /></> : null}
        <Panel minSize={30}>
          {decisions ? <DecisionsPane decisions={decisions} /> : <NotePane failed={failed} note={content} kind={kind} />}
        </Panel>
      </PanelGroup>
    </div>
  );
}

type ReaderProps = (TaskArtifactReaderTarget & { onSelectNote: (path: string) => void; onBack: () => void }) |
  { kind: "role-report"; report: RoleReportContent; onBack: () => void };

function Reader(props: ReaderProps) {
  return props.kind === "role-report"
    ? <ReaderLayout kind="role-report" heading="Report" path={props.report.path} onBack={props.onBack} failed={false} content={props.report} />
    : <NotesReaderViewerImpl {...props} />;
}

// Memoized (tab-switch CPU): kept mounted (hidden) once opened — the shell re-renders on
// every view switch with unchanged props, and the memo gate skips this subtree then; the reader's
// own state still drives its updates.
export const NotesReaderViewer = memo(Reader);
