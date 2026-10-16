import type { TaskDocumentRef, TaskSummary } from '../types/projection';
import { BoundedCache } from './boundedCache';
import { taskReferenceKey } from './taskReference';

export const taskBodyKey = (reference: TaskDocumentRef, revision: string): string =>
  `${taskReferenceKey(reference)}\n${revision}`;

export interface TaskBodyRecord {
  body: TaskSummary;
  etag: string | null;
  bytes: number;
}

/** Owned by the dashboard store, surviving reader unmounts and navigation. */
export class TaskDocumentResources {
  readonly bodies = new BoundedCache<string, TaskBodyRecord>(64, 16 * 1024 * 1024);
  private readonly views = new BoundedCache<string, object>(64, 16 * 1024 * 1024);
  private readonly pending = new Map<
    string,
    { controller: AbortController; promise: Promise<TaskBodyRecord> }
  >();

  /** Revision-keyed pure preparation records; callbacks stay on the live reader. */
  prepare<T extends object>(key: string, make: () => T): T {
    const cached = this.views.get(key);
    if (cached) return cached as T;
    const prepared = make();
    this.views.set(
      key,
      prepared,
      new TextEncoder().encode(key + JSON.stringify(prepared)).byteLength,
    );
    return prepared;
  }

  get retainedViews(): { entries: number; bytes: number } {
    return this.views.retained;
  }

  load(reference: TaskDocumentRef, revision: string, base = ''): Promise<TaskBodyRecord> {
    const key = taskBodyKey(reference, revision);
    const cached = this.bodies.get(key);
    if (cached) return Promise.resolve(cached);
    const pending = this.pending.get(key);
    if (pending) return pending.promise;
    if (this.pending.size >= 64) {
      const oldest = this.pending.entries().next().value;
      if (oldest) {
        oldest[1].controller.abort();
        this.pending.delete(oldest[0]);
      }
    }
    const controller = new AbortController();
    const promise = fetchTaskDocument(reference, base, controller.signal)
      .then((record) => {
        if (controller.signal.aborted)
          throw new DOMException('Superseded task body read', 'AbortError');
        // A response can cross a newer projection. Its bytes belong only to the
        // revision it actually carries, never the revision that started the call.
        this.bodies.set(taskBodyKey(reference, record.body.bodyRevision), record, record.bytes);
        return record;
      })
      .finally(() => {
        if (this.pending.get(key)?.controller === controller) this.pending.delete(key);
      });
    this.pending.set(key, { controller, promise });
    return promise;
  }
}

export async function fetchTaskDocument(
  reference: TaskDocumentRef,
  base = '',
  signal?: AbortSignal,
): Promise<TaskBodyRecord> {
  const response = await fetch(
    `${base}/api/task-document?path=${encodeURIComponent(taskReferenceKey(reference))}`,
    { signal },
  );
  if (!response.ok) throw new Error(`task document fetch failed: ${response.status}`);
  const text = await response.text();
  return {
    body: JSON.parse(text) as TaskSummary,
    etag: response.headers.get('etag'),
    bytes: new TextEncoder().encode(text).byteLength,
  };
}
