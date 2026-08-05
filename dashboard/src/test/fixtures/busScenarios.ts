import type { AgentPickupNode, SupervisorHeartbeat } from "../../types/projection";

// FEUI-L7 fixture pack: current pickup owner/redelivery fields plus one persisted legacy row whose
// optional ownership timestamps are absent and whose required attempt count serializes as zero.
// Tests use these same shapes for fleet/filter, escalation, replies, heartbeat counts, and
// backward-compatible rendering.

export const L7_DECISION_PICKUP = {
  id: "pickup-decision-1",
  entryId: "inbox-decision-1",
  lifecycleId: "lc-l6-controlled",
  agentId: "l6-controlled",
  senderAgentId: "architect-1",
  senderRole: "architect",
  recipientRole: "worker",
  ownerRole: "manager",
  ownerAgentId: "manager-l7",
  ownerLifecycleId: "lc-manager-l7",
  gateId: "gate-l7",
  messageKind: "decision-item",
  artifactPath: "notes/decision-l7.md",
  deliveryState: "delivered",
  deliveredToSession: "l6-controlled",
  attemptCount: 2,
  lastAttemptAt: "2026-07-17T19:01:00Z",
  nextAttemptAt: "2026-07-17T19:03:00Z",
  state: "waiting-for-agent",
  ageSeconds: 120,
  ttlSeconds: 900,
} satisfies AgentPickupNode;

export const L7_SENDER_AGENT_ONLY_PICKUP = {
  id: "pickup-sender-agent-only",
  entryId: "inbox-sender-agent-only",
  lifecycleId: "lc-original-recipient",
  agentId: "original-recipient",
  senderAgentId: "reviewer-agent-only",
  recipientRole: "worker",
  gateId: "gate-agent-only",
  messageKind: "escalation",
  artifactPath: "notes/escalation-agent-only.md",
  deliveryState: "unconfirmed",
  attemptCount: 0,
  state: "check-chat",
  ttlSeconds: 900,
} satisfies AgentPickupNode;

export const L7_SENDER_ROLE_ONLY_PICKUP = {
  id: "pickup-sender-role-only",
  entryId: "inbox-sender-role-only",
  lifecycleId: "lc-original-recipient-role-only",
  senderRole: "architect",
  recipientRole: "worker",
  messageKind: "decision-item",
  deliveryState: "queued",
  attemptCount: 0,
  state: "waiting-for-agent",
  ttlSeconds: 900,
} satisfies AgentPickupNode;

export const L7_LIFECYCLE_ONLY_PICKUP = {
  id: "pickup-lifecycle-only",
  entryId: "inbox-lifecycle-only",
  lifecycleId: "lc-original-recipient-only",
  recipientRole: "worker",
  messageKind: "decision-item",
  deliveryState: "queued",
  attemptCount: 0,
  state: "waiting-for-agent",
  ttlSeconds: 900,
} satisfies AgentPickupNode;

export const L7_ESCALATED_PICKUP = {
  id: "pickup-escalation-1",
  entryId: "inbox-escalation-1",
  lifecycleId: "lc-other-worker",
  agentId: "other-worker",
  senderAgentId: "reviewer-1",
  senderRole: "reviewer",
  recipientRole: "worker",
  ownerRole: "orchestrator",
  ownerAgentId: "orchestrator-1",
  messageKind: "escalation",
  deliveryState: "unconfirmed",
  attemptCount: 4,
  lastAttemptAt: "2026-07-17T19:05:00Z",
  nextAttemptAt: "2026-07-17T19:10:00Z",
  escalatedAt: "2026-07-17T19:06:00Z",
  state: "check-chat",
  ageSeconds: 780,
  ttlSeconds: 900,
} satisfies AgentPickupNode;

/** Persisted pre-owner projection: optional ownership timestamps remain visibly absent. */
export const L7_LEGACY_PICKUP = {
  id: "pickup-legacy-1",
  entryId: "inbox-legacy-1",
  senderRole: "system",
  recipientRole: "worker",
  messageKind: "dispatch-brief",
  deliveryState: "queued",
  attemptCount: 0,
  state: "waiting-for-agent",
  ageSeconds: 15,
  ttlSeconds: 300,
} satisfies AgentPickupNode;

export const L7_PICKUPS: AgentPickupNode[] = [
  L7_DECISION_PICKUP,
  L7_ESCALATED_PICKUP,
  L7_LEGACY_PICKUP,
];

export const L7_SUPERVISOR_HEARTBEAT = {
  lastTickAt: "2026-07-17T19:08:00Z",
  ageSeconds: 2,
  staleCutoffSeconds: 30,
  stale: false,
  pendingInboxCount: 3,
  redeliverableInboxCount: 1,
  lastSweepDurationSeconds: 0.18,
} satisfies SupervisorHeartbeat;
