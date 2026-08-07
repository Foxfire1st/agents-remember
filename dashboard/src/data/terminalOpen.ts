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

function openResponseRecord(body: unknown): Record<string, unknown> | null {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return null;
  return body as Record<string, unknown>;
}

function identityFailure(
  record: Record<string, unknown>,
  sessionId: string,
  kind: TerminalOpenKind,
  responseStatus: string | null,
  httpStatus: number,
): TerminalOpenFailure | null {
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
  return null;
}

function harnessClaimFailure(
  record: Record<string, unknown>,
  kind: TerminalOpenKind,
  harness: string | undefined,
  responseStatus: string | null,
  httpStatus: number,
): TerminalOpenFailure | null {
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
  return null;
}

function controlStateClaimFailure(
  record: Record<string, unknown>,
  kind: TerminalOpenKind,
  responseStatus: string | null,
  httpStatus: number,
): TerminalOpenFailure | null {
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
  return null;
}

function claimFailure(
  record: Record<string, unknown>,
  kind: TerminalOpenKind,
  harness: string | undefined,
  responseStatus: string | null,
  httpStatus: number,
): TerminalOpenFailure | null {
  const harnessError = harnessClaimFailure(record, kind, harness, responseStatus, httpStatus);
  if (harnessError !== null) return harnessError;
  return controlStateClaimFailure(record, kind, responseStatus, httpStatus);
}

function openedResult(
  record: Record<string, unknown>,
  sessionId: string,
  kind: TerminalOpenKind,
  responseHarness: string | null,
  label: string,
  httpStatus: number,
): TerminalOpenResult {
  const lifecycleId = stringField(record, "lifecycleId");
  const leafKey = stringField(record, "leafKey");
  const seatRole = stringField(record, "seatRole");
  const controlState =
    typeof record.controlState === "string"
      ? (record.controlState as HarnessControlState)
      : null;
  const resolvedModel = stringField(record, "resolvedModel");
  const resolvedEffort = stringField(record, "resolvedEffort");
  return {
    outcome: "opened",
    httpStatus,
    responseBody: record,
    session: {
      id: stringField(record, "session") ?? sessionId,
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

function classifyAcceptedOpen(
  sessionId: string,
  kind: TerminalOpenKind,
  harness: string | undefined,
  httpStatus: number,
  body: unknown,
): TerminalOpenResult {
  const record = openResponseRecord(body);
  if (record === null) {
    return failedOpen(
      "protocol",
      "protocol failure — the open response was not a JSON object",
      httpStatus,
      null,
      body,
    );
  }
  const responseStatus = stringField(record, "status");
  const identityError = identityFailure(record, sessionId, kind, responseStatus, httpStatus);
  if (identityError !== null) return identityError;
  const responseHarnessValue = record.harness;
  const responseHarness =
    typeof responseHarnessValue === "string" ? responseHarnessValue : null;
  const claimError = claimFailure(record, kind, harness, responseStatus, httpStatus);
  if (claimError !== null) return claimError;
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
  return openedResult(
    record,
    sessionId,
    kind,
    responseHarness,
    label,
    httpStatus,
  );
}

/** Stable visible copy for production callers; the failure class remains inspectable in the result. */
export function terminalOpenFailureMessage(result: TerminalOpenFailure): string {
  return `session open ${result.failure}: ${result.detail}`;
}

function openRequestBody(
  kind: TerminalOpenKind,
  harness: string | undefined,
  options: OpenTerminalOptions,
): Record<string, unknown> {
  return {
    kind,
    ...(harness ? { harness } : {}),
    ...(options.label ? { label: options.label } : {}),
    ...(options.lifecycleId ? { lifecycleId: options.lifecycleId } : {}),
    ...(options.leafKey ? { leafKey: options.leafKey } : {}),
    ...(options.model ? { model: options.model } : {}),
    ...(options.effort ? { effort: options.effort } : {}),
  };
}

function httpRejection(
  parsed: unknown,
  response: Response,
  kind: TerminalOpenKind,
): TerminalOpenResult {
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

function emptyBodyFailure(response: Response): TerminalOpenResult {
  return failedOpen(
    response.ok ? "missing-response" : "http",
    response.ok
      ? "missing open response — the server returned an empty success body"
      : `HTTP ${response.status} rejected the open without a response body`,
    response.status,
    null,
  );
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
  const body = openRequestBody(kind, harness, options);
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
    return emptyBodyFailure(response);
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
    return httpRejection(parsed, response, kind);
  }

  return classifyAcceptedOpen(sessionId, kind, harness, response.status, parsed);
}
