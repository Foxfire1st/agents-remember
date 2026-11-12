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
import { useEffect, useEffectEvent, useState } from 'react';

import { cx } from '../../styled-system/css';
import { listFolder, resolveNoteReferences } from '../data/notes';
import {
  requirementAddressFromReference,
  resolveRequirementReference,
  type RequirementEntry,
} from '../data/requirements';
import { docRefPath, listTaskReports, type TaskReports } from '../data/taskReports';
import type { TaskArtifactReaderTarget as NotesReaderTarget } from '../data/taskArtifacts';
import { Markdown } from '../grammar/Markdown';
import { useTaskRequirementLinks } from '../grammar/TaskRequirementLinks';
import { shortWhen } from './detail-panel/model';
import { Arrow, Block, MoreLink, Row, Rows, SubHead, plural } from './detail-panel/pageParts';
import { cellRight, cellTwo, dim, lineRow, link, linkPlain, none } from './detail-panel/pageStyles';

// A reference written as one Markdown link: the words to show and the place they lead to.
const WHOLE_LINK = /^\[([^\]]+)\]\(([^)\s]+)\)$/;

// One reference (MIK-R127 rule 10): a link is drawn as a link — its words open the note or
// the requirement packet where the server resolved one, and the place it names stands
// beside it, dimmed. A reference that is not one link keeps its own inline Markdown.
function ReferenceRow({
  reference,
  position,
  noteTarget,
  requirementTarget,
  openNote,
  openRequirement,
}: {
  reference: string;
  position: number;
  noteTarget: string | undefined;
  requirementTarget: string | undefined;
  openNote: (path: string) => void;
  openRequirement: (path: string) => void;
}) {
  const whole = WHOLE_LINK.exec(reference.trim());
  const words = whole ? <span>{whole[1]}</span> : <Markdown inline>{reference}</Markdown>;
  const place = whole ? <span className={dim}>{whole[2]}</span> : null;
  if (requirementTarget) {
    return (
      <Row>
        <button
          type="button"
          className={cx(link, whole ? undefined : cellTwo)}
          onClick={() => openRequirement(requirementTarget)}
          data-testid={`requirement-ref-${position}`}
          title={`open requirements/${requirementTarget}`}
        >
          {words}
        </button>
        {place}
      </Row>
    );
  }
  if (noteTarget) {
    return (
      <Row>
        <button
          type="button"
          className={cx(link, whole ? undefined : cellTwo)}
          onClick={() => openNote(noteTarget)}
          data-testid={`note-ref-${position}`}
          title={`open notes/${noteTarget}`}
        >
          {words}
        </button>
        {place}
      </Row>
    );
  }
  return (
    <Row>
      <span className={whole ? undefined : cellTwo} data-testid={`plain-ref-${position}`}>
        {words}
      </span>
      {place}
    </Row>
  );
}

function ReferenceRows({
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
    <Rows cols="link" testid="reference-rows">
      {references.map((reference, index) => {
        const requirementAddress = requirementAddressFromReference(reference);
        return (
          <ReferenceRow
            key={`${index}:${reference}`}
            reference={reference}
            position={index + 1}
            requirementTarget={resolveRequirementReference(reference, requirements)}
            noteTarget={requirementAddress ? undefined : resolvedNotes.get(reference)}
            openNote={openNote}
            openRequirement={openRequirement}
          />
        );
      })}
    </Rows>
  );
}

function NotesLine({ count, openRoot }: { count: number; openRoot: () => void }) {
  return (
    <Block title="Notes" block="notes">
      <div className={lineRow}>
        <button
          type="button"
          className={link}
          onClick={openRoot}
          data-testid="notes-root-link"
          title="open the notes reader at the notes root"
        >
          <span>Open the notes explorer</span>
          <Arrow />
        </button>
        <span className={dim} data-testid="notes-root-count">
          {plural(count, 'entry', 'entries')} in the top folder
        </span>
      </div>
    </Block>
  );
}

function TaskReportRows({
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
    <>
      {reports.reports.length > 0 ? (
        <Rows cols="pair" testid="task-report-rows">
          {reports.reports.map((row) => (
            <Row key={row.requestId}>
              <button
                type="button"
                className={cx(link, linkPlain)}
                onClick={() => (row.available ? openReport(row) : undefined)}
                disabled={!row.available}
                data-testid={`task-report-${row.requestId}`}
                title={
                  row.available ? `open the ${row.role} report` : 'the report is not written yet'
                }
              >
                {row.role} · {row.requestId.slice(0, 8)}
              </button>
              <span className={cx(dim, cellRight)} title={row.createdAt}>
                {shortWhen(row.createdAt)}
              </span>
            </Row>
          ))}
        </Rows>
      ) : (
        <p className={none} data-testid="task-reports-empty">
          No role reports recorded for this task.
        </p>
      )}
      {explorer ? (
        <MoreLink
          onClick={() => openExplorer(explorer)}
          testid="task-reports-explorer-link"
          title="open the reports folder in the notes explorer"
        >
          All {total.toLocaleString()} reports in the explorer
        </MoreLink>
      ) : null}
    </>
  );
}

// The task's own surface: references, its recorded role reports (tasks only), and one
// Notes line. The root-folder request and the reference resolution are the page's only
// notes requests; neither grows with the files under the notes root.
export interface TaskNotesData {
  resolved: Map<string, string>;
  notesFailed: boolean;
  requirements: RequirementEntry[];
  openNote: (path: string) => void;
  openRequirement: (path: string) => void;
  reports: TaskReports | null;
  reportsFailed: boolean;
  topCount: number | null;
  openFolder: (folder: string) => void;
  openReport: (row: { role: string; requestId: string }) => void;
  openExplorer: (folder: string) => void;
  openRoot: () => void;
}

// The recorded role reports of one task document (tasks only): one bounded
// list request keyed by the document, never by the files under any folder.
function useTaskReports(
  repo: string,
  master: string,
  docPath: string | undefined,
): { reports: TaskReports | null; reportsFailed: boolean } {
  const [reports, setReports] = useState<TaskReports | null>(null);
  const [reportsFailed, setReportsFailed] = useState(false);
  useEffect(() => {
    let live = true;
    setReports(null);
    setReportsFailed(false);
    if (!docPath) return () => {};
    const controller = new AbortController();
    void listTaskReports(repo, master, docPath, "", controller.signal).then(
      (data) => {
        if (!live) return;
        // A payload without report rows is unreadable: the record says the
        // reports could not be listed instead of crashing the page.
        if (!data || !Array.isArray(data.reports)) {
          setReportsFailed(true);
          return;
        }
        setReports(data);
      },
      () => {
        if (live) setReportsFailed(true);
      },
    );
    return () => {
      live = false;
      controller.abort();
    };
  }, [repo, master, docPath]);
  return { reports, reportsFailed };
}

// One resolution request holding the document's references (MIK-R127 rule 5).
// Shared by the whole-page read and the task page's placed sections so the
// affordance never diverges.
function useResolvedNotes(repo: string, master: string, references: string[]) {
  const [resolved, setResolved] = useState<Map<string, string>>(new Map());
  const [failed, setFailed] = useState(false);
  // The references array may be a new identity each render; key on its content so the
  // one resolution request fires per content, not per render.
  const referencesKey = references.join('\0');
  // The request names the list as it stands when that key changes; the list's
  // identity is no reason of its own to ask again.
  const listNow = useEffectEvent(() => references);
  useEffect(() => {
    let live = true;
    setResolved(new Map());
    setFailed(false);
    const controller = new AbortController();
    void resolveNoteReferences(repo, master, listNow(), "", controller.signal).then(
      (resolutions) => {
        if (live) setResolved(resolutions);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
      controller.abort();
    };
  }, [repo, master, referencesKey]);
  return { resolved, failed };
}

// The top notes folder entry count: one bounded request, never a tree walk.
// Null names a missing folder, an unreachable API, or a still-loading read.
function useNotesFolderTotal(repo: string, master: string) {
  const [topCount, setTopCount] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    setTopCount(null);
    setFailed(false);
    const controller = new AbortController();
    void listFolder(repo, master, '', 0, '', controller.signal).then(
      (folder) => {
        if (!live) return;
        setTopCount(folder.exists ? folder.total : null);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
      controller.abort();
    };
  }, [repo, master]);
  return { topCount, failed };
}
export function useTaskNotesData(
  repo: string,
  master: string,
  docPath: string | undefined,
  references: string[],
  onOpenNotes?: (target: NotesReaderTarget) => void,
): TaskNotesData {
  const { topCount, failed: folderFailed } = useNotesFolderTotal(repo, master);
  const { resolved, failed: resolveFailed } = useResolvedNotes(repo, master, references);
  const requirementLinks = useTaskRequirementLinks();
  // Either request failing degrades exactly as the joint read did: references
  // without a resolution render as plain text, and the Notes line stays back.
  const notesFailed = folderFailed || resolveFailed;

  const { reports, reportsFailed } = useTaskReports(repo, master, docPath);

  const openNote = (path: string) => onOpenNotes?.({ kind: 'notes', repo, master, path });
  const openFolder = (folder: string) =>
    onOpenNotes?.({ kind: 'notes', repo, master, path: folder, folder: true });
  return {
    resolved,
    notesFailed,
    requirements: requirementLinks?.requirements ?? [],
    openNote,
    openRequirement: (path) => requirementLinks?.open(path),
    reports,
    reportsFailed,
    topCount,
    openFolder,
    openReport: (row) =>
      onOpenNotes?.({
        kind: 'task-report',
        repo,
        master,
        role: row.role,
        requestId: row.requestId,
        taskRepository: repo,
        taskPath: docPath ? docRefPath(repo, docPath) : docRefPath(repo, ''),
      }),
    openExplorer: openFolder,
    openRoot: () => openFolder(''),
  };
}

export type TaskNotesRefsData = Pick<
  TaskNotesData,
  'resolved' | 'notesFailed' | 'requirements' | 'openNote' | 'openRequirement'
>;

export type TaskNotesReportsData = Pick<
  TaskNotesData,
  'reports' | 'reportsFailed' | 'openReport' | 'openExplorer'
>;

// The task page places references/reports before Further notes with Notes
// last, so it reads two disjoint islands: references+reports here, the Notes
// line below. The requests stay the page's bounded three (resolution,
// reports, folder count) with no duplication; the whole-page read above keeps
// its joint shape.
export function useTaskPageNotes(
  repo: string,
  master: string,
  docPath: string | undefined,
  references: string[],
  onOpenNotes?: (target: NotesReaderTarget) => void,
): { refs: TaskNotesRefsData; reports: TaskNotesReportsData } {
  const { resolved, failed } = useResolvedNotes(repo, master, references);
  const { reports, reportsFailed } = useTaskReports(repo, master, docPath);
  const requirementLinks = useTaskRequirementLinks();
  const openNote = (path: string) => onOpenNotes?.({ kind: 'notes', repo, master, path });
  const openFolder = (folder: string) =>
    onOpenNotes?.({ kind: 'notes', repo, master, path: folder, folder: true });
  return {
    refs: {
      resolved,
      notesFailed: failed,
      requirements: requirementLinks?.requirements ?? [],
      openNote,
      openRequirement: (path) => requirementLinks?.open(path),
    },
    reports: {
      reports,
      reportsFailed,
      openReport: (row) =>
        onOpenNotes?.({
          kind: 'task-report',
          repo,
          master,
          role: row.role,
          requestId: row.requestId,
          taskRepository: repo,
          taskPath: docPath ? docRefPath(repo, docPath) : docRefPath(repo, ''),
        }),
      openExplorer: openFolder,
    },
  };
}

export function useTaskPageNotesLine(
  repo: string,
  master: string,
  onOpenNotes?: (target: NotesReaderTarget) => void,
): { topCount: number | null; openRoot: () => void } {
  const { topCount } = useNotesFolderTotal(repo, master);
  return {
    topCount,
    openRoot: () => onOpenNotes?.({ kind: 'notes', repo, master, path: '', folder: true }),
  };
}

// References with the server-resolved affordance (MIK-R127 rule 5): the first
// `limit` lines inline plus a counted link to all where more exist. An
// unreachable notes API renders plain text, as the entry surface always has.
// The rows carry no heading: the block or the view that holds them names them once.
export function TaskNotesReferencesSection({
  data,
  references,
  limit,
  onOpenAll,
}: {
  data: TaskNotesRefsData;
  references: string[];
  limit?: number;
  onOpenAll?: () => void;
}) {
  if (references.length === 0) return null;
  const shown = limit === undefined ? references : references.slice(0, limit);
  return (
    <>
      <ReferenceRows
        references={shown}
        resolvedNotes={data.notesFailed ? new Map() : data.resolved}
        requirements={data.requirements}
        openNote={data.openNote}
        openRequirement={data.openRequirement}
      />
      {limit !== undefined && references.length > limit && onOpenAll ? (
        <MoreLink onClick={onOpenAll} testid="references-all-link" title="open all references">
          all {references.length} references
        </MoreLink>
      ) : null}
    </>
  );
}

// The recorded role reports, newest first. Null while the list is not read yet.
export function TaskNotesReportsSection({ data }: { data: TaskNotesReportsData }) {
  if (data.reportsFailed) {
    return (
      <p className={none} data-testid="task-reports-failed">
        The reports could not be listed.
      </p>
    );
  }
  if (!data.reports) return null;
  return (
    <TaskReportRows
      reports={data.reports}
      total={data.reports.total}
      explorer={data.reports.explorer}
      openReport={data.openReport}
      openExplorer={data.openExplorer}
    />
  );
}

// The task page's one block of references and reports (MIK-R127 rule 5, MIK-R126 rule 5):
// one heading, the references of the task under it, then the reports of its role agents.
export function TaskReferencesAndReports({
  refs,
  reports,
  references,
  limit,
  onOpenAll,
}: {
  refs: TaskNotesRefsData;
  reports: TaskNotesReportsData;
  references: string[];
  limit?: number;
  onOpenAll?: () => void;
}) {
  const listed = reports.reportsFailed || reports.reports !== null;
  if (references.length === 0 && !listed) return null;
  const count = reports.reports?.reports.length ?? 0;
  return (
    <Block title="References and reports" block="references">
      {references.length > 0 ? (
        <>
          <SubHead title="References of this task" count={references.length} />
          <TaskNotesReferencesSection
            data={refs}
            references={references}
            limit={limit}
            onOpenAll={onOpenAll}
          />
        </>
      ) : null}
      {listed ? (
        <>
          <SubHead
            title="Reports of its role agents"
            count={count > 0 ? `${count}, newest first` : undefined}
            testid="task-reports-head"
          />
          <TaskNotesReportsSection data={reports} />
        </>
      ) : null}
    </Block>
  );
}

export function TaskNotesNotesSection({
  data,
}: {
  data: Pick<TaskNotesData, 'topCount' | 'openRoot'>;
}) {
  if (data.topCount === null) return null;
  return <NotesLine count={data.topCount} openRoot={data.openRoot} />;
}

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
  const data = useTaskNotesData(repo, master, docPath, references, onOpenNotes);
  const noReports: TaskNotesReportsData = {
    reports: null,
    reportsFailed: false,
    openReport: data.openReport,
    openExplorer: data.openExplorer,
  };
  return (
    <>
      <TaskReferencesAndReports
        refs={data}
        reports={docPath && !data.notesFailed ? data : noReports}
        references={references}
      />
      {data.notesFailed ? null : <TaskNotesNotesSection data={data} />}
    </>
  );
}
