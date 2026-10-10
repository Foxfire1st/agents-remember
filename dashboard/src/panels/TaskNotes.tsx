// L9 → L126: the compact coordination-notes ENTRY SURFACE inside the task reader. The page
// lists no notes tree. It shows, beside one link, what belongs to the task:
//   - the document's references, resolved by ONE request holding the document's references
//     (each a link where it names an existing note or requirement; plain text otherwise);
//   - on a task (not a master), the reports of the role agents the product started for this
//     task — newest first, at most twenty — found by what the product recorded at each
//     start, with a link to their folder in the explorer where there are more;
//   - one "Notes" line naming how many entries the top folder holds, with a link that opens
//     the notes reader at the notes root. No total over the tree is ever computed.
// A master without a notes folder shows no Notes line and no error. An unreachable notes
// API renders references as plain text with no Notes line, as today; an unreadable report
// record says the reports could not be listed and keeps the Notes link.
import { useEffect, useState } from "react";

import { css } from "../../styled-system/css";
import { listFolder, resolveNoteReferences } from "../data/notes";
import {
  requirementAddressFromReference,
  resolveRequirementReference,
  type RequirementEntry,
} from "../data/requirements";
import { docRefPath, listTaskReports, type TaskReports } from "../data/taskReports";
import type { TaskArtifactReaderTarget as NotesReaderTarget } from "../data/taskArtifacts";
import { Markdown } from "../grammar/Markdown";
import { useTaskRequirementLinks } from "../grammar/TaskRequirementLinks";

const section = css({ display: "grid", gap: "0.3rem" });
const heading = css({
  margin: "0",
  fontSize: "0.72rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "amber",
});
const bullets = css({
  margin: "0",
  paddingLeft: "1.1rem",
  maxWidth: "78ch",
  display: "grid",
  gap: "0.2rem",
  fontSize: "0.84rem",
  lineHeight: "1.45",
});
// A resolved reference: the whole reference string becomes the openable link into the notes reader.
const refLink = css({
  font: "inherit",
  textAlign: "left",
  color: "cyan",
  background: "transparent",
  border: "0",
  padding: "0",
  cursor: "pointer",
  textDecoration: "underline",
  _hover: { color: "ink" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const noteLine = css({ fontSize: "0.84rem", color: "ink" });
const noteMeta = css({ color: "muted", fontSize: "0.72rem" });
const reportList = css({ listStyle: "none", margin: "0", padding: "0", display: "grid", gap: "0.15rem" });
const reportRow = css({
  display: "flex",
  justifyContent: "space-between",
  gap: "0.5rem",
  width: "100%",
  textAlign: "left",
  font: "inherit",
  fontSize: "0.78rem",
  paddingInline: "0.4rem",
  paddingBlock: "0.2rem",
  background: "bg",
  border: "0",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "grid",
  color: "ink",
  cursor: "pointer",
  _hover: { background: "oklch(0.7 0.1 200 / 0.12)", borderLeftColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

function ReferenceList({
  references,
  resolvedNotes,
  requirements,
  openNote,
  openRequirement,
}: {
  references: string[];
  resolvedNotes: Map<string, string>;
  requirements: RequirementEntry[];
  openNote: (path: string) => void;
  openRequirement: (path: string) => void;
}) {
  return (
    <section className={section}>
      <h3 className={heading}>References</h3>
      <ul className={bullets}>
        {references.map((reference, index) => {
          const requirementAddress = requirementAddressFromReference(reference);
          const requirementTarget = resolveRequirementReference(reference, requirements);
          const noteTarget = requirementAddress ? undefined : resolvedNotes.get(reference);
          return (
            <li key={reference}>
              {requirementTarget ? (
                <button
                  type="button"
                  className={refLink}
                  onClick={() => openRequirement(requirementTarget)}
                  data-testid={`requirement-ref-${index + 1}`}
                  title={`open requirements/${requirementTarget}`}
                >
                  {reference}
                </button>
              ) : noteTarget ? (
                <button
                  type="button"
                  className={refLink}
                  onClick={() => openNote(noteTarget)}
                  data-testid={`note-ref-${index + 1}`}
                  title={`open notes/${noteTarget}`}
                >
                  <Markdown inline>{reference}</Markdown>
                </button>
              ) : (
                <Markdown inline>{reference}</Markdown>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function NotesLine({
  count,
  openRoot,
}: {
  count: number;
  openRoot: () => void;
}) {
  return (
    <section className={section}>
      <h3 className={heading}>Notes</h3>
      <p className={noteLine}>
        <button
          type="button"
          className={refLink}
          onClick={openRoot}
          data-testid="notes-root-link"
          title="open the notes reader at the notes root"
        >
          Open the notes explorer
        </button>{" "}
        <span className={noteMeta} data-testid="notes-root-count">
          {count.toLocaleString()} {count === 1 ? "entry" : "entries"} in the top folder
        </span>
      </p>
    </section>
  );
}

function TaskReportsList({
  reports,
  total,
  explorer,
  openReport,
  openExplorer,
}: {
  reports: TaskReports;
  total: number;
  explorer: string | null;
  openReport: (row: { role: string; requestId: string }) => void;
  openExplorer: (folder: string) => void;
}) {
  return (
    <section className={section}>
      <h3 className={heading}>Reports</h3>
      {reports.reports.length > 0 ? (
        <ul className={reportList}>
          {reports.reports.map((row) => (
            <li key={row.requestId}>
              <button
                type="button"
                className={reportRow}
                onClick={() => (row.available ? openReport(row) : undefined)}
                disabled={!row.available}
                data-testid={`task-report-${row.requestId}`}
                title={row.available ? `open the ${row.role} report` : "the report is not written yet"}
              >
                <span>
                  {row.role} · {row.requestId.slice(0, 8)}
                </span>
                <span className={noteMeta}>{row.createdAt}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className={noteMeta} data-testid="task-reports-empty">
          No role reports recorded for this task.
        </p>
      )}
      {explorer ? (
        <p className={noteLine}>
          <button
            type="button"
            className={refLink}
            onClick={() => openExplorer(explorer)}
            data-testid="task-reports-explorer-link"
            title="open the reports folder in the notes explorer"
          >
            All {total.toLocaleString()} reports in the explorer
          </button>
        </p>
      ) : null}
    </section>
  );
}

// The task's own surface: references, its recorded role reports (tasks only), and one
// Notes line. The root-folder request and the reference resolution are the page's only
// notes requests; neither grows with the files under the notes root.
export function TaskNotes({
  repo,
  master,
  docPath,
  references,
  onOpenNotes,
}: {
  repo: string;
  master: string;
  // The task document's path under tasks/<repo>/ (e.g. "<master>/126_x.json"). Absent on
  // a master page, which shows its references and the Notes link but no report list.
  docPath?: string;
  references: string[];
  // Open the notes reader on a note, at a folder, or on a recorded task report. Optional
  // so a context without the takeover still renders the surface; rows are then inert.
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  const [topCount, setTopCount] = useState<number | null>(null);
  const [notesFailed, setNotesFailed] = useState(false);
  const [resolved, setResolved] = useState<Map<string, string>>(new Map());
  const [reports, setReports] = useState<TaskReports | null>(null);
  const [reportsFailed, setReportsFailed] = useState(false);
  const requirementLinks = useTaskRequirementLinks();
  // The references array may be a new identity each render; key on its content so the
  // one resolution request fires per content, not per render.
  const referencesKey = references.join("\0");

  useEffect(() => {
    let live = true;
    setTopCount(null);
    setNotesFailed(false);
    setResolved(new Map());
    void Promise.all([listFolder(repo, master, ""), resolveNoteReferences(repo, master, references)]).then(
      ([folder, resolutions]) => {
        if (!live) return;
        if (!folder.exists) {
          setTopCount(null);
        } else {
          setTopCount(folder.total);
        }
        setResolved(resolutions);
      },
      () => {
        if (live) setNotesFailed(true);
      },
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- references keyed by content above
  }, [repo, master, referencesKey]);

  useEffect(() => {
    let live = true;
    setReports(null);
    setReportsFailed(false);
    if (!docPath) return () => {};
    void listTaskReports(repo, master, docPath).then(
      (data) => {
        if (live) setReports(data);
      },
      () => {
        if (live) setReportsFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [repo, master, docPath]);

  const openNote = (path: string) => onOpenNotes?.({ kind: "notes", repo, master, path });
  const openFolder = (folder: string) =>
    onOpenNotes?.({ kind: "notes", repo, master, path: folder, folder: true });

  if (notesFailed) {
    return (
      <>
        {references.length > 0 ? (
          <ReferenceList
            references={references}
            resolvedNotes={new Map()}
            requirements={requirementLinks?.requirements ?? []}
            openNote={openNote}
            openRequirement={(path) => requirementLinks?.open(path)}
          />
        ) : null}
      </>
    );
  }

  return (
    <>
      {references.length > 0 ? (
        <ReferenceList
          references={references}
          resolvedNotes={resolved}
          requirements={requirementLinks?.requirements ?? []}
          openNote={openNote}
          openRequirement={(path) => requirementLinks?.open(path)}
        />
      ) : null}
      {docPath ? (
        reportsFailed ? (
          <section className={section}>
            <h3 className={heading}>Reports</h3>
            <p className={noteMeta} data-testid="task-reports-failed">
              The reports could not be listed.
            </p>
          </section>
        ) : reports ? (
          <TaskReportsList
            reports={reports}
            total={reports.total}
            explorer={reports.explorer}
            openReport={(row) =>
              onOpenNotes?.({
                kind: "task-report",
                repo,
                master,
                role: row.role,
                requestId: row.requestId,
                taskRepository: repo,
                taskPath: docRefPath(repo, docPath),
              })
            }
            openExplorer={openFolder}
          />
        ) : null
      ) : null}
      {topCount !== null ? <NotesLine count={topCount} openRoot={() => openFolder("")} /> : null}
    </>
  );
}
