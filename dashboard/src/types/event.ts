// TypeScript mirror of the observer event envelope (ar-observer-event/v1):
// mcp/src/agents_remember/observer/events.py. The raw `/api/events` SSE channel streams these
// verbatim (camelCase wire form, `exclude_none`); the Event River renders them with trust
// provenance — never pretending `declared` is `observed`.

export type Trust = "declared" | "observed" | "inferred" | "approved";
export type Actor = "model" | "system" | "developer";

export interface ObserverEvent {
  schema: string;
  id: string;
  ts: string;
  kind: string;
  trust: Trust;
  actor: Actor;
  data?: Record<string, unknown>;
  lifecycleId?: string;
  enclosure?: string;
  repoId?: string;
  sessionId?: string;
  spanId?: string;
}
