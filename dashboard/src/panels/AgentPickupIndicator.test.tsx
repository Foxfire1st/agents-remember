import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { dismissOperatorInboxEntry } from "../data/operatorInbox";
import { AgentPickupIndicator } from "./AgentPickupIndicator";

vi.mock("../data/operatorInbox", () => ({ dismissOperatorInboxEntry: vi.fn() }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentPickupIndicator", () => {
  it("renders waiting-for-agent while the response is fresh", () => {
    const { getByText, queryByTestId } = render(
      <AgentPickupIndicator
        pickup={{
          id: "pickup:I1",
          entryId: "I1",
          lifecycleId: "LC1",
          messageKind: "message",
          deliveryState: "queued",
          state: "waiting-for-agent",
          ttlSeconds: 300,
        }}
      />,
    );

    expect(getByText("waiting for agent")).toBeTruthy();
    expect(queryByTestId("agent-pickup-dismiss")).toBeNull();
  });

  it("dismisses the check-chat notice", async () => {
    vi.mocked(dismissOperatorInboxEntry).mockResolvedValue("dismissed");
    const { getByTestId, getByText } = render(
      <AgentPickupIndicator
        pickup={{
          id: "pickup:I1",
          entryId: "I1",
          lifecycleId: "LC1",
          messageKind: "message",
          deliveryState: "queued",
          state: "check-chat",
          ttlSeconds: 300,
        }}
      />,
    );

    expect(getByText("check chat")).toBeTruthy();
    fireEvent.click(getByTestId("agent-pickup-dismiss"));

    await waitFor(() => expect(dismissOperatorInboxEntry).toHaveBeenCalledWith("I1"));
  });
});
