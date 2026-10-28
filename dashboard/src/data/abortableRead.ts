/** Reads whose owner went away: an unmount or a task switch, never a failure to show. */

export const abortedRead = (): DOMException => new DOMException('Read aborted', 'AbortError');

export const isAbortedRead = (cause: unknown): boolean =>
  cause instanceof DOMException && cause.name === 'AbortError';

/**
 * Follow one owner's signal without cancelling a shared read another reader still wants.
 *
 * A store deduplicates reads by URL, so a second reader may have joined this promise.
 * When this owner's signal fires, only this owner's wait ends; the shared fetch keeps
 * running for whoever asked for it, and its completed answer is retained for them.
 */
export function withAbort<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(abortedRead());
    if (signal.aborted) {
      abort();
      return;
    }
    signal.addEventListener('abort', abort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener('abort', abort);
        resolve(value);
      },
      (cause: unknown) => {
        signal.removeEventListener('abort', abort);
        reject(cause);
      },
    );
  });
}

interface InFlightRead {
  promise: Promise<unknown>;
  controller: AbortController;
  // Mounted consumers still waiting for this answer. A consumer with no signal never leaves.
  holders: number;
  // Whether the request is on the wire, and whether it went there in the current synchronous pass.
  started: boolean;
  fresh: boolean;
}

const inFlight = new Map<string, InFlightRead>();

/**
 * One request per identical read in flight, cancelled on the wire once no consumer needs it.
 *
 * Every consumer asking the same `key` while its request is in flight joins that request instead of
 * sending another. When no mounted consumer wants the answer any more the request itself is aborted,
 * so the server sees the disconnect and stops its work -- at once, unless the request went on the wire
 * in this same synchronous pass: React StrictMode releases and re-acquires an effect inside one pass,
 * so that release waits one microtask and the re-acquired consumer joins the request the first one
 * asked for -- one request, never one sent and aborted. An answer is never kept after it settles: a
 * later read asks again.
 *
 * `defer` starts the request one microtask after it is first asked for instead of at once, so a
 * consumer that leaves in the same pass with nobody re-joining starts no network work at all.
 */
export function sharedRead<T>(
  key: string,
  start: (signal: AbortSignal | undefined) => Promise<T>,
  signal?: AbortSignal,
  defer = false,
): Promise<T> {
  if (signal?.aborted) return Promise.reject(abortedRead());
  const entry = inFlight.get(key) ?? openRead(key, start, signal !== undefined, defer);
  entry.holders += 1;
  if (!signal) return entry.promise as Promise<T>;
  const cancel = () => {
    if (entry.holders > 0) return;
    if (inFlight.get(key) === entry) inFlight.delete(key);
    entry.controller.abort();
  };
  const release = () => {
    entry.holders -= 1;
    if (entry.holders > 0) return;
    if (entry.started && !entry.fresh) cancel();
    else queueMicrotask(cancel);
  };
  // Kept after the answer arrives, as a consumer's own signal on its own request would be: once the
  // last consumer leaves, the request's signal says so even when the response is already in.
  signal.addEventListener('abort', release, { once: true });
  return withAbort(entry.promise as Promise<T>, signal);
}

// A request opened by a consumer with no signal is never cancelled (that consumer never leaves), so it
// is started with no signal at all, exactly as that consumer would have asked for it alone.
function openRead<T>(
  key: string,
  start: (signal: AbortSignal | undefined) => Promise<T>,
  cancellable: boolean,
  defer: boolean,
): InFlightRead {
  const controller = new AbortController();
  const entry: InFlightRead = {
    controller,
    holders: 0,
    started: false,
    fresh: false,
    promise: Promise.resolve(),
  };
  const send = () => {
    entry.started = true;
    entry.fresh = true;
    queueMicrotask(() => {
      entry.fresh = false;
    });
    return start(cancellable ? controller.signal : undefined);
  };
  entry.promise = (async () => {
    if (!defer) return send();
    await Promise.resolve();
    // Every consumer left before the request started (an unmount, a superseded question): no
    // network work at all, and no later reader may join this abandoned answer.
    if (controller.signal.aborted || entry.holders === 0) {
      if (inFlight.get(key) === entry) inFlight.delete(key);
      throw abortedRead();
    }
    return send();
  })().finally(() => {
    if (inFlight.get(key) === entry) inFlight.delete(key);
  });
  // Every consumer handles its own rejection; the shared promise itself is never left unhandled.
  entry.promise.catch(() => undefined);
  inFlight.set(key, entry);
  return entry;
}

/**
 * Stop letting new reads join the requests in flight now, because what they answer may predate a
 * change just announced (a memory edit, a publication, a moved comparison). Requests already joined
 * keep running for their consumers; the renewal a change starts sends a request of its own.
 */
export function detachInFlightReads(prefix = ''): void {
  for (const key of [...inFlight.keys()]) if (key.startsWith(prefix)) inFlight.delete(key);
}
