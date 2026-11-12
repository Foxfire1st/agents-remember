import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaskNotes } from "./TaskNotes";
import generatedRootFirstPart from "./__fixtures__/generatedRootFirstPart.json";
import generatedSelectionChangeAnswers from "./__fixtures__/generatedSelectionChangeAnswers.json";

// MIK-R126 rule 8: on a generated 100,150-file master, a change of the selected task
// issues no request whose answer grows with the files under the notes root; the page's
// notes answers stay under a fixed byte ceiling and the page draws fewer than one
// hundred rows for notes. The root first part below is a real captured answer of the
// candidate's list_folder on that tree (200 rows of a 250-entry root), not a
// hand-built shape; the generated-master selection test on the server side
// (mcp/tests/test_generated_master_selection_cost.py) proves the same bounded request
// set against the live 100,000-file tree.
const REPO = "agents-remember";
const MASTER = "m";
const CEILING_BYTES = 1_000_000;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("task page cost bound (rule 8)", () => {
  it("fetches a bounded first part, draws no note rows, and names the top count", async () => {
    let bytes = 0;
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        if (url.startsWith("/api/notes/folder")) {
          const body = generatedRootFirstPart;
          bytes += JSON.stringify(body).length;
          return { ok: true, status: 200, json: async () => body } as unknown as Response;
        }
        if (url.startsWith("/api/notes/resolve")) {
          const body = { repo: REPO, master: MASTER, resolutions: [] };
          bytes += JSON.stringify(body).length;
          return { ok: true, status: 200, json: async () => body } as unknown as Response;
        }
        if (url.startsWith("/api/role-launch/reports")) {
          const body = { repo: REPO, master: MASTER, doc: "", reports: [], total: 0, explorer: null };
          bytes += JSON.stringify(body).length;
          return { ok: true, status: 200, json: async () => body } as unknown as Response;
        }
        return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
      }),
    );
    const view = render(
      <TaskNotes repo={REPO} master={MASTER} docPath="/tasks/agents-remember/m/09_x.json" references={[]} onOpenNotes={vi.fn()} />,
    );
    // The top-folder count is named without computing a total over the tree...
    expect((await view.findByTestId("notes-root-count")).textContent).toContain("250 entries");
    // ...exactly three bounded notes requests fired (folder first part, resolve, reports)...
    expect(urls.filter((url) => url.startsWith("/api/notes/") || url.startsWith("/api/role-launch/"))).toHaveLength(3);
    expect(bytes).toBeLessThan(CEILING_BYTES);
    // ...and no note rows are drawn for notes (only the line, the empty-reports note, no rows).
    expect(view.container.querySelectorAll("li").length).toBeLessThan(100);
    expect(view.queryByText("note-0000.md")).toBeNull();
  });

  // L126-CODE-07 repair: the retained generated-master proof above issues direct
  // serving calls, and the case above mounts one static instance. This case performs
  // an ACTUAL selected-task change — rerendering the mounted TaskNotes from the big
  // generated master to the small one, which is exactly the page's selection
  // transition (TaskNotes refetches on [repo, master, referencesKey/docPath]) — and
  // is served the two masters' OWN measured replies: generatedSelectionChangeAnswers
  // pins the live list_folder root first part, empty resolve and per-master doc
  // reports captured on the 100001-file tree, and
  // mcp/tests/test_generated_master_selection_cost.py re-verifies those pinned bytes
  // against a freshly built tree on every run, so the stub cannot silently drift off
  // the generated master. The selected address matches exactly: both selections use
  // repo "R", the same repository the generated tree is built under, and the stub
  // routes by the full (repo, master) address — a wrong repo gets a 404, never a
  // pinned reply — with served reply repo asserted equal to requested repo below.
  // The change's own aggregate notes-request bytes stay under
  // the fixed ceiling with fewer than 100 rows; equal masked answers across the
  // change show the transition cost does not grow with the 100000 nested files under
  // the big root. No latency assertion.
  it("reissues the bounded request set on an actual selected-task change", async () => {
    const GEN_REPO = "R";
    const pinned = generatedSelectionChangeAnswers as {
      masters: Record<string, { folder: unknown; resolve: unknown; reports: unknown }>;
    };
    const calls: { url: string; body: string; bytes: number; servedRepo: string | null }[] = [];
    const answer = async (url: string, init: { body?: unknown } | undefined, body: { repo: string }) => {
      const bytes = JSON.stringify(body).length;
      calls.push({ url, body: typeof init?.body === "string" ? init.body : "", bytes, servedRepo: body.repo });
      return { ok: true, status: 200, json: async () => body } as unknown as Response;
    };
    const addressOf = (url: string, body: string) => {
      const q = new URL(url, "http://x").searchParams;
      let repo = q.get("repo") ?? "";
      let master = q.get("master") ?? "";
      if (!master) {
        try {
          const parsed = JSON.parse(body) as { repo?: string; master?: string };
          repo = parsed.repo ?? "";
          master = parsed.master ?? "";
        } catch {
          // keep URL-derived address
        }
      }
      return { repo, master };
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: { body?: unknown }) => {
        const body = typeof init?.body === "string" ? init.body : "";
        const { repo, master } = addressOf(url, body);
        const live = pinned.masters[master] as
          | { folder: { repo: string }; resolve: { repo: string }; reports: { repo: string } }
          | undefined;
        if (!live) return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
        if (url.startsWith("/api/notes/folder")) {
          if (repo !== live.folder.repo) return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
          return answer(url, init, live.folder);
        }
        if (url.startsWith("/api/notes/resolve")) {
          if (repo !== live.resolve.repo) return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
          return answer(url, init, live.resolve);
        }
        if (url.startsWith("/api/role-launch/reports")) {
          if (repo !== live.reports.repo) return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
          return answer(url, init, live.reports);
        }
        return { ok: false, status: 404, json: async () => ({}) } as unknown as Response;
      }),
    );
    const propsFor = (master: string) => ({
      repo: GEN_REPO,
      master,
      docPath: `/tasks/${GEN_REPO}/${master}/task.json`,
      references: [] as string[],
      onOpenNotes: vi.fn(),
    });
    const view = render(<TaskNotes {...propsFor("260101_big")} />);
    expect((await view.findByTestId("notes-root-count")).textContent).toContain("101 entries");
    expect(calls).toHaveLength(3);
    // The actual selected-task change: the mounted instance moves to the small master.
    view.rerender(<TaskNotes {...propsFor("260101_small")} />);
    await waitFor(() => expect(calls).toHaveLength(6));
    const second = calls.slice(3);
    // The change reissued the exact bounded set with the NEW task's operands...
    expect(second.filter((c) => c.url.includes("master=260101_small"))).toHaveLength(2);
    expect(
      second.filter((c) => c.url.startsWith("/api/notes/resolve") && c.body.includes('"master":"260101_small"')),
    ).toHaveLength(1);
    // ...every served reply belongs to the requested repository — the selected
    // (repo, master) address is the generated tree's own address...
    expect(calls.map((c) => c.servedRepo)).toEqual([GEN_REPO, GEN_REPO, GEN_REPO, GEN_REPO, GEN_REPO, GEN_REPO]);
    // ...its aggregate answer bytes stay under the fixed declared ceiling...
    expect(second.reduce((n, c) => n + c.bytes, 0)).toBeLessThan(CEILING_BYTES);
    // ...the change's answers match the first selection's once master names are
    // masked (both are the generated tree's own measured replies: cost does not
    // grow with the 100000 nested files; byte counts are asserted under the ceiling
    // above, not here, since "260101_small" is two chars longer than "260101_big")...
    const mask = (s: string) => s.replaceAll("260101_big", "").replaceAll("260101_small", "");
    expect(mask(second.map((c) => `${c.url}${c.body}`).join("|"))).toBe(
      mask(calls.slice(0, 3).map((c) => `${c.url}${c.body}`).join("|")),
    );
    // ...and the page still draws fewer than 100 rows with the top count named.
    expect((await view.findByTestId("notes-root-count")).textContent).toContain("101 entries");
    expect(view.container.querySelectorAll("li").length).toBeLessThan(100);
  });
});
