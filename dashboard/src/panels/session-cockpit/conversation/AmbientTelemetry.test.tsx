import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AmbientTelemetry } from "./AmbientTelemetry";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AmbientTelemetry retained-chat activity", () => {
  it("aborts the in-flight telemetry read as soon as its retained surface becomes inactive", async () => {
    let requestSignal: AbortSignal | undefined;
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit): Promise<Response> =>
        new Promise((_resolve, reject) => {
          requestSignal = init?.signal ?? undefined;
          requestSignal?.addEventListener(
            "abort",
            () => reject(new DOMException("The operation was aborted.", "AbortError")),
            { once: true },
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const view = render(
      <AmbientTelemetry sessionId="chat-a" epoch="epoch-a" statusRevision={1} active />,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(requestSignal?.aborted).toBe(false);

    view.rerender(
      <AmbientTelemetry sessionId="chat-a" epoch="epoch-a" statusRevision={1} active={false} />,
    );
    expect(requestSignal?.aborted).toBe(true);
  });
});
