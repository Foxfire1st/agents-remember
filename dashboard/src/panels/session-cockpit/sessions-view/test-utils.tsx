// Shared fixtures + afterEach for the SessionsView test family: session/catalog seeding, the
// live-projection helpers, the never-resolving fetch stub, and the store/localStorage reset.
// Each split test file imports this module (for helpers or as a side effect) so the assertion
// set stays identical across the split.
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { sessionCockpitStore } from "../../../data/sessionCockpitStore";
import { lifecycleNoticeStore } from "../../../data/sessionLifecycle";
import { emptyProjection } from "../../../data/conversation/reducer";
import { activeConversationStore } from "../../../data/conversation/store";
import type {
  ActiveConversationRef,
  ConversationStatus,
} from "../../../data/conversation/types";
import { fromTerminalSessionInfo, sessionStore } from "../../../data/sessions";
import { dashboardStore } from "../../../data/store";
import { catalogRow, FLEET } from "../../../test/fixtures/catalogRows";
import {
  conversationIdentity,
  conversationStatus,
} from "../../../test/fixtures/conversationWire";

// Terminal mount/unmount ledgers for the inline Terminal mock: each test file's vi.mock factory
// imports these lazily, and the launch/keep-alive tests assert dispose/recreate behavior on them.
export const mockTerminalMounts: string[] = [];
export const mockTerminalUnmounts: string[] = [];

export function seedReadyComposerSession() {
  const row = FLEET.find((candidate) => candidate.id === "architect")!;
  sessionStore.getState().hydrate([fromTerminalSessionInfo(row)]);
  sessionCockpitStore.setState({ focusedSessionId: null });
}

// A legacy-raw terminal (no controlState) keeps its interactive PTY as the primary
// stage body — the remaining home of the `pty` keyboard zone. The zone-contract tests target it.
export function seedLegacyRawSession() {
  sessionStore
    .getState()
    .hydrate([
      fromTerminalSessionInfo(
        catalogRow({
          id: "legacy-raw",
          label: "legacy raw terminal",
          kind: "terminal",
          harness: undefined,
          seatRole: "terminal",
          status: "running",
        }),
      ),
    ]);
  sessionCockpitStore.setState({ focusedSessionId: "legacy-raw" });
}

// A projected conversation status seeded straight into the active-conversation
// store. The stage's own connect is neutralized by a never-resolving fetch stub in each such test,
// so the seeded stream phase stays exactly where the test puts it.
export const L5Q_IDENTITY: ActiveConversationRef = conversationIdentity({
  vendorConversationId: "v",
  projectScope: "/r",
  identityDigest: "d",
  arSessionId: "worker-l4",
  bridgeEpoch: "e1",
});

export function l5qStatus(turnState: ConversationStatus["turn"]["state"]): ConversationStatus {
  return conversationStatus({
    identity: L5Q_IDENTITY,
    freshness: {
      state: "fresh",
      lastEvidenceAt: null,
      ageMs: null,
      staleAfterMs: 1,
      observationBound: "poll",
    },
    process: { state: "connected", generation: "g" },
    turn: { state: turnState, turnId: "turn-live-1", stateSince: null },
  });
}

export function seedLiveProjection(sessionId: string, turnState: ConversationStatus["turn"]["state"]): void {
  activeConversationStore.setState({
    bySession: {
      [sessionId]: {
        ...emptyProjection(L5Q_IDENTITY),
        stream: "live",
        status: l5qStatus(turnState),
      },
    },
  });
}

/** fetch that never resolves: the stage's conversation connect/hydrate can never clobber a seeded projection. */
export function stubHangingFetch(): void {
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
}

afterEach(() => {
  cleanup();
  window.localStorage.clear(); // react-resizable-panels persists layout under autoSaveId
  sessionStore.getState().hydrate([]);
  sessionCockpitStore.setState({ focusedSessionId: null, perSession: {} });
  activeConversationStore.getState().reset();
  dashboardStore.setState({ lifecycles: {} });
  lifecycleNoticeStore.setState({
    residuals: [],
    cleanupOutcome: null,
    cleanupFailure: null,
    sweptRetire: {},
  });
  vi.unstubAllGlobals();
});
