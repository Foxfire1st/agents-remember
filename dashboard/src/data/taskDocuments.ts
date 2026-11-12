import type { TaskDocumentRef, TaskSummary } from '../types/projection';
import { abortedRead, withAbort } from './abortableRead';
import { BoundedCache } from './boundedCache';
import { taskReferenceKey } from './taskReference';

export const taskBodyKey = (reference: TaskDocumentRef, revision: string): string =>
  `${taskReferenceKey(reference)}\n${revision}`;

export interface TaskBodyRecord {
  body: TaskSummary;
  etag: string | null;
  bytes: number;
}

interface PendingBody {
  controller: AbortController;
  promise: Promise<TaskBodyRecord>;
  waiters: number;
}

/**
 * Owned by the dashboard store, surviving reader unmounts and navigation.
 *
 * A body fetch belongs to the store, not to the reader that started it (L174-R1-01):
 * a reader's signal ends only that reader's wait, the request stops when the last
 * waiter leaves, and the entry is removed at once so an immediate replacement starts
 * its own fetch instead of joining an aborted one.
 */
export class TaskDocumentResources {
  readonly bodies = new BoundedCache<string, TaskBodyRecord>(64, 16 * 1024 * 1024);
  private readonly views = new BoundedCache<string, object>(64, 16 * 1024 * 1024);
  private readonly pending = new Map<string, PendingBody>();

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

  load(
    reference: TaskDocumentRef,
    revision: string,
    base = '',
    signal?: AbortSignal,
  ): Promise<TaskBodyRecord> {
    if (signal?.aborted) return Promise.reject(abortedRead());
    const key = taskBodyKey(reference, revision);
    const cached = this.bodies.get(key);
    if (cached) return Promise.resolve(cached);
    let entry = this.pending.get(key);
    if (!entry) {
      if (this.pending.size >= 64) {
        const oldest = this.pending.keys().next().value;
        if (oldest !== undefined) this.stop(oldest);
      }
      const controller = new AbortController();
      const started: PendingBody = {
        controller,
        waiters: 0,
        promise: Promise.resolve({} as TaskBodyRecord),
      };
      started.promise = fetchTaskDocument(reference, base, controller.signal)
        .then((record) => {
          if (controller.signal.aborted)
            throw new DOMException('Superseded task body read', 'AbortError');
          // A response can cross a newer projection. Its bytes belong only to the
          // revision it actually carries, never the revision that started the call.
          this.bodies.set(taskBodyKey(reference, record.body.bodyRevision), record, record.bytes);
          return record;
        })
        .finally(() => {
          if (this.pending.get(key) === started) this.pending.delete(key);
        });
      this.pending.set(key, started);
      entry = started;
    }
    const joined = entry;
    joined.waiters += 1;
    const leave = () => {
      joined.waiters -= 1;
      if (joined.waiters <= 0) this.stop(key, joined);
    };
    return withAbort(joined.promise, signal).finally(leave);
  }

  /** End one body fetch and forget it at once, so nobody joins an aborted request. */
  private stop(key: string, only?: PendingBody): void {
    const entry = this.pending.get(key);
    if (!entry || (only !== undefined && entry !== only)) return;
    this.pending.delete(key);
    entry.controller.abort();
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
