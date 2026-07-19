/** Locked Pi library helper: SessionManager 0.80.7 list/read/resolve over one JSON-lines loop. */

import { SessionManager, type SessionEntry, type SessionInfo } from "@earendil-works/pi-coding-agent";
import {
  buildHandshake,
  observedDependencyVersion,
  pageByOffset,
  probeRuntimeVersion,
  raiseHelperError,
  serveJsonLines,
  signatureOf,
  windowByOrdinal,
  type HelperRequest,
  type ListRequest,
  type ReadRequest,
  type ResolveResumeTargetRequest,
} from "./protocol.js";

const PI_PACKAGE = "@earendil-works/pi-coding-agent";

interface PiRow {
  sessionId: string;
  sessionFile: string;
  cwd: string;
  name?: string;
  created: string;
  modified: string;
  messageCount: number;
  firstMessage: string;
}

interface PiRecord {
  ordinal: number;
  id: string;
  parentId: string | null;
  type: string;
  timestamp: string;
  message?: unknown;
  customType?: string;
  display?: boolean;
  content?: unknown;
  summary?: string;
  thinkingLevel?: string;
  provider?: string;
  modelId?: string;
  name?: string;
  targetId?: string;
  label?: string;
  firstKeptEntryId?: string;
  tokensBefore?: number;
  fromHook?: boolean;
}

export async function handlePi(request: HelperRequest): Promise<unknown> {
  if (request.operation === "handshake") {
    const observedRuntime = await probeRuntimeVersion("pi");
    const observedHelper = observedDependencyVersion(PI_PACKAGE, PI_PACKAGE);
    return buildHandshake(request, observedRuntime, observedHelper);
  }
  if (request.operation === "list") {
    return await listPiSessions(request);
  }
  if (request.operation === "read") {
    return await readPiSession(request);
  }
  return await resolvePiResumeTarget(request);
}

async function listPiSessions(request: ListRequest): Promise<unknown> {
  const sessions = await SessionManager.list(request.canonicalProjectScope);
  const sorted = [...sessions].sort(
    (left, right) => right.modified.getTime() - left.modified.getTime(),
  );
  const rows: PiRow[] = sorted.map((session) => {
    const row: PiRow = {
      sessionId: session.id,
      sessionFile: session.path,
      cwd: session.cwd,
      created: session.created.toISOString(),
      modified: session.modified.toISOString(),
      messageCount: session.messageCount,
      firstMessage: session.firstMessage,
    };
    if (session.name !== undefined) {
      row.name = session.name;
    }
    return row;
  });
  const page = pageByOffset(rows, request.cursor, request.limit);
  return {
    signature: signatureOf([
      "pi:list",
      request.canonicalProjectScope,
      sorted.map((session) => `${session.id}:${session.modified.getTime()}`),
    ]),
    rows: page.rows,
    nextCursor: page.nextCursor,
  };
}

async function readPiSession(request: ReadRequest): Promise<unknown> {
  const sessionFile = await findSessionFile(
    request.canonicalProjectScope,
    request.vendorConversationId,
  );
  const manager = SessionManager.open(sessionFile);
  const branch = manager.getBranch();
  if (branch.length === 0) {
    return raiseHelperError(
      "stale-identity",
      "the native Pi conversation carries no readable entries",
    );
  }
  const window = windowByOrdinal(branch, request.cursor, request.limit);
  const items: PiRecord[] = window.items.map((entry, index) =>
    recordFromEntry(entry, window.firstOrdinal + index),
  );
  const leaf = branch[branch.length - 1];
  return {
    signature: signatureOf([
      "pi:read",
      request.vendorConversationId,
      branch.length,
      leaf?.id ?? "",
    ]),
    totalItems: branch.length,
    items,
    hasOlder: window.hasOlder,
    olderOrdinal: window.olderOrdinal,
  };
}

async function resolvePiResumeTarget(
  request: ResolveResumeTargetRequest,
): Promise<unknown> {
  const sessionFile = await findSessionFile(
    request.canonicalProjectScope,
    request.vendorConversationId,
  );
  const manager = SessionManager.open(sessionFile);
  const header = manager.getHeader();
  return {
    vendorConversationId: request.vendorConversationId,
    sessionFile,
    cwd: header?.cwd ?? request.canonicalProjectScope,
  };
}

async function findSessionFile(scope: string, sessionId: string): Promise<string> {
  const sessions = await SessionManager.list(scope);
  const found = sessions.find((session: SessionInfo) => session.id === sessionId);
  if (found === undefined) {
    return raiseHelperError(
      "stale-identity",
      "the native Pi conversation is absent from this scope",
    );
  }
  return found.path;
}

function recordFromEntry(entry: SessionEntry, ordinal: number): PiRecord {
  const record: PiRecord = {
    ordinal,
    id: entry.id,
    parentId: entry.parentId,
    type: entry.type,
    timestamp: entry.timestamp,
  };
  if (entry.type === "message") {
    record.message = entry.message;
  } else if (entry.type === "custom_message") {
    record.customType = entry.customType;
    record.content = entry.content;
    record.display = entry.display;
  } else if (entry.type === "custom") {
    record.customType = entry.customType;
  } else if (entry.type === "compaction") {
    record.summary = entry.summary;
    record.firstKeptEntryId = entry.firstKeptEntryId;
    record.tokensBefore = entry.tokensBefore;
    record.fromHook = entry.fromHook === true;
  } else if (entry.type === "branch_summary") {
    record.summary = entry.summary;
    record.fromHook = entry.fromHook === true;
  } else if (entry.type === "thinking_level_change") {
    record.thinkingLevel = entry.thinkingLevel;
  } else if (entry.type === "model_change") {
    record.provider = entry.provider;
    record.modelId = entry.modelId;
  } else if (entry.type === "session_info") {
    if (entry.name !== undefined) {
      record.name = entry.name;
    }
  } else if (entry.type === "label") {
    record.targetId = entry.targetId;
    if (entry.label !== undefined) {
      record.label = entry.label;
    }
  }
  return record;
}

await serveJsonLines(handlePi);
