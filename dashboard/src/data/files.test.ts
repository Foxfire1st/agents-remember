import { afterEach, describe, expect, it, vi } from "vitest";

import {
  FilesApiError,
  FILES_REPOS_REQUEST_TIMEOUT_MS,
  fetchRepos,
  listDir,
  readFile,
  resolveForward,
  resolveReverse,
} from "./files";

// A half-dead socket (the hung-transport class): never settles on its own; rejects only when
// the request's abort signal fires — what a REAL fetch does when the fetchWithTimeout bound
// aborts it.
const hungSocketImplementation = (_url: string, init?: RequestInit) =>
  new Promise((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () =>
      reject(new DOMException("The operation was aborted.", "AbortError")),
    );
  });

function stubFetch(payload: unknown, ok = true, status = 200) {
  const fn = vi.fn(
    async () => ({ ok, status, statusText: "", json: async () => payload }) as unknown as Response,
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("data/files client", () => {
  it("builds the catalog / list / read / onboarding URLs", async () => {
    const fn = stubFetch({});
    await fetchRepos();
    await listDir("agents-remember", "mainline", "dashboard/src");
    await readFile("agents-remember", "files-api-ar", "x.ts");
    await resolveForward("agents-remember", "mainline", "x.ts");
    await resolveReverse("agents-remember", "mainline", "onboarding/x.ts.md");
    const urls = (fn.mock.calls as unknown as string[][]).map((c) => c[0]);
    expect(urls[0]).toBe("/api/files/repos");
    expect(urls[1]).toBe("/api/files/list?repo=agents-remember&scope=mainline&path=dashboard%2Fsrc");
    expect(urls[2]).toBe("/api/files/read?repo=agents-remember&scope=files-api-ar&path=x.ts");
    expect(urls[3]).toContain("direction=forward");
    expect(urls[4]).toContain("direction=reverse");
  });

  it("throws FilesApiError carrying the server status code on a non-ok response", async () => {
    stubFetch({ status: "bad-path" }, false, 400);
    await expect(listDir("r", "mainline", "..")).rejects.toBeInstanceOf(FilesApiError);
  });
});

describe("fetchRepos single-flight (data/inflight)", () => {
  it("shares one in-flight request between concurrent callers; both resolve", async () => {
    const catalog = { repos: [{ repo: "agents-remember" }] };
    const fn = stubFetch(catalog);
    const one = fetchRepos();
    const two = fetchRepos();
    expect(fn).toHaveBeenCalledTimes(1);
    await expect(one).resolves.toEqual(catalog);
    await expect(two).resolves.toEqual(catalog);
  });

  it("delivers the same rejection to every caller, then clears so a retry re-fires", async () => {
    const fn = vi.fn(async () => {
      throw new Error("offline");
    });
    vi.stubGlobal("fetch", fn);
    const one = fetchRepos();
    const two = fetchRepos();
    await expect(one).rejects.toThrow("offline");
    await expect(two).rejects.toThrow("offline");
    expect(fn).toHaveBeenCalledTimes(1);
    // The rejected read released the slot: a later call re-fires the request.
    await expect(fetchRepos()).rejects.toThrow("offline");
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("re-fires after a successful read settles (single-flight, not a cache)", async () => {
    const fn = stubFetch({ repos: [] });
    await fetchRepos();
    await fetchRepos();
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("a hung catalog socket rejects within the bound, clearing the slot for a fresh retry", async () => {
    vi.useFakeTimers();
    try {
      const catalog = { repos: [] };
      const fn = vi
        .fn()
        .mockImplementationOnce(hungSocketImplementation)
        .mockImplementationOnce(
          async () =>
            ({ ok: true, status: 200, statusText: "", json: async () => catalog }) as unknown as Response,
        );
      vi.stubGlobal("fetch", fn);

      const hung = fetchRepos();
      // Attach the rejection expectation BEFORE advancing time so the abort rejection never
      // surfaces as an unhandled rejection.
      const expired = expect(hung).rejects.toThrow(/abort/i);
      await vi.advanceTimersByTimeAsync(FILES_REPOS_REQUEST_TIMEOUT_MS);
      await expired; // the bound turned the wedge into a rejection

      // The rejection released the slot: the retry fires a fresh request and succeeds.
      await expect(fetchRepos()).resolves.toEqual(catalog);
      expect(fn).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
