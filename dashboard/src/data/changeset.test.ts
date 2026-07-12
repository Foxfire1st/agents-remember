import { afterEach, describe, expect, it, vi } from "vitest";

import { fileDiff, leafChangeset, leafFileDiff, masterChangeset, taskChangeset } from "./changeset";
import { FilesApiError } from "./files";

function stubFetch(payload: unknown, ok = true, status = 200) {
  const fn = vi.fn(
    async () => ({ ok, status, statusText: "", json: async () => payload }) as unknown as Response,
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("data/changeset client", () => {
  it("builds the task / file-diff / master URLs (reusing the files.ts wrapper)", async () => {
    const fn = stubFetch({});
    await taskChangeset("agents-remember", "files-api-ar");
    await fileDiff("agents-remember", "files-api-ar", "memory", "onboarding/x.ts.md");
    await masterChangeset("agents-remember", "260628_operations-integration", {
      includeLeaves: false,
    });
    const urls = (fn.mock.calls as unknown as string[][]).map((c) => c[0]);
    expect(urls[0]).toBe("/api/changeset/task?repo=agents-remember&scope=files-api-ar");
    expect(urls[1]).toBe(
      "/api/changeset/file-diff?repo=agents-remember&scope=files-api-ar&kind=memory&path=onboarding%2Fx.ts.md",
    );
    expect(urls[2]).toBe(
      "/api/changeset/master?repo=agents-remember&master=260628_operations-integration&includeLeaves=false",
    );
  });

  it("builds the L4a leaf committed/working URLs (same task & file-diff routes, leaf+mode selector)", async () => {
    const fn = stubFetch({});
    await leafChangeset("agents-remember", "260628_operations-integration", "260628-L4a", "committed");
    await leafFileDiff(
      "agents-remember",
      "260628_operations-integration",
      "260628-L4a",
      "code",
      "mcp/x.py",
      "working",
    );
    const urls = (fn.mock.calls as unknown as string[][]).map((c) => c[0]);
    expect(urls[0]).toBe(
      "/api/changeset/task?repo=agents-remember&master=260628_operations-integration&leaf=260628-L4a&mode=committed",
    );
    expect(urls[1]).toBe(
      "/api/changeset/file-diff?repo=agents-remember&master=260628_operations-integration&leaf=260628-L4a&kind=code&path=mcp%2Fx.py&mode=working",
    );
  });

  it("throws FilesApiError carrying the server status on a 404 (e.g. a completed task / no worktree)", async () => {
    stubFetch({ status: "not-found" }, false, 404);
    await expect(taskChangeset("agents-remember", "gone")).rejects.toBeInstanceOf(FilesApiError);
  });
});
