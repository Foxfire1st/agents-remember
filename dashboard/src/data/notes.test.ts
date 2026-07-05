import { afterEach, describe, expect, it, vi } from "vitest";

import { FilesApiError } from "./files";
import { listNotes, readNote, resolveNoteReference } from "./notes";

function stubFetch(payload: unknown, ok = true, status = 200) {
  const fn = vi.fn(
    async () => ({ ok, status, statusText: "", json: async () => payload }) as unknown as Response,
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("data/notes client", () => {
  it("builds the list / read URLs", async () => {
    const fn = stubFetch({});
    await listNotes("agents-remember", "260703_agent-orchestration");
    await readNote("agents-remember", "260703_agent-orchestration", "reports/260703-L1.md");
    const urls = (fn.mock.calls as unknown as string[][]).map((c) => c[0]);
    expect(urls[0]).toBe("/api/notes/list?repo=agents-remember&master=260703_agent-orchestration");
    expect(urls[1]).toBe(
      "/api/notes/read?repo=agents-remember&master=260703_agent-orchestration&path=reports%2F260703-L1.md",
    );
  });

  it("throws the shared FilesApiError on a non-ok response", async () => {
    stubFetch({ status: "bad-path" }, false, 400);
    await expect(readNote("r", "m", "../escape.md")).rejects.toBeInstanceOf(FilesApiError);
  });
});

describe("resolveNoteReference", () => {
  const notePaths = [
    "design-agent-orchestration.md",
    "friction-ledger.md",
    "reports/260703-L1-worker-report.md",
  ];

  it("resolves a notes/-prefixed reference with trailing prose", () => {
    expect(
      resolveNoteReference("notes/friction-ledger.md (F-M — the finding this leaf closes)", notePaths),
    ).toBe("friction-ledger.md");
  });

  it("resolves a nested notes-relative path with or without the notes/ prefix", () => {
    expect(resolveNoteReference("notes/reports/260703-L1-worker-report.md", notePaths)).toBe(
      "reports/260703-L1-worker-report.md",
    );
    expect(resolveNoteReference("reports/260703-L1-worker-report.md", notePaths)).toBe(
      "reports/260703-L1-worker-report.md",
    );
  });

  it("resolves an unambiguous bare filename", () => {
    expect(resolveNoteReference("see friction-ledger.md for the row", notePaths)).toBe(
      "friction-ledger.md",
    );
  });

  it("does not resolve an ambiguous bare filename", () => {
    const ambiguous = ["a/summary.md", "b/summary.md"];
    expect(resolveNoteReference("summary.md", ambiguous)).toBeUndefined();
  });

  it("leaves a code-path reference unresolved", () => {
    expect(
      resolveNoteReference(
        "mcp/src/agents_remember/serving/files.py (the confinement idiom to reuse)",
        notePaths,
      ),
    ).toBeUndefined();
    expect(
      resolveNoteReference("dashboard/src/panels/DetailPanel.tsx (task reader home)", notePaths),
    ).toBeUndefined();
  });

  it("leaves plain prose and empty note lists unresolved", () => {
    expect(resolveNoteReference("the doctrine chapter on gates", notePaths)).toBeUndefined();
    expect(resolveNoteReference("notes/friction-ledger.md", [])).toBeUndefined();
  });
});
