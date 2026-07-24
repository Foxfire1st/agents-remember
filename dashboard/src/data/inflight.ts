// Shared single-flight for idempotent boot GET reads: React
// StrictMode double-fires every mount effect, and several always-mounted layers read the
// same resource at boot (File Viewer -> /api/files/repos, the rails -> /api/harnesses, the
// catalog poll -> /api/terminal/sessions). Concurrent callers of one key share the SAME
// in-flight promise — result AND rejection — so error behavior is identical for every
// caller. The slot is released on settle (success or failure): this is a single-flight,
// not a cache — a later read re-fires and a rejected read never blocks a retry.
// (Per-key mirror of the single-flight idiom in data/capabilityCatalog.ts.)
//
// Every `run` MUST be bounded at the transport (data/fetchWithTimeout.ts): a shared promise
// makes an unbounded hung socket fatal to every caller, not just the first — a timeout here
// could only stop WAITING on the promise, never kill the socket, so the bound lives in the
// fetch, not in this map.

const inflight = new Map<string, Promise<unknown>>();

/**
 * Run `run` at most once concurrently per `key`: every concurrent caller receives the
 * first caller's promise. The key is forgotten as soon as that promise settles.
 */
export function shareInflight<T>(key: string, run: () => Promise<T>): Promise<T> {
  const pending = inflight.get(key);
  if (pending) return pending as Promise<T>;
  const promise = run().finally(() => {
    // Identity-guarded release: only the read that owns the slot may clear it.
    if (inflight.get(key) === promise) inflight.delete(key);
  });
  inflight.set(key, promise);
  return promise;
}
