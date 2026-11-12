import { abortedRead, withAbort } from './abortableRead';
import { BoundedCache } from './boundedCache';
import { openingFetch } from './reviewOpen';

interface Kept {
  value: unknown;
  etag: string | null;
  facts: string;
}

interface PendingRead {
  facts: string;
  controller: AbortController;
  promise: Promise<unknown>;
  waiters: number;
}

/**
 * Store-owned HTTP results. Revalidation never retains handlers or a failed read.
 *
 * A fetch belongs to the store, not to the reader that started it (L174-R1-01): the
 * owner's signal ends only that owner's wait. The request stops when the last waiter
 * leaves, and the entry is removed at that moment so an immediate replacement starts a
 * fresh fetch instead of joining an already-aborted one.
 */
export class ConditionalReads {
  private readonly values: BoundedCache<string, Kept>;
  private readonly pending = new Map<string, PendingRead>();

  constructor(
    private readonly capacity: number,
    maxBytes = Infinity,
  ) {
    this.values = new BoundedCache(capacity, maxBytes);
  }

  peek<T>(url: string, facts = url): T | undefined {
    const kept = this.values.get(url);
    return kept?.facts === facts ? (kept.value as T) : undefined;
  }

  load<T>(
    url: string,
    decode: (response: Response) => Promise<T>,
    facts = url,
    signal?: AbortSignal,
  ): Promise<T> {
    if (signal?.aborted) return Promise.reject(abortedRead());
    let entry = this.pending.get(url);
    if (entry && entry.facts !== facts) {
      this.stop(url);
      entry = undefined;
    }
    if (!entry) {
      if (this.pending.size >= this.capacity) {
        const oldest = this.pending.keys().next().value;
        if (oldest !== undefined) this.stop(oldest);
      }
      const controller = new AbortController();
      const started: PendingRead = { facts, controller, waiters: 0, promise: Promise.resolve() };
      started.promise = this.read(url, facts, controller.signal, decode).finally(() => {
        if (this.pending.get(url) === started) this.pending.delete(url);
      });
      this.pending.set(url, started);
      entry = started;
    }
    const joined = entry;
    joined.waiters += 1;
    const leave = () => {
      joined.waiters -= 1;
      if (joined.waiters <= 0) this.stop(url, joined);
    };
    return withAbort(joined.promise as Promise<T>, signal).finally(leave);
  }

  /** End one fetch and forget it at once, so nobody joins an aborted request. */
  private stop(url: string, only?: PendingRead): void {
    const entry = this.pending.get(url);
    if (!entry || (only !== undefined && entry !== only)) return;
    this.pending.delete(url);
    entry.controller.abort();
  }

  private async read<T>(
    url: string,
    facts: string,
    signal: AbortSignal,
    decode: (response: Response) => Promise<T>,
  ): Promise<T> {
    const kept = this.values.get(url);
    try {
      const response = await openingFetch(url, {
        signal,
        ...(kept?.etag ? { headers: { 'If-None-Match': kept.etag } } : {}),
      });
      if (signal.aborted) throw new DOMException('Superseded read', 'AbortError');
      if (response.status === 304) {
        if (!kept) throw new Error('Conditional read answered 304 without retained bytes');
        kept.facts = facts;
        return kept.value as T;
      }
      const value = await decode(response);
      if (signal.aborted) throw new DOMException('Superseded read', 'AbortError');
      this.values.delete(url);
      if (response.ok) {
        this.values.set(
          url,
          { value, etag: response.headers.get('etag'), facts },
          new TextEncoder().encode(url + JSON.stringify(value)).byteLength,
        );
      }
      return value;
    } catch (cause) {
      // A replacement request owns its own bytes; a late aborted one cannot evict them.
      if (!signal.aborted) this.values.delete(url);
      throw cause;
    }
  }

  get retained(): { entries: number; bytes: number; pending: number } {
    return { ...this.values.retained, pending: this.pending.size };
  }
}
