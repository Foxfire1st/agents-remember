import { useEffect, useState } from 'react';

import type { TaskDocNode } from '../types/projection';

import { fetchTaskDocument } from './taskDocuments';

export type TaskDocumentBodyState = 'loading' | 'available' | 'unavailable';

const taskDocumentBodyKey = (
  doc: Pick<TaskDocNode, 'docPath' | 'bodyRevision'> | undefined,
): string => (doc ? `${doc.docPath}\n${doc.bodyRevision}` : '');

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
  summary: TaskDocNode,
  body: Partial<TaskDocNode>,
): Pick<TaskDocNode, 'statusNote' | 'statusNoteTruncated' | 'headerNotes'> {
  const statusNote = fullText(summary.statusNote, summary.statusNoteTruncated, body.statusNote);
  const headerNotes =
    body.headerNotes && body.headerNotes.length > (summary.headerNotes ?? []).length
      ? body.headerNotes
      : summary.headerNotes;
  return { statusNote: statusNote.text, statusNoteTruncated: statusNote.preview, headerNotes };
}

function mergeCurrentSteps(summary: TaskDocNode, body: Partial<TaskDocNode>): TaskDocNode['steps'] {
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
  summary: TaskDocNode,
  body: Partial<TaskDocNode>,
): TaskDocNode['reviewState'] {
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
  summary: TaskDocNode,
  body: Partial<TaskDocNode>,
): Pick<TaskDocNode, 'nonGoals' | 'acceptance'> {
  return {
    nonGoals: body.nonGoals ?? summary.nonGoals,
    acceptance: body.acceptance ?? summary.acceptance,
  };
}

function mergeLongContent(
  summary: TaskDocNode,
  body: Partial<TaskDocNode>,
): Pick<
  TaskDocNode,
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
  | 'subTasks'
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
    subTasks: body.subTasks ?? summary.subTasks,
  };
}

const mergeTaskDocumentBody = (summary: TaskDocNode, body: Partial<TaskDocNode>): TaskDocNode => {
  // The fetched body is older than the current summary whenever the summary
  // changed after the fetch: the body contributes only its long-form reader
  // content, and every head/step/review fact comes from the current summary
  // (L127-C-F01). The revision invalidates the cache entry itself; this merge
  // keeps the already-open page correct while the refetch is in flight.
  // Where the body is current (same revision), it additionally restores the
  // fields the summary carries only as previews, so no reachable document
  // truth stays capped away (L127-C-F06).
  const merged: TaskDocNode = { ...summary, ...mergeLongContent(summary, body) };
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

export function useTaskDocumentBody(targetDoc: TaskDocNode | undefined): {
  documentFor: (doc: TaskDocNode) => TaskDocNode;
  state: TaskDocumentBodyState | undefined;
} {
  const [bodies, setBodies] = useState<Record<string, Partial<TaskDocNode>>>({});
  const [states, setStates] = useState<Record<string, Exclude<TaskDocumentBodyState, 'loading'>>>(
    {},
  );
  const targetKey = taskDocumentBodyKey(targetDoc);
  const targetPath = targetDoc?.docPath;
  const cachedBody = targetKey ? bodies[targetKey] : undefined;

  useEffect(() => {
    if (!targetPath || !targetKey || cachedBody) return;
    let live = true;
    void fetchTaskDocument(targetPath).then(
      (body) => {
        if (!live) return;
        setBodies((current) => (current[targetKey] ? current : { ...current, [targetKey]: body }));
        setStates((current) => ({ ...current, [targetKey]: 'available' }));
      },
      () => {
        if (live) setStates((current) => ({ ...current, [targetKey]: 'unavailable' }));
      },
    );
    return () => {
      live = false;
    };
  }, [targetPath, targetKey, cachedBody]);

  const state = !targetDoc
    ? undefined
    : cachedBody
      ? 'available'
      : (states[targetKey] ?? 'loading');

  return {
    documentFor: (doc) => {
      const body = bodies[taskDocumentBodyKey(doc)];
      return body ? mergeTaskDocumentBody(doc, body) : doc;
    },
    state,
  };
}
