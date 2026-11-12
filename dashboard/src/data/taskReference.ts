import type { TaskDocumentRef, TaskEntry } from "../types/projection";

export type DocumentTaskEntry = TaskEntry & { reference: TaskDocumentRef };

/** The same qualified document key used by the server and role-agent binding. */
export const taskReferenceKey = (reference: TaskDocumentRef): string =>
  `${reference.repository}/${reference.path}`;

export const taskSelectionKey = (reference: TaskDocumentRef): string =>
  `taskdoc:${taskReferenceKey(reference)}`;

/** An entry key is opaque; rows without a document have no document reference. */
export const taskEntrySelectionKey = (key: string): string => `taskdoc:${key}`;
