import { afterEach, describe, expect, it, vi } from "vitest";

import { FilesApiError, fetchRepos, listDir, readFile, resolveForward, resolveReverse } from "./files";

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
