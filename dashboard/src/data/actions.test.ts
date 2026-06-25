import { afterEach, describe, expect, it, vi } from "vitest";

import { postGateDecision } from "./actions";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("postGateDecision", () => {
  it("posts the targeted gate id and optional note", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("{}", { status: 202 }));
    vi.stubGlobal("fetch", fetch);

    const status = await postGateDecision("LC1", "reject", {
      gateId: "G1",
      note: "Needs another pass.",
    });

    expect(status).toBe("recorded");
    expect(fetch).toHaveBeenCalledWith("/api/actions/reject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target: "LC1",
        gateId: "G1",
        note: "Needs another pass.",
      }),
    });
  });

  it("distinguishes stale gates from missing open gates", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "stale-gate" }), { status: 409 })),
    );

    await expect(postGateDecision("LC1", "approve", { gateId: "old" })).resolves.toBe("stale-gate");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "no-open-gate" }), { status: 409 })),
    );
    await expect(postGateDecision("LC1", "approve")).resolves.toBe("no-open-gate");
  });
});
