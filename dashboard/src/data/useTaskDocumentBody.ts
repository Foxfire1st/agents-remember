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
  const [bodies, setBodies] = useState<Record<string, Partial<TaskDocNode>>>({});
  const [states, setStates] = useState<Record<string, Exclude<TaskDocumentBodyState, "loading">>>(
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
        setBodies((current) =>
          current[targetKey] ? current : { ...current, [targetKey]: body },
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
  }, [targetPath, targetKey, cachedBody]);

  const state = !targetDoc
    ? undefined
    : cachedBody
      ? "available"
      : (states[targetKey] ?? "loading");

  return {
    documentFor: (doc) => {
      const body = bodies[taskDocumentBodyKey(doc)];
      return body ? mergeTaskDocumentBody(doc, body) : doc;
    },
    state,
  };
}
