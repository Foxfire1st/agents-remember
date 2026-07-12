import { useEffect, useState } from "react";

import type { TaskDocNode } from "../types/projection";

import { fetchTaskDocument } from "./taskDocuments";

export type TaskDocumentBodyState = "loading" | "available" | "unavailable";

const taskDocumentBodyKey = (
  doc: Pick<TaskDocNode, "docPath" | "bodyRevision"> | undefined,
): string => (doc ? `${doc.docPath}\n${doc.bodyRevision ?? ""}` : "");

const mergeTaskDocumentBody = (
  summary: TaskDocNode,
  body: Partial<TaskDocNode>,
): TaskDocNode => ({
  ...summary,
  ...body,
  steps: body.steps ?? summary.steps,
  requirements: body.requirements ?? summary.requirements,
  codeExamples: body.codeExamples ?? summary.codeExamples,
  decisions: body.decisions ?? summary.decisions,
  openQuestions: body.openQuestions ?? summary.openQuestions,
  references: body.references ?? summary.references,
  subTasks: body.subTasks ?? summary.subTasks,
  sections: body.sections ?? summary.sections,
});

export function useTaskDocumentBody(targetDoc: TaskDocNode | undefined): {
  documentFor: (doc: TaskDocNode) => TaskDocNode;
  state: TaskDocumentBodyState | undefined;
} {
  const [documents, setDocuments] = useState<Record<string, TaskDocNode>>({});
  const [states, setStates] = useState<Record<string, Exclude<TaskDocumentBodyState, "loading">>>(
    {},
  );
  const targetKey = taskDocumentBodyKey(targetDoc);
  const cachedTarget = targetKey ? documents[targetKey] : undefined;

  useEffect(() => {
    if (!targetDoc || !targetKey || cachedTarget) return;
    let live = true;
    void fetchTaskDocument(targetDoc.docPath).then(
      (body) => {
        if (!live) return;
        setDocuments((current) =>
          current[targetKey]
            ? current
            : { ...current, [targetKey]: mergeTaskDocumentBody(targetDoc, body) },
        );
        setStates((current) => ({ ...current, [targetKey]: "available" }));
      },
      () => {
        if (live) setStates((current) => ({ ...current, [targetKey]: "unavailable" }));
      },
    );
    return () => {
      live = false;
    };
  }, [targetDoc, targetKey, cachedTarget]);

  const state = !targetDoc
    ? undefined
    : cachedTarget
      ? "available"
      : (states[targetKey] ?? "loading");

  return {
    documentFor: (doc) => documents[taskDocumentBodyKey(doc)] ?? doc,
    state,
  };
}
