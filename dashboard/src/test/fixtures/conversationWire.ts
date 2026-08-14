// The one place a dashboard test may build a node of the conversation wire grammar
// (`data/conversation/types.ts` ← `serving/conversation/models.py`, and
// `data/conversation-library/types.ts` ← `library/api.py`). Companion to `wire.ts`, which does the
// same job for the served projection; see that file's header for why R6 needs either.
//
// This grammar had two failure modes of its own, and both were one token wide:
//
//   `{} as unknown as ConversationCapabilities` — a page whose capability tree is EMPTY. The wire
//     model declares twenty-three `FeatureCapability` leaves and the server fills every one, so
//     the fixture asserted against a page the server cannot send. `conversationCapabilities()`
//     below produces the full tree and takes per-group overrides, so a test that cares about
//     `controls.interrupt` names `controls.interrupt` and gets the other twenty-two for free.
//
//   `undefined as unknown as ConversationCapabilities` — worse: `ConversationPage.capabilities` is
//     REQUIRED, so this fixture stated that a required field is absent. Note what removing the cast
//     did NOT do: `conversationPage({ capabilities: undefined })` reaches the same page with no
//     assertion at all, because a `Partial<T>` slot admits an explicit `undefined` whenever
//     `exactOptionalPropertyTypes` is off — and it is off here. That is why the builders below take
//     an `Overrides<O, Node>` rather than a bare `Partial<Node>`; read `overrides.ts` for the
//     measurement behind that choice and for the residue it leaves.
//
// The branded tokens are the one thing here that must be minted with an assertion.
// `ActivePageCursor`/`ActiveEventCursor`/`LibraryConversationKey` are `string & { __brand }`:
// opaque server-issued strings whose brand exists only so a page cursor can never be handed to an
// event route. There is no structure to get wrong and no other way to make one, so the seventeen
// scattered `"evt-0" as ActiveEventCursor` casts collapse into the three named mints below — each
// registered in `wireFixtureGuard.test.ts` with its reason, which is what makes them the only
// three rather than the first three.

import type {
  ActiveConversationRef,
  ActiveEventCursor,
  ActivePageCursor,
  AttachmentCapability,
  CapabilityState,
  ConversationCapabilities,
  ConversationItem,
  ConversationPage,
  ConversationStatus,
  FeatureCapability,
} from "../../data/conversation/types";
import type {
  ConversationLibraryAgentRow,
  ConversationLibraryRow,
  HistoryCapabilities,
  LibraryConversationKey,
} from "../../data/conversation-library/types";
import type { NoOverrides, Overrides } from "./overrides";

// ── the opaque tokens ────────────────────────────────────────────────────────

/** Mint an `ActivePageCursor`. The brand is compile-time only; the wire form is a bare string. */
export function pageCursor(raw: string): ActivePageCursor {
  return raw as ActivePageCursor;
}

/** Mint an `ActiveEventCursor`. */
export function eventCursor(raw: string): ActiveEventCursor {
  return raw as ActiveEventCursor;
}

/** Mint a `LibraryConversationKey` — server-minted on the wire, including for sub-agent rows. */
export function libraryConversationKey(raw: string): LibraryConversationKey {
  return raw as LibraryConversationKey;
}

// ── capabilities ─────────────────────────────────────────────────────────────

export function featureCapability<O extends Partial<FeatureCapability> = NoOverrides>(
  overrides?: Overrides<O, FeatureCapability>,
): FeatureCapability {
  const over: Partial<FeatureCapability> = overrides ?? {};
  return { state: "supported", reason: "", evidenceTier: "adapter", ...over };
}

export function attachmentCapability<O extends Partial<AttachmentCapability> = NoOverrides>(
  overrides?: Overrides<O, AttachmentCapability>,
): AttachmentCapability {
  const over: Partial<AttachmentCapability> = overrides ?? {};
  return {
    ...featureCapability(),
    allowedMimeTypes: ["image/png"],
    maxBytes: 1024,
    maxCount: 1,
    description: "required",
    ...over,
  };
}

/**
 * Per-group overrides, DERIVED from the wire type rather than re-listed: a group the server adds
 * is overridable the day the mirror declares it, and a group it drops stops being nameable.
 *
 * The one builder here whose parameter is NOT an `Overrides<O, Node>`: it is a level deeper than
 * the flat builders, so `conversationCapabilities({ controls: { interrupt: undefined } })` still
 * writes an `undefined` onto a required leaf. Left as-is rather than half-generalized — see the
 * closing note in `overrides.ts`.
 */
export type ConversationCapabilityOverrides = {
  [Group in keyof ConversationCapabilities]?: Partial<ConversationCapabilities[Group]>;
};

export function conversationCapabilities(
  over: ConversationCapabilityOverrides = {},
): ConversationCapabilities {
  const leaf = featureCapability();
  return {
    live: {
      text: leaf,
      thinking: leaf,
      tools: leaf,
      diffs: leaf,
      interactions: leaf,
      completeness: leaf,
      ...over.live,
    },
    history: {
      list: leaf,
      read: leaf,
      resume: leaf,
      completeness: leaf,
      toolCompleteness: leaf,
      ...over.history,
    },
    controls: {
      interrupt: leaf,
      steer: leaf,
      followUp: leaf,
      attachments: {
        image: attachmentCapability(),
        file: attachmentCapability(),
        resource: attachmentCapability(),
      },
      policyRead: leaf,
      ...over.controls,
    },
    telemetry: {
      context: leaf,
      usage: leaf,
      cost: leaf,
      rateLimit: leaf,
      compaction: leaf,
      ...over.telemetry,
    },
  };
}

/** The capability tree with one control at a chosen state — the shape the controls tests want. */
export function capabilitiesWithInterrupt(state: CapabilityState): ConversationCapabilities {
  return conversationCapabilities({
    controls: { interrupt: featureCapability({ state, reason: `interrupt ${state}` }) },
  });
}

export function historyCapabilities<O extends Partial<HistoryCapabilities> = NoOverrides>(
  overrides?: Overrides<O, HistoryCapabilities>,
): HistoryCapabilities {
  const over: Partial<HistoryCapabilities> = overrides ?? {};
  const leaf = featureCapability();
  return {
    list: leaf,
    read: leaf,
    resume: leaf,
    completeness: leaf,
    toolCompleteness: leaf,
    ...over,
  };
}

// ── identity, status, items, pages ───────────────────────────────────────────

export function conversationIdentity<O extends Partial<ActiveConversationRef> = NoOverrides>(
  overrides?: Overrides<O, ActiveConversationRef>,
): ActiveConversationRef {
  const over: Partial<ActiveConversationRef> = overrides ?? {};
  return {
    harnessId: "codex",
    vendorConversationId: "vc-1",
    projectScope: "/repo",
    identityDigest: "digest-1",
    arSessionId: "ar-1",
    bridgeEpoch: "epoch-1",
    ...over,
  };
}

export function conversationStatus<O extends Partial<ConversationStatus> = NoOverrides>(
  overrides?: Overrides<O, ConversationStatus>,
): ConversationStatus {
  const over: Partial<ConversationStatus> = overrides ?? {};
  return {
    identity: conversationIdentity(),
    revision: 1,
    observedAt: "2026-07-20T00:00:00Z",
    freshness: {
      state: "fresh",
      lastEvidenceAt: null,
      ageMs: null,
      staleAfterMs: 10_000,
      observationBound: "poll",
    },
    process: { state: "connected", generation: "g1" },
    turn: { state: "ready", turnId: null, stateSince: null },
    evidence: { strength: "exact", origin: "codex" },
    ...over,
  };
}

export function conversationItem<
  O extends Partial<ConversationItem> & Pick<ConversationItem, "itemId" | "globalOrdinal">,
>(overrides: Overrides<O, ConversationItem>): ConversationItem {
  const over: Partial<ConversationItem> & Pick<ConversationItem, "itemId" | "globalOrdinal"> =
    overrides;
  return {
    revision: 1,
    turnId: "t1",
    lane: "harness",
    source: "harness-live",
    provenance: { strength: "exact", origin: "codex" },
    role: "assistant",
    kind: "message",
    phase: "completed",
    blocks: [{ blockId: `${over.itemId}-b1`, type: "markdown", markdown: "hello" }],
    ...over,
  };
}

export function conversationPage<O extends Partial<ConversationPage> = NoOverrides>(
  overrides?: Overrides<O, ConversationPage>,
): ConversationPage {
  const over: Partial<ConversationPage> = overrides ?? {};
  const items = over.items ?? [];
  return {
    identity: conversationIdentity(),
    page: { olderCursor: null, hasOlder: false, totalItems: items.length },
    eventCursor: eventCursor("evt-0"),
    hydrationId: "hy-1",
    status: conversationStatus(),
    capabilities: conversationCapabilities(),
    ...over,
    items,
  };
}

// ── the dormant library ──────────────────────────────────────────────────────

export function conversationLibraryRow<O extends Partial<ConversationLibraryRow> = NoOverrides>(
  overrides?: Overrides<O, ConversationLibraryRow>,
): ConversationLibraryRow {
  const over: Partial<ConversationLibraryRow> = overrides ?? {};
  return {
    conversationKey: libraryConversationKey("key-parent"),
    identityDigest: "digest-parent",
    title: "the parent conversation",
    safeNativeIdSuffix: "P4R3NT",
    lastActivityAt: "2026-07-20T00:00:00Z",
    capabilities: historyCapabilities(),
    ...over,
  };
}

export function conversationLibraryAgentRow<
  O extends Partial<ConversationLibraryAgentRow> = NoOverrides,
>(overrides?: Overrides<O, ConversationLibraryAgentRow>): ConversationLibraryAgentRow {
  const over: Partial<ConversationLibraryAgentRow> = overrides ?? {};
  return {
    conversationKey: libraryConversationKey("key-agent-1"),
    identityDigest: "digest-agent-1",
    title: "agent abcdef12",
    ...over,
  };
}
