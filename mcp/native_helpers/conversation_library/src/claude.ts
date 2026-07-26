/** Locked Claude library helper: SDK 0.3.207 list/read/resolve over one JSON-lines loop. */

import {
  getSessionInfo,
  getSessionMessages,
  listSessions,
  type SessionMessage,
} from "@anthropic-ai/claude-agent-sdk";
import { readdir, readFile, realpath, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
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
  /** Sub-agent children of this session; always present. */
  agents: ClaudeAgentRow[];
}

interface ClaudeAgentRow {
  agentId: string;
  agentType?: string;
  description?: string;
  toolUseId?: string;
  model?: string;
  spawnDepth?: number;
  lastModified: number;
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
  // Sub-agent enumeration: one sweep over every session's
  // subagents/ directory so the catalog signature covers agents too, then each page row
  // carries its own children.
  const projectDirs = await projectDirCandidates(request.canonicalProjectScope);
  const agentsBySession = new Map<string, ClaudeAgentRow[]>();
  for (const session of sorted) {
    agentsBySession.set(session.sessionId, await listSubagents(projectDirs, session.sessionId));
  }
  const rows: ClaudeRow[] = sorted.map((session) => {
    const row: ClaudeRow = {
      sessionId: session.sessionId,
      summary: session.summary,
      lastModified: session.lastModified,
      agents: agentsBySession.get(session.sessionId) ?? [],
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
      sorted.map((session) =>
        (agentsBySession.get(session.sessionId) ?? []).map(
          (agent) => `${agent.agentId}:${agent.lastModified}`,
        ),
      ),
    ]),
    // Response-level proof of sub-agent enumeration: over an EMPTY catalog there is no row to carry the per-row `agents`
    // evidence, so only this marker lets the library tell "no agents exist" from "the
    // helper predates enumeration" — a helper without it degrades to the visible
    // unavailability note even with zero rows.
    agentsEnumerated: true,
    rows: page.rows,
    nextCursor: page.nextCursor,
  };
}

// -- Sub-agent transcripts -------------------------------------
//
// On-disk authority: ``<configDir>/projects/<slug>/<sessionId>/subagents/`` holding
// ``agent-<agentId>.jsonl`` transcripts plus ``agent-<agentId>.meta.json`` identity
// (agentType/description/toolUseId/spawnDepth/model). The project-dir slug rule is the
// installed claude-agent-sdk's own (verified against sdk.mjs 0.3.207 and live directories):
// non-alphanumerics become "-", and slugs over 200 chars truncate with a base36 Java-hash
// suffix of the ORIGINAL path. Symlinked scopes resolve through the realpath candidate.

const SLUG_MAX_LENGTH = 200;

function javaHash(value: string): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
  }
  return hash;
}

function projectSlug(scope: string): string {
  const slug = scope.replace(/[^a-zA-Z0-9]/g, "-");
  if (slug.length <= SLUG_MAX_LENGTH) {
    return slug;
  }
  return `${slug.slice(0, SLUG_MAX_LENGTH)}-${Math.abs(javaHash(scope)).toString(36)}`;
}

function projectDirFor(scope: string): string {
  const configDir = process.env.CLAUDE_CONFIG_DIR ?? join(homedir(), ".claude");
  return join(configDir, "projects", projectSlug(scope));
}

async function projectDirCandidates(scope: string): Promise<string[]> {
  const candidates = [projectDirFor(scope)];
  try {
    const resolved = await realpath(scope);
    if (resolved !== scope) {
      candidates.push(projectDirFor(resolved));
    }
  } catch {
    // A scope that does not resolve has no provable project directory; the plain
    // candidate remains and simply finds no subagents directory.
  }
  return candidates;
}

async function listSubagents(
  projectDirs: readonly string[],
  sessionId: string,
): Promise<ClaudeAgentRow[]> {
  for (const projectDir of projectDirs) {
    const subagentsDir = join(projectDir, sessionId, "subagents");
    let entries: string[];
    try {
      entries = await readdir(subagentsDir);
    } catch {
      continue;
    }
    const agents: ClaudeAgentRow[] = [];
    for (const entry of entries) {
      const match = /^agent-(.+)\.jsonl$/.exec(entry);
      if (match === null || match[1] === undefined) {
        continue;
      }
      agents.push(await readAgentRow(subagentsDir, match[1]));
    }
    agents.sort((left, right) => left.agentId.localeCompare(right.agentId));
    return agents;
  }
  return [];
}

async function readAgentRow(subagentsDir: string, agentId: string): Promise<ClaudeAgentRow> {
  const transcript = join(subagentsDir, `agent-${agentId}.jsonl`);
  const info = await stat(transcript);
  const row: ClaudeAgentRow = { agentId, lastModified: Math.round(info.mtimeMs) };
  let metaRaw: string;
  try {
    metaRaw = await readFile(join(subagentsDir, `agent-${agentId}.meta.json`), "utf8");
  } catch {
    return row; // No meta evidence: identity stays the honest agent-id fallback.
  }
  let meta: unknown;
  try {
    meta = JSON.parse(metaRaw);
  } catch {
    return raiseHelperError(
      "helper-failed",
      "a native Claude sub-agent .meta.json is not valid JSON",
    );
  }
  if (typeof meta !== "object" || meta === null) {
    return raiseHelperError(
      "helper-failed",
      "a native Claude sub-agent .meta.json is not an object",
    );
  }
  const record = meta as Record<string, unknown>;
  if (typeof record.agentType === "string" && record.agentType.length > 0) {
    row.agentType = record.agentType;
  }
  if (typeof record.description === "string" && record.description.length > 0) {
    row.description = record.description;
  }
  if (typeof record.toolUseId === "string" && record.toolUseId.length > 0) {
    row.toolUseId = record.toolUseId;
  }
  if (typeof record.model === "string" && record.model.length > 0) {
    row.model = record.model;
  }
  if (Number.isSafeInteger(record.spawnDepth)) {
    row.spawnDepth = record.spawnDepth as number;
  }
  return row;
}

interface RawTranscriptLine {
  type: string;
  uuid: string;
  parentToolUseId: string | null;
  parentAgentId: string | null;
  timestamp?: string;
  role: string | null;
  content: unknown;
}

function parseAgentTranscript(content: string): RawTranscriptLine[] {
  const lines: RawTranscriptLine[] = [];
  for (const raw of content.split("\n")) {
    if (!raw.trim()) {
      continue;
    }
    let line: unknown;
    try {
      line = JSON.parse(raw);
    } catch {
      return raiseHelperError(
        "helper-failed",
        "a native Claude sub-agent transcript line is not valid JSON",
      );
    }
    if (typeof line !== "object" || line === null) {
      return raiseHelperError(
        "helper-failed",
        "a native Claude sub-agent transcript line is not an object",
      );
    }
    const record = line as Record<string, unknown>;
    if (typeof record.type !== "string" || typeof record.uuid !== "string") {
      return raiseHelperError(
        "helper-failed",
        "a native Claude sub-agent transcript line lacks type/uuid",
      );
    }
    const message =
      typeof record.message === "object" && record.message !== null
        ? (record.message as Record<string, unknown>)
        : null;
    const parsed: RawTranscriptLine = {
      type: record.type,
      uuid: record.uuid,
      parentToolUseId:
        typeof record.parent_tool_use_id === "string"
          ? record.parent_tool_use_id
          : typeof record.parentToolUseId === "string"
            ? record.parentToolUseId
            : null,
      parentAgentId: typeof record.agentId === "string" ? record.agentId : null,
      role: message !== null && typeof message.role === "string" ? message.role : null,
      content: message !== null ? (message.content ?? null) : null,
    };
    if (typeof record.timestamp === "string" && record.timestamp.length > 0) {
      parsed.timestamp = record.timestamp;
    }
    lines.push(parsed);
  }
  return lines;
}

async function readClaudeAgentTranscript(request: ReadRequest): Promise<unknown> {
  const agentId = request.agentId as string;
  const projectDirs = await projectDirCandidates(request.canonicalProjectScope);
  let lines: RawTranscriptLine[] | null = null;
  for (const projectDir of projectDirs) {
    const file = join(
      projectDir,
      request.vendorConversationId,
      "subagents",
      `agent-${agentId}.jsonl`,
    );
    try {
      lines = parseAgentTranscript(await readFile(file, "utf8"));
      break;
    } catch (error) {
      if ((error as { helperError?: unknown }).helperError !== undefined) {
        throw error; // Malformed native content fails closed, never skipped.
      }
      // File absent under this candidate: try the next one.
    }
  }
  if (lines === null || lines.length === 0) {
    return raiseHelperError(
      "stale-identity",
      "the native Claude sub-agent conversation is absent from this scope",
    );
  }
  const window = windowByOrdinal(lines, request.cursor, request.limit);
  const items: ClaudeRecord[] = window.items.map((line, index) => {
    const record: ClaudeRecord = {
      ordinal: window.firstOrdinal + index,
      type: line.type,
      uuid: line.uuid,
      parentToolUseId: line.parentToolUseId,
      parentAgentId: line.parentAgentId,
      role: line.role,
      content: line.content,
    };
    if (line.timestamp !== undefined) {
      record.timestamp = line.timestamp;
    }
    return record;
  });
  return {
    signature: signatureOf([
      "claude:read",
      request.vendorConversationId,
      agentId,
      lines.length,
      lines[lines.length - 1]?.uuid ?? "",
    ]),
    totalItems: lines.length,
    items,
    hasOlder: window.hasOlder,
    olderOrdinal: window.olderOrdinal,
  };
}

async function readClaudeSession(request: ReadRequest): Promise<unknown> {
  if (request.agentId !== undefined) {
    return await readClaudeAgentTranscript(request);
  }
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
