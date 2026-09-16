// MOUNTED UI EVIDENCE for an eve conversation (packet behaviour 3).
//
// This file mounts the SHIPPED conversation surface — the same `ConversationSurface` the cockpit
// renders — over the projection an eve session really produces, and asserts what a reader sees.
// The input is `test/fixtures/eveConversationCapture.json`: the wire body serialized by the
// production server path from the real eve projector (`PageResult` items + status, dumped through
// the `WireModel` aliases), covering a tool round-trip, a completion, a cancellation, the park and
// a failed step. The Python side asserts the same file equals what the projector produces today, so
// this test cannot drift away from the projection it claims to render.
//
// It is a DOM mount in the repository's own component-test harness (jsdom via vitest), not a
// projector assertion: every expectation below reads rendered text from the live React tree.

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ActiveConversationProjection } from "../../../data/conversation/reducer";
import { emptyProjection } from "../../../data/conversation/reducer";
import { activeConversationStore } from "../../../data/conversation/store";
import type {
  ConversationItem,
  ConversationStatus,
} from "../../../data/conversation/types";
import {
  eveConversationItems,
  eveConversationStatus,
} from "../../../test/fixtures/eveConversationCapture";
import { ConversationSurface } from "./ConversationSurface";

vi.mock("./AmbientTelemetry", () => ({ AmbientTelemetry: () => null }));

// Decoded, not asserted: `eveConversationCapture.ts` reads the production-serialized JSON through
// the wire mirror's own unions and throws on a token the mirror does not declare, so this file
// carries no cast and the guard in `test/wireFixtureGuard.test.ts` stays satisfied.
const ITEMS: ConversationItem[] = eveConversationItems;
const STATUS: ConversationStatus = eveConversationStatus;
const SESSION_ID = STATUS.identity.arSessionId;

/** Seed the store the way hydration does: ids in order plus the item map the timeline reads. */
function seed(items: ConversationItem[]): void {
  const base: ActiveConversationProjection = {
    ...emptyProjection(STATUS.identity),
    stream: "live" as const,
    status: STATUS,
    lastAppliedDelivery: "live" as const,
  };
  activeConversationStore.setState({
    bySession: {
      [SESSION_ID]: {
        ...base,
        orderedItemIds: items.map((item) => item.itemId),
        itemsById: Object.fromEntries(items.map((item) => [item.itemId, item])),
      },
    },
  });
}

beforeEach(() => {
  // jsdom has no layout: pin a fixed geometry so the virtualized timeline renders rows.
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 600 });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 800 });
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, value: 6000 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, value: 600 });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, value: 800 });
});

afterEach(async () => {
  cleanup();
  await new Promise((resolve) => setTimeout(resolve, 200));
  activeConversationStore.getState().reset();
});

function surface() {
  return (
    <ConversationSurface
      sessionId={SESSION_ID}
      visible={true}
      onRetry={() => {}}
      onShowDiagnostics={() => {}}
    />
  );
}

describe("mounted eve conversation (behaviour 3)", () => {
  it("renders the surface for an eve session identity", () => {
    seed(ITEMS);
    render(surface());
    expect(screen.getByTestId("conversation-surface")).toBeTruthy();
    expect(STATUS.identity.harnessId).toBe("eve");
  });

  it("shows the operator message, the tool round-trip and the finalized answer", () => {
    seed(ITEMS);
    render(surface());
    const body = screen.getByTestId("conversation-surface").textContent ?? "";
    expect(body).toContain("run the fixture tool round-trip");
    expect(body).toContain("ar_workspace_write");
    expect(body).toContain("note.txt written");
  });

  it("shows a cancelled turn as an interrupted turn boundary, not as a clean completion", () => {
    seed(ITEMS);
    render(surface());
    const rows = screen.getAllByTestId("conversation-turn-result");
    const labels = rows.map((row) => row.textContent ?? "");
    expect(labels.some((label) => label.includes("interrupted"))).toBe(true);
    expect(labels.some((label) => label.includes("turn complete"))).toBe(true);
  });

  it("shows the failure state and the parked-session notice as their own rows", () => {
    seed(ITEMS);
    render(surface());
    const body = screen.getByTestId("conversation-surface").textContent ?? "";
    expect(body).toContain("MODEL_CALL_FAILED");
    expect(body).toContain("eve session parked and ready for");
  });

  it("shows the input request and the authorization challenge as answerable rows", () => {
    seed(ITEMS);
    render(surface());

    // The packet's "an input request" / behaviour 3's "questions/approvals", read from the live DOM
    // through the surface's own interaction test-ids: the question text, its phase, and the answer
    // options the runtime offered.
    const rows = screen.getAllByTestId("conversation-interaction");
    expect(rows).toHaveLength(2);
    const text = rows.map((row) => row.textContent ?? "");
    expect(text.some((row) => row.includes("question: Proceed with the write?"))).toBe(true);
    expect(text.some((row) => row.includes("write note.txt"))).toBe(true);
    // The options are the runtime's own answer tokens, rendered by the shared choices block.
    const question = rows.find((row) => (row.textContent ?? "").includes("Proceed"))!;
    const optionText = question.textContent ?? "";
    expect(optionText).toContain("Approve");
    expect(optionText).toContain("Deny");
    for (const phase of screen.getAllByTestId("interaction-phase")) {
      expect(phase.textContent ?? "").toBe("waiting for answer");
    }
  });

  it("renders nothing as an unknown-vendor row for a pinned-release frame", () => {
    // The frames the pinned release emits routinely (step and partial-argument frames) are consumed
    // by the projector; a mounted surface must not show them as preserved vendor evidence.
    seed(ITEMS);
    render(surface());
    const body = screen.getByTestId("conversation-surface").textContent ?? "";
    expect(body).not.toContain("unknown vendor event");
  });
});
