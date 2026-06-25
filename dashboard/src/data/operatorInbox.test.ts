import { describe, expect, it, vi } from "vitest";

import { dismissOperatorInboxEntry, postOperatorInbox } from "./operatorInbox";

describe("postOperatorInbox", () => {
  it("POSTs the external inbox payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    const status = await postOperatorInbox({
      lifecycleId: "LC1",
      gateId: "G1",
      ask: "Continue?",
      response: "Proceed.",
    });

    expect(status).toBe("posted");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/operator-inbox",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          lifecycleId: "LC1",
          gateId: "G1",
          ask: "Continue?",
          response: "Proceed.",
        }),
      }),
    );
    vi.unstubAllGlobals();
  });

  it("returns error on non-ok or network failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    await expect(
      postOperatorInbox({ lifecycleId: "LC1", ask: "Continue?", response: "Proceed." }),
    ).resolves.toBe("error");

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(
      postOperatorInbox({ lifecycleId: "LC1", ask: "Continue?", response: "Proceed." }),
    ).resolves.toBe("error");
    vi.unstubAllGlobals();
  });
});

describe("dismissOperatorInboxEntry", () => {
  it("POSTs the dismiss endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ status: 200 });
    vi.stubGlobal("fetch", fetchMock);

    await expect(dismissOperatorInboxEntry("I1")).resolves.toBe("dismissed");
    expect(fetchMock).toHaveBeenCalledWith("/api/operator-inbox/I1/dismiss", { method: "POST" });
    vi.unstubAllGlobals();
  });

  it("distinguishes not-found and transport failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 404 }));
    await expect(dismissOperatorInboxEntry("missing")).resolves.toBe("not-found");

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(dismissOperatorInboxEntry("I1")).resolves.toBe("error");
    vi.unstubAllGlobals();
  });
});
