import { BoundedCache } from './boundedCache';

interface Kept {
  value: unknown;
  etag: string | null;
  facts: string;
}

/** Store-owned HTTP results. Revalidation never retains handlers or a failed read. */
export class ConditionalReads {
  private readonly values: BoundedCache<string, Kept>;
  private readonly pending = new Map<
    string,
    { facts: string; controller: AbortController; promise: Promise<unknown> }
  >();

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

  load<T>(url: string, decode: (response: Response) => Promise<T>, facts = url): Promise<T> {
    const pending = this.pending.get(url);
    if (pending?.facts === facts) return pending.promise as Promise<T>;
    pending?.controller.abort();
    this.pending.delete(url);
    if (this.pending.size >= this.capacity) {
      const oldest = this.pending.entries().next().value;
      if (oldest) {
        oldest[1].controller.abort();
        this.pending.delete(oldest[0]);
      }
    }
    const controller = new AbortController();
    const promise = this.read(url, facts, controller.signal, decode).finally(() => {
      if (this.pending.get(url)?.controller === controller) this.pending.delete(url);
    });
    this.pending.set(url, { facts, controller, promise });
    return promise;
  }

  private async read<T>(
    url: string,
    facts: string,
    signal: AbortSignal,
    decode: (response: Response) => Promise<T>,
  ): Promise<T> {
    const kept = this.values.get(url);
    try {
      const response = await fetch(url, {
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
