// Shared own-view bodies for the three task-page skeletons (MIK-R127 rule 6).
// Every reader routes its text/record views through DocContentView, so a
// requirement, reference, note, objective, design, section, register or
// findings view reads the same bytes on every form; each reader keeps only its
// collection views (index groups, sprint rows, code examples). Small
// dispatchers keep every function inside the repository lint budget.
import { Markdown } from '../../grammar/Markdown';
import { TaskNotesReferencesSection, useTaskNotesData } from '../TaskNotes';
import type { TaskArtifactReaderTarget as NotesReaderTarget } from '../../data/taskArtifacts';
import type { TaskSummary } from '../../types/projection';
import type { DocumentTaskEntry } from '../../data/taskReference';
import { dirName } from './model';
import {
  DocViewShell,
  FindingsViewBody,
  HeaderNotesViewBody,
  RegisterViewBody,
  RequirementsViewBody,
  type DocView,
} from './skeletons';

export interface DocContentHandlers {
  onBack: () => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}

// A projected document or a series-derived master view: every content view
// reads only the shared reader fields.
export type ContentDoc = TaskSummary;

function ReferencesView({
  entry,
  doc,
  onOpenNotes,
  onBack,
}: {
  entry: DocumentTaskEntry;
  doc: ContentDoc;
  onOpenNotes?: (target: NotesReaderTarget) => void;
  onBack: () => void;
}) {
  // Resolved on open: the same server-resolved affordance as the page's own
  // reference previews (MIK-R127 rule 5), never inert text.
  const data = useTaskNotesData(
    entry.reference.repository,
    dirName(entry.reference.path),
    undefined,
    doc.references,
    onOpenNotes,
  );
  return (
    <DocViewShell title="References" count={`${doc.references.length}`} onBack={onBack}>
      <TaskNotesReferencesSection data={data} references={doc.references} />
    </DocViewShell>
  );
}

function TextViews({
  entry,
  view,
  doc,
  onBack,
  onOpenNotes,
}: {
  view: DocView;
  entry: DocumentTaskEntry;
  doc: ContentDoc;
  onBack: () => void;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  if (view.kind === 'requirements') {
    return (
      <DocViewShell title="Requirements" count={`${doc.requirements.length}`} onBack={onBack}>
        <RequirementsViewBody requirements={doc.requirements} />
      </DocViewShell>
    );
  }
  if (view.kind === 'references') {
    return <ReferencesView entry={entry} doc={doc} onOpenNotes={onOpenNotes} onBack={onBack} />;
  }
  if (view.kind === 'headerNotes') {
    return (
      <DocViewShell title="Header notes" count={`${doc.headerNotes.length}`} onBack={onBack}>
        <HeaderNotesViewBody doc={doc} />
      </DocViewShell>
    );
  }
  if (view.kind === 'objective' || view.kind === 'design') {
    const title = view.kind === 'objective' ? 'Objective' : 'Design';
    const text = view.kind === 'objective' ? doc.objective : (doc.design ?? '');
    return (
      <DocViewShell title={title} count={`${text.split('\n').length} lines`} onBack={onBack}>
        <Markdown>{text}</Markdown>
      </DocViewShell>
    );
  }
  return null;
}

function RecordViews({
  view,
  doc,
  onBack,
}: {
  view: DocView;
  doc: ContentDoc;
  onBack: () => void;
}) {
  if (view.kind === 'section' || view.kind === 'register') {
    // Every section byte one link away (MIK-R127 rule 7): the full section
    // body with its prose and tables, never the parsed preview rows.
    const section = doc.sections.find((entry) => entry.heading === view.heading);
    if (!section) {
      return (
        <DocViewShell title="Section" onBack={onBack}>
          <p className="muted">This section is no longer in the document.</p>
        </DocViewShell>
      );
    }
    return (
      <DocViewShell
        title={section.heading}
        count={`${section.body.length} characters`}
        onBack={onBack}
      >
        <RegisterViewBody section={section} />
      </DocViewShell>
    );
  }
  if (view.kind === 'findings' && doc.reviewState) {
    return (
      <DocViewShell
        title="Review findings"
        count={`${doc.reviewState.openFindings} of ${doc.reviewState.totalFindings} open · round ${doc.reviewState.round}`}
        onBack={onBack}
      >
        <FindingsViewBody review={doc.reviewState} />
      </DocViewShell>
    );
  }
  return null;
}

export function DocContentView({
  entry,
  view,
  doc,
  handlers,
  scope,
}: {
  view: DocView;
  entry: DocumentTaskEntry;
  doc: ContentDoc;
  handlers: DocContentHandlers;
  scope: 'sprint' | 'master' | 'task';
}) {
  if (view.kind === 'register' || view.kind === 'section' || view.kind === 'findings') {
    return <RecordViews view={view} doc={doc} onBack={handlers.onBack} />;
  }
  if (
    view.kind === 'requirements' ||
    view.kind === 'references' ||
    view.kind === 'headerNotes' ||
    view.kind === 'objective' ||
    view.kind === 'design'
  ) {
    return (
      <TextViews
        entry={entry}
        view={view}
        doc={doc}
        onBack={handlers.onBack}
        onOpenNotes={handlers.onOpenNotes}
      />
    );
  }
  return (
    <DocViewShell title="View" onBack={handlers.onBack}>
      <p className="muted">This view does not apply to a {scope}.</p>
    </DocViewShell>
  );
}
