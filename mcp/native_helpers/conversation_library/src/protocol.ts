/** Versioned JSON-lines contract shared by the Python host and locked native helpers. */

import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

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

/** A handler maps one validated helper request to its operation result payload. */
export type HelperHandler = (request: HelperRequest) => Promise<unknown>;

/**
 * One JSON-lines request/response loop over stdin/stdout for a locked helper entry.
 *
 * Parse failures answer `invalid-request`; handler failures answer `helper-failed` with the
 * allow-list redacted detail. Every line gets exactly one correlated response so the Python
 * host's request/response pairing can never drift.
 */
export async function serveJsonLines(handler: HelperHandler): Promise<void> {
  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of rl) {
    if (!line.trim()) {
      continue;
    }
    let response: HelperResponse<unknown>;
    let requestId = "unknown";
    try {
      const request = parseHelperRequest(line);
      requestId = request.requestId;
      try {
        const result = await handler(request);
        response = {
          protocolVersion: PROTOCOL_VERSION,
          requestId,
          status: "ok",
          result,
        };
      } catch (error) {
        response = failureFor(requestId, error);
      }
    } catch {
      response = {
        protocolVersion: PROTOCOL_VERSION,
        requestId,
        status: "error",
        error: "invalid-request",
        detail: "request failed the locked helper protocol contract",
      };
    }
    process.stdout.write(`${JSON.stringify(response)}\n`);
  }
}

/** Map one handler failure to the protocol's typed error vocabulary, redacted. */
export function failureFor(requestId: string, error: unknown): HelperFailure {
  const tagged = error as { helperError?: unknown; detail?: unknown };
  if (
    tagged &&
    typeof tagged.helperError === "string" &&
    ["invalid-request", "unsupported", "stale-identity", "helper-failed"].includes(
      tagged.helperError,
    )
  ) {
    return {
      protocolVersion: PROTOCOL_VERSION,
      requestId,
      status: "error",
      error: tagged.helperError as HelperFailure["error"],
      // Operation-level failures may carry allow-listed copy supplied by the helper itself.
      detail:
        typeof tagged.detail === "string" && tagged.detail.length > 0
          ? tagged.detail.slice(0, MAX_SAFE_ERROR_CHARS)
          : redactHelperError(""),
    };
  }
  return {
    protocolVersion: PROTOCOL_VERSION,
    requestId,
    status: "error",
    error: "helper-failed",
    detail: redactHelperError(""),
  };
}

/** Raise a typed helper error the serve loop maps onto the protocol vocabulary. */
export function raiseHelperError(
  error: "invalid-request" | "unsupported" | "stale-identity" | "helper-failed",
  detail: string,
): never {
  const failure = new Error(detail) as Error & { helperError: string };
  failure.helperError = error;
  throw failure;
}

/** Observe one installed runtime's `--version` output; first semver token wins. */
export async function probeRuntimeVersion(command: string): Promise<string> {
  const { stdout } = await execFileAsync(command, ["--version"], {
    timeout: 5000,
    maxBuffer: 64 * 1024,
  });
  const match = /(\d+\.\d+\.\d+)/.exec(stdout);
  if (match === null || match[1] === undefined) {
    throw new Error(`could not observe a semver version from ${command} --version`);
  }
  return match[1];
}

/**
 * Observe the installed version of the helper's OWN locked dependency. Resolution goes through
 * the standard ESM resolver from inside this package, so only the declared lockfile dependency
 * can ever be found — never an npm cache, checkout, or global install.
 */
export function observedDependencyVersion(specifier: string, packageName: string): string {
  let resolved: string;
  try {
    resolved = import.meta.resolve(specifier);
  } catch {
    throw new Error(`locked dependency ${packageName} is not installed`);
  }
  let directory = dirname(fileURLToPath(resolved));
  for (let depth = 0; depth < 4; depth += 1) {
    const candidate = join(directory, "package.json");
    if (existsSync(candidate)) {
      const parsed: unknown = JSON.parse(readFileSync(candidate, "utf8"));
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        (parsed as { name?: unknown }).name === packageName &&
        typeof (parsed as { version?: unknown }).version === "string"
      ) {
        return (parsed as { version: string }).version;
      }
    }
    const parent = dirname(directory);
    if (parent === directory) {
      break;
    }
    directory = parent;
  }
  throw new Error(`locked dependency ${packageName} has no readable package version`);
}

/** Deterministic native-store signature: one SHA-256 over the canonical observable parts. */
export function signatureOf(parts: readonly unknown[]): string {
  return createHash("sha256").update(JSON.stringify(parts)).digest("hex");
}

/** Offset paging over one freshly listed native set; the cursor is the decimal next offset. */
export function pageByOffset<T>(
  all: readonly T[],
  cursor: string | null,
  limit: number,
): { rows: T[]; nextCursor: string | null } {
  const offset = parseCursorInt(cursor, 0, "list");
  const rows = all.slice(offset, offset + limit);
  const next = offset + limit;
  return { rows, nextCursor: next < all.length ? String(next) : null };
}

export interface OrdinalWindow<T> {
  items: T[];
  /** 1-based ordinal of the first returned item within the full chronological list. */
  firstOrdinal: number;
  hasOlder: boolean;
  /** Present only when older items exist: the ordinal the next read passes as `before`. */
  olderOrdinal: number | null;
}

/**
 * Newest-window paging over one freshly read native conversation. Every item's stable global
 * ordinal is its 1-based index in the full native order; the cursor names the ordinal the next
 * older page must stop below.
 */
export function windowByOrdinal<T>(
  all: readonly T[],
  cursor: string | null,
  limit: number,
): OrdinalWindow<T> {
  const bound = cursor === null ? null : parseCursorInt(cursor, 2, "read");
  const upper = Math.min(bound === null ? all.length : bound - 1, all.length);
  const lower = Math.max(0, upper - limit);
  const items = all.slice(lower, upper);
  const firstOrdinal = lower + 1;
  const hasOlder = lower > 0;
  return {
    items,
    firstOrdinal,
    hasOlder,
    olderOrdinal: hasOlder ? firstOrdinal : null,
  };
}

function parseCursorInt(cursor: string | null, minimum: number, label: string): number {
  if (cursor === null) {
    return label === "list" ? 0 : minimum;
  }
  if (!/^\d+$/.test(cursor)) {
    return raiseHelperError("invalid-request", `${label} cursor must be a decimal integer`);
  }
  const value = Number.parseInt(cursor, 10);
  if (!Number.isSafeInteger(value) || value < minimum) {
    return raiseHelperError("invalid-request", `${label} cursor is out of range`);
  }
  return value;
}

export function pinnedHelperVersion(harnessId: HarnessId): string {
  return harnessId === "claude" ? CLAUDE_SDK_VERSION : PI_CODING_AGENT_VERSION;
}

export function buildHandshake(
  request: HandshakeRequest,
  observedRuntimeVersion: string,
  observedHelperVersion: string,
): HelperHandshake {
  // 260718-CHATS-L5F R4 (developer ruling 2026-07-21): THE CONTRACT IS THE ONLY GATE. The
  // handshake reports the observed runtime/helper versions as informational evidence and is always
  // ``ready`` once the helper loaded and matched the wire protocol version — it never compares
  // versions to a locked/expected constant to refuse. The real gate is whether the subsequent
  // list/read/resolve operation succeeds against the installed runtime. ``expectedRuntimeVersion``/
  // ``expectedHelperVersion`` survive on the request as informational provenance only.
  return {
    protocolVersion: PROTOCOL_VERSION,
    requestId: request.requestId,
    status: "ready",
    harnessId: request.harnessId,
    runtimeVersion: observedRuntimeVersion,
    helperVersion: observedHelperVersion,
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
