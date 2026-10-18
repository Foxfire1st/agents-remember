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
