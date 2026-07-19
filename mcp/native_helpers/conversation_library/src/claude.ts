/** Locked Claude library helper: SDK 0.3.207 list/read/resolve over one JSON-lines loop. */

import {
  getSessionInfo,
  getSessionMessages,
  listSessions,
  type SessionMessage,
} from "@anthropic-ai/claude-agent-sdk";
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

const CLAUDE_PACKAGE = "@anthropic-ai/claude-agent-sdk";

interface ClaudeRow {
  sessionId: string;
  summary: string;
  customTitle?: string;
  firstPrompt?: string;
  lastModified: number;
  createdAt?: number;
  cwd?: string;
  fileSize?: number;
  gitBranch?: string;
  tag?: string;
}

interface ClaudeRecord {
  ordinal: number;
  type: string;
  uuid: string;
  parentToolUseId: string | null;
  parentAgentId: string | null;
  timestamp?: string;
  role: string | null;
  content: unknown;
}

export async function handleClaude(request: HelperRequest): Promise<unknown> {
  if (request.operation === "handshake") {
    const observedRuntime = await probeRuntimeVersion("claude");
    const observedHelper = observedDependencyVersion(CLAUDE_PACKAGE, CLAUDE_PACKAGE);
    return buildHandshake(request, observedRuntime, observedHelper);
  }
  if (request.operation === "list") {
    return await listClaudeSessions(request);
  }
  if (request.operation === "read") {
    return await readClaudeSession(request);
  }
  return await resolveClaudeResumeTarget(request);
}

async function listClaudeSessions(request: ListRequest): Promise<unknown> {
  // Scope-exact listing: worktree sessions belong to their own canonical scope; the library
  // never widens a caller's authorized project scope to another checkout's history.
  const sessions = await listSessions({
    dir: request.canonicalProjectScope,
    includeWorktrees: false,
  });
  const sorted = [...sessions].sort((left, right) => right.lastModified - left.lastModified);
  const rows: ClaudeRow[] = sorted.map((session) => {
    const row: ClaudeRow = {
      sessionId: session.sessionId,
      summary: session.summary,
      lastModified: session.lastModified,
    };
    if (session.customTitle !== undefined) row.customTitle = session.customTitle;
    if (session.firstPrompt !== undefined) row.firstPrompt = session.firstPrompt;
    if (session.createdAt !== undefined) row.createdAt = session.createdAt;
    if (session.cwd !== undefined) row.cwd = session.cwd;
    if (session.fileSize !== undefined) row.fileSize = session.fileSize;
    if (session.gitBranch !== undefined) row.gitBranch = session.gitBranch;
    if (session.tag !== undefined) row.tag = session.tag;
    return row;
  });
  const page = pageByOffset(rows, request.cursor, request.limit);
  return {
    signature: signatureOf([
      "claude:list",
      request.canonicalProjectScope,
      sorted.map((session) => `${session.sessionId}:${session.lastModified}`),
    ]),
    rows: page.rows,
    nextCursor: page.nextCursor,
  };
}

async function readClaudeSession(request: ReadRequest): Promise<unknown> {
  let messages: SessionMessage[];
  try {
    messages = await getSessionMessages(request.vendorConversationId, {
      dir: request.canonicalProjectScope,
    });
  } catch (error) {
    return raiseHelperError(
      "stale-identity",
      "the native Claude conversation is not readable in this scope",
    );
  }
  if (messages.length === 0) {
    return raiseHelperError(
      "stale-identity",
      "the native Claude conversation is absent from this scope",
    );
  }
  const window = windowByOrdinal(messages, request.cursor, request.limit);
  const items: ClaudeRecord[] = window.items.map((message, index) => {
    const record: ClaudeRecord = {
      ordinal: window.firstOrdinal + index,
      type: message.type,
      uuid: message.uuid,
      parentToolUseId: message.parent_tool_use_id,
      parentAgentId: message.parent_agent_id,
      role: messageRole(message),
      content: messageContent(message),
    };
    const timestamp = (message as { timestamp?: unknown }).timestamp;
    if (typeof timestamp === "string" && timestamp.length > 0) {
      record.timestamp = timestamp;
    }
    return record;
  });
  return {
    signature: signatureOf([
      "claude:read",
      request.vendorConversationId,
      messages.length,
      messages[messages.length - 1]?.uuid ?? "",
    ]),
    totalItems: messages.length,
    items,
    hasOlder: window.hasOlder,
    olderOrdinal: window.olderOrdinal,
  };
}

async function resolveClaudeResumeTarget(
  request: ResolveResumeTargetRequest,
): Promise<unknown> {
  const info = await getSessionInfo(request.vendorConversationId, {
    dir: request.canonicalProjectScope,
  });
  if (info === undefined) {
    return raiseHelperError(
      "stale-identity",
      "the native Claude conversation is absent from this scope",
    );
  }
  const result: { vendorConversationId: string; cwd?: string; lastModified: number } = {
    vendorConversationId: info.sessionId,
    lastModified: info.lastModified,
  };
  if (info.cwd !== undefined) {
    result.cwd = info.cwd;
  }
  return result;
}

function messageRole(message: SessionMessage): string | null {
  const body = message.message;
  if (typeof body === "object" && body !== null) {
    const role = (body as { role?: unknown }).role;
    if (typeof role === "string" && role.length > 0) {
      return role;
    }
  }
  return null;
}

function messageContent(message: SessionMessage): unknown {
  const body = message.message;
  if (typeof body === "object" && body !== null && "content" in body) {
    return (body as { content?: unknown }).content ?? null;
  }
  return body ?? null;
}

await serveJsonLines(handleClaude);
