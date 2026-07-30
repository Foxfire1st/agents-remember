// HTTP client for the landed active + control conversation routes. Follows the house data-client
// idiom (setClient.ts / terminal.ts): same-origin `base` param, template paths with
// encodeURIComponent, an injectable fetch for tests, "or-null" reads, and typed evidence for control
// operations (never guessing a refusal into success). Route shapes are the exact landed wire
// (active/api.py, control/api.py); every epoch-guarded route sends the camelCase `expectedBridgeEpoch`
// query param.

import type {
  ActiveEventCursor,
  ActivePageCursor,
  ConversationPage,
  ConversationRouteError,
  ConversationTelemetry,
  InterruptOperation,
} from "./types";

export type FetchLike = typeof fetch;

const ACTIVE_PREFIX = "/api/terminal";

function activeBase(base: string, sessionId: string): string {
  return `${base}${ACTIVE_PREFIX}/${encodeURIComponent(sessionId)}/conversation`;
}

function epochQuery(epoch: string): string {
  return `expectedBridgeEpoch=${encodeURIComponent(epoch)}`;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

function asRouteError(body: unknown, httpStatus: number): ConversationRouteError {
  if (body !== null && typeof body === "object" && "status" in body) {
    const record = body as { status?: unknown; detail?: unknown };
    return {
      status: typeof record.status === "string" ? record.status : "unknown",
      detail: typeof record.detail === "string" ? record.detail : "",
      httpStatus,
    };
  }
  return { status: "transport", detail: `HTTP ${httpStatus}`, httpStatus };
}

export interface PageQuery {
  before?: ActivePageCursor | null;
  limit?: number;
}

// A page read yields the page, OR the server's typed error (threaded to the banner — §14.5), OR
// a null-error transport drop. A read failure NEVER fabricates a page (§9.1).
export type PageResult =
  | { ok: true; page: ConversationPage }
  | { ok: false; error: ConversationRouteError | null };

// Hung-transport class (observed live): a request on a half-dead socket neither resolves
// nor rejects, freezing hydrate/telemetry/interrupt flows in their pending phase forever. Every
// conversation request carries a hard timeout so a hung transport becomes an ordinary rejection
// the existing recovery paths (quiet retry, re-page, fail-loud) already know how to handle.
const CONVERSATION_REQUEST_TIMEOUT_MS = 15_000;
const withTimeout = (init: RequestInit): RequestInit => ({
  ...init,
  signal: AbortSignal.timeout(CONVERSATION_REQUEST_TIMEOUT_MS),
});

/** GET the native-hydrated active page, preserving the server's typed refusal reason on failure. */
export async function fetchConversationPage(
  sessionId: string,
  epoch: string,
  query: PageQuery = {},
  base = "",
  fetchImpl: FetchLike = fetch,
): Promise<PageResult> {
  const params = [epochQuery(epoch)];
  if (query.before) params.push(`before=${encodeURIComponent(query.before)}`);
  if (query.limit !== undefined) params.push(`limit=${query.limit}`);
  const url = `${activeBase(base, sessionId)}?${params.join("&")}`;
  try {
    const response = await fetchImpl(url, withTimeout({ headers: { Accept: "application/json" } }));
    if (!response.ok) {
      return { ok: false, error: asRouteError(await readJson(response), response.status) };
    }
    return { ok: true, page: (await response.json()) as ConversationPage };
  } catch {
    return { ok: false, error: null };
  }
}

/** GET evidence-bound telemetry. Absent metrics are omitted server-side; null means unavailable. */
export async function fetchConversationTelemetry(
  sessionId: string,
  epoch: string,
  base = "",
  fetchImpl: FetchLike = fetch,
): Promise<ConversationTelemetry | null> {
  const url = `${activeBase(base, sessionId)}/telemetry?${epochQuery(epoch)}`;
  try {
    const response = await fetchImpl(url, withTimeout({ headers: { Accept: "application/json" } }));
    if (!response.ok) return null;
    return (await response.json()) as ConversationTelemetry;
  } catch {
    return null;
  }
}

export type AgentHistoryStatus =
  | "hydrated"
  | "already-hydrated"
  | "unavailable"
  | "not-eligible";

export interface AgentHistoryOutcome {
  status: AgentHistoryStatus;
  agentId: string;
  detail?: string;
  code?: string;
}

export type AgentHistoryResult =
  | { ok: true; outcome: AgentHistoryOutcome }
  | { ok: false; error: ConversationRouteError };

function parseAgentHistory(
  body: unknown,
  httpStatus: number,
): AgentHistoryResult {
  if (body !== null && typeof body === "object") {
    const record = body as {
      status?: unknown;
      agentId?: unknown;
      detail?: unknown;
      code?: unknown;
    };
    if (
      (record.status === "hydrated" ||
        record.status === "already-hydrated" ||
        record.status === "unavailable" ||
        record.status === "not-eligible") &&
      typeof record.agentId === "string"
    ) {
      return {
        ok: true,
        outcome: {
          status: record.status,
          agentId: record.agentId,
          ...(typeof record.detail === "string" ? { detail: record.detail } : {}),
          ...(typeof record.code === "string" ? { code: record.code } : {}),
        },
      };
    }
  }
  return {
    ok: false,
    error: {
      status: "invalid-response",
      detail: "selected child history returned an invalid response",
      httpStatus,
    },
  };
}

function agentHistoryTransportError(error: unknown): ConversationRouteError {
  const name =
    error !== null && typeof error === "object" && "name" in error
      ? String((error as { name?: unknown }).name)
      : "";
  const timedOut = name === "TimeoutError" || name === "AbortError";
  return {
    status: "transport",
    detail: timedOut ? "selected child history request timed out" : "network",
    httpStatus: 0,
  };
}

/** Ask the active projector to backfill only the operator-selected child conversation. */
export async function requestAgentHistory(
  sessionId: string,
  epoch: string,
  agentId: string,
  base = "",
  fetchImpl: FetchLike = fetch,
): Promise<AgentHistoryResult> {
  const url = `${activeBase(base, sessionId)}/agents/${encodeURIComponent(agentId)}/history?${epochQuery(epoch)}`;
  try {
    const response = await fetchImpl(url, withTimeout({ method: "POST" }));
    const body = await readJson(response);
    if (!response.ok) {
      return { ok: false, error: asRouteError(body, response.status) };
    }
    return parseAgentHistory(body, response.status);
  } catch (error) {
    return { ok: false, error: agentHistoryTransportError(error) };
  }
}

export type ControlResult<T> =
  | { ok: true; operation: T }
  | { ok: false; error: ConversationRouteError };

function transportError(): { ok: false; error: ConversationRouteError } {
  return { ok: false, error: { status: "transport", detail: "network", httpStatus: 0 } };
}

// An interrupt response is EITHER the operation (any acknowledgement/settlement, incl. rejected at
// 422) OR a typed error envelope (e.g. a capability refusal). Discriminate on the operation's own
// discriminator so a refusal is never rendered as an accepted interrupt.
function parseInterrupt(
  body: unknown,
  httpStatus: number,
): ControlResult<InterruptOperation> {
  if (body !== null && typeof body === "object" && "acknowledgement" in body) {
    return { ok: true, operation: body as InterruptOperation };
  }
  return { ok: false, error: asRouteError(body, httpStatus) };
}

async function postInterrupt(
  route: "interrupt" | "interrupt-status" | "interrupt-reconcile",
  sessionId: string,
  epoch: string,
  turnId: string,
  requestId: string,
  base: string,
  fetchImpl: FetchLike,
): Promise<ControlResult<InterruptOperation>> {
  const url = `${activeBase(base, sessionId)}/${route}?${epochQuery(epoch)}`;
  try {
    const response = await fetchImpl(url, withTimeout({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ turnId, requestId }),
    }));
    return parseInterrupt(await readJson(response), response.status);
  } catch {
    return transportError();
  }
}

/**
 * Request one exact-turn interrupt with a caller-stable requestId (idempotent; a lost response is
 * reconciled under the SAME id, never retried under a fresh one — §9.5, invariant 27). For pi the
 * turnId names the active AR operation id, not a native turn id; the caller
 * supplies whichever the harness uses.
 */
export function requestInterrupt(
  sessionId: string,
  epoch: string,
  turnId: string,
  requestId: string,
  base = "",
  fetchImpl: FetchLike = fetch,
): Promise<ControlResult<InterruptOperation>> {
  return postInterrupt("interrupt", sessionId, epoch, turnId, requestId, base, fetchImpl);
}

export function interruptStatus(
  sessionId: string,
  epoch: string,
  turnId: string,
  requestId: string,
  base = "",
  fetchImpl: FetchLike = fetch,
): Promise<ControlResult<InterruptOperation>> {
  return postInterrupt("interrupt-status", sessionId, epoch, turnId, requestId, base, fetchImpl);
}

export function interruptReconcile(
  sessionId: string,
  epoch: string,
  turnId: string,
  requestId: string,
  base = "",
  fetchImpl: FetchLike = fetch,
): Promise<ControlResult<InterruptOperation>> {
  return postInterrupt("interrupt-reconcile", sessionId, epoch, turnId, requestId, base, fetchImpl);
}

/** Build the resumable SSE URL for a fresh page's atomically-captured event cursor (§9.2). */
export function conversationEventsUrl(
  sessionId: string,
  epoch: string,
  after: ActiveEventCursor | null,
  base = "",
): string {
  const params = [epochQuery(epoch)];
  if (after) params.push(`after=${encodeURIComponent(after)}`);
  return `${activeBase(base, sessionId)}/events?${params.join("&")}`;
}
