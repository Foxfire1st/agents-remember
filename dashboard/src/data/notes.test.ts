import { afterEach, describe, expect, it, vi } from "vitest";

import { FilesApiError } from "./files";
import { listFolder, readNote, resolveNoteReferences } from "./notes";

function stubFetch(handler: (url: string, init?: unknown) => unknown) {
  const fn = vi.fn(async (url: string, init?: unknown) => ({
    ok: true,
    status: 200,
    statusText: "",
    json: async () => handler(url, init),
  }) as unknown as Response);
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("data/notes client", () => {
  it("builds the folder / read URLs", async () => {
    const fn = stubFetch(() => ({}));
    await listFolder("agents-remember", "260703_agent-orchestration", "reports", 200);
    await readNote("agents-remember", "260703_agent-orchestration", "reports/260703-L1.md");
    const urls = (fn.mock.calls as unknown as string[][]).map((c) => c[0]);
    expect(urls[0]).toBe(
      "/api/notes/folder?repo=agents-remember&master=260703_agent-orchestration&path=reports&offset=200",
    );
    expect(urls[1]).toBe(
      "/api/notes/read?repo=agents-remember&master=260703_agent-orchestration&path=reports%2F260703-L1.md",
    );
  });

  it("posts the document's references for resolution and keeps the resolved map", async () => {
    const fn = stubFetch(() => ({
      repo: "r",
      master: "m",
      resolutions: [
        { reference: "notes/a.md", path: "a.md" },
        { reference: "missing.md", path: null },
      ],
    }));
    const resolved = await resolveNoteReferences("r", "m", ["notes/a.md", "missing.md"]);
    expect(resolved).toEqual(new Map([["notes/a.md", "a.md"]]));
    const [url, init] = fn.mock.calls[0] as unknown as [string, { method: string; body: string }];
    expect(url).toBe("/api/notes/resolve");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ repo: "r", master: "m", references: ["notes/a.md", "missing.md"] });
  });

  it("throws the shared FilesApiError on a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 400, statusText: "", json: async () => ({ status: "bad-path" }) }) as unknown as Response),
    );
    await expect(readNote("r", "m", "../escape.md")).rejects.toBeInstanceOf(FilesApiError);
  });
});
