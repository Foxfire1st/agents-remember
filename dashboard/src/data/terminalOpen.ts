import type {
  HarnessControlState,
  TerminalOpenKind,
} from "../types/terminalCatalog";

export interface OpenTerminalOptions {
  label?: string;
  lifecycleId?: string;
  leafKey?: string;
  // 260715-FEUI-L3 R5: the launch pair. COMPLETE pair or neither — a partial pair is refused
  // synchronously (400 launch-selection-invalid); catalog validity is NOT checked at open time.
  model?: string;
  effort?: string;
}

/** The server-owned row facts that are safe to materialize after an accepted open. */
export interface OpenedTerminalSession {
  id: string;
  label: string;
  kind: TerminalOpenKind;
  harness?: string;
  lifecycleId?: string;
  leafKey?: string;
  seatRole?: string;
  status: "running";
  controlState?: HarnessControlState;
  resolvedModel?: string;
  resolvedEffort?: string;
}

export type TerminalOpenFailureKind =
  | "network"
  | "http"
  | "protocol"
  | "harness"
  | "missing-response";

export type TerminalOpenResult =
  | {
      outcome: "opened";
      session: OpenedTerminalSession;
      httpStatus: number;
      responseBody: Record<string, unknown>;
    }
  | {
      outcome: "failed";
      failure: TerminalOpenFailureKind;
      detail: string;
      httpStatus: number | null;
      responseStatus: string | null;
      responseBody?: unknown;
    };

export type TerminalOpenFailure = Extract<TerminalOpenResult, { outcome: "failed" }>;

const stringField = (record: Record<string, unknown>, key: string): string | null =>
  typeof record[key] === "string" ? record[key] : null;

function failedOpen(
  failure: TerminalOpenFailureKind,
  detail: string,
  httpStatus: number | null,
  responseStatus: string | null,
  responseBody?: unknown,
): TerminalOpenFailure {
  return {
    outcome: "failed",
    failure,
    detail,
    httpStatus,
    responseStatus,
    ...(responseBody === undefined ? {} : { responseBody }),
  };
}

function classifyAcceptedOpen(
  sessionId: string,
  kind: TerminalOpenKind,
  harness: string | undefined,
  httpStatus: number,
  body: unknown,
): TerminalOpenResult {
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    return failedOpen(
      "protocol",
      "protocol failure — the open response was not a JSON object",
      httpStatus,
      null,
      body,
    );
  }
  const record = body as Record<string, unknown>;
  const responseStatus = stringField(record, "status");
  const responseSession = stringField(record, "session");
  if (responseSession === null) {
    return failedOpen(
      "missing-response",
      "missing open response — the server did not acknowledge a session id",
      httpStatus,
      responseStatus,
      record,
    );
  }
  if (responseSession !== sessionId) {
    return failedOpen(
      "protocol",
      `protocol failure — server acknowledged session ${responseSession}, expected ${sessionId}`,
      httpStatus,
      responseStatus,
      record,
    );
  }
  const responseKind = stringField(record, "kind");
  if (responseKind !== kind) {
    return failedOpen(
      "protocol",
      `protocol failure — server acknowledged kind ${responseKind ?? "missing"}, expected ${kind}`,
      httpStatus,
      responseStatus,
      record,
    );
  }
  const responseHarnessValue = record.harness;
  const responseHarness =
    typeof responseHarnessValue === "string" ? responseHarnessValue : null;
  if (kind === "harness" && responseHarness !== harness) {
    return failedOpen(
      "protocol",
      `protocol failure — server acknowledged harness ${responseHarness ?? "missing"}, expected ${harness ?? "missing"}`,
      httpStatus,
      responseStatus,
      record,
    );
  }
  if (
    kind === "terminal" &&
    responseHarnessValue !== undefined &&
    responseHarnessValue !== null
  ) {
    return failedOpen(
      "protocol",
      `protocol failure — raw terminal response claimed harness ${JSON.stringify(responseHarnessValue)}`,
      httpStatus,
      responseStatus,
      record,
    );
  }
  const responseControlStateValue = record.controlState;
  if (
    kind === "terminal" &&
    responseControlStateValue !== undefined &&
    responseControlStateValue !== null
  ) {
    return failedOpen(
      "protocol",
      `protocol failure — raw terminal response claimed harness control state ${JSON.stringify(responseControlStateValue)}`,
      httpStatus,
      responseStatus,
      record,
    );
  }
  const label = stringField(record, "label");
  if (label === null || record.status !== "running") {
    return failedOpen(
      "missing-response",
      "missing open response — the accepted row did not include its label and running state",
      httpStatus,
      responseStatus,
      record,
    );
  }
  const lifecycleId = stringField(record, "lifecycleId");
  const leafKey = stringField(record, "leafKey");
  const seatRole = stringField(record, "seatRole");
  const controlState =
    typeof responseControlStateValue === "string"
      ? (responseControlStateValue as HarnessControlState)
      : null;
  const resolvedModel = stringField(record, "resolvedModel");
  const resolvedEffort = stringField(record, "resolvedEffort");
  return {
    outcome: "opened",
    httpStatus,
    responseBody: record,
    session: {
      id: responseSession,
      label,
      kind,
      ...(responseHarness ? { harness: responseHarness } : {}),
      ...(lifecycleId ? { lifecycleId } : {}),
      ...(leafKey ? { leafKey } : {}),
      ...(seatRole ? { seatRole } : {}),
      status: "running",
      ...(controlState ? { controlState } : {}),
      ...(resolvedModel ? { resolvedModel } : {}),
      ...(resolvedEffort ? { resolvedEffort } : {}),
    },
  };
}

/** Stable visible copy for production callers; the failure class remains inspectable in the result. */
export function terminalOpenFailureMessage(result: TerminalOpenFailure): string {
  return `session open ${result.failure}: ${result.detail}`;
}

/**
 * The one browser-side server opener. A row is authoritative only after a parseable 2xx response
 * acknowledges the exact caller-minted session, kind, and harness identity. Development scenarios
 * simulate that server response through the `/dev/bench` fetch injector; production never fails open.
 */
export async function openTerminalSession(
  sessionId: string,
  kind: TerminalOpenKind = "terminal",
  base = "",
  harness?: string,
  options: OpenTerminalOptions = {},
): Promise<TerminalOpenResult> {
  const body = {
    kind,
    ...(harness ? { harness } : {}),
    ...(options.label ? { label: options.label } : {}),
    ...(options.lifecycleId ? { lifecycleId: options.lifecycleId } : {}),
    ...(options.leafKey ? { leafKey: options.leafKey } : {}),
    ...(options.model ? { model: options.model } : {}),
    ...(options.effort ? { effort: options.effort } : {}),
  };
  let response: Response;
  try {
    response = await fetch(`${base}/api/terminal/${encodeURIComponent(sessionId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return failedOpen(
      "network",
      "network failure — the open POST did not answer",
      null,
      null,
    );
  }

  let rawBody: string;
  try {
    rawBody = await response.text();
  } catch {
    return failedOpen(
      "protocol",
      "protocol failure — the open response body could not be read",
      response.status,
      null,
    );
  }
  if (!rawBody.trim()) {
    return failedOpen(
      response.ok ? "missing-response" : "http",
      response.ok
        ? "missing open response — the server returned an empty success body"
        : `HTTP ${response.status} rejected the open without a response body`,
      response.status,
      null,
    );
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(rawBody) as unknown;
  } catch {
    return failedOpen(
      "protocol",
      "protocol failure — the open response was not valid JSON",
      response.status,
      null,
      rawBody,
    );
  }

  if (!response.ok) {
    const record =
      typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : {};
    const responseStatus = stringField(record, "status");
    const detail = stringField(record, "detail") ?? `HTTP ${response.status}`;
    const failure: TerminalOpenFailureKind =
      kind === "harness" &&
      (responseStatus === "bad-kind" || responseStatus === "launch-selection-invalid")
        ? "harness"
        : "http";
    return failedOpen(
      failure,
      `${failure === "harness" ? "harness refusal" : `HTTP ${response.status}`} — ${detail}`,
      response.status,
      responseStatus,
      parsed,
    );
  }

  return classifyAcceptedOpen(sessionId, kind, harness, response.status, parsed);
}
