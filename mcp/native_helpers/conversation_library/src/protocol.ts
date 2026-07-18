/** Versioned JSON-lines contract shared by the Python host and locked native helpers. */

export const PROTOCOL_VERSION = "ar-conversation-library-helper/v1" as const;
export const CLAUDE_SDK_VERSION = "0.3.207" as const;
export const PI_CODING_AGENT_VERSION = "0.80.7" as const;
export const MAX_REQUEST_BYTES = 1024 * 1024;
export const MAX_SAFE_ERROR_CHARS = 512;

export type HarnessId = "claude" | "pi";
export type HelperOperation = "handshake" | "list" | "read" | "resolve-resume-target";

export interface HelperRequestBase {
  protocolVersion: typeof PROTOCOL_VERSION;
  requestId: string;
  operation: HelperOperation;
}

export interface HandshakeRequest extends HelperRequestBase {
  operation: "handshake";
  harnessId: HarnessId;
  expectedRuntimeVersion: string;
  expectedHelperVersion: string;
}

export interface ListRequest extends HelperRequestBase {
  operation: "list";
  harnessId: HarnessId;
  canonicalProjectScope: string;
  cursor: string | null;
  limit: number;
}

export interface ReadRequest extends HelperRequestBase {
  operation: "read";
  harnessId: HarnessId;
  vendorConversationId: string;
  expectedIdentityDigest: string;
  canonicalProjectScope: string;
  cursor: string | null;
  limit: number;
}

export interface ResolveResumeTargetRequest extends HelperRequestBase {
  operation: "resolve-resume-target";
  harnessId: HarnessId;
  vendorConversationId: string;
  expectedIdentityDigest: string;
  canonicalProjectScope: string;
}

export type HelperRequest =
  | HandshakeRequest
  | ListRequest
  | ReadRequest
  | ResolveResumeTargetRequest;

export interface HelperHandshake {
  protocolVersion: typeof PROTOCOL_VERSION;
  requestId: string;
  status: "ready" | "incompatible";
  harnessId: HarnessId;
  runtimeVersion: string;
  helperVersion: string;
  detail?: string;
}

export interface HelperSuccess<T> {
  protocolVersion: typeof PROTOCOL_VERSION;
  requestId: string;
  status: "ok";
  result: T;
}

export interface HelperFailure {
  protocolVersion: typeof PROTOCOL_VERSION;
  requestId: string;
  status: "error";
  error: "invalid-request" | "unsupported" | "stale-identity" | "helper-failed";
  detail: string;
}

export type HelperResponse<T> = HelperHandshake | HelperSuccess<T> | HelperFailure;

const SAFE_HELPER_FAILURE_DETAIL = "helper process failed; raw detail withheld";

export function redactHelperError(_detail: string): string {
  // Raw helper stderr is never a public detail authority. Returning only fixed copy is the
  // allow-list boundary; no future secret syntax or local path can bypass a regex vocabulary.
  return SAFE_HELPER_FAILURE_DETAIL.slice(0, MAX_SAFE_ERROR_CHARS);
}

export function pinnedHelperVersion(harnessId: HarnessId): string {
  return harnessId === "claude" ? CLAUDE_SDK_VERSION : PI_CODING_AGENT_VERSION;
}

export function buildHandshake(
  request: HandshakeRequest,
  observedRuntimeVersion: string,
  observedHelperVersion: string,
): HelperHandshake {
  const selectedVersion = pinnedHelperVersion(request.harnessId);
  const compatible =
    request.expectedHelperVersion === selectedVersion &&
    observedHelperVersion === selectedVersion &&
    observedRuntimeVersion === request.expectedRuntimeVersion;
  return {
    protocolVersion: PROTOCOL_VERSION,
    requestId: request.requestId,
    status: compatible ? "ready" : "incompatible",
    harnessId: request.harnessId,
    runtimeVersion: observedRuntimeVersion,
    helperVersion: observedHelperVersion,
    ...(compatible
      ? {}
      : {
          detail: "runtime/helper version does not match the requested locked fixture",
        }),
  };
}

export function parseHelperRequest(line: string): HelperRequest {
  if (Buffer.byteLength(line, "utf8") > MAX_REQUEST_BYTES) {
    throw new Error("helper request exceeds the protocol byte limit");
  }
  const value: unknown = JSON.parse(line);
  if (!isRecord(value)) {
    throw new Error("helper request must be a JSON object");
  }
  if (value.protocolVersion !== PROTOCOL_VERSION) {
    throw new Error("helper protocol version mismatch");
  }
  if (typeof value.requestId !== "string" || value.requestId.length === 0) {
    throw new Error("helper requestId must be non-empty");
  }
  if (!isOperation(value.operation)) {
    throw new Error("helper operation is unsupported");
  }
  return validateOperationShape(value);
}

function validateOperationShape(value: Record<string, unknown>): HelperRequest {
  const operation = value.operation as HelperOperation;
  requireHarnessId(value.harnessId);
  if (operation === "handshake") {
    requireExactKeys(value, [
      "protocolVersion",
      "requestId",
      "operation",
      "harnessId",
      "expectedRuntimeVersion",
      "expectedHelperVersion",
    ]);
    requireText(value.expectedRuntimeVersion, "expectedRuntimeVersion");
    requireText(value.expectedHelperVersion, "expectedHelperVersion");
    return {
      protocolVersion: PROTOCOL_VERSION,
      requestId: value.requestId as string,
      operation,
      harnessId: value.harnessId,
      expectedRuntimeVersion: value.expectedRuntimeVersion,
      expectedHelperVersion: value.expectedHelperVersion,
    };
  }
  requireText(value.canonicalProjectScope, "canonicalProjectScope");
  if (operation === "list") {
    requireExactKeys(value, [
      "protocolVersion",
      "requestId",
      "operation",
      "harnessId",
      "canonicalProjectScope",
      "cursor",
      "limit",
    ]);
    requirePage(value);
    return {
      protocolVersion: PROTOCOL_VERSION,
      requestId: value.requestId as string,
      operation,
      harnessId: value.harnessId,
      canonicalProjectScope: value.canonicalProjectScope,
      cursor: value.cursor as string | null,
      limit: value.limit as number,
    };
  }
  requireText(value.vendorConversationId, "vendorConversationId");
  requireText(value.expectedIdentityDigest, "expectedIdentityDigest");
  if (operation === "read") {
    requireExactKeys(value, [
      "protocolVersion",
      "requestId",
      "operation",
      "harnessId",
      "vendorConversationId",
      "expectedIdentityDigest",
      "canonicalProjectScope",
      "cursor",
      "limit",
    ]);
    requirePage(value);
    return {
      protocolVersion: PROTOCOL_VERSION,
      requestId: value.requestId as string,
      operation,
      harnessId: value.harnessId,
      vendorConversationId: value.vendorConversationId,
      expectedIdentityDigest: value.expectedIdentityDigest,
      canonicalProjectScope: value.canonicalProjectScope,
      cursor: value.cursor as string | null,
      limit: value.limit as number,
    };
  }
  requireExactKeys(value, [
    "protocolVersion",
    "requestId",
    "operation",
    "harnessId",
    "vendorConversationId",
    "expectedIdentityDigest",
    "canonicalProjectScope",
  ]);
  return {
    protocolVersion: PROTOCOL_VERSION,
    requestId: value.requestId as string,
    operation,
    harnessId: value.harnessId,
    vendorConversationId: value.vendorConversationId,
    expectedIdentityDigest: value.expectedIdentityDigest,
    canonicalProjectScope: value.canonicalProjectScope,
  };
}

function requireExactKeys(value: Record<string, unknown>, allowed: readonly string[]): void {
  const allowedKeys = new Set(allowed);
  if (Object.keys(value).some((key) => !allowedKeys.has(key))) {
    throw new Error("helper request contains fields outside its operation contract");
  }
}

function requireHarnessId(value: unknown): asserts value is HarnessId {
  if (value !== "claude" && value !== "pi") {
    throw new Error("helper harnessId must be claude or pi");
  }
}

function requireText(value: unknown, field: string): asserts value is string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`helper ${field} must be non-empty`);
  }
}

function requirePage(value: Record<string, unknown>): void {
  if (value.cursor !== null && typeof value.cursor !== "string") {
    throw new Error("helper cursor must be a string or null");
  }
  if (!Number.isSafeInteger(value.limit) || (value.limit as number) < 1) {
    throw new Error("helper limit must be a positive safe integer");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isOperation(value: unknown): value is HelperOperation {
  return (
    value === "handshake" ||
    value === "list" ||
    value === "read" ||
    value === "resolve-resume-target"
  );
}
