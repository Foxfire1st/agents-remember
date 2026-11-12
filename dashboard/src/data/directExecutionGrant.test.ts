import { afterEach, describe, expect, it, vi } from "vitest";

import { postDirectExecutionGrant } from "./directExecutionGrant";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("postDirectExecutionGrant", () => {
  it("posts repository, path and action to the grant control", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          operation: "direct_execution_grant",
          state: "granted",
          summary: "repo-a:direct-master/test-direct-leaf.json has its direct-execution grant",
          changed: true,
          grant: { grantedBy: "developer", grantedAt: "2026-10-06T02:00:00+00:00" },
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetch);

    const outcome = await postDirectExecutionGrant(
      "repo-a",
      "direct-master/test-direct-leaf.json",
      "give",
    );

    expect(outcome.ok).toBe(true);
    expect(outcome.state).toBe("granted");
    expect(outcome.grant).toEqual({ grantedBy: "developer", grantedAt: "2026-10-06T02:00:00+00:00" });
    expect(fetch).toHaveBeenCalledWith("/api/direct-execution-grant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repository: "repo-a",
        path: "direct-master/test-direct-leaf.json",
        action: "give",
      }),
    });
  });

  it("keeps the server's refusal words", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ok: false,
            operation: "direct_execution_grant",
            state: "refused",
            status: "direct-grant-leaf-started",
            detail: "the grant is refused: the leaf has started (status=inProgress).",
            changed: false,
            grant: null,
          }),
          { status: 200 },
        ),
      ),
    );

    const outcome = await postDirectExecutionGrant("repo-a", "m/leaf.json", "give");

    expect(outcome.ok).toBe(false);
    expect(outcome.state).toBe("refused");
    expect(outcome.text).toContain("the leaf has started");
    expect(outcome.grant).toBeNull();
  });

  it("reports an unreachable control without inventing a payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("network down")),
    );

    const outcome = await postDirectExecutionGrant("repo-a", "m/leaf.json", "revoke");

    expect(outcome.ok).toBe(false);
    expect(outcome.text).toContain("could not reach");
  });
});
