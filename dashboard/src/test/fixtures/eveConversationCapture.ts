// The eve mounted case's input: the production-serialized conversation capture, decoded into the
// wire mirror (`data/conversation/types.ts` ← `serving/conversation/models.py`).
//
// WHY A DECODER RATHER THAN A CAST. `eveConversationCapture.json` is written by the Python side —
// it is `capture_payload(await eve_conversation_page())`, i.e. the real `ActiveSessionProjector`
// over the real eve mapper, serialized through the production `WireModel` aliases. That makes it an
// *untrusted-from-TypeScript's-point-of-view* JSON import, and the dashboard's wire-fixture guard
// (`test/wireFixtureGuard.test.ts`) forbids the two easy answers: `as ConversationItem[]` is a
// fixture asserting the server's shape instead of checking it, and a bare JSON import reaching a
// wire-typed slot is an unchecked value in a checked slot. Every field below is therefore read
// through a membership predicate that THROWS on a token the mirror does not declare, so a capture
// that drifts from the mirror fails the mounted test with the offending field named instead of
// rendering something the server could never send.
//
// Every token list is the mirror's own union spelled out. That duplication is deliberate: it is the
// runtime check, and keeping it here means a new union member is a one-line change in a file whose
// whole job is this boundary.

import capture from "./eveConversationCapture.json";
import type {
  ActiveConversationRef,
  ConversationContentBlock,
  ConversationItem,
  ConversationItemKind,
  ConversationItemPhase,
  ConversationLane,
  ConversationRole,
  ConversationSource,
  ConversationStatus,
  ProvenanceEvidence,
  ProvenanceStrength,
} from "../../data/conversation/types";

const ITEM_KINDS: readonly ConversationItemKind[] = [
  "message",
  "thinking",
  "plan",
  "tool-call",
  "tool-result",
  "interaction",
  "turn-result",
  "notice",
  "error",
  "telemetry",
  "unknown-vendor",
];
const ITEM_PHASES: readonly ConversationItemPhase[] = [
  "pending",
  "streaming",
  "waiting",
  "completed",
  "failed",
  "interrupted",
  "unknown",
];
const LANES: readonly ConversationLane[] = [
  "operator",
  "harness",
  "agent-bus",
  "unknown-input",
  "interaction",
  "control",
  "system",
];
const SOURCES: readonly ConversationSource[] = [
  "cockpit-composer",
  "terminal-controlled",
  "durable-inbox",
  "harness-live",
  "harness-replay",
  "interaction-response",
  "control-authority",
  "native-history",
];
const ROLES: readonly ConversationRole[] = ["user", "assistant", "system", "tool"];
const STRENGTHS: readonly ProvenanceStrength[] = ["exact", "correlated", "native-only", "unknown"];
// The producer vocabulary lives inline in `ProvenanceEvidence`; the mirror exports no name for it.
const PRODUCERS = ["operator", "agent-bus", "controlled-terminal", "harness", "system"] as const;
const PROCESS_STATES = [
  "starting",
  "connected",
  "disconnected",
  "exited",
  "failed",
] as const;
const PROCESS_OUTCOMES = ["clean-exit", "failed-exit", "signal", "unknown"] as const;
const TURN_STATES = [
  "ready",
  "working",
  "waiting",
  "needs-input",
  "settling",
  "retrying",
  "compacting",
  "interrupted",
  "failed",
] as const;
const TURN_OUTCOMES = ["completed", "interrupted", "failed", "unknown"] as const;
const FRESHNESS_STATES = ["fresh", "stale", "unknown"] as const;
const HARNESS_IDS = ["codex", "claude", "pi", "eve"] as const;

const BLOCK_TYPES = [
  "markdown",
  "text",
  "thinking",
  "code",
  "tool-input",
  "tool-output",
  "diff",
  "image-ref",
  "file-ref",
  "resource-ref",
  "choices",
  "unknown-vendor",
] as const;
type BlockType = (typeof BLOCK_TYPES)[number];

function record(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${field}: expected an object`);
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, field: string): string {
  if (typeof value !== "string") throw new Error(`${field}: expected a string`);
  return value;
}

function optionalText(value: unknown, field: string): string | undefined {
  if (value === undefined || value === null) return undefined;
  return text(value, field);
}

function number(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${field}: expected a finite number`);
  }
  return value;
}

function nullableNumber(value: unknown, field: string): number | null {
  return value === undefined || value === null ? null : number(value, field);
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], field: string): T {
  if (typeof value !== "string" || !(allowed as readonly string[]).includes(value)) {
    throw new Error(`${field}: ${JSON.stringify(value)} is not one of ${allowed.join(", ")}`);
  }
  return value as T;
}

/** The blocks one captured item carries, each rebuilt as its own mirror member. */
function decodeBlocks(value: unknown, field: string): ConversationContentBlock[] {
  if (!Array.isArray(value)) throw new Error(`${field}: expected an array`);
  return value.map((raw, index) => {
    const block = record(raw, `${field}[${index}]`);
    const blockId = text(block.blockId, `${field}[${index}].blockId`);
    const type: BlockType = oneOf(block.type, BLOCK_TYPES, `${field}[${index}].type`);
    switch (type) {
      case "text":
        return { blockId, type, text: text(block.text, `${field}[${index}].text`) };
      case "markdown":
        return { blockId, type, markdown: text(block.markdown, `${field}[${index}].markdown`) };
      case "thinking":
        return { blockId, type, markdown: text(block.markdown, `${field}[${index}].markdown`) };
      case "code":
        return {
          blockId,
          type,
          text: text(block.text, `${field}[${index}].text`),
          language: optionalText(block.language, `${field}[${index}].language`),
        };
      case "tool-input":
        return {
          blockId,
          type,
          summary: text(block.summary, `${field}[${index}].summary`),
          data: block.data,
        };
      case "tool-output":
        return {
          blockId,
          type,
          text: optionalText(block.text, `${field}[${index}].text`),
          data: block.data,
        };
      case "choices": {
        const options = block.options;
        if (!Array.isArray(options)) throw new Error(`${field}[${index}].options: expected an array`);
        return {
          blockId,
          type,
          interactionId: text(block.interactionId, `${field}[${index}].interactionId`),
          options: options.map((option, position) => {
            const entry = record(option, `${field}[${index}].options[${position}]`);
            return {
              optionId: text(entry.optionId, `${field}[${index}].options[${position}].optionId`),
              label: text(entry.label, `${field}[${index}].options[${position}].label`),
              description: optionalText(
                entry.description,
                `${field}[${index}].options[${position}].description`,
              ),
            };
          }),
        };
      }
      case "unknown-vendor":
        return {
          blockId,
          type,
          vendorType: text(block.vendorType, `${field}[${index}].vendorType`),
          safeSummary: text(block.safeSummary, `${field}[${index}].safeSummary`),
          evidenceRef: text(block.evidenceRef, `${field}[${index}].evidenceRef`),
        };
      default:
        throw new Error(
          `${field}[${index}].type ${type}: this capture carries no block of that kind, so the ` +
            `decoder has no member to build — add it here with the mirror's own fields`,
        );
    }
  });
}

function decodeProvenance(value: unknown, field: string): ProvenanceEvidence {
  const raw = record(value, field);
  return {
    strength: oneOf(raw.strength, STRENGTHS, `${field}.strength`),
    origin: text(raw.origin, `${field}.origin`),
    producer:
      raw.producer === undefined
        ? undefined
        : oneOf(raw.producer, PRODUCERS, `${field}.producer`),
    observedAt: optionalText(raw.observedAt, `${field}.observedAt`),
    evidenceRef: optionalText(raw.evidenceRef, `${field}.evidenceRef`),
    reason: optionalText(raw.reason, `${field}.reason`),
  };
}

function decodeItem(value: unknown, index: number): ConversationItem {
  const raw = record(value, `items[${index}]`);
  const field = `items[${index}]`;
  const correlation = raw.correlation === undefined ? undefined : record(raw.correlation, field);
  return {
    itemId: text(raw.itemId, `${field}.itemId`),
    revision: number(raw.revision, `${field}.revision`),
    globalOrdinal: number(raw.globalOrdinal, `${field}.globalOrdinal`),
    turnId: optionalText(raw.turnId, `${field}.turnId`),
    parentItemId: optionalText(raw.parentItemId, `${field}.parentItemId`),
    lane: oneOf(raw.lane, LANES, `${field}.lane`),
    source: oneOf(raw.source, SOURCES, `${field}.source`),
    provenance: decodeProvenance(raw.provenance, `${field}.provenance`),
    role: oneOf(raw.role, ROLES, `${field}.role`),
    kind: oneOf(raw.kind, ITEM_KINDS, `${field}.kind`),
    phase: oneOf(raw.phase, ITEM_PHASES, `${field}.phase`),
    blocks: decodeBlocks(raw.blocks, `${field}.blocks`),
    correlation:
      correlation === undefined
        ? undefined
        : {
            requestId: optionalText(correlation.requestId, `${field}.correlation.requestId`),
            vendorCorrelationId: optionalText(
              correlation.vendorCorrelationId,
              `${field}.correlation.vendorCorrelationId`,
            ),
            interactionId: optionalText(
              correlation.interactionId,
              `${field}.correlation.interactionId`,
            ),
            toolCallId: optionalText(correlation.toolCallId, `${field}.correlation.toolCallId`),
          },
    createdAt: optionalText(raw.createdAt, `${field}.createdAt`),
    updatedAt: optionalText(raw.updatedAt, `${field}.updatedAt`),
    evidenceRef: optionalText(raw.evidenceRef, `${field}.evidenceRef`),
  };
}

function decodeIdentity(value: unknown, field: string): ActiveConversationRef {
  const raw = record(value, field);
  return {
    harnessId: oneOf(raw.harnessId, HARNESS_IDS, `${field}.harnessId`),
    vendorConversationId: text(raw.vendorConversationId, `${field}.vendorConversationId`),
    projectScope: text(raw.projectScope, `${field}.projectScope`),
    identityDigest: text(raw.identityDigest, `${field}.identityDigest`),
    arSessionId: text(raw.arSessionId, `${field}.arSessionId`),
    bridgeEpoch: text(raw.bridgeEpoch, `${field}.bridgeEpoch`),
  };
}

function decodeStatus(value: unknown): ConversationStatus {
  const raw = record(value, "status");
  const freshness = record(raw.freshness, "status.freshness");
  const process = record(raw.process, "status.process");
  const turn = record(raw.turn, "status.turn");
  return {
    identity: decodeIdentity(raw.identity, "status.identity"),
    revision: number(raw.revision, "status.revision"),
    observedAt: text(raw.observedAt, "status.observedAt"),
    freshness: {
      state: oneOf(freshness.state, FRESHNESS_STATES, "status.freshness.state"),
      lastEvidenceAt: optionalText(freshness.lastEvidenceAt, "status.freshness.lastEvidenceAt") ?? null,
      ageMs: nullableNumber(freshness.ageMs, "status.freshness.ageMs"),
      staleAfterMs: number(freshness.staleAfterMs, "status.freshness.staleAfterMs"),
      observationBound: text(freshness.observationBound, "status.freshness.observationBound"),
    },
    process: {
      state: oneOf(process.state, PROCESS_STATES, "status.process.state"),
      generation: text(process.generation, "status.process.generation"),
      terminalOutcome:
        process.terminalOutcome === undefined
          ? undefined
          : oneOf(process.terminalOutcome, PROCESS_OUTCOMES, "status.process.terminalOutcome"),
      detail: optionalText(process.detail, "status.process.detail"),
    },
    turn: {
      state: oneOf(turn.state, TURN_STATES, "status.turn.state"),
      // The wire drops nulls (`exclude_none`), so an absent id means "no turn"; the mirror declares
      // it required-and-nullable, and this is where the two are reconciled.
      turnId: optionalText(turn.turnId, "status.turn.turnId") ?? null,
      stateSince: optionalText(turn.stateSince, "status.turn.stateSince") ?? null,
      waiting:
        turn.waiting === undefined
          ? undefined
          : {
              reason: text(
                record(turn.waiting, "status.turn.waiting").reason,
                "status.turn.waiting.reason",
              ),
              interactionId: optionalText(
                record(turn.waiting, "status.turn.waiting").interactionId,
                "status.turn.waiting.interactionId",
              ),
              operationRef: optionalText(
                record(turn.waiting, "status.turn.waiting").operationRef,
                "status.turn.waiting.operationRef",
              ),
            },
      terminalOutcome:
        turn.terminalOutcome === undefined
          ? undefined
          : {
              state: oneOf(
                record(turn.terminalOutcome, "status.turn.terminalOutcome").state,
                TURN_OUTCOMES,
                "status.turn.terminalOutcome.state",
              ),
              stopReason: optionalText(
                record(turn.terminalOutcome, "status.turn.terminalOutcome").stopReason,
                "status.turn.terminalOutcome.stopReason",
              ),
              operationRef: optionalText(
                record(turn.terminalOutcome, "status.turn.terminalOutcome").operationRef,
                "status.turn.terminalOutcome.operationRef",
              ),
            },
    },
    evidence: {
      ...decodeProvenance(raw.evidence, "status.evidence"),
      adapterRevision:
        record(raw.evidence, "status.evidence").adapterRevision === undefined
          ? undefined
          : number(
              record(raw.evidence, "status.evidence").adapterRevision,
              "status.evidence.adapterRevision",
            ),
    },
  };
}

function decodeItems(value: unknown): ConversationItem[] {
  if (!Array.isArray(value)) throw new Error("capture.items: expected an array");
  return value.map(decodeItem);
}

/** The captured conversation, decoded and validated against the wire mirror. */
export const eveConversationItems: ConversationItem[] = decodeItems(capture.items);

/** The captured session status, decoded and validated against the wire mirror. */
export const eveConversationStatus: ConversationStatus = decodeStatus(capture.status);
