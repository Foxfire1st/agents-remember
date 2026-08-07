import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DetailPanel } from "./DetailPanel";
import {
  enclosure,
  seedTaskDocuments,
  taskDoc,
} from "./test-utils";

describe("DetailPanel on-demand task body (task 11)", () => {
  it("loads the complete task body before mounting reader ancillary requests", async () => {
    const lifecycleId = "LC-READER-PRIORITY";
    const summary = taskDoc({
      lifecycleId,
      kind: "subTask",
      docPath: "/tasks/agents-remember/260712_reader-priority/01_reader.json",
      title: "Reader priority",
      objective: "Summary objective.",
      steps: [{ id: "S1", title: "Keep the summary visible", status: "inProgress", substeps: [] }],
    });
    seedTaskDocuments([summary], {
      lifecycles: [
        {
          id: lifecycleId,
          state: "running",
          phase: "build",
          fleeting: false,
          enclosure: "/contracts/reader-priority",
          repoId: "agents-remember",
          tokens: 0,
          startedAt: "2026-07-12T08:00:00+00:00",
          lastEventTs: "2026-07-12T08:00:01+00:00",
          stateEnteredAt: "2026-07-12T08:00:00+00:00",
          inferred: false,
          actions: [],
          tokenSeries: [],
        },
      ],
      enclosures: [
        enclosure({
          enclosure: "/contracts/reader-priority",
          lifecycleId,
          leafId: summary.id,
          taskId: "READER-PRIORITY-MASTER",
          taskName: "260712_reader-priority",
          worktreeGroup: "/worktrees/reader-priority-ar",
        }),
      ],
      activeWorktreeGroups: ["reader-priority-ar"],
    });
    let resolveBody!: (response: Response) => void;
    const pendingBody = new Promise<Response>((resolve) => {
      resolveBody = resolve;
    });
    const requested: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string): Promise<Response> => {
        requested.push(url);
        if (url.startsWith("/api/task-document")) return pendingBody;
        if (url.startsWith("/api/notes/list")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({
              repo: "repo-a",
              master: "260712_reader-priority",
              notes: [],
              truncated: false,
            }),
          } as unknown as Response);
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            counters: {
              code: { files: 0, insertions: 0, deletions: 0 },
              memory: { files: 0, insertions: 0, deletions: 0 },
            },
          }),
        } as unknown as Response);
      }),
    );

    const { getByText, queryByText } = render(
      <DetailPanel
        selectedId={lifecycleId}
        onOpenChangeSet={vi.fn()}
      />,
    );

    expect(getByText("Summary objective.")).toBeTruthy();
    expect(getByText("Loading complete task document…")).toBeTruthy();
    await waitFor(() => expect(requested).toHaveLength(1));
    expect(requested[0]).toContain("/api/task-document");
    expect(requested.some((url) => url.startsWith("/api/changeset/"))).toBe(false);
    expect(requested.some((url) => url.startsWith("/api/notes/list"))).toBe(false);

    resolveBody({
      ok: true,
      status: 200,
      json: async () => ({ ...summary, objective: "Complete objective." }),
    } as unknown as Response);

    await waitFor(() => expect(getByText("Complete objective.")).toBeTruthy());
    expect(queryByText("Loading complete task document…")).toBeNull();
    await waitFor(() => {
      expect(requested.some((url) => url.startsWith("/api/changeset/"))).toBe(true);
      expect(requested.some((url) => url.startsWith("/api/notes/list"))).toBe(true);
    });
  });

  it("renders the complete on-demand task-document body while retaining its summary", async () => {
    const summary = taskDoc({
      kind: "subTask",
      docPath: "/tasks/agents-remember/260628_operations-integration/16_reader.json",
      title: "Reader residual",
      objective: "Summary objective.",
      steps: [{ id: "S1", title: "Render once", status: "inProgress", substeps: [] }],
    });
    seedTaskDocuments([summary]);
    const body = {
      ...summary,
      objective: "Complete objective.",
      requirements: ["Show the complete body."],
      design: "Hydrate the visible reader first.",
      codeExamples: [
        {
          id: "E1",
          title: "Body priority",
          distinctChange: "Delay ancillary requests.",
          why: "The complete task is the reader's primary content.",
          language: "tsx",
          snippet: "bodyState !== 'loading'",
        },
      ],
      decisions: [
        {
          at: "2026-07-10",
          decision: "Use the on-demand body.",
          rationale: "It is authoritative.",
        },
      ],
      openQuestions: ["Does every reader entry path share this state?"],
      references: ["notes/reader.md"],
      sections: [{ kind: "freeform", heading: "Evidence", body: "The live request was starved." }],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/task-document")) {
          return { ok: true, status: 200, json: async () => body } as unknown as Response;
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            counters: {
              code: { files: 0, insertions: 0, deletions: 0 },
              memory: { files: 0, insertions: 0, deletions: 0 },
            },
          }),
        } as unknown as Response;
      }),
    );

    const { getByText, getAllByText } = render(
      <DetailPanel selectedId={`taskdoc:${summary.docPath}`} />,
    );
    expect(getByText("Summary objective.")).toBeTruthy();
    await waitFor(() => expect(getByText("Complete objective.")).toBeTruthy());
    expect(getByText("Show the complete body.")).toBeTruthy();
    expect(getByText("Hydrate the visible reader first.")).toBeTruthy();
    expect(getByText("E1 — Body priority")).toBeTruthy();
    expect(getByText("Use the on-demand body.")).toBeTruthy();
    expect(getByText("Does every reader entry path share this state?")).toBeTruthy();
    expect(getByText("The live request was starved.")).toBeTruthy();
    expect(getByText("notes/reader.md")).toBeTruthy();
    expect(getAllByText("S1 — Render once")).toHaveLength(1);
  });

  it("shows the available summary when the on-demand task-document body is absent", async () => {
    const doc = taskDoc({
      kind: "subTask",
      docPath: "/tasks/agents-remember/260628_operations-integration/17_reader.json",
      objective: "Available summary objective.",
    });
    seedTaskDocuments([doc]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/task-document")) {
          return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
        }
        return { ok: true, status: 200, json: async () => ({}) } as unknown as Response;
      }),
    );

    const { getByText } = render(<DetailPanel selectedId={`taskdoc:${doc.docPath}`} />);
    await waitFor(() =>
      expect(getByText("Full task document details are unavailable; showing the available summary.")).toBeTruthy(),
    );
    expect(getByText("Available summary objective.")).toBeTruthy();
  });

  it("reuses an unchanged task body and refetches when its revision changes", async () => {
    const summary = taskDoc({
      kind: "subTask",
      docPath: "/tasks/agents-remember/260712_reader-priority/02_cache.json",
      bodyRevision: "revision-1",
      objective: "Summary objective.",
    });
    seedTaskDocuments([summary]);
    const fetchMock = vi.fn(async (url: string) => {
      if (url.startsWith("/api/task-document")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ objective: "Complete objective." }),
        } as unknown as Response;
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          repo: "repo-a",
          master: "260712_reader-priority",
          notes: [],
          truncated: false,
        }),
      } as unknown as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    const selection = `taskdoc:${summary.docPath}`;
    const { getByText, rerender } = render(<DetailPanel selectedId={selection} />);
    await waitFor(() => expect(getByText("Complete objective.")).toBeTruthy());
    rerender(<DetailPanel selectedId={selection} />);
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).startsWith("/api/task-document")),
    ).toHaveLength(1);

    seedTaskDocuments([{ ...summary, bodyRevision: "revision-2" }]);
    rerender(<DetailPanel selectedId={selection} />);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).startsWith("/api/task-document")),
      ).toHaveLength(2),
    );
  });
});
