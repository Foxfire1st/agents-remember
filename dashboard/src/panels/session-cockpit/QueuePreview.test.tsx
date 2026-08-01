// The queue steer action: head-row only, gated on the WIRE interrupt
// capability (never a hardcoded harness list), firing the exact-turn interrupt whose settlement
// lets the authority dispatch the queued head itself — the row is never withdrawn or duplicated.
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activeConversationStore } from "../../data/conversation/store";
import type {
  ActiveConversationRef,
  CapabilityState,
  ConversationCapabilities,
  ConversationPage,
  ConversationStatus,
} from "../../data/conversation/types";
import type { QueuedSubmit } from "../../data/sessionCockpitStore";
import {
  conversationCapabilities,
  conversationIdentity,
  conversationPage,
  conversationStatus,
  featureCapability,
} from "../../test/fixtures/conversationWire";
import { QueuePreview } from "./QueuePreview";

const SESSION = "seat-steer";

function identity(sessionId: string): ActiveConversationRef {
  return conversationIdentity({
    vendorConversationId: "v",
    projectScope: "/r",
    identityDigest: "d",
    arSessionId: sessionId,
    bridgeEpoch: "ep-1",
  });
}

/** The codex-shaped control tree: interrupt is the probed leaf, steer/follow-up are not offered. */
function capabilities(interruptState: CapabilityState): ConversationCapabilities {
  return conversationCapabilities({
    controls: {
      interrupt: featureCapability({ state: interruptState }),
      steer: featureCapability({ state: "unavailable" }),
      followUp: featureCapability({ state: "unavailable" }),
    },
  });
}

function page(
  sessionId: string,
  options: { turnState: "working" | "ready"; interruptState: CapabilityState },
): ConversationPage {
  const status: ConversationStatus = conversationStatus({
    identity: identity(sessionId),
    observedAt: "2026-07-22T00:00:00Z",
    turn: {
      state: options.turnState,
      turnId: options.turnState === "working" ? "turn-1" : null,
      stateSince: null,
    },
  });
  return conversationPage({
    identity: identity(sessionId),
    status,
    capabilities: capabilities(options.interruptState),
  });
}

function queued(requestId: string, text: string): QueuedSubmit {
  return {
    requestId,
    text,
    preview: text,
    queuedAt: Date.now(),
    expectedBridgeEpoch: "ep-1",
    state: "queued",
  };
}

const QUEUE = [queued("req-1", "first queued message"), queued("req-2", "second queued message")];

function seed(options: { turnState: "working" | "ready"; interruptState: CapabilityState }) {
  activeConversationStore.getState().applyPage(SESSION, page(SESSION, options), "initial");
}

beforeEach(() => {
  activeConversationStore.getState().reset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("QueuePreview steer (260718-CHATS-L5I)", () => {
  it("offers steer on the HEAD row only when the wire says interrupt-supported + a turn works", () => {
    seed({ turnState: "working", interruptState: "supported" });
    const { getByTestId, getAllByTestId } = render(
      <QueuePreview queue={QUEUE} sessionId={SESSION} />,
    );
    expect(getAllByTestId("queue-steer")).toHaveLength(1);
    const items = getAllByTestId("queue-preview-item");
    expect(within(items[0]).getByTestId("queue-steer")).toBeDefined();
    expect(within(items[1]).queryByTestId("queue-steer")).toBeNull();
    expect(getByTestId("queue-steer").getAttribute("title")).toContain(
      "interrupts the current turn and runs this now",
    );
  });

  it("hides steer when the wire capability is merely unverified (honest absence)", () => {
    seed({ turnState: "working", interruptState: "unverified" });
    const { queryByTestId } = render(<QueuePreview queue={QUEUE} sessionId={SESSION} />);
    expect(queryByTestId("queue-steer")).toBeNull();
  });

  it("hides steer when no turn is working — the queue drains on its own", () => {
    seed({ turnState: "ready", interruptState: "supported" });
    const { queryByTestId } = render(<QueuePreview queue={QUEUE} sessionId={SESSION} />);
    expect(queryByTestId("queue-steer")).toBeNull();
  });

  it("hides steer without projection evidence (no capability read at all)", () => {
    const { queryByTestId } = render(<QueuePreview queue={QUEUE} sessionId={SESSION} />);
    expect(queryByTestId("queue-steer")).toBeNull();
  });

  it("steer fires the exact-turn interrupt; the queued rows stay put (never withdrawn/duplicated)", async () => {
    seed({ turnState: "working", interruptState: "supported" });
    const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), body: JSON.parse(String(init?.body)) });
        return {
          ok: true,
          status: 200,
          json: async () => ({
            requestId: calls[calls.length - 1]?.body.requestId,
            requestFingerprint: "fp-1",
            revision: 1,
            bridgeEpoch: "ep-1",
            turnId: "turn-1",
            acknowledgement: "accepted",
            settlement: "interrupted",
            requestedAt: "2026-07-22T00:00:00Z",
          }),
        } as Response;
      }),
    );
    const { getByTestId, getAllByTestId } = render(
      <QueuePreview queue={QUEUE} sessionId={SESSION} />,
    );
    fireEvent.click(getByTestId("queue-steer"));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]?.url).toBe(
      `/api/terminal/${SESSION}/conversation/interrupt?expectedBridgeEpoch=ep-1`,
    );
    expect(calls[0]?.body).toMatchObject({ turnId: "turn-1" });
    expect(typeof calls[0]?.body.requestId).toBe("string");
    // The queue is untouched by steering: same two rows, same order, still queued.
    const items = getAllByTestId("queue-preview-item");
    expect(items).toHaveLength(2);
    expect(getAllByTestId("queued-user-block").map((block) => block.textContent)).toEqual([
      "> first queued message",
      "> second queued message",
    ]);
  });
});
