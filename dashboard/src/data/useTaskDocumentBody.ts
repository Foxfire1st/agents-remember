import { useEffect, useState } from 'react';

import type { TaskEntry, TaskSummary } from '../types/projection';

import { isAbortedRead } from './abortableRead';
import { taskBodyKey, type TaskBodyRecord, type TaskDocumentResources } from './taskDocuments';
import { useDashboard } from './store';

export type TaskDocumentBodyState = 'loading' | 'available' | 'unavailable';

// A summary preview restored to its whole string: where the summary names a
// truncation and the body carries strictly more, the body is the full detail
// (L127-C-F06); otherwise the summary stands, whole and fresher (L127-C-F01).
function fullText(
  summaryText: string | undefined,
  truncated: boolean | undefined,
  bodyText: string | undefined,
): { text: string | undefined; preview: boolean } {
  if (
    truncated &&
    bodyText !== undefined &&
    (summaryText === undefined || bodyText.length > summaryText.length)
  ) {
    return { text: bodyText, preview: false };
  }
  return { text: summaryText, preview: truncated ?? false };
}

function mergeCurrentStatus(
  summary: TaskSummary,
  body: Partial<TaskSummary>,
): Pick<TaskSummary, 'statusNote' | 'statusNoteTruncated' | 'headerNotes'> {
  const statusNote = fullText(summary.statusNote, summary.statusNoteTruncated, body.statusNote);
  const headerNotes =
    body.headerNotes && body.headerNotes.length > (summary.headerNotes ?? []).length
      ? body.headerNotes
      : summary.headerNotes;
  return { statusNote: statusNote.text, statusNoteTruncated: statusNote.preview, headerNotes };
}

function mergeCurrentSteps(summary: TaskSummary, body: Partial<TaskSummary>): TaskSummary['steps'] {
  if (
    !body.steps ||
    body.steps.length !== summary.steps.length ||
    !body.steps.every((step, index) => step.id === summary.steps[index].id)
  ) {
    return summary.steps;
  }
  return summary.steps.map((summaryStep, index) => {
    const bodyStep = body.steps?.[index];
    const note = fullText(summaryStep.note, summaryStep.noteTruncated, bodyStep?.note);
    const outcome = fullText(summaryStep.outcome, summaryStep.outcomeTruncated, bodyStep?.outcome);
    return {
      ...summaryStep,
      note: note.text,
      noteTruncated: note.preview,
      outcome: outcome.text,
      outcomeTruncated: outcome.preview,
    };
  });
}

function mergeCurrentReview(
  summary: TaskSummary,
  body: Partial<TaskSummary>,
): TaskSummary['reviewState'] {
  if (!summary.reviewState || body.reviewState?.round !== summary.reviewState.round) {
    return summary.reviewState;
  }
  const summaryFindings = summary.reviewState.findings;
  const bodyFindings = body.reviewState.findings ?? [];
  if (bodyFindings.length > summaryFindings.length) {
    return { ...summary.reviewState, findings: bodyFindings };
  }
  return {
    ...summary.reviewState,
    findings: summaryFindings.map((summaryFinding) => {
      const bodyFinding = bodyFindings.find(
        (finding) => finding.findingId === summaryFinding.findingId,
      );
      const description = fullText(
        summaryFinding.description,
        summaryFinding.descriptionTruncated,
        bodyFinding?.description,
      );
      return {
        ...summaryFinding,
        description: description.text ?? summaryFinding.description,
        descriptionTruncated: description.preview,
      };
    }),
  };
}

function mergeFixedProse(
  summary: TaskSummary,
  body: Partial<TaskSummary>,
): Pick<TaskSummary, 'nonGoals' | 'acceptance'> {
  return {
    nonGoals: body.nonGoals ?? summary.nonGoals,
    acceptance: body.acceptance ?? summary.acceptance,
  };
}

function mergeLongContent(
  summary: TaskSummary,
  body: Partial<TaskSummary>,
): Pick<
  TaskSummary,
  | 'objective'
  | 'nonGoals'
  | 'acceptance'
  | 'requirements'
  | 'design'
  | 'codeExamples'
  | 'decisions'
  | 'openQuestions'
  | 'references'
  | 'sections'
> {
  return {
    objective: body.objective ?? summary.objective,
    ...mergeFixedProse(summary, body),
    requirements: body.requirements ?? summary.requirements,
    design: body.design ?? summary.design,
    codeExamples: body.codeExamples ?? summary.codeExamples,
    decisions: body.decisions ?? summary.decisions,
    openQuestions: body.openQuestions ?? summary.openQuestions,
    references: body.references ?? summary.references,
    sections: body.sections ?? summary.sections,
  };
}

const mergeTaskDocumentBody = (summary: TaskSummary, body: Partial<TaskSummary>): TaskSummary => {
  // The fetched body is older than the current summary whenever the summary
  // changed after the fetch: the body contributes only its long-form reader
  // content, and every head/step/review fact comes from the current summary
  // (L127-C-F01). The revision invalidates the cache entry itself; this merge
  // keeps the already-open page correct while the refetch is in flight.
  // Where the body is current (same revision), it additionally restores the
  // fields the summary carries only as previews, so no reachable document
  // truth stays capped away (L127-C-F06).
  const merged: TaskSummary = { ...summary, ...mergeLongContent(summary, body) };
  if ((body.bodyRevision ?? summary.bodyRevision) !== summary.bodyRevision) {
    return merged;
  }
  return {
    ...merged,
    ...mergeCurrentStatus(summary, body),
    steps: mergeCurrentSteps(summary, body),
    reviewState: mergeCurrentReview(summary, body),
  };
};

interface SettledBody {
  key: string;
  failed: boolean;
  record?: TaskBodyRecord;
}

function availableBody(resources: TaskDocumentResources, key: string, settled?: SettledBody) {
  return resources.bodies.get(key) ?? (settled?.key === key ? settled.record : undefined);
}

export function useTaskDocumentBody(entry: TaskEntry | undefined): {
  document: TaskSummary | undefined;
  state: TaskDocumentBodyState | undefined;
} {
  const resources = useDashboard((state) => state.taskDocuments);
  const [settled, setSettled] = useState<SettledBody>();
  const summary = entry?.summary;
  const key = bodyKeyFor(entry);
  // A body beyond the retention budget still belongs on the active page. It
  // lives only with this reader; navigation cannot retain an oversized entry.
  const active = availableBody(resources, key, settled);
  const repository = entry?.reference?.repository;
  const path = entry?.reference?.path;
  const revision = summary?.bodyRevision;
  useEffect(() => {
    if (!repository || !path || revision === undefined || active) return;
    let live = true;
    // A task switch (or a page that goes away) ends this body's request with it,
    // so its bytes never arrive for a reader that is gone and leave no body in
    // the store (MIK-R176 rule 1).
    const controller = new AbortController();
    void resources.load({ repository, path }, revision, '', controller.signal).then(
      (record) => {
        if (live)
          setSettled(
            record.body.bodyRevision === revision
              ? { key, failed: false, record }
              : { key, failed: true },
          );
      },
      (cause: unknown) => {
        if (live && !isAbortedRead(cause)) setSettled({ key, failed: true });
      },
    );
    return () => {
      live = false;
      controller.abort();
    };
  }, [resources, repository, path, revision, key, active]);
  return {
    document: mergeAvailableBody(summary, active?.body),
    state: resolveBodyState(
      Boolean(summary),
      Boolean(active),
      settled?.key === key && settled.failed,
    ),
  };
}

function resolveBodyState(
  present: boolean,
  cached: boolean,
  failed: boolean | undefined,
): TaskDocumentBodyState | undefined {
  if (!present) return undefined;
  if (cached) return 'available';
  return failed ? 'unavailable' : 'loading';
}

function bodyKeyFor(entry: TaskEntry | undefined): string {
  return entry?.reference && entry.summary
    ? taskBodyKey(entry.reference, entry.summary.bodyRevision)
    : '';
}

function mergeAvailableBody(
  summary: TaskSummary | undefined,
  body: TaskSummary | undefined,
): TaskSummary | undefined {
  return summary && body ? mergeTaskDocumentBody(summary, body) : summary;
}
