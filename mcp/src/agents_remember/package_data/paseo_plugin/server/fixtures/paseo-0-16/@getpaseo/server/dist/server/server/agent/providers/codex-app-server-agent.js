import { applyProviderOptionsPatch } from "../option-controls.js";
import { assertChangesAreLive, validateProviderOptions } from "../provider-options.js";
import { CodexBackgroundInventory } from "./codex/background-inventory.js";
import { getAgentStreamEventTurnId, } from "../agent-sdk-types.js";
import { importSessionFromPersistence } from "../provider-session-import.js";
import { runProviderRefreshActivity } from "../provider-refresh-deadline.js";
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";
import { z } from "zod";
import { renderPromptAttachmentAsText } from "../prompt-attachments.js";
import { composeSystemPromptParts } from "../system-prompt.js";
import { CodexAsyncQuestions, codexAsyncQuestionToTimeline } from "./codex/async-questions.js";
import { fileChangesToPreview, mapCodexToolCallEnvelope, mapCodexToolCallFromThreadItem, normalizeCommandExecutionCommand, splitCodexMcpToolResultImages, } from "./codex/tool-call-mapper.js";
import { normalizeCodexFilePath } from "./codex/tool-call-detail-parser.js";
import { checkProviderLaunchAvailable, createProviderEnv, resolveProviderLaunch, } from "../provider-launch-config.js";
import { findExecutable, probeExecutable, } from "../../../executable-resolution/executable-resolution.js";
import { createPathEquivalenceMatcher } from "../../../utils/path.js";
import { spawnProcess } from "../../../utils/spawn.js";
import { extractCodexTerminalSessionId, nonEmptyString } from "./tool-call-mapper-utils.js";
import { buildCodexControls, CodexServiceTierSchema, readCodexServiceTier, } from "./codex-feature-definitions.js";
import { CodexAppServerClient, CodexAppServerRpcError, parseCodexThreadForkResponse, parseCodexThreadRollbackResponse, } from "./codex/app-server-transport.js";
import { codexAppServerStartup } from "./codex/app-server-startup.js";
import { revertCodexConversation } from "./codex/rewind.js";
import { CodexThreadHistoryTooLargeError, readCodexThreadHistory, } from "./codex/thread-history.js";
import { materializeProviderImage, renderProviderImageOutputAsAssistantMarkdown, } from "./provider-image-output.js";
import { normalizeProviderReplayTimestamp } from "../provider-history-timestamps.js";
import { limitAgentTimelineItemContent } from "../agent-timeline-content.js";
import { formatProviderDiagnostic, formatProviderDiagnosticError, buildBinaryDiagnosticRows, buildCommandResolutionDiagnosticRows, resolveBinaryVersion, } from "./diagnostic-utils.js";
import { appendOrReplaceGrowingAssistantMessage, runProviderTurn } from "./provider-runner.js";
import { MODE_APPLIES_NEXT_TURN_NOTICE, THINKING_APPLIES_NEXT_TURN_NOTICE, } from "../provider-notices.js";
import { applyCodexToolPolicy, CodexProviderOptionsSchema, } from "./codex/options.js";
function assertChildWithPipes(child) {
    if (!child.stdin || !child.stdout || !child.stderr) {
        throw new Error("Child process did not expose stdio pipes");
    }
}
function isRecord(value) {
    return value != null && typeof value === "object" && !Array.isArray(value);
}
function isArchivedCodexThreadResumeError(error, threadId) {
    if (!(error instanceof Error))
        return false;
    const expectedMessage = `session ${threadId} is archived. ` +
        `Run \`codex unarchive ${threadId}\` to unarchive it first.`;
    return error.message === expectedMessage;
}
function isCodexAlreadyUnarchivedError(error, threadId) {
    const message = error instanceof Error ? error.message : String(error);
    return message.includes(`no archived rollout found for thread id ${threadId}`);
}
const TURN_START_TIMEOUT_MS = 90 * 1000;
const INTERRUPT_TIMEOUT_MS = 2000;
// `turn/interrupt` ends a turn but leaves the shell commands it started running.
// Each terminate is bounded by the first constant. A turn start waits for the
// interrupted turn's commands to end, at most the interrupt's own bound plus one
// terminate's and a margin, so it gives up only when a request outlives its timeout.
const INTERRUPTED_TURN_TERMINATE_TIMEOUT_MS = 2000;
const INTERRUPTED_TURN_CLEANUP_WAIT_MS = INTERRUPT_TIMEOUT_MS + INTERRUPTED_TURN_TERMINATE_TIMEOUT_MS + 500;
// Turns whose commands are remembered; older turns are forgotten first.
const INTERRUPTED_TURN_TRACKED_TURNS = 16;
const CODEX_PROVIDER = "codex";
// These native provider identities belong to Codex, not to an older custom
// Paseo provider. Never include them in a custom provider's legacy import scan.
const CODEX_BUILTIN_MODEL_PROVIDER_IDS = new Set([
    "openai",
    "ollama",
    "lmstudio",
    "amazon-bedrock",
    "amazon-bedrock-runtime",
    "gcp",
]);
// Codex treats most app-server client names as the model-request originator.
// This reserved Codex name is non-originating, so requests keep Codex's default
// CLI identity instead of showing up as Paseo in provider usage logs.
const CODEX_NON_ORIGINATING_APP_SERVER_CLIENT_INFO = {
    name: "codex_app_server_daemon",
    title: "Codex App Server Daemon",
    version: "0.0.0",
};
const ASSISTANT_MESSAGE_BOUNDARY_MARKDOWN = "\n\n---\n\n";
const MAX_PENDING_SUB_AGENT_THREADS = 32;
const MAX_PENDING_SUB_AGENT_NOTIFICATIONS_PER_THREAD = 128;
// COMPAT(codexLegacyCollabAgentToolCall): Codex <0.143 emits this shape. Added in
// Paseo v0.1.105; remove after 2027-01-09 once the supported Codex floor is >=0.143.
const CODEX_TOOL_THREAD_ITEM_TYPES = new Set([
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "webSearch",
    "collabAgentToolCall",
    "subAgentActivity",
]);
const CODEX_CONTEXT_COMPACTION_TYPE = "contextCompaction";
const CODEX_PLAN_IMPLEMENTATION_PROMPT_PREFIX = "The user approved the plan. Implement it now. Do not restate or revise the plan unless blocked.";
// Codex's experimental `goals` feature ships in 0.128.0+. Older binaries reject
// `--enable goals` at launch, so we gate by version and silently skip the flag
// (and the /goal slash command) when the binary is too old.
const CODEX_GOALS_MIN_VERSION = [0, 128, 0];
const CODEX_AUTO_REVIEW_MIN_VERSION = [0, 115, 0];
// Codex removed `thread/rollback` in 0.156.0, so rewinding a legacy thread there
// has to fork before the target turn instead.
const CODEX_THREAD_ROLLBACK_REMOVED_VERSION = [0, 156, 0];
function parseCodexVersion(versionOutput) {
    const match = versionOutput.match(/(\d+)\.(\d+)\.(\d+)/);
    if (!match)
        return null;
    return [Number(match[1]), Number(match[2]), Number(match[3])];
}
function codexVersionAtLeast(versionOutput, min) {
    const parsed = parseCodexVersion(versionOutput);
    if (!parsed)
        return false;
    for (let i = 0; i < 3; i += 1) {
        if (parsed[i] > min[i])
            return true;
        if (parsed[i] < min[i])
            return false;
    }
    return true;
}
// The initialize userAgent starts with `<client>/<codex version>`. An agent we
// cannot parse is treated as an older server that still has rollback.
function codexServerHasThreadRollback(userAgent) {
    return (typeof userAgent !== "string" ||
        !codexVersionAtLeast(userAgent, CODEX_THREAD_ROLLBACK_REMOVED_VERSION));
}
function parseGoalSubcommand(args) {
    const trimmed = (args ?? "").trim();
    if (!trimmed)
        return { kind: "usage" };
    const lower = trimmed.toLowerCase();
    if (lower === "pause")
        return { kind: "pause" };
    if (lower === "resume")
        return { kind: "resume" };
    if (lower === "clear")
        return { kind: "clear" };
    return { kind: "set", objective: trimmed };
}
function formatOutOfBandStatusMessage(text) {
    return `${text.replace(/\n+$/u, "")}\n\n`;
}
const CODEX_APP_SERVER_CAPABILITIES = {
    supportsStreaming: true,
    supportsSessionPersistence: true,
    supportsSessionListing: true,
    supportsDynamicModes: false,
    supportsMcpServers: true,
    supportsReasoningStream: true,
    supportsToolInvocations: true,
    supportsRewindConversation: true,
    supportsRewindFiles: false,
    supportsRewindBoth: false,
};
const CODEX_MODES = [
    {
        id: "auto",
        label: "Default Permissions",
        description: "Edit files and run commands with Codex's default approval flow.",
    },
    {
        id: "auto-review",
        label: "Auto-review",
        description: "Same workspace-write permissions as Default, but eligible `on-request` approvals are routed through the auto-reviewer subagent.",
    },
    {
        id: "full-access",
        label: "Full Access",
        description: "Edit files, run commands, and access the network without additional prompts.",
    },
];
const DEFAULT_CODEX_MODE_ID = "auto";
const MODE_PRESETS = {
    "read-only": {
        approvalPolicy: "on-request",
        sandbox: "read-only",
        approvalsReviewer: "user",
    },
    auto: {
        approvalPolicy: "on-request",
        sandbox: "workspace-write",
        approvalsReviewer: "user",
    },
    "auto-review": {
        approvalPolicy: "on-request",
        sandbox: "workspace-write",
        approvalsReviewer: "auto_review",
    },
    "full-access": {
        approvalPolicy: "never",
        sandbox: "danger-full-access",
        approvalsReviewer: "user",
    },
};
function isAutoReviewReviewer(value) {
    return value === "auto_review" || value === "guardian_subagent";
}
function shouldPromoteThreadResponseToAutoReview(params) {
    return (isAutoReviewReviewer(params.approvalsReviewer) &&
        params.approvalPolicy === "on-request" &&
        params.sandbox === "workspace-write");
}
function validateCodexMode(modeId) {
    if (!(modeId in MODE_PRESETS)) {
        const validModes = Object.keys(MODE_PRESETS).join(", ");
        throw new Error(`Invalid Codex mode "${modeId}". Valid modes are: ${validModes}`);
    }
}
function normalizeCodexThinkingOptionId(thinkingOptionId) {
    if (typeof thinkingOptionId !== "string") {
        return undefined;
    }
    const normalized = thinkingOptionId.trim();
    if (!normalized || normalized === "default") {
        return undefined;
    }
    return normalized;
}
function normalizeCodexModelId(modelId) {
    if (typeof modelId !== "string") {
        return undefined;
    }
    const normalized = modelId.trim();
    if (!normalized) {
        return undefined;
    }
    return normalized;
}
function normalizeCodexModelLabel(displayName) {
    return displayName.replace(/\bgpt\b/gi, "GPT");
}
function isSchemaRecord(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}
function isObjectSchemaNode(schema) {
    const type = schema.type;
    return (isSchemaRecord(schema.properties) ||
        type === "object" ||
        (Array.isArray(type) && type.includes("object")));
}
// Keywords whose value maps names to schemas.
const SCHEMA_MAP_KEYWORDS = [
    "properties",
    "patternProperties",
    "$defs",
    "definitions",
    "dependentSchemas",
];
// Keywords whose value is a schema or an array of schemas.
const SUBSCHEMA_KEYWORDS = [
    "items",
    "prefixItems",
    "additionalItems",
    "contains",
    "additionalProperties",
    "unevaluatedProperties",
    "unevaluatedItems",
    "propertyNames",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
];
function normalizeCodexOutputSubschema(schema, schemaPath) {
    if (Array.isArray(schema)) {
        return schema.map((entry, index) => normalizeCodexOutputSchemaNode(entry, `${schemaPath}[${index}]`));
    }
    return normalizeCodexOutputSchemaNode(schema, schemaPath);
}
function normalizeCodexOutputSchemaNode(schema, schemaPath) {
    if (!isSchemaRecord(schema)) {
        return schema;
    }
    const normalized = { ...schema };
    for (const keyword of SCHEMA_MAP_KEYWORDS) {
        const schemas = schema[keyword];
        if (isSchemaRecord(schemas)) {
            normalized[keyword] = Object.fromEntries(Object.entries(schemas).map(([name, child]) => [
                name,
                normalizeCodexOutputSchemaNode(child, `${schemaPath}.${keyword}.${name}`),
            ]));
        }
    }
    for (const keyword of SUBSCHEMA_KEYWORDS) {
        if (keyword in schema) {
            normalized[keyword] = normalizeCodexOutputSubschema(schema[keyword], `${schemaPath}.${keyword}`);
        }
    }
    if (!isObjectSchemaNode(normalized)) {
        return normalized;
    }
    if (normalized.additionalProperties === undefined) {
        normalized.additionalProperties = false;
    }
    else if (normalized.additionalProperties !== false) {
        throw new Error(`Codex structured outputs require ${schemaPath} to set additionalProperties to false for object schemas.`);
    }
    const properties = isSchemaRecord(normalized.properties) ? normalized.properties : null;
    if (!properties) {
        return normalized;
    }
    const propertyKeys = Object.keys(properties);
    const existingRequired = Array.isArray(normalized.required)
        ? normalized.required.filter((entry) => typeof entry === "string")
        : [];
    normalized.required = Array.from(new Set([...existingRequired, ...propertyKeys]));
    return normalized;
}
export function normalizeCodexOutputSchema(schema) {
    if (!isSchemaRecord(schema)) {
        throw new Error("Codex structured outputs require a JSON object schema.");
    }
    const normalized = normalizeCodexOutputSchemaNode(schema, "$");
    if (!isSchemaRecord(normalized) || !isObjectSchemaNode(normalized)) {
        throw new Error("Codex structured outputs require a root object schema.");
    }
    return normalized;
}
function mergeCodexConfiguredDefaults(primary, fallback) {
    return {
        model: primary.model ?? fallback.model,
        thinkingOptionId: primary.thinkingOptionId ?? fallback.thinkingOptionId,
    };
}
function codexMicrosoftStorePackageRoot() {
    const localAppData = process.env.LOCALAPPDATA;
    if (!localAppData) {
        return null;
    }
    return path.join(localAppData, "Packages");
}
export function codexMicrosoftStoreBinaryCandidates(packageRoot, entries) {
    return entries
        .filter((entry) => entry.isDirectory() && entry.name.startsWith("OpenAI.Codex_"))
        .map((entry) => path.join(packageRoot, entry.name, "LocalCache", "Local", "OpenAI", "Codex", "bin", "codex.exe"))
        .sort();
}
export async function findCodexMicrosoftStoreBinary() {
    if (process.platform !== "win32") {
        return null;
    }
    const packageRoot = codexMicrosoftStorePackageRoot();
    if (!packageRoot) {
        return null;
    }
    let entries;
    try {
        entries = await fs.readdir(packageRoot, { withFileTypes: true });
    }
    catch {
        return null;
    }
    for (const candidate of codexMicrosoftStoreBinaryCandidates(packageRoot, entries)) {
        if (await probeExecutable(candidate)) {
            return candidate;
        }
    }
    return null;
}
export async function findDefaultCodexBinary() {
    const pathBinary = await findExecutable("codex");
    if (pathBinary)
        return pathBinary;
    return await findCodexMicrosoftStoreBinary();
}
async function resolveCodexLaunchPrefix(runtimeSettings) {
    const launch = await resolveCodexLaunch(runtimeSettings);
    const availability = await checkCodexLaunchAvailable(launch);
    if (!availability.available) {
        throw new Error("Codex binary not found. Install the Codex CLI (https://github.com/openai/codex) and ensure it is available in your shell PATH.");
    }
    return {
        command: launch.source === "override" ? launch.command : (availability.resolvedPath ?? launch.command),
        args: launch.args,
    };
}
const defaultCodexAppServerProcess = {
    resolvePrefix: ({ runtimeSettings }) => resolveCodexLaunchPrefix(runtimeSettings),
    spawn: ({ command, args, options }) => spawnProcess(command, args, options),
};
async function resolveCodexLaunch(runtimeSettings) {
    return resolveProviderLaunch({
        commandConfig: runtimeSettings?.command,
        defaultBinary: {
            command: "codex",
            resolvePath: findDefaultCodexBinary,
        },
    });
}
async function checkCodexLaunchAvailable(launch) {
    return checkProviderLaunchAvailable(launch, {
        command: "codex",
        resolvePath: findDefaultCodexBinary,
    });
}
function resolveCodexHomeDir(env) {
    return env.CODEX_HOME ?? path.join(os.homedir(), ".codex");
}
function decodeEscapedChar(next) {
    if (next === "n")
        return "\n";
    if (next === "t")
        return "\t";
    return next;
}
function resolvePermissionDecision(response) {
    if (response.behavior === "allow")
        return "accept";
    if (response.interrupt)
        return "cancel";
    return "decline";
}
function firstPositiveFiniteNumber(primary, secondary) {
    if (typeof primary === "number" && Number.isFinite(primary) && primary > 0) {
        return primary;
    }
    if (typeof secondary === "number" && Number.isFinite(secondary) && secondary > 0) {
        return secondary;
    }
    return undefined;
}
function tokenizeCommandArgs(args) {
    const tokens = [];
    let current = "";
    let quote = null;
    for (let i = 0; i < args.length; i += 1) {
        const ch = args[i];
        if (quote) {
            if (ch === quote) {
                quote = null;
                continue;
            }
            if (ch === "\\" && i + 1 < args.length) {
                const next = args[i + 1];
                if (next === quote || next === "\\" || next === "n" || next === "t") {
                    i += 1;
                    current += decodeEscapedChar(next);
                    continue;
                }
            }
            current += ch;
            continue;
        }
        if (ch === "'" || ch === '"') {
            quote = ch;
            continue;
        }
        if (/\s/.test(ch)) {
            if (current) {
                tokens.push(current);
                current = "";
            }
            continue;
        }
        current += ch;
    }
    if (current) {
        tokens.push(current);
    }
    return tokens;
}
function parseFrontMatter(markdown) {
    const lines = markdown.split("\n");
    if (lines[0]?.trim() !== "---") {
        return { frontMatter: {}, body: markdown };
    }
    let end = -1;
    for (let i = 1; i < lines.length; i += 1) {
        if (lines[i]?.trim() === "---") {
            end = i;
            break;
        }
    }
    if (end === -1) {
        return { frontMatter: {}, body: markdown };
    }
    const metaLines = lines.slice(1, end);
    const body = lines.slice(end + 1).join("\n");
    const frontMatter = {};
    for (const line of metaLines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith("#")) {
            continue;
        }
        const idx = trimmed.indexOf(":");
        if (idx <= 0) {
            continue;
        }
        const key = trimmed.slice(0, idx).trim();
        let value = trimmed.slice(idx + 1).trim();
        value = value.replace(/^['"]/, "").replace(/['"]$/, "");
        if (key && value) {
            frontMatter[key] = value;
        }
    }
    return { frontMatter, body };
}
async function listCodexCustomPrompts(codexHome) {
    const promptsDir = path.join(codexHome, "prompts");
    let entries;
    try {
        entries = await fs.readdir(promptsDir, { withFileTypes: true });
    }
    catch {
        return [];
    }
    const mdEntries = entries.filter((entry) => entry.isFile() && entry.name.endsWith(".md") && entry.name.slice(0, -".md".length));
    const parsedCommands = await Promise.all(mdEntries.map(async (entry) => {
        const name = entry.name.slice(0, -".md".length);
        const fullPath = path.join(promptsDir, entry.name);
        let content;
        try {
            content = await fs.readFile(fullPath, "utf8");
        }
        catch {
            return null;
        }
        const parsed = parseFrontMatter(content);
        const description = parsed.frontMatter["description"] ?? "Custom prompt";
        const argumentHint = parsed.frontMatter["argument-hint"] ?? parsed.frontMatter["argument_hint"] ?? "";
        return {
            name: `prompts:${name}`,
            description,
            argumentHint,
            kind: "command",
        };
    }));
    const commands = parsedCommands.filter((cmd) => cmd !== null);
    return commands.sort((a, b) => a.name.localeCompare(b.name));
}
export async function listCodexSkills(cwd, codexHome, workspaceGitService) {
    const candidates = [];
    candidates.push(path.join(cwd, ".codex", "skills"));
    const repoRoot = workspaceGitService
        ? await workspaceGitService.resolveRepoRoot(cwd).catch(() => null)
        : null;
    if (repoRoot) {
        candidates.push(path.join(path.dirname(cwd), ".codex", "skills"));
        candidates.push(path.join(repoRoot, ".codex", "skills"));
    }
    candidates.push(path.join(codexHome, "skills"));
    const candidateReads = await Promise.all(candidates.map(async (dir) => {
        let entries;
        try {
            entries = await fs.readdir(dir, { withFileTypes: true });
        }
        catch {
            return [];
        }
        const dirEntries = entries.filter((entry) => entry.isDirectory() || entry.isSymbolicLink());
        const skillContents = await Promise.all(dirEntries.map(async (entry) => {
            const skillDir = path.join(dir, entry.name);
            const skillPath = path.join(skillDir, "SKILL.md");
            try {
                return await fs.readFile(skillPath, "utf8");
            }
            catch {
                return null;
            }
        }));
        return skillContents.filter((content) => content !== null);
    }));
    const commandsByName = new Map();
    for (const skillContents of candidateReads) {
        for (const content of skillContents) {
            const { frontMatter } = parseFrontMatter(content);
            const name = frontMatter["name"];
            const description = frontMatter["description"];
            if (!name || !description) {
                continue;
            }
            if (!commandsByName.has(name)) {
                commandsByName.set(name, {
                    name,
                    description,
                    argumentHint: "",
                    kind: "skill",
                });
            }
        }
    }
    return Array.from(commandsByName.values()).sort((a, b) => a.name.localeCompare(b.name));
}
function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function expandCodexCustomPrompt(template, args) {
    const trimmedArgs = args ? args.trim() : "";
    const tokens = trimmedArgs ? tokenizeCommandArgs(trimmedArgs) : [];
    const named = {};
    const positional = [];
    for (const token of tokens) {
        const idx = token.indexOf("=");
        if (idx > 0) {
            const key = token.slice(0, idx);
            const value = token.slice(idx + 1);
            if (key) {
                named[key] = value;
                continue;
            }
        }
        positional.push(token);
    }
    // Match every placeholder in one pass, so inserted argument text is never expanded again.
    const namedPlaceholders = Object.keys(named)
        .sort((a, b) => b.length - a.length)
        .map((key) => `${escapeRegExp(key)}\\b`);
    const placeholder = new RegExp(`\\$(${["\\$", "ARGUMENTS", "[1-9]", ...namedPlaceholders].join("|")})`, "g");
    return template.replace(placeholder, (match, name) => {
        if (name === "$")
            return "$";
        if (name === "ARGUMENTS")
            return trimmedArgs;
        if (/^[1-9]$/.test(name))
            return positional[Number(name) - 1] ?? "";
        return named[name] ?? match;
    });
}
function toCodexMcpConfig(config) {
    switch (config.type) {
        case "stdio":
            return {
                command: config.command,
                args: config.args,
                env: config.env,
            };
        case "http":
            return {
                url: config.url,
                http_headers: config.headers,
            };
        case "sse":
            return {
                url: config.url,
                http_headers: config.headers,
            };
        default: {
            const _exhaustive = config;
            throw new Error(`Unsupported MCP config type: ${String(_exhaustive.type)}`);
        }
    }
}
function toObjectRecord(value) {
    return isRecord(value) ? value : undefined;
}
function isDefinitiveCodexSteerRejection(error) {
    if (!(error instanceof CodexAppServerRpcError))
        return false;
    if (error.code === -32601)
        return true;
    if (error.code !== -32600)
        return false;
    const data = toObjectRecord(error.data);
    if (data && isRecord(toObjectRecord(data.codexErrorInfo)?.activeTurnNotSteerable))
        return true;
    // These app-server invalid-request messages describe requests that reached
    // Codex but could not have submitted input. Keep this exact: a generic
    // invalid-request, timeout, disconnect, or unknown error is ambiguous.
    return (error.message === "no active turn to steer" ||
        /^expected active turn id `[^`]+` but found `[^`]+`$/.test(error.message) ||
        error.message === "active turn uses a different output schema");
}
function isCodexAlreadyIdleInterrupt(error) {
    return (error instanceof CodexAppServerRpcError &&
        error.code === -32600 &&
        error.message === "no active turn to interrupt");
}
const CodexModelListResponseSchema = z.object({
    data: z
        .array(z.object({
        id: z.string(),
        serviceTiers: z.array(CodexServiceTierSchema).optional(),
        displayName: z.string().optional(),
        description: z.string().optional(),
        isDefault: z.boolean().optional(),
        model: z.string().optional(),
        defaultReasoningEffort: z.string().optional(),
        supportedReasoningEfforts: z
            .array(z.object({
            reasoningEffort: z.string().optional(),
            description: z.string().optional(),
        }))
            .optional(),
    }))
        .optional(),
});
/**
 * Read the newest `window.limit` threads Codex knows about.
 *
 * Codex caps every `thread/list` response at 100 rows whatever limit it is
 * given (codex-cli 0.153 and 0.155), so one request only ever sees the newest
 * page and hands back a cursor for the rest. Follow that cursor until the
 * caller's window is full or Codex runs out of threads.
 */
async function readCodexThreadWindow(client, logger, window) {
    // A thread updated while the scan is paging moves under `updated_at` order
    // and can come back on a later page, so identify each one and keep it once.
    // A row Codex sends without an id stands for itself.
    const threads = new Map();
    let cursor;
    while (threads.size < window.limit) {
        const response = toObjectRecord(await client.request("thread/list", {
            limit: window.limit - threads.size,
            // Rank the window by last use. Codex pages by creation time by default,
            // which drops an old conversation that is still being worked in.
            // Older Codex builds ignore the unknown key and keep that order.
            sortKey: "updated_at",
            ...(window.cwd ? { cwd: window.cwd } : {}),
            ...(window.modelProviders ? { modelProviders: window.modelProviders } : {}),
            ...(cursor ? { cursor } : {}),
        }));
        const page = Array.isArray(response?.data) ? response.data.filter(isRecord) : [];
        const sizeBeforePage = threads.size;
        for (const thread of page) {
            const identity = typeof thread.id === "string" ? thread.id : thread;
            if (!threads.has(identity))
                threads.set(identity, thread);
        }
        const nextCursor = typeof response?.nextCursor === "string" ? response.nextCursor : undefined;
        if (!nextCursor)
            break;
        if (threads.size === sizeBeforePage) {
            // Every page that continues the scan brings a thread the scan has not
            // seen, so the loop is bounded by the window. A page that brings none
            // would let a Codex-side cursor bug (a stuck cursor, or a cycle) page for
            // ever behind a caller that has already timed out.
            logger.warn({ cursor: nextCursor }, "codex thread/list returned no new threads mid-scan, stopping the session scan");
            break;
        }
        cursor = nextCursor;
    }
    return [...threads.values()];
}
function filterCodexThreadsByCwd(threads, cwd) {
    if (!cwd) {
        return threads;
    }
    // thread/list rows carry an optional cwd. The descriptor builder later
    // falls back to process.cwd() if the field is missing, so we only match
    // here when the row genuinely carries a cwd string — otherwise threads
    // with no cwd would falsely match the daemon's own cwd.
    const belongsToWorkspace = createPathEquivalenceMatcher(cwd);
    return threads.filter((thread) => typeof thread.cwd === "string" && belongsToWorkspace(thread.cwd));
}
export function toAgentUsage(tokenUsage) {
    const usage = toObjectRecord(tokenUsage);
    if (!usage)
        return undefined;
    const last = toObjectRecord(usage.last);
    const contextWindowMaxTokens = firstPositiveFiniteNumber(usage.model_context_window, usage.modelContextWindow);
    const contextWindowUsedTokens = firstPositiveFiniteNumber(last?.total_tokens, last?.totalTokens);
    return {
        inputTokens: typeof last?.inputTokens === "number" ? last.inputTokens : undefined,
        cachedInputTokens: typeof last?.cachedInputTokens === "number" ? last.cachedInputTokens : undefined,
        outputTokens: typeof last?.outputTokens === "number" ? last.outputTokens : undefined,
        ...(contextWindowMaxTokens !== undefined ? { contextWindowMaxTokens } : {}),
        ...(contextWindowUsedTokens !== undefined ? { contextWindowUsedTokens } : {}),
    };
}
function extractUserText(content) {
    if (!Array.isArray(content))
        return null;
    const parts = [];
    for (const item of content) {
        const record = toObjectRecord(item);
        if (!record) {
            continue;
        }
        if (record.type === "text" && typeof record.text === "string") {
            parts.push(record.text);
        }
    }
    return parts.length > 0 ? parts.join("\n") : null;
}
function normalizePlanMarkdown(text) {
    return text
        .split("\n")
        .map((line) => line.replace(/\s+$/, ""))
        .join("\n")
        .trim();
}
export function planStepsToMarkdown(steps) {
    const lines = steps
        .map((entry) => entry.step.trim())
        .filter((step) => step.length > 0)
        .map((step) => {
        if (/^(#{1,6}\s|[-*+]\s|\d+\.\s)/.test(step)) {
            return step;
        }
        return `- ${step}`;
    });
    return normalizePlanMarkdown(lines.join("\n"));
}
export function mapCodexPlanUpdateToTodo(steps) {
    return {
        type: "todo",
        items: steps.flatMap((entry, index) => {
            const text = entry.step?.trim();
            if (!text)
                return [];
            const status = normalizeCodexTaskStatus(entry.status);
            return [{ id: String(index), text, status, completed: status === "completed" }];
        }),
    };
}
function normalizeCodexTaskStatus(status) {
    if (status === "completed")
        return "completed";
    if (status === "inProgress" || status === "in_progress")
        return "in_progress";
    return "pending";
}
export function mapCodexPlanToToolCall(params) {
    const text = normalizePlanMarkdown(params.text);
    if (!text) {
        return null;
    }
    return {
        type: "tool_call",
        callId: params.callId,
        name: "plan",
        status: "completed",
        error: null,
        detail: {
            type: "plan",
            text,
        },
    };
}
// Codex reports each model-stream reconnect as an `error` notification with `willRetry: true`.
// One status line per reconnect run shows the latest attempt and settles when the turn moves on.
function mapCodexStreamRetryToStatusLine(retry, outcome) {
    const base = {
        type: "tool_call",
        callId: retry.callId,
        name: "codex_retry",
        detail: {
            type: "plain_text",
            label: outcome.status === "completed" ? "Reconnected" : retry.message,
            ...(retry.details ? { text: retry.details } : {}),
            icon: "sparkles",
        },
        metadata: { synthetic: true, source: "codex_stream_retry" },
    };
    if (outcome.status === "failed") {
        return { ...base, status: "failed", error: outcome.error };
    }
    return { ...base, status: outcome.status, error: null };
}
function buildPlanPermissionActions(options) {
    const actions = [
        {
            id: "dismiss",
            label: "Dismiss",
            behavior: "deny",
            variant: "danger",
            intent: "dismiss",
        },
        {
            id: "implement",
            label: "Implement",
            behavior: "allow",
            variant: "primary",
            intent: "implement",
        },
    ];
    if (options?.includeResumeAction && options.resumeLabel) {
        actions.push({
            id: "implement_resume",
            label: options.resumeLabel,
            behavior: "allow",
            variant: "secondary",
            intent: "implement_resume",
        });
    }
    return actions;
}
function buildCodexPlanImplementationPrompt(planText) {
    const normalizedPlan = normalizePlanMarkdown(planText);
    if (!normalizedPlan) {
        return `${CODEX_PLAN_IMPLEMENTATION_PROMPT_PREFIX} Make the required code changes and verify them.`;
    }
    return [
        CODEX_PLAN_IMPLEMENTATION_PROMPT_PREFIX,
        "Approved plan:",
        normalizedPlan,
        "Carry out the work, make the necessary code changes, and verify the result.",
    ].join("\n\n");
}
export function normalizeCodexQuestionPrompts(raw) {
    if (!Array.isArray(raw)) {
        return [];
    }
    const questions = [];
    for (const item of raw) {
        const record = toObjectRecord(item);
        if (!record) {
            continue;
        }
        const id = nonEmptyString(record.id);
        const header = nonEmptyString(record.header);
        const question = nonEmptyString(record.question);
        if (!id || !header || !question) {
            continue;
        }
        const options = Array.isArray(record.options)
            ? record.options.flatMap((option) => {
                const optionRecord = toObjectRecord(option);
                if (!optionRecord) {
                    return [];
                }
                const label = nonEmptyString(optionRecord.label);
                if (!label) {
                    return [];
                }
                return [
                    {
                        label,
                        ...(typeof optionRecord.description === "string" &&
                            optionRecord.description.trim().length > 0
                            ? { description: optionRecord.description }
                            : {}),
                    },
                ];
            })
            : [];
        questions.push({
            id,
            header,
            question,
            options,
            ...(record.multiSelect === true ? { multiSelect: true } : {}),
            ...(record.isOther === true ? { isOther: true } : {}),
            ...(record.isSecret === true ? { isSecret: true } : {}),
        });
    }
    return questions;
}
export function formatCodexQuestionPrompts(questions) {
    return questions
        .map((question) => {
        const lines = [`${question.header}: ${question.question}`];
        if (question.options.length > 0) {
            lines.push(`Options: ${question.options.map((option) => option.label).join(", ")}`);
        }
        return lines.join("\n");
    })
        .join("\n\n")
        .trim();
}
export function mapCodexQuestionRequestToToolCall(params) {
    const formattedQuestions = formatCodexQuestionPrompts(params.questions);
    const formattedAnswers = params.answers && Object.keys(params.answers).length > 0
        ? Object.entries(params.answers)
            .map(([id, values]) => `${id}: ${values.join(", ")}`)
            .join("\n")
        : null;
    const detailText = params.status === "completed" && formattedAnswers
        ? [formattedQuestions, "Answers:", formattedAnswers].filter(Boolean).join("\n\n")
        : formattedQuestions;
    const base = {
        type: "tool_call",
        callId: params.callId,
        name: "request_user_input",
        detail: {
            type: "plain_text",
            text: detailText,
            icon: "brain",
        },
        metadata: {
            questions: params.questions,
            ...(params.answers ? { answers: params.answers } : {}),
        },
    };
    if (params.status === "failed") {
        return {
            ...base,
            status: "failed",
            error: params.error ?? { message: "Question dismissed" },
        };
    }
    if (params.status === "canceled") {
        return {
            ...base,
            status: "canceled",
            error: null,
        };
    }
    if (params.status === "running") {
        return {
            ...base,
            status: "running",
            error: null,
        };
    }
    return {
        ...base,
        status: "completed",
        error: null,
    };
}
function mapCodexQuestionResponseByHeader(params) {
    if (params.response.behavior !== "allow") {
        return null;
    }
    const updatedInputRecord = toObjectRecord(params.response.updatedInput);
    const answersRecord = toObjectRecord(updatedInputRecord?.answers);
    if (!answersRecord) {
        return null;
    }
    const answers = {};
    for (const question of params.questions) {
        const rawAnswer = answersRecord[question.header];
        if (typeof rawAnswer !== "string") {
            continue;
        }
        const normalizedAnswer = rawAnswer.trim();
        if (!normalizedAnswer) {
            continue;
        }
        const values = question.multiSelect
            ? normalizedAnswer
                .split(",")
                .map((entry) => entry.trim())
                .filter((entry) => entry.length > 0)
            : [normalizedAnswer];
        if (values.length > 0) {
            answers[question.id] = { answers: values };
        }
    }
    return Object.keys(answers).length > 0 ? answers : null;
}
function codexPatchMovePath(value) {
    const record = toObjectRecord(value);
    if (!record)
        return undefined;
    const kind = toObjectRecord(record.kind);
    const destination = record.move_path ?? kind?.move_path;
    return typeof destination === "string" ? destination : undefined;
}
function extractPatchLikeText(value) {
    const record = toObjectRecord(value);
    if (!record) {
        return undefined;
    }
    const candidates = [
        record.diff,
        record.patch,
        record.unified_diff,
        record.unifiedDiff,
        record.content,
        record.newString,
    ];
    for (const candidate of candidates) {
        if (typeof candidate === "string" && candidate.length > 0) {
            return candidate;
        }
    }
    return undefined;
}
function normalizeCodexThreadItemType(rawType) {
    if (!rawType) {
        return rawType;
    }
    switch (rawType) {
        case "UserMessage":
            return "userMessage";
        case "AgentMessage":
            return "agentMessage";
        case "Reasoning":
            return "reasoning";
        case "Plan":
            return "plan";
        case "CommandExecution":
            return "commandExecution";
        case "FileChange":
            return "fileChange";
        case "McpToolCall":
            return "mcpToolCall";
        case "WebSearch":
            return "webSearch";
        case "CollabAgentToolCall":
            return "collabAgentToolCall";
        case "SubAgentActivity":
            return "subAgentActivity";
        case "ImageView":
            return "imageView";
        case "ImageGeneration":
            return "imageGeneration";
        default:
            return rawType;
    }
}
function normalizeCodexCommandValue(value) {
    if (typeof value === "string") {
        const trimmed = value.trim();
        if (!trimmed.length) {
            return null;
        }
        const wrapperMatch = trimmed.match(/^(?:\/bin\/)?(?:zsh|bash|sh)\s+-(?:lc|c)\s+([\s\S]+)$/);
        if (!wrapperMatch) {
            return trimmed;
        }
        const candidate = wrapperMatch[1]?.trim() ?? "";
        if (!candidate.length) {
            return trimmed;
        }
        if ((candidate.startsWith('"') && candidate.endsWith('"')) ||
            (candidate.startsWith("'") && candidate.endsWith("'"))) {
            return candidate.slice(1, -1);
        }
        return candidate;
    }
    if (!Array.isArray(value)) {
        return null;
    }
    const parts = value
        .filter((entry) => typeof entry === "string")
        .map((entry) => entry.trim())
        .filter((entry) => entry.length > 0);
    if (parts.length === 0) {
        return null;
    }
    if (parts.length >= 3 && (parts[1] === "-lc" || parts[1] === "-c")) {
        return parts[2] ?? parts;
    }
    return parts;
}
function parseCodexPatchChanges(changes) {
    const resolvePathFromRecord = (record) => {
        const directPath = (typeof record.path === "string" && record.path.trim().length > 0
            ? record.path.trim()
            : "") ||
            (typeof record.file_path === "string" && record.file_path.trim().length > 0
                ? record.file_path.trim()
                : "") ||
            (typeof record.filePath === "string" && record.filePath.trim().length > 0
                ? record.filePath.trim()
                : "");
        return directPath;
    };
    if (!changes || typeof changes !== "object") {
        return [];
    }
    if (Array.isArray(changes)) {
        return changes
            .map((entry) => {
            const record = toObjectRecord(entry);
            if (!record) {
                return null;
            }
            const pathValue = resolvePathFromRecord(record);
            if (!pathValue) {
                return null;
            }
            return {
                path: pathValue,
                kind: (typeof record.kind === "string" && record.kind) ||
                    (typeof record.type === "string" && record.type) ||
                    undefined,
                content: extractPatchLikeText(record),
                movePath: codexPatchMovePath(record),
            };
        })
            .filter((entry) => entry !== null);
    }
    const recordChanges = toObjectRecord(changes);
    if (!recordChanges) {
        return [];
    }
    const directPathValue = resolvePathFromRecord(recordChanges);
    if (directPathValue) {
        return [
            {
                path: directPathValue,
                kind: (typeof recordChanges.kind === "string" && recordChanges.kind) ||
                    (typeof recordChanges.type === "string" && recordChanges.type) ||
                    undefined,
                content: extractPatchLikeText(recordChanges),
                movePath: codexPatchMovePath(recordChanges),
            },
        ];
    }
    return Object.entries(recordChanges)
        .map(([entryPath, value]) => {
        const normalizedPath = entryPath.trim();
        if (!normalizedPath) {
            return null;
        }
        return {
            path: normalizedPath,
            kind: value &&
                typeof value === "object" &&
                typeof value.type === "string"
                ? (value.type ?? undefined)
                : undefined,
            content: extractPatchLikeText(value),
            movePath: codexPatchMovePath(value),
        };
    })
        .filter((entry) => entry !== null);
}
function codexPatchTextFields(text) {
    if (typeof text !== "string") {
        return {};
    }
    const normalized = text.trimStart();
    const looksLikeUnifiedDiff = normalized.startsWith("diff --git") ||
        normalized.startsWith("@@") ||
        normalized.startsWith("--- ") ||
        normalized.startsWith("+++ ");
    return looksLikeUnifiedDiff ? { patch: text } : { content: text };
}
function toRunningToolCall(item) {
    return {
        ...item,
        status: "running",
        error: null,
    };
}
function isEditToolCallWithoutContent(item) {
    if (item.type !== "tool_call") {
        return false;
    }
    if (item.detail.type !== "edit") {
        return false;
    }
    const hasDiff = typeof item.detail.unifiedDiff === "string" && item.detail.unifiedDiff.trim().length > 0;
    const hasNewString = typeof item.detail.newString === "string" && item.detail.newString.trim().length > 0;
    return !hasDiff && !hasNewString;
}
function decodeCodexOutputDeltaChunk(chunk) {
    const trimmed = chunk.trim();
    if (trimmed.length === 0) {
        return chunk;
    }
    if (!/^[A-Za-z0-9+/=]+$/.test(trimmed) || trimmed.length % 4 !== 0) {
        return chunk;
    }
    try {
        const decoded = Buffer.from(trimmed, "base64").toString("utf8");
        if (decoded.length === 0) {
            return chunk;
        }
        const normalizedInput = trimmed.replace(/=+$/, "");
        const normalizedRoundTrip = Buffer.from(decoded, "utf8").toString("base64").replace(/=+$/, "");
        return normalizedRoundTrip === normalizedInput ? decoded : chunk;
    }
    catch {
        return chunk;
    }
}
function mapCodexExecNotificationToToolCall(params) {
    const command = normalizeCodexCommandValue(params.command);
    if (!command) {
        return null;
    }
    const isFailure = params.running
        ? false
        : params.success === false || (typeof params.exitCode === "number" && params.exitCode !== 0);
    const output = params.running
        ? null
        : {
            command,
            ...(params.output !== null && params.output !== undefined ? { output: params.output } : {}),
            ...(params.exitCode !== null && params.exitCode !== undefined
                ? { exitCode: params.exitCode }
                : {}),
        };
    const mapped = mapCodexToolCallEnvelope({
        callId: params.callId ?? null,
        name: "shell",
        input: {
            command,
            ...(params.cwd ? { cwd: params.cwd } : {}),
        },
        output,
        error: isFailure ? { message: params.stderr?.trim() || "Command failed" } : null,
        cwd: params.cwd ?? null,
    });
    if (!mapped) {
        return null;
    }
    return params.running ? toRunningToolCall(mapped) : mapped;
}
function codexPatchPreview(files, cwd) {
    const preview = files.length > 1
        ? fileChangesToPreview(files.map((file) => ({
            path: normalizeCodexFilePath(file.path, cwd) ?? file.path,
            kind: file.kind,
            diff: file.content,
            movePath: file.movePath ? normalizeCodexFilePath(file.movePath, cwd) : undefined,
        })))
        : undefined;
    const firstPath = preview?.filePath ?? files[0]?.path;
    const firstPatchText = files
        .map((file) => file.content?.trim())
        .find((value) => typeof value === "string" && value.length > 0);
    return { filePath: firstPath, patchText: preview?.unifiedDiff ?? firstPatchText };
}
export function mapCodexPatchNotificationToToolCall(params) {
    const files = parseCodexPatchChanges(params.changes);
    const { filePath: firstPath, patchText } = codexPatchPreview(files, params.cwd);
    const patchFields = codexPatchTextFields(patchText);
    const mapped = mapCodexToolCallEnvelope({
        callId: params.callId ?? null,
        name: "apply_patch",
        input: firstPath
            ? {
                path: firstPath,
                ...patchFields,
                files: files.map((file) => ({ path: file.path, kind: file.kind })),
            }
            : {
                changes: params.changes ?? null,
                ...patchFields,
            },
        output: params.running
            ? null
            : {
                ...(files.length > 0
                    ? {
                        files: files.map((file) => Object.assign({ path: file.path }, file.kind ? { kind: file.kind } : {}, codexPatchTextFields(file.content ?? patchText))),
                    }
                    : {}),
                ...(params.stdout ? { stdout: params.stdout } : {}),
                ...(params.stderr ? { stderr: params.stderr } : {}),
                ...(params.success !== null && params.success !== undefined
                    ? { success: params.success }
                    : {}),
            },
        error: params.running || params.success !== false
            ? null
            : { message: params.stderr?.trim() || "Patch apply failed" },
        cwd: params.cwd ?? null,
    });
    if (!mapped) {
        return null;
    }
    return params.running ? toRunningToolCall(mapped) : mapped;
}
function mapCodexTerminalInteractionToToolCall(params) {
    const processId = nonEmptyString(params.processId ?? undefined);
    const label = nonEmptyString(params.command ?? undefined);
    return {
        type: "tool_call",
        callId: params.callId,
        name: "terminal",
        status: "completed",
        error: null,
        detail: {
            type: "plain_text",
            ...(label ? { label } : {}),
            ...(params.stdin !== null && params.stdin !== undefined ? { text: params.stdin } : {}),
            icon: "square_terminal",
        },
        ...(processId ? { metadata: { processId } } : {}),
    };
}
function mapCodexThreadPlanItem(normalizedItem) {
    const callId = nonEmptyString(normalizedItem.id ?? normalizedItem.itemId ?? undefined) ??
        `plan:${normalizePlanMarkdown(typeof normalizedItem.text === "string" ? normalizedItem.text : "")}`;
    return mapCodexPlanToToolCall({
        callId,
        text: typeof normalizedItem.text === "string" ? normalizedItem.text : "",
    });
}
function mapCodexThreadReasoningItem(normalizedItem) {
    const summary = Array.isArray(normalizedItem.summary) ? normalizedItem.summary.join("\n") : "";
    const content = Array.isArray(normalizedItem.content) ? normalizedItem.content.join("\n") : "";
    const text = summary || content;
    return text ? { type: "reasoning", text } : null;
}
function mapCodexThreadUserMessageItem(normalizedItem, includeUserMessage) {
    if (!includeUserMessage) {
        return null;
    }
    const text = extractUserText(normalizedItem.content) ?? "";
    const messageId = nonEmptyString(normalizedItem.id);
    const clientMessageId = nonEmptyString(normalizedItem.clientId ?? normalizedItem.client_id ?? normalizedItem.clientUserMessageId);
    return {
        type: "user_message",
        text,
        ...(messageId ? { messageId } : {}),
        ...(clientMessageId ? { clientMessageId } : {}),
    };
}
function firstStringField(record, fields) {
    for (const field of fields) {
        const value = record[field];
        if (typeof value === "string") {
            return value;
        }
    }
    return null;
}
function readCodexHistoryTimestamp(item) {
    const record = toObjectRecord(item);
    if (!record) {
        return null;
    }
    return (normalizeProviderReplayTimestamp(record.timestamp) ??
        normalizeProviderReplayTimestamp(record.createdAt) ??
        normalizeProviderReplayTimestamp(record.created_at));
}
/** Falls back to the row's start time for a user message and its end time otherwise. */
function readCodexHistoryRowTimestamp(row, timelineItem) {
    const startedAt = normalizeProviderReplayTimestamp(row.startedAt);
    const completedAt = normalizeProviderReplayTimestamp(row.completedAt);
    if (timelineItem.type === "user_message") {
        return startedAt ?? completedAt;
    }
    return completedAt ?? startedAt;
}
function isTerminalSubAgentStatus(status) {
    return status === "completed" || status === "failed" || status === "canceled";
}
function readCodexSubAgentActivity(item) {
    const record = toObjectRecord(item);
    if (!record) {
        return null;
    }
    const normalizedType = normalizeCodexThreadItemType(typeof record.type === "string" ? record.type : undefined);
    if (normalizedType !== "subAgentActivity" ||
        typeof record.agentThreadId !== "string" ||
        (record.kind !== "started" && record.kind !== "interacted" && record.kind !== "interrupted")) {
        return null;
    }
    return {
        id: nonEmptyString(record.id) ?? null,
        agentThreadId: record.agentThreadId,
        kind: record.kind,
    };
}
function shouldIgnoreMirroredLifecycleItem(source, item) {
    return source === "codex_event" && !readCodexSubAgentActivity(item);
}
function settleHistoricalSubAgentActivity(item, kind) {
    // thread/read returns completed parent items, not a live child snapshot.
    // Only an explicit interruption remains non-completed when replayed.
    return {
        ...item,
        status: kind === "interrupted" ? "canceled" : "completed",
        error: null,
    };
}
function updateHistoricalSubAgentActivity(timeline, index, kind, subAgentType) {
    const existing = timeline[index];
    if (existing?.item.type !== "tool_call") {
        return;
    }
    const settledItem = settleHistoricalSubAgentActivity(existing.item, kind);
    timeline[index] = {
        ...existing,
        item: subAgentType && settledItem.detail.type === "sub_agent"
            ? {
                ...settledItem,
                detail: { ...settledItem.detail, subAgentType },
            }
            : settledItem,
    };
}
function readCodexHistoricalSubAgentThreadIds(item) {
    const activity = readCodexSubAgentActivity(item);
    if (activity) {
        return activity.kind === "started" ? [activity.agentThreadId] : [];
    }
    const record = toObjectRecord(item);
    const normalizedType = normalizeCodexThreadItemType(typeof record?.type === "string" ? record.type : undefined);
    if (normalizedType !== "collabAgentToolCall" ||
        record?.tool !== "spawnAgent" ||
        !Array.isArray(record.receiverThreadIds)) {
        return [];
    }
    return record.receiverThreadIds.filter((threadId) => typeof threadId === "string" && threadId.length > 0);
}
function codexImageOutputFromResult(result) {
    if (typeof result === "string") {
        const trimmed = result.trim();
        if (trimmed.toLowerCase().startsWith("data:image/") ||
            (/^[A-Za-z0-9+/]+={0,2}$/.test(trimmed) && trimmed.length > 64)) {
            return { data: trimmed };
        }
        return { url: trimmed };
    }
    const resultRecord = toObjectRecord(result);
    if (!resultRecord) {
        return null;
    }
    return {
        path: firstStringField(resultRecord, ["path", "savedPath", "saved_path"]),
        url: firstStringField(resultRecord, ["url"]),
        data: firstStringField(resultRecord, ["data"]),
        mimeType: firstStringField(resultRecord, ["mimeType", "mime_type"]),
    };
}
async function mapCodexThreadImageItem(normalizedType, normalizedItem) {
    if (normalizedType === "imageView") {
        return await renderProviderImageOutputAsAssistantMarkdown({
            path: firstStringField(normalizedItem, ["path"]),
        });
    }
    if (normalizedItem.status === "failed") {
        return mapCodexToolCallEnvelope({
            callId: firstStringField(normalizedItem, ["id"]),
            name: "image_generation",
            input: { prompt: firstStringField(normalizedItem, ["revisedPrompt", "revised_prompt"]) },
            error: normalizedItem.failure ?? { message: "Image generation failed" },
        });
    }
    const savedPath = firstStringField(normalizedItem, ["savedPath", "saved_path"]);
    const result = codexImageOutputFromResult(normalizedItem.result);
    return await renderProviderImageOutputAsAssistantMarkdown({
        path: savedPath ?? result?.path ?? null,
        url: result?.url ?? null,
        data: result?.data ?? null,
        mimeType: result?.mimeType ?? null,
    }, { materialize: materializeProviderImage });
}
function mapCodexAgentMessage(item) {
    const question = codexAsyncQuestionToTimeline(item);
    if (question)
        return question;
    const messageId = nonEmptyString(item.id);
    return {
        type: "assistant_message",
        text: typeof item.text === "string" ? item.text : "",
        ...(messageId ? { messageId } : {}),
    };
}
export async function threadItemToTimeline(item, options) {
    const itemRecord = toObjectRecord(item);
    if (!itemRecord)
        return null;
    const includeUserMessage = options?.includeUserMessage ?? true;
    const cwd = options?.cwd ?? null;
    const normalizedType = normalizeCodexThreadItemType(typeof itemRecord.type === "string" ? itemRecord.type : undefined);
    const normalizedItem = normalizedType && normalizedType !== itemRecord.type
        ? { ...itemRecord, type: normalizedType }
        : itemRecord;
    if (normalizedType === "imageView" || normalizedType === "imageGeneration") {
        return await mapCodexThreadImageItem(normalizedType, normalizedItem);
    }
    if (normalizedType && CODEX_TOOL_THREAD_ITEM_TYPES.has(normalizedType)) {
        return mapCodexToolCallFromThreadItem(normalizedItem, { cwd });
    }
    switch (normalizedType) {
        case "userMessage":
            return mapCodexThreadUserMessageItem(normalizedItem, includeUserMessage);
        case "agentMessage":
            return mapCodexAgentMessage(normalizedItem);
        case "plan":
            return mapCodexThreadPlanItem(normalizedItem);
        case "reasoning":
            return mapCodexThreadReasoningItem(normalizedItem);
        case CODEX_CONTEXT_COMPACTION_TYPE:
            return {
                type: "compaction",
                status: "completed",
            };
        default:
            return null;
    }
}
async function mcpToolResultImagesToTimeline(item) {
    const itemRecord = toObjectRecord(item);
    if (!itemRecord) {
        return [];
    }
    const normalizedType = normalizeCodexThreadItemType(typeof itemRecord.type === "string" ? itemRecord.type : undefined);
    if (normalizedType !== "mcpToolCall") {
        return [];
    }
    const { images } = splitCodexMcpToolResultImages(itemRecord.result);
    const items = [];
    for (const image of images) {
        const imageItem = await renderProviderImageOutputAsAssistantMarkdown(image, {
            materialize: materializeProviderImage,
        });
        if (imageItem)
            items.push(imageItem);
    }
    return items;
}
async function threadItemToTimelineEntries(item, options) {
    const timelineItem = await threadItemToTimeline(item, options);
    if (!timelineItem) {
        return [];
    }
    return [timelineItem, ...(await mcpToolResultImagesToTimeline(item))];
}
async function loadCodexThreadHistoryTimeline(params) {
    const timeline = [];
    const subAgentTimelineIndexByThreadId = new Map();
    try {
        await projectCodexThreadHistory(params, { timeline, subAgentTimelineIndexByThreadId });
    }
    catch (error) {
        if (!(error instanceof CodexThreadHistoryTooLargeError))
            throw error;
        // The session stays usable; the row says why the history stops here.
        timeline.push({ item: { type: "notification", level: "warning", message: error.message } });
    }
    const subAgentRoutes = Array.from(subAgentTimelineIndexByThreadId.entries()).flatMap(([childThreadId, timelineIndex]) => {
        const item = timeline[timelineIndex]?.item;
        return item?.type === "tool_call" && item.detail.type === "sub_agent"
            ? [{ childThreadId, toolCall: item }]
            : [];
    });
    return { timeline, subAgentRoutes };
}
async function projectCodexThreadHistory(params, projection) {
    const { timeline, subAgentTimelineIndexByThreadId } = projection;
    for await (const row of readCodexThreadHistory(params.client, params.threadId)) {
        const { item } = row;
        const historicalSubAgentActivity = readCodexSubAgentActivity(item);
        if (historicalSubAgentActivity) {
            const existingIndex = subAgentTimelineIndexByThreadId.get(historicalSubAgentActivity.agentThreadId);
            if (existingIndex !== undefined) {
                const activityTimelineItem = await threadItemToTimeline(item, { cwd: params.cwd });
                updateHistoricalSubAgentActivity(timeline, existingIndex, historicalSubAgentActivity.kind, activityTimelineItem?.type === "tool_call" &&
                    activityTimelineItem.detail.type === "sub_agent"
                    ? activityTimelineItem.detail.subAgentType
                    : undefined);
                continue;
            }
        }
        for (const timelineItem of await threadItemToTimelineEntries(item, { cwd: params.cwd })) {
            const timestamp = readCodexHistoryTimestamp(item) ?? readCodexHistoryRowTimestamp(row, timelineItem);
            const settledTimelineItem = historicalSubAgentActivity && timelineItem.type === "tool_call"
                ? settleHistoricalSubAgentActivity(timelineItem, historicalSubAgentActivity.kind)
                : timelineItem;
            timeline.push({
                // Cap each item as it is projected so a large history is never held uncapped.
                item: limitAgentTimelineItemContent(settledTimelineItem),
                timestamp: timestamp ?? undefined,
                ...(timelineItem.type === "user_message" && row.turnId !== undefined
                    ? { providerTurnId: row.turnId }
                    : {}),
            });
            for (const childThreadId of readCodexHistoricalSubAgentThreadIds(item)) {
                subAgentTimelineIndexByThreadId.set(childThreadId, timeline.length - 1);
            }
        }
    }
}
export async function forkCodexThread(client, params) {
    if (client.forkThread) {
        return client.forkThread(params);
    }
    return parseCodexThreadForkResponse(await client.request("thread/fork", params));
}
export async function rollbackCodexThread(client, params) {
    if (client.rollbackThread) {
        return client.rollbackThread(params);
    }
    return parseCodexThreadRollbackResponse(await client.request("thread/rollback", params));
}
function toSandboxPolicy(type, workspaceWrite) {
    switch (type) {
        case "read-only":
            return { type: "readOnly" };
        case "workspace-write":
            return {
                type: "workspaceWrite",
                networkAccess: workspaceWrite?.network_access ?? false,
                writableRoots: workspaceWrite?.writable_roots ?? [],
                excludeSlashTmp: workspaceWrite?.exclude_slash_tmp ?? false,
                excludeTmpdirEnvVar: workspaceWrite?.exclude_tmpdir_env_var ?? false,
            };
        case "danger-full-access":
            return { type: "dangerFullAccess" };
        default:
            return { type: "workspaceWrite", networkAccess: false, writableRoots: [] };
    }
}
function readSandboxWorkspaceWrite(value) {
    const record = toObjectRecord(value);
    if (!record)
        return null;
    const workspaceWrite = {};
    const writableRoots = record.writable_roots ?? record.writableRoots;
    if (Array.isArray(writableRoots)) {
        workspaceWrite.writable_roots = writableRoots.filter((root) => typeof root === "string");
    }
    const networkAccess = record.network_access ?? record.networkAccess;
    if (typeof networkAccess === "boolean")
        workspaceWrite.network_access = networkAccess;
    const excludeSlashTmp = record.exclude_slash_tmp ?? record.excludeSlashTmp;
    if (typeof excludeSlashTmp === "boolean")
        workspaceWrite.exclude_slash_tmp = excludeSlashTmp;
    const excludeTmpdirEnvVar = record.exclude_tmpdir_env_var ?? record.excludeTmpdirEnvVar;
    if (typeof excludeTmpdirEnvVar === "boolean") {
        workspaceWrite.exclude_tmpdir_env_var = excludeTmpdirEnvVar;
    }
    return workspaceWrite;
}
function toCodexSandboxPolicyType(type) {
    switch (type) {
        case "workspace-write":
            return "workspaceWrite";
        case "read-only":
            return "readOnly";
        default:
            return "dangerFullAccess";
    }
}
const ThreadStartedNotificationSchema = z
    .object({
    thread: z.object({ id: z.string() }).passthrough(),
})
    .passthrough();
const TurnStartedNotificationSchema = z
    .object({
    threadId: z.string().optional(),
    turn: z.object({ id: z.string() }).passthrough(),
})
    .passthrough();
const TurnCompletedNotificationSchema = z
    .object({
    threadId: z.string().optional(),
    turn: z
        .object({
        id: z.string().optional(),
        status: z.string(),
        error: z
            .object({
            message: z.string().optional(),
        })
            .passthrough()
            .nullable()
            .optional(),
    })
        .passthrough(),
})
    .passthrough();
const TurnErrorNotificationSchema = z
    .object({
    threadId: z.string().optional(),
    willRetry: z.boolean(),
    error: z
        .object({
        message: z.string(),
        additionalDetails: z.string().nullable().optional(),
    })
        .passthrough(),
})
    .passthrough();
const WarningNotificationSchema = z
    .object({
    threadId: z.string().nullable().optional(),
    message: z.string(),
})
    .passthrough();
const TurnPlanUpdatedNotificationSchema = z
    .object({
    threadId: z.string().optional(),
    plan: z.array(z
        .object({
        step: z.string().optional(),
        status: z.string().optional(),
    })
        .passthrough()),
})
    .passthrough();
const TurnDiffUpdatedNotificationSchema = z
    .object({
    threadId: z.string().optional(),
    diff: z.string(),
})
    .passthrough();
const ThreadTokenUsageUpdatedNotificationSchema = z
    .object({
    threadId: z.string().optional(),
    tokenUsage: z.unknown(),
})
    .passthrough();
const ItemTextDeltaNotificationSchema = z
    .object({
    threadId: z.string().optional(),
    itemId: z.string(),
    delta: z.string(),
})
    .passthrough();
const ItemLifecycleNotificationSchema = z
    .object({
    threadId: z.string().optional(),
    turnId: z.string().optional(),
    item: z
        .object({
        id: z.string().optional(),
        type: z.string().optional(),
    })
        .passthrough(),
})
    .passthrough();
const ContextCompactedNotificationSchema = z
    .object({
    threadId: z.string(),
    turnId: z.string().optional(),
})
    .passthrough();
const CodexEventThreadIdFields = {
    threadId: z.string().optional(),
    thread_id: z.string().optional(),
};
const CodexEventTurnIdFields = {
    turnId: z.string().optional(),
    turn_id: z.string().optional(),
};
function getCodexEventThreadId(params) {
    return params.threadId ?? params.thread_id ?? params.msg.threadId ?? params.msg.thread_id ?? null;
}
function getCodexEventTurnId(params) {
    return params.turnId ?? params.turn_id ?? params.msg.turnId ?? params.msg.turn_id ?? null;
}
const CodexEventTurnAbortedNotificationSchema = z
    .object({
    ...CodexEventThreadIdFields,
    msg: z
        .object({
        ...CodexEventThreadIdFields,
        type: z.literal("turn_aborted"),
        reason: z.string().optional(),
    })
        .passthrough(),
})
    .passthrough();
const CodexEventTaskCompleteNotificationSchema = z
    .object({
    ...CodexEventThreadIdFields,
    msg: z
        .object({
        ...CodexEventThreadIdFields,
        type: z.literal("task_complete"),
    })
        .passthrough(),
})
    .passthrough();
const CodexEventItemLifecycleNotificationSchema = z
    .object({
    ...CodexEventThreadIdFields,
    ...CodexEventTurnIdFields,
    msg: z
        .object({
        ...CodexEventThreadIdFields,
        ...CodexEventTurnIdFields,
        type: z.enum(["item_started", "item_completed"]),
        item: z
            .object({
            id: z.string().optional(),
            type: z.string().optional(),
        })
            .passthrough(),
    })
        .passthrough(),
})
    .passthrough();
const CodexEventExecCommandBeginNotificationSchema = z
    .object({
    ...CodexEventThreadIdFields,
    msg: z
        .object({
        ...CodexEventThreadIdFields,
        type: z.literal("exec_command_begin"),
        call_id: z.string().optional(),
        command: z.unknown().optional(),
        cwd: z.string().optional(),
    })
        .passthrough(),
})
    .passthrough();
const CodexEventExecCommandEndNotificationSchema = z
    .object({
    ...CodexEventThreadIdFields,
    msg: z
        .object({
        ...CodexEventThreadIdFields,
        type: z.literal("exec_command_end"),
        call_id: z.string().optional(),
        command: z.unknown().optional(),
        cwd: z.string().optional(),
        stdout: z.string().optional(),
        stderr: z.string().optional(),
        aggregated_output: z.string().nullable().optional(),
        aggregatedOutput: z.string().nullable().optional(),
        formatted_output: z.string().optional(),
        exit_code: z.number().nullable().optional(),
        exitCode: z.number().nullable().optional(),
        success: z.boolean().optional(),
    })
        .passthrough(),
})
    .passthrough();
const CodexEventExecCommandOutputDeltaNotificationSchema = z
    .object({
    ...CodexEventThreadIdFields,
    msg: z
        .object({
        ...CodexEventThreadIdFields,
        type: z.literal("exec_command_output_delta"),
        call_id: z.string().optional(),
        stream: z.string().optional(),
        chunk: z.string().optional(),
        delta: z.string().optional(),
    })
        .passthrough(),
})
    .passthrough();
const CodexEventTerminalInteractionNotificationSchema = z
    .object({
    ...CodexEventThreadIdFields,
    msg: z
        .object({
        ...CodexEventThreadIdFields,
        type: z.literal("terminal_interaction"),
        call_id: z.string().optional(),
        process_id: z.union([z.string(), z.number()]).optional(),
        stdin: z.string().optional(),
    })
        .passthrough(),
})
    .passthrough();
const ItemCommandExecutionTerminalInteractionNotificationSchema = z
    .object({
    threadId: z.string().optional(),
    itemId: z.string().optional(),
    processId: z.union([z.string(), z.number()]).optional(),
    stdin: z.string().optional(),
})
    .passthrough();
const CodexEventPatchApplyBeginNotificationSchema = z
    .object({
    ...CodexEventThreadIdFields,
    msg: z
        .object({
        ...CodexEventThreadIdFields,
        type: z.literal("patch_apply_begin"),
        call_id: z.string().optional(),
        changes: z.unknown().optional(),
    })
        .passthrough(),
})
    .passthrough();
const CodexEventPatchApplyEndNotificationSchema = z
    .object({
    ...CodexEventThreadIdFields,
    msg: z
        .object({
        ...CodexEventThreadIdFields,
        type: z.literal("patch_apply_end"),
        call_id: z.string().optional(),
        changes: z.unknown().optional(),
        stdout: z.string().optional(),
        stderr: z.string().optional(),
        success: z.boolean().optional(),
    })
        .passthrough(),
})
    .passthrough();
const ItemFileChangeOutputDeltaNotificationSchema = z
    .object({
    threadId: z.string().optional(),
    itemId: z.string(),
    delta: z.string().optional(),
    chunk: z.string().optional(),
})
    .passthrough();
const CodexEventTurnDiffNotificationSchema = z
    .object({
    ...CodexEventThreadIdFields,
    msg: z
        .object({
        ...CodexEventThreadIdFields,
        type: z.literal("turn_diff"),
        unified_diff: z.string().optional(),
        diff: z.string().optional(),
    })
        .passthrough(),
})
    .passthrough();
const CodexEventThreadRolledBackNotificationSchema = z
    .object({
    ...CodexEventThreadIdFields,
    msg: z
        .object({
        ...CodexEventThreadIdFields,
        type: z.literal("thread_rolled_back"),
        num_turns: z.number().int().nonnegative().optional(),
        numTurns: z.number().int().nonnegative().optional(),
    })
        .passthrough(),
})
    .passthrough();
function getCodexNotificationThreadId(parsed) {
    return "threadId" in parsed ? parsed.threadId : null;
}
function isCodexDeltaNotification(parsed) {
    return (parsed.kind === "agent_message_delta" ||
        parsed.kind === "reasoning_delta" ||
        parsed.kind === "exec_command_output_delta" ||
        parsed.kind === "file_change_output_delta");
}
const CodexNotificationSchema = z.union([
    z
        .object({ method: z.literal("thread/started"), params: ThreadStartedNotificationSchema })
        .transform(({ params }) => ({
        kind: "thread_started",
        threadId: params.thread.id,
    })),
    z.object({ method: z.literal("thread/started"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z.object({ method: z.literal("turn/started"), params: TurnStartedNotificationSchema }).transform(({ params }) => ({
        kind: "turn_started",
        turnId: params.turn.id,
        threadId: params.threadId ?? null,
    })),
    z.object({ method: z.literal("turn/started"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({ method: z.literal("turn/completed"), params: TurnCompletedNotificationSchema })
        .transform(({ params }) => ({
        kind: "turn_completed",
        status: params.turn.status,
        errorMessage: params.turn.error?.message ?? null,
        threadId: params.threadId ?? null,
    })),
    z.object({ method: z.literal("turn/completed"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z.object({ method: z.literal("error"), params: TurnErrorNotificationSchema }).transform(({ params }) => ({
        kind: "turn_error",
        message: params.error.message,
        details: params.error.additionalDetails ?? null,
        willRetry: params.willRetry,
        threadId: params.threadId ?? null,
    })),
    z.object({ method: z.literal("error"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z.object({ method: z.literal("warning"), params: WarningNotificationSchema }).transform(({ params }) => ({
        kind: "warning",
        message: params.message,
        threadId: params.threadId ?? null,
    })),
    z.object({ method: z.literal("warning"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({ method: z.literal("turn/plan/updated"), params: TurnPlanUpdatedNotificationSchema })
        .transform(({ params }) => ({
        kind: "plan_updated",
        plan: params.plan.map((entry) => ({
            step: entry.step ?? null,
            status: entry.status ?? null,
        })),
        threadId: params.threadId ?? null,
    })),
    z.object({ method: z.literal("turn/plan/updated"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({ method: z.literal("turn/diff/updated"), params: TurnDiffUpdatedNotificationSchema })
        .transform(({ params }) => ({
        kind: "diff_updated",
        diff: params.diff,
        threadId: params.threadId ?? null,
    })),
    z.object({ method: z.literal("turn/diff/updated"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("thread/tokenUsage/updated"),
        params: ThreadTokenUsageUpdatedNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "token_usage_updated",
        tokenUsage: params.tokenUsage,
        threadId: params.threadId ?? null,
    })),
    z.object({ method: z.literal("thread/tokenUsage/updated"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({ method: z.literal("thread/compacted"), params: ContextCompactedNotificationSchema })
        .transform(({ params }) => ({
        kind: "context_compacted",
        threadId: params.threadId,
        turnId: params.turnId ?? null,
    })),
    z.object({ method: z.literal("thread/compacted"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("item/agentMessage/delta"),
        params: ItemTextDeltaNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "agent_message_delta",
        itemId: params.itemId,
        delta: params.delta,
        threadId: params.threadId ?? null,
    })),
    z.object({ method: z.literal("item/agentMessage/delta"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("item/reasoning/summaryTextDelta"),
        params: ItemTextDeltaNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "reasoning_delta",
        itemId: params.itemId,
        delta: params.delta,
        threadId: params.threadId ?? null,
    })),
    z.object({ method: z.literal("item/reasoning/summaryTextDelta"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({ method: z.literal("item/completed"), params: ItemLifecycleNotificationSchema })
        .transform(({ params }) => ({
        kind: "item_completed",
        source: "item",
        threadId: params.threadId ?? null,
        turnId: params.turnId ?? null,
        item: params.item,
    })),
    z.object({ method: z.literal("item/completed"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({ method: z.literal("item/started"), params: ItemLifecycleNotificationSchema })
        .transform(({ params }) => ({
        kind: "item_started",
        source: "item",
        threadId: params.threadId ?? null,
        turnId: params.turnId ?? null,
        item: params.item,
    })),
    z.object({ method: z.literal("item/started"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/item_started"),
        params: CodexEventItemLifecycleNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "item_started",
        source: "codex_event",
        threadId: getCodexEventThreadId(params),
        turnId: getCodexEventTurnId(params),
        item: params.msg.item,
    })),
    z.object({ method: z.literal("codex/event/item_started"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/item_completed"),
        params: CodexEventItemLifecycleNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "item_completed",
        source: "codex_event",
        threadId: getCodexEventThreadId(params),
        turnId: getCodexEventTurnId(params),
        item: params.msg.item,
    })),
    z.object({ method: z.literal("codex/event/item_completed"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/exec_command_begin"),
        params: CodexEventExecCommandBeginNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "exec_command_started",
        callId: params.msg.call_id ?? null,
        command: params.msg.command ?? null,
        cwd: params.msg.cwd ?? null,
        threadId: getCodexEventThreadId(params),
    })),
    z.object({ method: z.literal("codex/event/exec_command_begin"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/exec_command_end"),
        params: CodexEventExecCommandEndNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "exec_command_completed",
        callId: params.msg.call_id ?? null,
        command: params.msg.command ?? null,
        cwd: params.msg.cwd ?? null,
        output: params.msg.aggregated_output ??
            params.msg.aggregatedOutput ??
            params.msg.formatted_output ??
            params.msg.stdout ??
            null,
        exitCode: params.msg.exit_code ?? params.msg.exitCode ?? null,
        success: params.msg.success ?? null,
        stderr: params.msg.stderr ?? null,
        threadId: getCodexEventThreadId(params),
    })),
    z.object({ method: z.literal("codex/event/exec_command_end"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/exec_command_output_delta"),
        params: CodexEventExecCommandOutputDeltaNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "exec_command_output_delta",
        callId: params.msg.call_id ?? null,
        stream: params.msg.stream ?? null,
        chunk: params.msg.chunk ?? params.msg.delta ?? null,
        threadId: getCodexEventThreadId(params),
    })),
    z
        .object({
        method: z.literal("codex/event/exec_command_output_delta"),
        params: z.unknown(),
    })
        .transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/terminal_interaction"),
        params: CodexEventTerminalInteractionNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "terminal_interaction",
        source: "codex_event",
        callId: params.msg.call_id ?? null,
        processId: typeof params.msg.process_id === "number"
            ? String(params.msg.process_id)
            : (params.msg.process_id ?? null),
        stdin: params.msg.stdin ?? null,
        threadId: getCodexEventThreadId(params),
    })),
    z
        .object({ method: z.literal("codex/event/terminal_interaction"), params: z.unknown() })
        .transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("item/commandExecution/terminalInteraction"),
        params: ItemCommandExecutionTerminalInteractionNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "terminal_interaction",
        source: "item",
        callId: params.itemId ?? null,
        processId: typeof params.processId === "number"
            ? String(params.processId)
            : (params.processId ?? null),
        stdin: params.stdin ?? null,
        threadId: params.threadId ?? null,
    })),
    z
        .object({
        method: z.literal("item/commandExecution/terminalInteraction"),
        params: z.unknown(),
    })
        .transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/patch_apply_begin"),
        params: CodexEventPatchApplyBeginNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "patch_apply_started",
        callId: params.msg.call_id ?? null,
        changes: params.msg.changes ?? null,
        threadId: getCodexEventThreadId(params),
    })),
    z.object({ method: z.literal("codex/event/patch_apply_begin"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/patch_apply_end"),
        params: CodexEventPatchApplyEndNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "patch_apply_completed",
        callId: params.msg.call_id ?? null,
        changes: params.msg.changes ?? null,
        stdout: params.msg.stdout ?? null,
        stderr: params.msg.stderr ?? null,
        success: params.msg.success ?? null,
        threadId: getCodexEventThreadId(params),
    })),
    z.object({ method: z.literal("codex/event/patch_apply_end"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("item/fileChange/outputDelta"),
        params: ItemFileChangeOutputDeltaNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "file_change_output_delta",
        itemId: params.itemId,
        delta: params.delta ?? params.chunk ?? null,
        threadId: params.threadId ?? null,
    })),
    z.object({ method: z.literal("item/fileChange/outputDelta"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/turn_diff"),
        params: CodexEventTurnDiffNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "diff_updated",
        diff: params.msg.unified_diff ?? params.msg.diff ?? "",
        threadId: getCodexEventThreadId(params),
    })),
    z.object({ method: z.literal("codex/event/turn_diff"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/turn_aborted"),
        params: CodexEventTurnAbortedNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "turn_completed",
        status: "interrupted",
        errorMessage: null,
        threadId: getCodexEventThreadId(params),
    })),
    z.object({ method: z.literal("codex/event/turn_aborted"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/task_complete"),
        params: CodexEventTaskCompleteNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "turn_completed",
        status: "completed",
        errorMessage: null,
        threadId: getCodexEventThreadId(params),
    })),
    z.object({ method: z.literal("codex/event/task_complete"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({
        method: z.literal("codex/event/thread_rolled_back"),
        params: CodexEventThreadRolledBackNotificationSchema,
    })
        .transform(({ params }) => ({
        kind: "thread_rolled_back",
        numTurns: params.msg.num_turns ?? params.msg.numTurns ?? 0,
        threadId: getCodexEventThreadId(params),
    })),
    z.object({ method: z.literal("codex/event/thread_rolled_back"), params: z.unknown() }).transform(({ method, params }) => ({
        kind: "invalid_payload",
        method,
        params,
    })),
    z
        .object({ method: z.string(), params: z.unknown() })
        .transform(({ method, params }) => ({ kind: "unknown_method", method, params })),
]);
async function readCodexConfiguredDefaults(client, logger) {
    let savedConfigDefaults = {};
    try {
        const response = toObjectRecord(await client.request("getUserSavedConfig", {}));
        const config = toObjectRecord(response?.config);
        const modelValue = typeof config?.model === "string" ? config.model : undefined;
        const thinkingOptionValue = typeof config?.modelReasoningEffort === "string" ? config.modelReasoningEffort : null;
        savedConfigDefaults = {
            model: normalizeCodexModelId(modelValue),
            thinkingOptionId: normalizeCodexThinkingOptionId(thinkingOptionValue),
        };
    }
    catch (error) {
        logger.debug({ error }, "Failed to read Codex saved config defaults");
    }
    if (savedConfigDefaults.model && savedConfigDefaults.thinkingOptionId) {
        return savedConfigDefaults;
    }
    let configReadDefaults = {};
    try {
        const response = toObjectRecord(await client.request("config/read", {}));
        const config = toObjectRecord(response?.config);
        const modelValue = typeof config?.model === "string" ? config.model : undefined;
        const thinkingOptionValue = typeof config?.model_reasoning_effort === "string" ? config.model_reasoning_effort : null;
        configReadDefaults = {
            model: normalizeCodexModelId(modelValue),
            thinkingOptionId: normalizeCodexThinkingOptionId(thinkingOptionValue),
        };
    }
    catch (error) {
        logger.debug({ error }, "Failed to read Codex config defaults");
    }
    return mergeCodexConfiguredDefaults(savedConfigDefaults, configReadDefaults);
}
function enabledCodexSkills(entries) {
    const skillsByName = new Map();
    for (const entry of entries) {
        const skillRecord = toObjectRecord(entry);
        if (!skillRecord ||
            skillRecord.enabled === false ||
            typeof skillRecord.name !== "string" ||
            typeof skillRecord.path !== "string" ||
            skillsByName.has(skillRecord.name)) {
            continue;
        }
        skillsByName.set(skillRecord.name, {
            name: skillRecord.name,
            description: resolveSkillDescription(skillRecord),
            path: skillRecord.path,
        });
    }
    return Array.from(skillsByName.values());
}
export async function codexAppServerTurnInputFromPrompt(prompt, logger) {
    if (typeof prompt === "string") {
        return [toCodexTextInput(prompt)];
    }
    const output = [];
    let previousTextBlock = false;
    for (const block of prompt) {
        if (block.type === "text") {
            output.push(toCodexTextInput(block.text));
            previousTextBlock = block.text.length > 0;
            continue;
        }
        if (block.type === "skill") {
            output.push(block);
            previousTextBlock = false;
            continue;
        }
        if (block.type === "image") {
            try {
                const filePath = (await materializeProviderImage({
                    data: block.data,
                    mimeType: block.mimeType,
                })).path;
                output.push({ type: "localImage", path: filePath });
            }
            catch (error) {
                const message = error instanceof Error ? error.message : String(error);
                logger.warn({ message }, "Failed to write Codex image attachment");
                output.push({
                    ...toCodexTextInput(`User attached image (failed to write temp file): ${message}`),
                });
            }
            previousTextBlock = false;
            continue;
        }
        const attachmentText = renderPromptAttachmentAsText(block);
        output.push(toCodexTextInput(previousTextBlock ? `\n\n${attachmentText}` : attachmentText));
        previousTextBlock = true;
    }
    return output;
}
function toCodexTextInput(text) {
    return {
        type: "text",
        text,
        text_elements: [],
    };
}
export function buildCodexAppServerEnv(runtimeSettings, launchEnv) {
    return createProviderEnv({
        runtimeSettings,
        overlays: [launchEnv],
    });
}
function codexStartupStateDirKey(environment) {
    const home = process.platform === "win32"
        ? environment.USERPROFILE || os.homedir()
        : environment.HOME || os.homedir();
    return path.resolve(environment.CODEX_HOME || path.join(home, ".codex"));
}
function buildCodexAppServerInitializeParams() {
    return {
        clientInfo: CODEX_NON_ORIGINATING_APP_SERVER_CLIENT_INFO,
        capabilities: {
            experimentalApi: true,
            mcpServerOpenaiFormElicitation: true,
        },
    };
}
function normalizeOpenAICompatibleBaseUrl(value) {
    const trimmed = value.trim();
    if (!trimmed) {
        return null;
    }
    const withoutTrailingSlashes = trimmed.replace(/\/+$/u, "");
    if (withoutTrailingSlashes.endsWith("/v1")) {
        return withoutTrailingSlashes;
    }
    return `${withoutTrailingSlashes}/v1`;
}
function buildCodexCustomProviderConfig(runtimeSettings, customProvider) {
    if (customProvider?.extends !== CODEX_PROVIDER) {
        return null;
    }
    const baseUrl = runtimeSettings?.env?.OPENAI_BASE_URL;
    if (typeof baseUrl !== "string") {
        return null;
    }
    const normalizedBaseUrl = normalizeOpenAICompatibleBaseUrl(baseUrl);
    if (!normalizedBaseUrl) {
        return null;
    }
    const providerConfig = {
        name: customProvider.label,
        base_url: normalizedBaseUrl,
        wire_api: "responses",
    };
    if (runtimeSettings?.env?.OPENAI_API_KEY?.trim()) {
        providerConfig.env_key = "OPENAI_API_KEY";
        providerConfig.requires_openai_auth = false;
    }
    // Namespace Paseo ids so they cannot override Codex's built-ins. A colon is
    // forbidden in Paseo ids, keeping these keys distinct from every legacy id
    // when both generations are listed together.
    const providerKey = `paseo:${customProvider.id}`;
    return {
        model_provider: providerKey,
        model_providers: {
            [providerKey]: providerConfig,
        },
    };
}
function resolveCodexParentSubagentId(parentCallId, emittingThreadId) {
    return parentCallId ? emittingThreadId : null;
}
export class CodexAppServerAgentSession {
    usageSession() {
        if (this.closed)
            return null;
        return {
            provider: "codex",
            model: this.config.model,
            env: this.harnessEnvironment,
            sessionKey: this.usageSessionKey,
        };
    }
    constructor(config, resumeHandle, logger, spawnAppServer, deps = {}, ephemeral = false, goalsEnabled = false, autoReviewEnabled = false, agentId, initialResumePurpose = "interactive") {
        this.resumeHandle = resumeHandle;
        this.spawnAppServer = spawnAppServer;
        this.deps = deps;
        this.ephemeral = ephemeral;
        this.goalsEnabled = goalsEnabled;
        this.autoReviewEnabled = autoReviewEnabled;
        this.agentId = agentId;
        this.initialResumePurpose = initialResumePurpose;
        this.provider = CODEX_PROVIDER;
        this.capabilities = CODEX_APP_SERVER_CAPABILITIES;
        this.resolvedWorkspaceWrite = null;
        this.resolvedSandboxPolicy = null;
        this.currentThreadId = null;
        // Codex writes a new thread's rollout at its first turn. Until then the thread exists only
        // in this app-server, so no other app-server can resume it and it is not reported.
        this.savedThreadId = null;
        this.currentTurnId = null;
        this.pendingForegroundTurnIdentification = null;
        this.pendingForegroundStart = null;
        this.client = null;
        this.background = new CodexBackgroundInventory((event) => this.emitEvent(event));
        this.threadRollbackAvailable = true;
        this.subscribers = new Set();
        this.startupEvents = null;
        /** How the opened session differs from the options it was given; reported at first subscribe. */
        this.openOptionsPatch = {};
        this.turnNamespace = randomUUID();
        this.nextTurnOrdinal = 0;
        this.activeForegroundTurnId = null;
        this.activeClientMessageId = null;
        this.cachedRuntimeInfo = null;
        /** The tier the options ask for; `serviceTier` is what the current model can honor. */
        this.requestedServiceTier = null;
        this.speedModels = [];
        this.planModeEnabled = false;
        // History read by rewind or an archived resume, held until streamHistory consumes it.
        this.stagedHistory = null;
        // Collects provider subagent events emitted while a thread history read rebuilds routes.
        this.historySubagentEvents = null;
        // A resumed thread's earlier turns and sub-agents are known only after reading its history.
        this.historyIndexed = true;
        this.pendingPermissions = new Map();
        this.mcpElicitationPermissionIds = new Map();
        this.pendingPermissionHandlers = new Map();
        this.resolvedPermissionRequests = new Set();
        this.pendingAgentMessages = new Map();
        this.pendingReasoning = new Map();
        this.pendingCommandOutputDeltas = new Map();
        this.pendingFileChangeOutputDeltas = new Map();
        this.pendingToolCallDetails = new Map();
        this.pendingAssistantMessageBoundary = false;
        // The root turn's model-stream reconnect run that has not yet resumed or ended.
        this.activeStreamRetry = null;
        this.shownWarningMessages = new Set();
        this.terminalCommandByProcessId = new Map();
        // The root thread's still-running command process ids, keyed by the native turn that
        // started them. Codex's `thread/backgroundTerminals/list` carries no turn, so
        // `item/started` is the only record of which turn owns a process.
        this.terminalProcessIdsByTurnId = new Map();
        this.interruptedTurnCleanup = null;
        this.pendingUnlabeledTerminalInteractions = new Map();
        this.nextTerminalInteractionOrdinal = 0;
        this.emittedTerminalInteractionKeys = new Set();
        this.emittedExecCommandStartedCallIds = new Set();
        this.emittedExecCommandCompletedCallIds = new Set();
        this.emittedItemStartedIds = new Set();
        this.emittedItemCompletedIds = new Set();
        this.emittedProviderSubagentUserMessageKeys = new Set();
        this.subAgentCallsByCallId = new Map();
        this.subAgentCallIdByChildThreadId = new Map();
        this.pendingSubAgentNotificationsByThreadId = new Map();
        this.warnedUnknownNotificationMethods = new Set();
        this.warnedInvalidNotificationPayloads = new Set();
        this.warnedIncompleteEditToolCallIds = new Set();
        this.latestPlanResult = null;
        this.userMessageTurnIndexes = new Map();
        this.userMessageTurnIds = [];
        this.userMessageProviderTurnIds = new Map();
        this.pendingManualCompactionStarts = 0;
        this.compactionTriggerByItemId = new Map();
        this.pendingRootCompactionItemIds = new Set();
        this.pendingAnonymousRootCompactions = 0;
        // Child threads with a compaction shown as loading. Some Codex builds settle it only through
        // `thread/compacted` or the end of the child's turn.
        this.pendingChildCompactionThreadIds = new Set();
        // Codex can report one completed compaction through both channels:
        // `thread/compacted` and a completed `contextCompaction` item.
        this.unpairedCompactionNotificationCompletions = 0;
        this.unpairedCompactionItemCompletions = 0;
        this.connectionState = "disconnected";
        this.connectionPromise = null;
        this.startupAbort = new AbortController();
        this.closed = false;
        this.collaborationModes = [];
        this.resolvedCollaborationMode = null;
        this.cachedSkills = null;
        this.usageSessionKey = randomUUID();
        this.pendingNotifications = Promise.resolve();
        this.logger = logger.child({
            module: "agent",
            provider: CODEX_PROVIDER,
            agentId: this.agentId,
        });
        if (config.modeId !== undefined) {
            validateCodexMode(config.modeId);
        }
        this.hasWorkflowModeOverride = config.modeId !== undefined;
        this.currentMode = config.modeId ?? DEFAULT_CODEX_MODE_ID;
        this.providerOptions =
            validateProviderOptions("codex", CodexProviderOptionsSchema, config.providerOptions) ?? {};
        this.config = config;
        this.harnessEnvironment = deps.environment ?? buildCodexAppServerEnv();
        this.asyncQuestions = new CodexAsyncQuestions(resumeHandle?.metadata?.asyncQuestions);
        this.codexHome = deps.codexHome ?? resolveCodexHomeDir(process.env);
        this.config.thinkingOptionId = normalizeCodexThinkingOptionId(this.config.thinkingOptionId);
        this.requestedServiceTier = readCodexServiceTier(this.providerOptions);
        this.planModeEnabled = this.providerOptions.plan_mode === true;
        if (this.resumeHandle?.sessionId) {
            this.currentThreadId = this.resumeHandle.sessionId;
            this.savedThreadId = this.resumeHandle.sessionId;
            this.historyIndexed = false;
            // Resuming can start a goal before the caller receives the session.
            if (this.initialResumePurpose === "interactive")
                this.startupEvents = [];
        }
    }
    get id() {
        return this.currentThreadId;
    }
    get controls() {
        return buildCodexControls({
            serviceTiers: this.currentServiceTiers(),
            planModeAvailable: this.hasPlanCollaborationMode(),
        });
    }
    async connect() {
        // A replacement process must not inherit the previous process's queued events.
        if (this.connectionState === "disconnected")
            await this.pendingNotifications;
        if (this.closed) {
            throw this.createClosedError();
        }
        if (this.connectionState !== "disconnected")
            return;
        if (this.connectionPromise) {
            await this.connectionPromise;
            if (this.closed) {
                throw this.createClosedError();
            }
            return;
        }
        const connectionPromise = this.establishConnection();
        this.connectionPromise = connectionPromise;
        try {
            await connectionPromise;
        }
        finally {
            if (this.connectionPromise === connectionPromise) {
                this.connectionPromise = null;
            }
        }
        if (this.closed) {
            throw this.createClosedError();
        }
    }
    async establishConnection() {
        if (this.initialResumePurpose === "history") {
            this.stagedHistory = await this.readArchivedHistory();
            this.connectionState = "history-ready";
            return;
        }
        const { client, initialized } = await codexAppServerStartup.start({
            stateDirKey: codexStartupStateDirKey(this.harnessEnvironment),
            spawn: this.spawnAppServer,
            createClient: (child) => new CodexAppServerClient(child, this.logger, () => this.traceContext()),
            initializeParams: buildCodexAppServerInitializeParams(),
            logger: this.logger,
            signal: this.startupAbort.signal,
        });
        if (this.closed) {
            await client.dispose();
            throw this.createClosedError();
        }
        this.client = client;
        // Resume can emit autonomous commands before connection setup finishes. Their
        // ownership must live for the whole connection, including startup notifications.
        this.background.start({ client, thread: null });
        client.setUnexpectedTerminationHandler((error) => {
            this.pendingNotifications = this.queueUnexpectedTermination(error);
        });
        client.setNotificationHandler((method, params) => this.handleNotification(method, params));
        this.registerRequestHandlers();
        try {
            this.threadRollbackAvailable = codexServerHasThreadRollback(toObjectRecord(initialized)?.userAgent);
            this.speedModels =
                CodexModelListResponseSchema.parse(await client.request("model/list", {})).data ?? [];
            this.normalizeSpeed();
            await this.loadResolvedWorkspaceWrite();
            await this.loadCollaborationModes();
            await this.loadSkills();
            if (this.currentThreadId) {
                await this.ensureThreadLoaded();
                await this.applyDefaultModelAndThinking();
            }
            if (this.closed) {
                throw this.createClosedError();
            }
            this.connectionState = "connected";
            this.background.changed(this.currentThreadId);
        }
        catch (error) {
            try {
                if (this.client === client) {
                    await this.disposeClient();
                }
                else {
                    await client.dispose();
                }
            }
            catch (disposeError) {
                this.logger.warn({ err: disposeError, connectError: error }, "Failed to dispose Codex app-server client after connection failure");
            }
            throw error;
        }
    }
    async loadResolvedWorkspaceWrite() {
        if (!this.client)
            return;
        try {
            const response = toObjectRecord(await this.client.request("config/read", { cwd: this.config.cwd ?? null }));
            const config = toObjectRecord(response?.config);
            this.resolvedWorkspaceWrite = readSandboxWorkspaceWrite(config?.sandbox_workspace_write);
        }
        catch (error) {
            this.logger.debug({ error }, "Failed to read resolved Codex workspace-write config");
        }
    }
    rememberResolvedSandboxPolicy(response) {
        const sandbox = toObjectRecord(toObjectRecord(response)?.sandbox);
        this.resolvedSandboxPolicy = sandbox ?? null;
        if (sandbox?.type !== "workspaceWrite")
            return;
        this.resolvedWorkspaceWrite = readSandboxWorkspaceWrite(sandbox);
    }
    createClosedError() {
        return new Error("Codex app-server session is closed");
    }
    traceContext() {
        return {
            agentId: this.agentId,
            sessionId: this.currentThreadId ?? undefined,
            turnId: this.activeForegroundTurnId ?? undefined,
        };
    }
    async queueUnexpectedTermination(error) {
        this.connectionState = "disconnected";
        this.background.stop();
        // Finish notifications already received from this process before ending
        // their turn, so queued retry notices cannot reopen a failed retry row.
        await this.pendingNotifications;
        if (!this.closed)
            this.handleUnexpectedTermination(error);
    }
    handleUnexpectedTermination(error) {
        this.connectionState = "disconnected";
        this.background.stop();
        const hasActiveRootTurn = this.activeForegroundTurnId !== null || this.currentTurnId !== null;
        this.clearPendingPermissions({ preservePlanApprovals: !hasActiveRootTurn });
        if (hasActiveRootTurn) {
            this.settleStreamRetry({ status: "failed", error: error.message });
            this.emitEvent({
                type: "turn_failed",
                provider: CODEX_PROVIDER,
                error: error.message,
            });
        }
        this.activeForegroundTurnId = null;
        this.activeClientMessageId = null;
        this.currentTurnId = null;
        this.pendingForegroundTurnIdentification?.resolve(null);
        this.pendingForegroundTurnIdentification = null;
    }
    async loadCollaborationModes() {
        if (!this.client)
            return;
        try {
            const response = toObjectRecord(await this.client.request("collaborationMode/list", {}));
            const data = Array.isArray(response?.data) ? response.data : [];
            this.collaborationModes = data.map((entry) => {
                const record = toObjectRecord(entry);
                return {
                    name: typeof record?.name === "string" ? record.name : "",
                    mode: typeof record?.mode === "string" ? record.mode : null,
                    model: typeof record?.model === "string" ? record.model : null,
                    reasoning_effort: typeof record?.reasoning_effort === "string" ? record.reasoning_effort : null,
                    developer_instructions: typeof record?.developer_instructions === "string"
                        ? record.developer_instructions
                        : null,
                };
            });
        }
        catch (error) {
            this.logger.trace({
                agentId: this.agentId,
                provider: CODEX_PROVIDER,
                sessionId: this.currentThreadId,
                turnId: this.activeForegroundTurnId ?? undefined,
                error,
            }, "provider.codex.metadata.collaboration_modes_failed");
            this.collaborationModes = [];
        }
        this.refreshResolvedCollaborationMode();
    }
    async loadSkills() {
        if (!this.client)
            return;
        try {
            const response = toObjectRecord(await this.client.request("skills/list", {
                cwds: [this.config.cwd],
            }));
            const entries = Array.isArray(response?.data) ? response.data : [];
            const allSkills = [];
            for (const entry of entries) {
                const entryRecord = toObjectRecord(entry);
                const list = Array.isArray(entryRecord?.skills) ? entryRecord.skills : [];
                allSkills.push(...list);
            }
            this.cachedSkills = enabledCodexSkills(allSkills);
        }
        catch (error) {
            this.logger.trace({
                agentId: this.agentId,
                provider: CODEX_PROVIDER,
                sessionId: this.currentThreadId,
                turnId: this.activeForegroundTurnId ?? undefined,
                error,
            }, "provider.codex.metadata.skills_failed");
            this.cachedSkills = null;
        }
    }
    findCollaborationMode(target) {
        if (this.collaborationModes.length === 0)
            return null;
        const findByName = (predicate) => this.collaborationModes.find((entry) => predicate(entry.name.toLowerCase()));
        if (target === "plan") {
            return findByName((name) => name.includes("plan") || name.includes("read")) ?? null;
        }
        return (findByName((name) => name.includes("auto") || name.includes("code")) ??
            this.collaborationModes.find((entry) => {
                const name = entry.name.toLowerCase();
                return !name.includes("plan") && !name.includes("read");
            }) ??
            this.collaborationModes[0] ??
            null);
    }
    hasPlanCollaborationMode() {
        return this.findCollaborationMode("plan") !== null;
    }
    resolveCollaborationMode() {
        const match = this.findCollaborationMode(this.planModeEnabled ? "plan" : "code");
        if (!match)
            return null;
        const settings = {};
        if (match.model)
            settings.model = match.model;
        if (match.reasoning_effort)
            settings.reasoning_effort = match.reasoning_effort;
        const developerInstructions = composeSystemPromptParts(match.developer_instructions, this.config.systemPrompt, this.config.daemonAppendSystemPrompt);
        if (developerInstructions)
            settings.developer_instructions = developerInstructions;
        if (this.config.model)
            settings.model = this.config.model;
        const thinkingOptionId = normalizeCodexThinkingOptionId(this.config.thinkingOptionId);
        if (thinkingOptionId)
            settings.reasoning_effort = thinkingOptionId;
        return { mode: match.mode ?? "code", settings, name: match.name };
    }
    refreshResolvedCollaborationMode() {
        this.resolvedCollaborationMode = this.resolveCollaborationMode();
    }
    currentServiceTiers() {
        const selectedModel = this.config.model
            ? this.speedModels.find((model) => model.id === this.config.model || model.model === this.config.model)
            : (this.speedModels.find((model) => model.isDefault) ?? this.speedModels[0]);
        return selectedModel?.serviceTiers ?? [];
    }
    /**
     * A saved `service_tier: "fast"` runs as the model's Fast tier, and a tier the opening model
     * lacks runs as Normal; the session says so in `openOptionsPatch`, so the options and the
     * control agree (as when the model is switched later).
     */
    normalizeSpeed() {
        const requested = this.requestedServiceTier;
        const resolved = this.resolveServiceTier(requested);
        if (requested === resolved)
            return;
        const runs = resolved ?? "default";
        this.providerOptions = { ...this.providerOptions, service_tier: runs };
        this.requestedServiceTier = runs;
        this.openOptionsPatch = { service_tier: runs };
    }
    get serviceTier() {
        return this.resolveServiceTier(this.requestedServiceTier);
    }
    resolveServiceTier(requested) {
        // Until the model list arrives there is nothing to check the request against.
        if (this.speedModels.length === 0 || requested === "default")
            return requested;
        const tiers = this.currentServiceTiers();
        // `fast` names whichever tier the model calls Fast.
        if (requested === "fast" && !tiers.some((tier) => tier.id === "fast")) {
            return tiers.find((tier) => tier.name.toLowerCase() === "fast")?.id ?? null;
        }
        return tiers.some((tier) => tier.id === requested) ? requested : null;
    }
    rememberPlanResult(item) {
        if (item.detail.type !== "plan") {
            return;
        }
        this.latestPlanResult = {
            callId: item.callId,
            text: item.detail.text,
            turnId: this.currentTurnId,
        };
    }
    emitSyntheticPlanApprovalRequest(planText) {
        this.dismissPendingPlanApprovals("Superseded by a newer plan");
        const requestId = `permission-${randomUUID()}`;
        const request = {
            id: requestId,
            provider: CODEX_PROVIDER,
            name: "CodexPlanApproval",
            kind: "plan",
            title: "Plan",
            description: "Review the proposed plan before implementation starts.",
            input: { plan: planText },
            actions: buildPlanPermissionActions(),
            metadata: {
                planText,
                source: "codex_plan_approval",
            },
        };
        this.pendingPermissions.set(requestId, request);
        this.pendingPermissionHandlers.set(requestId, {
            resolve: () => undefined,
            kind: "plan",
            plan: { text: planText, turnId: this.activeForegroundTurnId ?? undefined },
        });
        // Reserve the proposal's place before a later prompt can resolve it.
        this.emitEvent({
            type: "timeline",
            provider: CODEX_PROVIDER,
            item: {
                type: "tool_call",
                callId: requestId,
                name: "plan_approval",
                status: "running",
                error: null,
                detail: { type: "plan", text: planText },
            },
        });
        this.emitEvent({ type: "permission_requested", provider: CODEX_PROVIDER, request });
    }
    /**
     * Prepare the session for plan implementation by disabling plan mode
     * and returning the implementation prompt. The caller is responsible for
     * starting the turn through the normal streamAgent path.
     */
    preparePlanImplementation(params) {
        const planText = typeof params.planText === "string" ? normalizePlanMarkdown(params.planText) : "";
        this.setPlanMode(false);
        // The user left plan mode; their options follow so controls and reopened sessions agree.
        this.emitEvent({
            type: "options_changed",
            provider: CODEX_PROVIDER,
            patch: { plan_mode: false },
        });
        return buildCodexPlanImplementationPrompt(planText);
    }
    registerRequestHandlers() {
        if (!this.client)
            return;
        this.client.setRequestHandler("item/commandExecution/requestApproval", (params) => this.handleCommandApprovalRequest(params));
        this.client.setRequestHandler("item/fileChange/requestApproval", (params) => this.handleFileChangeApprovalRequest(params));
        this.client.setRequestHandler("item/tool/requestUserInput", (params) => this.handleToolApprovalRequest(params));
        this.client.setRequestHandler("mcpServer/elicitation/request", (params, requestId) => this.handleMcpElicitationRequest(params, requestId));
        // Keep the legacy method name for older Codex builds.
        this.client.setRequestHandler("tool/requestUserInput", (params) => this.handleToolApprovalRequest(params));
    }
    async readArchivedHistory() {
        const { client } = await codexAppServerStartup.start({
            stateDirKey: codexStartupStateDirKey(this.harnessEnvironment),
            spawn: this.spawnAppServer,
            createClient: (child) => new CodexAppServerClient(child, this.logger, () => this.traceContext()),
            initializeParams: buildCodexAppServerInitializeParams(),
            logger: this.logger,
            signal: this.startupAbort.signal,
        });
        try {
            return await this.readThreadHistory(client);
        }
        finally {
            await client.dispose();
        }
    }
    // Reads the thread and its sub-agent threads, rebuilding the turn index and
    // sub-agent routes. The caller owns the returned history; nothing retains it.
    async readThreadHistory(client) {
        if (!client || !this.currentThreadId) {
            return { timeline: [], providerSubagentEvents: [] };
        }
        const { timeline, subAgentRoutes } = await loadCodexThreadHistoryTimeline({
            client,
            threadId: this.currentThreadId,
            cwd: this.config.cwd ?? null,
        });
        this.subAgentCallsByCallId.clear();
        this.subAgentCallIdByChildThreadId.clear();
        this.pendingSubAgentNotificationsByThreadId.clear();
        const providerSubagentEvents = [];
        this.historySubagentEvents = providerSubagentEvents;
        try {
            await this.loadPersistedSubAgentHistories(client, subAgentRoutes);
        }
        finally {
            this.historySubagentEvents = null;
        }
        this.resetCodexUserMessageTurns();
        for (const entry of timeline) {
            if (entry.item.type === "tool_call" && entry.item.name === "request_user_input_async") {
                entry.item = this.asyncQuestions.timeline(entry.item.callId) ?? entry.item;
            }
            if (entry.item.type === "user_message") {
                this.rememberCodexUserMessageTurn(entry.item.messageId, entry.providerTurnId);
            }
        }
        this.historyIndexed = true;
        return { timeline, providerSubagentEvents };
    }
    // Turns and rewind route by the thread's earlier turns and sub-agents. A resumed
    // session whose projection Paseo already holds reads them here and drops the rest.
    async ensureHistoryIndexed() {
        if (this.historyIndexed)
            return;
        await this.readThreadHistory(this.client);
    }
    async loadPersistedSubAgentHistories(client, rootRoutes) {
        const queue = rootRoutes.map((route) => ({
            route,
            parentCallId: null,
            parentSubagentId: null,
        }));
        const visitedThreadIds = new Set(this.currentThreadId ? [this.currentThreadId] : []);
        while (queue.length > 0 && visitedThreadIds.size < 100) {
            const next = queue.shift();
            if (!next || visitedThreadIds.has(next.route.childThreadId)) {
                continue;
            }
            visitedThreadIds.add(next.route.childThreadId);
            this.registerSubAgentToolCall({
                timelineItem: next.route.toolCall,
                rawItem: { agentThreadId: next.route.childThreadId },
                parentCallId: next.parentCallId,
                parentSubagentId: next.parentSubagentId,
            });
            try {
                const childHistory = await loadCodexThreadHistoryTimeline({
                    client,
                    threadId: next.route.childThreadId,
                    cwd: this.config.cwd ?? null,
                });
                for (const entry of childHistory.timeline) {
                    this.emitProviderSubagentTimeline(next.route.childThreadId, entry.item, entry.timestamp);
                }
                for (const route of childHistory.subAgentRoutes) {
                    queue.push({
                        route,
                        parentCallId: next.route.toolCall.callId,
                        parentSubagentId: next.route.childThreadId,
                    });
                }
            }
            catch (error) {
                this.logger.trace({ err: error, childThreadId: next.route.childThreadId }, "Failed to load persisted Codex child history");
            }
        }
    }
    async ensureThreadLoaded() {
        if (!this.client || !this.currentThreadId)
            return;
        // Paseo reads history only when it replays it; the resume response needs no turns.
        const params = { threadId: this.currentThreadId, excludeTurns: true };
        if (this.deps.customCodexConfig) {
            // Resume otherwise prefers the provider stored on the thread, including
            // the unnamespaced key emitted by earlier Paseo versions.
            params.modelProvider = this.deps.customCodexConfig.model_provider;
        }
        const developerInstructions = composeSystemPromptParts(this.config.systemPrompt, this.config.daemonAppendSystemPrompt);
        if (developerInstructions) {
            params.developerInstructions = developerInstructions;
        }
        const codexConfig = this.buildCodexInnerConfig();
        if (codexConfig) {
            params.config = codexConfig;
        }
        try {
            const loaded = toObjectRecord(await this.client.request("thread/loaded/list", {}));
            const ids = Array.isArray(loaded?.data) ? loaded.data : [];
            if (ids.includes(this.currentThreadId)) {
                return;
            }
            const response = await this.client.request("thread/resume", params);
            this.rememberResolvedSandboxPolicy(response);
        }
        catch (error) {
            const threadId = this.currentThreadId;
            const message = error instanceof Error ? error.message : String(error);
            if (isArchivedCodexThreadResumeError(error, threadId)) {
                try {
                    await this.client.request("thread/unarchive", { threadId });
                }
                catch (unarchiveError) {
                    if (!isCodexAlreadyUnarchivedError(unarchiveError, threadId)) {
                        throw unarchiveError;
                    }
                }
                const response = await this.client.request("thread/resume", params);
                this.rememberResolvedSandboxPolicy(response);
                this.logger.info({ threadId }, "Unarchived Codex thread to restore active Paseo agent");
                return;
            }
            this.logger.warn({ error, threadId }, "Failed to resume persisted Codex thread");
            throw new Error(`Failed to resume Codex thread ${threadId}: ${message}`, { cause: error });
        }
    }
    parseSlashCommandInput(text) {
        const trimmed = text.trim();
        if (!trimmed.startsWith("/") || trimmed.length <= 1) {
            return null;
        }
        const withoutPrefix = trimmed.slice(1);
        const firstWhitespaceIdx = withoutPrefix.search(/\s/);
        const commandName = firstWhitespaceIdx === -1 ? withoutPrefix : withoutPrefix.slice(0, firstWhitespaceIdx);
        if (!commandName || commandName.includes("/")) {
            return null;
        }
        const rawArgs = firstWhitespaceIdx === -1 ? "" : withoutPrefix.slice(firstWhitespaceIdx + 1).trim();
        return rawArgs.length > 0 ? { commandName, args: rawArgs } : { commandName };
    }
    async resolveSlashCommandInvocation(prompt) {
        if (this.deps.resolveSlashCommandInvocation) {
            return this.deps.resolveSlashCommandInvocation(prompt);
        }
        if (typeof prompt !== "string") {
            return null;
        }
        const parsed = this.parseSlashCommandInput(prompt);
        if (!parsed) {
            return null;
        }
        try {
            const commands = await this.listCommands();
            return commands.some((command) => command.name === parsed.commandName) ? parsed : null;
        }
        catch (error) {
            this.logger.warn({ err: error, commandName: parsed.commandName }, "Failed to resolve slash command; falling back to plain prompt input");
            return null;
        }
    }
    async buildCommandPromptInput(commandName, args) {
        if (commandName.startsWith("prompts:")) {
            const promptName = commandName.slice("prompts:".length);
            const promptPath = path.join(this.codexHome, "prompts", `${promptName}.md`);
            const raw = await fs.readFile(promptPath, "utf8");
            const parsed = parseFrontMatter(raw);
            return expandCodexCustomPrompt(parsed.body, args);
        }
        if (this.connectionState === "disconnected") {
            await this.connect();
        }
        else {
            await this.loadSkills();
        }
        const skill = this.cachedSkills?.find((entry) => entry.name === commandName);
        if (skill) {
            const trimmedArgs = args?.trim() ?? "";
            const text = trimmedArgs ? `$${skill.name} ${trimmedArgs}` : `$${skill.name}`;
            const input = [
                { type: "skill", name: skill.name, path: skill.path },
                { type: "text", text },
            ];
            return input;
        }
        return args ? `$${commandName} ${args}` : `$${commandName}`;
    }
    async buildTurnStartParams(prompt, options) {
        const input = await this.buildUserInput(prompt);
        const preset = MODE_PRESETS[this.currentMode] ?? MODE_PRESETS[DEFAULT_CODEX_MODE_ID];
        const params = {
            threadId: this.currentThreadId,
            input,
        };
        const { approvalPolicy, sandboxPolicyType } = this.applyTurnWorkflowPolicy(params, preset);
        if (this.config.model) {
            params.model = this.config.model;
        }
        const thinkingOptionId = normalizeCodexThinkingOptionId(this.config.thinkingOptionId);
        if (thinkingOptionId) {
            params.effort = thinkingOptionId;
        }
        params.serviceTier = this.serviceTier ?? "default";
        if (this.resolvedCollaborationMode) {
            params.collaborationMode = {
                mode: this.resolvedCollaborationMode.mode,
                settings: this.resolvedCollaborationMode.settings,
            };
        }
        if (this.config.cwd) {
            params.cwd = this.config.cwd;
        }
        if (options?.outputSchema) {
            params.outputSchema = normalizeCodexOutputSchema(options.outputSchema);
        }
        const developerInstructions = composeSystemPromptParts(this.config.systemPrompt, this.config.daemonAppendSystemPrompt);
        if (developerInstructions) {
            params.developerInstructions = developerInstructions;
        }
        const codexConfig = this.buildCodexInnerConfig();
        if (codexConfig) {
            params.config = codexConfig;
        }
        return {
            params,
            thinkingOptionId,
            approvalPolicy,
            sandboxPolicyType,
            hasOutputSchema: Boolean(options?.outputSchema),
            hasDeveloperInstructions: Boolean(developerInstructions),
            hasCodexConfig: Boolean(codexConfig),
        };
    }
    applyTurnWorkflowPolicy(params, preset) {
        const approvalPolicy = this.hasWorkflowModeOverride ? preset.approvalPolicy : undefined;
        const sandboxPolicyType = this.providerOptions.sandbox_mode ??
            (this.hasWorkflowModeOverride ? preset.sandbox : undefined);
        if (approvalPolicy && this.providerOptions.approval_policy === undefined) {
            params.approvalPolicy = approvalPolicy;
        }
        if (sandboxPolicyType) {
            const nativeType = toCodexSandboxPolicyType(sandboxPolicyType);
            const workspaceWrite = {
                ...this.resolvedWorkspaceWrite,
                ...this.providerOptions.sandbox_workspace_write,
            };
            params.sandboxPolicy =
                this.resolvedSandboxPolicy?.type === nativeType
                    ? this.resolvedSandboxPolicy
                    : toSandboxPolicy(sandboxPolicyType, workspaceWrite);
        }
        if (this.hasWorkflowModeOverride) {
            params.approvalsReviewer = preset.approvalsReviewer;
        }
        return { approvalPolicy, sandboxPolicyType };
    }
    logTurnStartSummary({ turnId, thinkingOptionId, approvalPolicy, sandboxPolicyType, hasOutputSchema, hasDeveloperInstructions, hasCodexConfig, }) {
        this.logger.info({
            turnId,
            threadId: this.currentThreadId,
            model: this.config.model ?? null,
            modeId: this.currentMode ?? null,
            effort: thinkingOptionId ?? null,
            serviceTier: this.serviceTier,
            cwd: this.config.cwd ?? null,
            approvalPolicy: approvalPolicy ?? null,
            sandboxPolicyType: sandboxPolicyType ?? null,
            hasCollaborationMode: Boolean(this.resolvedCollaborationMode),
            hasOutputSchema,
            hasDeveloperInstructions,
            hasCodexConfig,
        }, "Starting Codex app-server turn");
    }
    async run(prompt, options) {
        let currentAssistantMessageId = null;
        let currentAssistantMessageHasBoundary = false;
        let hasAssistantMessage = false;
        return runProviderTurn({
            prompt,
            runOptions: options,
            startTurn: (p, o) => this.startTurn(p, o),
            subscribe: (callback) => this.subscribe(callback),
            getSessionId: async () => (await this.getRuntimeInfo()).sessionId ?? "",
            reduceFinalText: ({ current, item }) => {
                if (item.type === "assistant_message") {
                    const hasPreviousAssistantMessage = hasAssistantMessage;
                    hasAssistantMessage = true;
                    const isNewMessage = item.messageId === undefined || item.messageId !== currentAssistantMessageId;
                    if (isNewMessage) {
                        currentAssistantMessageId = item.messageId ?? null;
                        currentAssistantMessageHasBoundary =
                            hasPreviousAssistantMessage &&
                                item.text.startsWith(ASSISTANT_MESSAGE_BOUNDARY_MARKDOWN);
                    }
                    const finalTextItem = currentAssistantMessageHasBoundary
                        ? {
                            ...item,
                            text: item.text.startsWith(ASSISTANT_MESSAGE_BOUNDARY_MARKDOWN)
                                ? item.text.slice(ASSISTANT_MESSAGE_BOUNDARY_MARKDOWN.length)
                                : item.text,
                        }
                        : item;
                    return isNewMessage
                        ? finalTextItem.text
                        : appendOrReplaceGrowingAssistantMessage({ current, item: finalTextItem });
                }
                if (item.type === "tool_call" && item.detail.type === "plan") {
                    currentAssistantMessageId = null;
                    currentAssistantMessageHasBoundary = false;
                    return item.detail.text;
                }
                return current;
            },
        });
    }
    async startTurn(prompt, options) {
        if (this.activeForegroundTurnId || this.pendingForegroundStart) {
            throw new Error("A foreground turn is already active");
        }
        let resolveStart;
        const pendingStart = {
            promise: new Promise((resolve) => {
                resolveStart = resolve;
            }),
            resolve: () => resolveStart(),
            cancelRequested: false,
        };
        this.pendingForegroundStart = pendingStart;
        this.dismissPendingPlanApprovals("Dismissed by a new prompt");
        try {
            await this.pendingNotifications;
            await this.connect();
            if (!this.client) {
                throw new Error("Codex client not initialized");
            }
            const slashCommand = await this.resolveSlashCommandInvocation(prompt);
            const effectivePrompt = slashCommand
                ? await this.buildCommandPromptInput(slashCommand.commandName, slashCommand.args)
                : prompt;
            if (this.currentThreadId) {
                await this.ensureThreadLoaded();
                await this.ensureHistoryIndexed();
            }
            else {
                await this.ensureThread();
            }
            const turnStart = await this.buildTurnStartParams(effectivePrompt, options);
            const turnId = this.createTurnId();
            this.activeForegroundTurnId = turnId;
            this.activeClientMessageId = options?.clientMessageId ?? null;
            this.currentTurnId = null;
            this.pendingForegroundTurnIdentification?.resolve(null);
            let resolveTurnIdentification;
            const turnIdentification = new Promise((resolvePromise) => {
                resolveTurnIdentification = resolvePromise;
            });
            this.pendingForegroundTurnIdentification = {
                foregroundTurnId: turnId,
                promise: turnIdentification,
                resolve: resolveTurnIdentification,
            };
            this.logTurnStartSummary({
                turnId,
                thinkingOptionId: turnStart.thinkingOptionId,
                approvalPolicy: turnStart.approvalPolicy,
                sandboxPolicyType: turnStart.sandboxPolicyType,
                hasOutputSchema: turnStart.hasOutputSchema,
                hasDeveloperInstructions: turnStart.hasDeveloperInstructions,
                hasCodexConfig: turnStart.hasCodexConfig,
            });
            // The interrupted turn's commands end before this turn begins, so none of them
            // outlives it into the new turn.
            await this.waitForInterruptedTurnCleanup();
            if (pendingStart.cancelRequested) {
                throw new Error("Codex turn start was interrupted before reaching Codex");
            }
            await this.client.request("turn/start", turnStart.params, TURN_START_TIMEOUT_MS);
            return { turnId };
        }
        catch (error) {
            this.pendingForegroundTurnIdentification?.resolve(null);
            this.pendingForegroundTurnIdentification = null;
            this.activeForegroundTurnId = null;
            this.activeClientMessageId = null;
            throw error;
        }
        finally {
            if (this.pendingForegroundStart === pendingStart) {
                this.pendingForegroundStart = null;
            }
            pendingStart.resolve();
        }
    }
    async steerActiveTurn(prompt, options) {
        const client = this.client;
        const threadId = this.currentThreadId;
        let nativeTurnId = this.currentTurnId;
        const foregroundTurnId = this.activeForegroundTurnId;
        if (!client || !threadId || foregroundTurnId !== options.expectedTurnId) {
            return { status: "unavailable" };
        }
        const pendingIdentification = this.pendingForegroundTurnIdentification;
        // The start acknowledgement precedes the notification that identifies the native
        // turn. Keep this steer attached to the accepted foreground turn while it arrives.
        if (!nativeTurnId && pendingIdentification?.foregroundTurnId === foregroundTurnId) {
            nativeTurnId = await pendingIdentification.promise;
        }
        if (!nativeTurnId) {
            return { status: "unavailable" };
        }
        if (await this.resolveSlashCommandInvocation(prompt))
            return { status: "unavailable" };
        if (!this.matchesSteerAdmission({ client, threadId, nativeTurnId, foregroundTurnId })) {
            return { status: "unavailable" };
        }
        const input = await this.buildUserInput(prompt);
        if (!this.matchesSteerAdmission({ client, threadId, nativeTurnId, foregroundTurnId })) {
            return { status: "unavailable" };
        }
        try {
            const response = await client.request("turn/steer", {
                threadId,
                expectedTurnId: nativeTurnId,
                input,
                ...(options.clientMessageId ? { clientUserMessageId: options.clientMessageId } : {}),
            }, TURN_START_TIMEOUT_MS);
            const record = toObjectRecord(response);
            const turn = record ? toObjectRecord(record.turn) : null;
            const acknowledgedTurnId = nonEmptyString(record?.turnId) ?? nonEmptyString(turn?.id);
            if (acknowledgedTurnId !== nativeTurnId) {
                throw new Error("Codex returned an invalid steer acknowledgement");
            }
            if (options.clearPendingPermissions) {
                await this.clearPendingPermissionsForSteer();
            }
            return { status: "accepted" };
        }
        catch (error) {
            if (isDefinitiveCodexSteerRejection(error))
                return { status: "unavailable" };
            throw error;
        }
    }
    matchesSteerAdmission(admission) {
        return (this.client === admission.client &&
            this.currentThreadId === admission.threadId &&
            this.currentTurnId === admission.nativeTurnId &&
            this.activeForegroundTurnId === admission.foregroundTurnId);
    }
    rememberCodexUserMessageTurn(messageId, providerTurnId) {
        if (typeof messageId !== "string" || messageId.length === 0) {
            return false;
        }
        if (this.userMessageTurnIndexes.has(messageId)) {
            if (providerTurnId) {
                this.userMessageProviderTurnIds.set(messageId, providerTurnId);
            }
            return false;
        }
        this.userMessageTurnIndexes.set(messageId, this.userMessageTurnIds.length);
        this.userMessageTurnIds.push(messageId);
        if (providerTurnId) {
            this.userMessageProviderTurnIds.set(messageId, providerTurnId);
        }
        return true;
    }
    resetCodexUserMessageTurns() {
        this.userMessageTurnIndexes.clear();
        this.userMessageTurnIds.length = 0;
        this.userMessageProviderTurnIds.clear();
    }
    truncateCodexUserMessageTurns(numTurns) {
        if (numTurns <= 0) {
            return;
        }
        const retainedCount = Math.max(0, this.userMessageTurnIds.length - numTurns);
        const removedMessageIds = this.userMessageTurnIds.splice(retainedCount);
        for (const messageId of removedMessageIds) {
            this.userMessageProviderTurnIds.delete(messageId);
        }
        this.userMessageTurnIndexes.clear();
        this.userMessageTurnIds.forEach((messageId, index) => {
            this.userMessageTurnIndexes.set(messageId, index);
        });
    }
    codexUserMessageTurns() {
        return {
            resolve: (messageId) => {
                const index = this.userMessageTurnIndexes.get(messageId);
                return index === undefined
                    ? null
                    : { index, turnId: this.userMessageProviderTurnIds.get(messageId) ?? null };
            },
            count: () => this.userMessageTurnIds.length,
        };
    }
    subscribe(callback) {
        this.subscribers.add(callback);
        const startupEvents = this.startupEvents;
        this.startupEvents = null;
        for (const event of startupEvents ?? [])
            this.notifySubscribers(event);
        const background = this.background.snapshot();
        if (background)
            callback(background);
        return () => {
            this.subscribers.delete(callback);
        };
    }
    async *streamHistory() {
        const history = this.stagedHistory ?? (await this.readThreadHistory(this.client));
        this.stagedHistory = null;
        for (const event of history.providerSubagentEvents) {
            yield event;
        }
        for (const entry of history.timeline) {
            yield {
                type: "timeline",
                provider: CODEX_PROVIDER,
                item: entry.item,
                timestamp: entry.timestamp,
            };
        }
    }
    async getRuntimeInfo() {
        if (this.cachedRuntimeInfo)
            return { ...this.cachedRuntimeInfo };
        if (this.connectionState === "disconnected") {
            await this.connect();
        }
        if (!this.currentThreadId) {
            await this.ensureThread();
        }
        const info = {
            provider: CODEX_PROVIDER,
            sessionId: this.savedCurrentThreadId(),
            model: this.config.model ?? null,
            thinkingOptionId: normalizeCodexThinkingOptionId(this.config.thinkingOptionId) ?? null,
            modeId: this.currentMode ?? null,
            extra: this.resolvedCollaborationMode
                ? { collaborationMode: this.resolvedCollaborationMode.name }
                : undefined,
        };
        this.cachedRuntimeInfo = info;
        return { ...info };
    }
    async getAvailableModes() {
        if (this.autoReviewEnabled) {
            return CODEX_MODES;
        }
        return CODEX_MODES.filter((mode) => mode.id !== "auto-review");
    }
    async getCurrentMode() {
        return this.currentMode ?? null;
    }
    async setMode(modeId) {
        validateCodexMode(modeId);
        this.currentMode = modeId;
        this.hasWorkflowModeOverride = true;
        this.config.modeId = modeId;
        this.cachedRuntimeInfo = null;
        if (this.activeForegroundTurnId) {
            return MODE_APPLIES_NEXT_TURN_NOTICE;
        }
    }
    async setModel(modelId) {
        this.config.model = modelId ?? undefined;
        this.refreshResolvedCollaborationMode();
        this.cachedRuntimeInfo = null;
        this.dropUnavailableServiceTier();
    }
    /**
     * A speed the new model does not offer turns into Normal and is reported, so choosing a model
     * that does offer it again does not bring the paid tier back. It is reported as Normal rather
     * than removed so a configured default cannot fill the old speed back in.
     */
    dropUnavailableServiceTier() {
        const requested = this.requestedServiceTier;
        if (requested === null || this.speedModels.length === 0)
            return;
        if (this.isServiceTierAvailable(requested))
            return;
        this.requestedServiceTier = "default";
        this.providerOptions = { ...this.providerOptions, service_tier: "default" };
        this.emitEvent({
            type: "options_changed",
            provider: CODEX_PROVIDER,
            patch: { service_tier: "default" },
        });
    }
    async setThinkingOption(thinkingOptionId) {
        this.config.thinkingOptionId = normalizeCodexThinkingOptionId(thinkingOptionId);
        this.refreshResolvedCollaborationMode();
        this.cachedRuntimeInfo = null;
        if (this.activeForegroundTurnId) {
            return THINKING_APPLIES_NEXT_TURN_NOTICE;
        }
    }
    /** Applies only the options the patch sets; the rest of the running options stay as they are. */
    async applyOptions(options) {
        // The approval policy and sandbox are sent when the thread starts, not with each turn.
        assertChangesAreLive({
            provider: "codex",
            patch: options,
            current: this.providerOptions,
            live: ["service_tier", "plan_mode"],
        });
        const valid = validateProviderOptions("codex", CodexProviderOptionsSchema, applyProviderOptionsPatch({ options: this.providerOptions, patch: options })) ?? {};
        const requested = "service_tier" in options ? readCodexServiceTier(options) : null;
        let accepted = this.requestedServiceTier;
        if ("service_tier" in options) {
            if (requested !== accepted && !this.isServiceTierAvailable(requested)) {
                throw new Error(`Codex speed '${String(requested)}' is not available for model '${this.config.model ?? "default"}'`);
            }
            // The session keeps the catalog's id for the tier, so a later model switch sees what runs.
            accepted = requested && (this.resolveServiceTier(requested) ?? requested);
        }
        this.providerOptions = { ...valid, ...(accepted ? { service_tier: accepted } : {}) };
        this.requestedServiceTier = accepted || null;
        if ("plan_mode" in options)
            this.setPlanMode(options.plan_mode === true);
        this.cachedRuntimeInfo = null;
        // The catalog may name the tier differently from the request (`fast` is the Fast tier's id).
        const tier = this.serviceTier;
        return tier !== null && requested !== null && tier !== requested ? { service_tier: tier } : {};
    }
    isServiceTierAvailable(tier) {
        if (tier === null || tier === "default")
            return true;
        const tiers = this.currentServiceTiers();
        const namesFast = tier === "fast" && tiers.some((entry) => entry.name.toLowerCase() === "fast");
        return namesFast || tiers.some((entry) => entry.id === tier);
    }
    setPlanMode(enabled) {
        if (this.planModeEnabled === enabled)
            return;
        this.planModeEnabled = enabled;
        this.refreshResolvedCollaborationMode();
        this.cachedRuntimeInfo = null;
    }
    getPendingPermissions() {
        return [...this.pendingPermissions.values(), ...this.asyncQuestions.pending()];
    }
    async respondToPermission(requestId, response) {
        if (this.asyncQuestions.hasPending(requestId)) {
            return this.respondToAsyncQuestion(requestId, response);
        }
        const pending = this.pendingPermissionHandlers.get(requestId);
        if (!pending) {
            throw new Error(`No pending Codex app-server permission request with id '${requestId}'`);
        }
        const pendingRequest = this.pendingPermissions.get(requestId) ?? null;
        if (pending.kind === "plan") {
            return this.handlePlanPermissionResponse({ requestId, response, pending, pendingRequest });
        }
        this.pendingPermissionHandlers.delete(requestId);
        this.pendingPermissions.delete(requestId);
        this.resolvedPermissionRequests.add(requestId);
        if (response.behavior === "deny" && pendingRequest?.kind === "tool") {
            this.emitDeniedToolCallTimelineEvent({ requestId, response, pendingRequest });
        }
        this.emitEvent({
            type: "permission_resolved",
            provider: CODEX_PROVIDER,
            requestId,
            resolution: response,
        });
        if (pending.kind === "command") {
            pending.resolve({ decision: resolvePermissionDecision(response) });
            return;
        }
        if (pending.kind === "file") {
            pending.resolve({ decision: resolvePermissionDecision(response) });
            return;
        }
        if (pending.kind === "mcp_elicitation") {
            pending.resolve({
                action: resolvePermissionDecision(response),
                content: response.behavior === "allow" ? {} : null,
                _meta: null,
            });
            return;
        }
        const questions = pending.questions ?? [];
        const itemId = typeof pendingRequest?.metadata?.itemId === "string"
            ? pendingRequest.metadata.itemId
            : requestId;
        if (response.behavior === "allow") {
            const mappedAnswers = mapCodexQuestionResponseByHeader({
                questions,
                response,
            });
            const answers = mappedAnswers ??
                Object.fromEntries(questions
                    .map((question) => {
                    const fallback = question.options[0]?.label?.trim();
                    return fallback ? [question.id, { answers: [fallback] }] : null;
                })
                    .filter((entry) => entry !== null));
            this.emitEvent({
                type: "timeline",
                provider: CODEX_PROVIDER,
                item: mapCodexQuestionRequestToToolCall({
                    callId: itemId,
                    questions,
                    status: "completed",
                    answers: Object.fromEntries(Object.entries(answers).map(([id, value]) => [id, value.answers])),
                }),
            });
            pending.resolve({ answers });
            return;
        }
        this.emitEvent({
            type: "timeline",
            provider: CODEX_PROVIDER,
            item: mapCodexQuestionRequestToToolCall({
                callId: itemId,
                questions,
                status: response.interrupt ? "canceled" : "failed",
                error: { message: response.message ?? "Question dismissed" },
            }),
        });
        pending.resolve({ answers: {} });
    }
    async respondToAsyncQuestion(requestId, response) {
        const prepared = this.asyncQuestions.prepareResponse(requestId, response);
        let followUpPrompt = prepared.prompt;
        const expectedTurnId = this.activeForegroundTurnId;
        if (prepared.prompt && expectedTurnId) {
            const result = await this.steerActiveTurn(prepared.prompt, {
                expectedTurnId,
                clientMessageId: randomUUID(),
            });
            if (result.status !== "accepted") {
                throw new Error("The active Codex turn changed. Retry sending your answer.");
            }
            followUpPrompt = undefined;
        }
        this.emitEvent({ type: "timeline", provider: CODEX_PROVIDER, item: prepared.complete() });
        this.emitEvent({
            type: "permission_resolved",
            provider: CODEX_PROVIDER,
            requestId,
            resolution: response,
        });
        return followUpPrompt ? { followUpPrompt } : undefined;
    }
    handlePlanPermissionResponse(params) {
        const { requestId, response, pending, pendingRequest } = params;
        let followUpPrompt;
        if (response.behavior === "allow") {
            followUpPrompt = this.preparePlanImplementation({
                planText: pending.plan?.text ?? pendingRequest?.metadata?.planText,
            });
        }
        this.resolvePlanPermission(requestId, response);
        if (followUpPrompt) {
            return { followUpPrompt };
        }
    }
    dismissPendingPlanApprovals(message) {
        const requestIds = Array.from(this.pendingPermissionHandlers)
            .filter(([, pending]) => pending.kind === "plan")
            .map(([requestId]) => requestId);
        for (const requestId of requestIds) {
            this.resolvePlanPermission(requestId, { behavior: "deny", message });
        }
    }
    async clearPendingPermissionsForSteer() {
        const requestIds = Array.from(this.pendingPermissionHandlers.keys());
        for (const requestId of requestIds) {
            if (!this.pendingPermissionHandlers.has(requestId))
                continue;
            await this.respondToPermission(requestId, {
                behavior: "deny",
                message: "The user answered with a message instead of approving. Their message follows.",
            });
        }
    }
    resolvePlanPermission(requestId, resolution) {
        this.recordPlanOutcome(requestId, resolution.behavior === "allow" ? "approved" : "rejected");
        this.pendingPermissionHandlers.delete(requestId);
        this.pendingPermissions.delete(requestId);
        this.resolvedPermissionRequests.add(requestId);
        this.emitEvent({
            type: "permission_resolved",
            provider: CODEX_PROVIDER,
            requestId,
            resolution,
        });
    }
    recordPlanOutcome(requestId, outcome) {
        const plan = this.pendingPermissionHandlers.get(requestId)?.plan;
        if (plan) {
            this.emitEvent({
                type: "timeline",
                provider: CODEX_PROVIDER,
                turnId: plan.turnId,
                item: {
                    type: "tool_call",
                    callId: requestId,
                    name: "plan_approval",
                    status: outcome === "canceled" ? "canceled" : "completed",
                    error: null,
                    detail: { type: "plan", text: plan.text },
                    metadata: outcome === "canceled" ? {} : { approved: outcome === "approved" },
                },
            });
        }
    }
    emitDeniedToolCallTimelineEvent(params) {
        const { requestId, response, pendingRequest } = params;
        let fallbackName;
        if (pendingRequest.name === "CodexBash") {
            fallbackName = "shell";
        }
        else if (pendingRequest.name === "CodexFileChange") {
            fallbackName = "apply_patch";
        }
        else {
            fallbackName = pendingRequest.name;
        }
        this.emitEvent({
            type: "timeline",
            provider: CODEX_PROVIDER,
            item: {
                type: "tool_call",
                callId: requestId,
                name: fallbackName,
                status: "failed",
                error: { message: response.message ?? "Permission denied" },
                detail: pendingRequest.detail ?? {
                    type: "unknown",
                    input: pendingRequest.input ?? null,
                    output: null,
                },
                metadata: {
                    permissionRequestId: requestId,
                    denied: true,
                },
            },
        });
    }
    describePersistence() {
        const threadId = this.savedCurrentThreadId();
        if (!threadId)
            return null;
        const thinkingOptionId = normalizeCodexThinkingOptionId(this.config.thinkingOptionId) ?? null;
        return {
            provider: CODEX_PROVIDER,
            sessionId: threadId,
            nativeHandle: threadId,
            metadata: {
                provider: CODEX_PROVIDER,
                cwd: this.config.cwd,
                title: this.config.title ?? null,
                threadId,
                modeId: this.config.modeId,
                model: this.config.model ?? null,
                thinkingOptionId,
                toolPolicy: this.config.toolPolicy,
                systemPrompt: this.config.systemPrompt,
                mcpServers: this.config.mcpServers,
                asyncQuestions: this.asyncQuestions.serialize(),
            },
        };
    }
    savedCurrentThreadId() {
        return this.currentThreadId !== null && this.currentThreadId === this.savedThreadId
            ? this.currentThreadId
            : null;
    }
    async revertConversation(input) {
        await this.connect();
        if (!this.client) {
            throw new Error("Codex client is not initialized");
        }
        if (this.currentThreadId) {
            await this.ensureThreadLoaded();
        }
        else {
            await this.ensureThread();
        }
        await this.ensureHistoryIndexed();
        await revertCodexConversation({
            client: this.client,
            threadId: this.currentThreadId,
            messageId: input.messageId,
            cwd: this.config.cwd ?? null,
            model: this.config.model ?? null,
            serviceTier: this.serviceTier,
            config: this.buildCodexInnerConfig(),
            userMessageTurns: this.codexUserMessageTurns(),
            threadRollbackAvailable: this.threadRollbackAvailable,
            setThreadId: async (threadId) => {
                this.currentThreadId = threadId;
                // Codex writes a forked thread's rollout when it forks it, even before any turn.
                this.savedThreadId = threadId;
                this.cachedRuntimeInfo = null;
                this.stagedHistory = await this.readThreadHistory(this.client);
                this.reconcileAsyncQuestionsAfterRewind(this.stagedHistory.timeline);
            },
        });
    }
    async interrupt() {
        const pendingStart = this.pendingForegroundStart;
        if (pendingStart) {
            pendingStart.cancelRequested = true;
            await pendingStart.promise;
        }
        if (!this.client || !this.currentThreadId) {
            if (!this.activeForegroundTurnId &&
                !this.currentTurnId &&
                !this.pendingForegroundTurnIdentification) {
                return;
            }
            throw new Error("Cannot interrupt Codex before the active thread is initialized");
        }
        let turnId = this.currentTurnId;
        const foregroundTurnId = this.activeForegroundTurnId;
        const pendingIdentification = this.pendingForegroundTurnIdentification;
        // turn/start is accepted before Codex publishes the native turn id. Keep the
        // interrupt attached to this foreground turn until that ordered notification arrives.
        if (!turnId &&
            foregroundTurnId &&
            pendingIdentification?.foregroundTurnId === foregroundTurnId) {
            turnId = await pendingIdentification.promise;
        }
        if (!turnId && !this.activeForegroundTurnId && !this.currentTurnId) {
            return;
        }
        if (!turnId || (foregroundTurnId && this.activeForegroundTurnId !== foregroundTurnId)) {
            throw new Error("Cannot interrupt Codex before turn/started identifies the active turn");
        }
        const client = this.client;
        const threadId = this.currentThreadId;
        const interruptRequest = client.request("turn/interrupt", {
            threadId,
            turnId,
        }, INTERRUPT_TIMEOUT_MS);
        // Once Codex accepts the interrupt, end the commands this turn started. Registered
        // before the await, so a turn start racing this interrupt waits for it. An already-idle
        // turn was not interrupted, and what it left running stays.
        this.trackInterruptedTurnCleanup(interruptRequest.then(() => this.endInterruptedTurnProcesses(client, threadId, turnId), () => undefined));
        try {
            await interruptRequest;
        }
        catch (error) {
            if (!isCodexAlreadyIdleInterrupt(error)) {
                throw error;
            }
            this.activeForegroundTurnId = null;
            this.activeClientMessageId = null;
            this.currentTurnId = null;
            this.pendingForegroundTurnIdentification?.resolve(null);
            this.pendingForegroundTurnIdentification = null;
        }
    }
    stopBackgroundWork(id) {
        return this.background.stopItem(id);
    }
    async canStopRuntime() {
        // A history session released its app-server once it read the thread, so nothing runs.
        if (this.connectionState === "history-ready")
            return true;
        if (!this.client ||
            this.client.hasPendingRequests() ||
            !this.currentThreadId ||
            this.activeForegroundTurnId ||
            this.currentTurnId ||
            this.pendingForegroundStart ||
            this.pendingPermissionHandlers.size ||
            this.getPendingPermissions().length ||
            this.pendingSubAgentNotificationsByThreadId.size)
            return false;
        // Older app servers reject this request. A failed or malformed probe must retain
        // the process: command completion alone does not mean its terminal has exited.
        const loaded = z
            .object({ data: z.array(z.string()) })
            .parse(await this.client.request("thread/loaded/list", {}, 10000));
        const threadIds = new Set([this.currentThreadId, ...loaded.data]);
        for (const threadId of threadIds) {
            const response = z
                .object({ data: z.array(z.unknown()), nextCursor: z.string().nullable() })
                .parse(await this.client.request("thread/backgroundTerminals/list", { threadId }, 10000));
            if (response.data.length > 0 || response.nextCursor !== null)
                return false;
        }
        return !this.client.hasPendingRequests();
    }
    // Remember which turn started a command, so an interrupt ends that turn's commands and
    // no other turn's. Only the root thread's: a subagent's commands live on its own thread.
    // A command that starts after its turn's interrupt was accepted is ended as soon as it
    // is seen.
    recordTurnTerminalProcess(parsed) {
        const processId = parsed.item.processId;
        if (parsed.source !== "item" ||
            !parsed.turnId ||
            !parsed.threadId ||
            parsed.threadId !== this.currentThreadId ||
            typeof processId !== "string" ||
            !processId ||
            normalizeCodexThreadItemType(typeof parsed.item.type === "string" ? parsed.item.type : undefined) !== "commandExecution") {
            return;
        }
        const record = this.terminalProcessIdsByTurnId.get(parsed.turnId);
        if (parsed.kind === "item_completed") {
            // Only a completed command has exited. A failed one can still be running:
            // Codex reports an interrupted turn's live command as failed.
            if (parsed.item.status === "completed")
                record?.processIds.delete(processId);
            return;
        }
        const current = record ?? this.getTurnTerminalRecord(parsed.turnId);
        current.processIds.add(processId);
        if (current.interruptAccepted && this.client) {
            this.trackInterruptedTurnCleanup(this.endInterruptedTurnProcesses(this.client, parsed.threadId, parsed.turnId));
        }
    }
    // An accepted interrupt needs a record even before any of its turn's commands is seen:
    // an `item/started` can still be queued behind an earlier notification's conversion.
    getTurnTerminalRecord(turnId) {
        const existing = this.terminalProcessIdsByTurnId.get(turnId);
        if (existing)
            return existing;
        const record = {
            processIds: new Set(),
            terminatedProcessIds: new Set(),
            interruptAccepted: false,
        };
        this.terminalProcessIdsByTurnId.set(turnId, record);
        for (const oldestTurnId of this.terminalProcessIdsByTurnId.keys()) {
            if (this.terminalProcessIdsByTurnId.size <= INTERRUPTED_TURN_TRACKED_TURNS)
                break;
            this.terminalProcessIdsByTurnId.delete(oldestTurnId);
        }
        return record;
    }
    // A later sweep joins the pending one, so a turn start waits for both.
    trackInterruptedTurnCleanup(cleanup) {
        const tracked = Promise.all([this.interruptedTurnCleanup, cleanup])
            .then(() => undefined)
            .finally(() => {
            if (this.interruptedTurnCleanup === tracked)
                this.interruptedTurnCleanup = null;
        });
        this.interruptedTurnCleanup = tracked;
    }
    // Terminate each process the interrupted turn started, by its own id, once each.
    // `thread/backgroundTerminals/clean` is never used: it ends every terminal on the thread,
    // an earlier turn's included. A process already gone answers `terminated: false`. Never
    // rejects; failures are logged.
    async endInterruptedTurnProcesses(client, threadId, turnId) {
        if (!threadId)
            return;
        const record = this.getTurnTerminalRecord(turnId);
        record.interruptAccepted = true;
        const processIds = [...record.processIds].filter((processId) => !record.terminatedProcessIds.has(processId));
        for (const processId of processIds)
            record.terminatedProcessIds.add(processId);
        const logContext = { agentId: this.agentId, provider: CODEX_PROVIDER, threadId, turnId };
        await Promise.all(processIds.map(async (processId) => {
            try {
                const response = await client.request("thread/backgroundTerminals/terminate", { threadId, processId }, INTERRUPTED_TURN_TERMINATE_TIMEOUT_MS);
                this.logger.info({ ...logContext, processId, terminated: toObjectRecord(response)?.terminated ?? null }, "provider.codex.interrupt.terminal_terminated");
            }
            catch (error) {
                this.logger.warn({ ...logContext, processId, err: error }, "provider.codex.interrupt.terminal_terminate_failed");
            }
        }));
    }
    // A new turn starts only after every pending sweep of interrupted turns' commands has
    // ended, or after the bound, whichever is first.
    async waitForInterruptedTurnCleanup() {
        if (!this.interruptedTurnCleanup)
            return;
        let timer;
        const timedOut = await Promise.race([
            (async () => {
                while (this.interruptedTurnCleanup)
                    await this.interruptedTurnCleanup;
                return false;
            })(),
            new Promise((resolve) => {
                timer = setTimeout(() => resolve(true), INTERRUPTED_TURN_CLEANUP_WAIT_MS);
            }),
        ]);
        clearTimeout(timer);
        if (timedOut) {
            this.logger.warn({
                agentId: this.agentId,
                provider: CODEX_PROVIDER,
                waitMs: INTERRUPTED_TURN_CLEANUP_WAIT_MS,
            }, "provider.codex.interrupt.terminal_cleanup_timed_out");
        }
    }
    async close() {
        this.closed = true;
        this.startupAbort.abort(this.createClosedError());
        this.background.stop();
        this.clearPendingPermissions();
        this.pendingToolCallDetails.clear();
        this.pendingSubAgentNotificationsByThreadId.clear();
        this.settleStreamRetry({ status: "canceled" });
        this.subscribers.clear();
        this.startupEvents = null;
        this.activeForegroundTurnId = null;
        this.activeClientMessageId = null;
        this.pendingForegroundTurnIdentification?.resolve(null);
        this.pendingForegroundTurnIdentification = null;
        await this.disposeClient();
        this.currentThreadId = null;
    }
    clearPendingPermissions(options) {
        for (const [requestId, pending] of this.pendingPermissionHandlers) {
            if (options?.preservePlanApprovals && pending.kind === "plan") {
                continue;
            }
            if (pending.kind === "plan") {
                this.recordPlanOutcome(requestId, "canceled");
            }
            pending.resolve({ decision: "cancel" });
            this.pendingPermissionHandlers.delete(requestId);
            this.pendingPermissions.delete(requestId);
        }
        this.mcpElicitationPermissionIds.clear();
        this.resolvedPermissionRequests.clear();
    }
    async disposeClient() {
        this.background.stop();
        const client = this.client;
        this.connectionState = "disconnected";
        this.currentTurnId = null;
        if (client) {
            await client.dispose();
        }
        this.client = null;
    }
    async listCommands() {
        const prompts = await listCodexCustomPrompts(this.codexHome);
        if (this.connectionState === "disconnected") {
            await this.connect();
        }
        else {
            await this.loadSkills();
        }
        const appServerSkills = (this.cachedSkills ?? []).map((skill) => ({
            name: skill.name,
            description: skill.description,
            argumentHint: "",
            kind: "skill",
        }));
        const fallbackSkills = this.cachedSkills === null
            ? await listCodexSkills(this.config.cwd, this.codexHome, this.deps.workspaceGitService)
            : [];
        const builtin = [
            {
                name: "compact",
                description: "Summarize conversation to prevent hitting the context limit",
                argumentHint: "",
                kind: "command",
            },
        ];
        if (this.goalsEnabled) {
            builtin.push({
                name: "goal",
                description: "Set, pause, resume, or clear the agent's goal",
                argumentHint: "[<objective>|pause|resume|clear]",
                kind: "command",
            });
        }
        return [...builtin, ...appServerSkills, ...fallbackSkills, ...prompts].sort((a, b) => a.name.localeCompare(b.name));
    }
    tryHandleOutOfBand(prompt) {
        if (typeof prompt !== "string")
            return null;
        const parsed = this.parseSlashCommandInput(prompt);
        if (!parsed)
            return null;
        if (parsed.commandName === "compact") {
            return {
                run: async ({ emit }) => {
                    const error = await this.executeCompactCommand();
                    if (error) {
                        emit({
                            type: "timeline",
                            provider: CODEX_PROVIDER,
                            item: { type: "assistant_message", text: formatOutOfBandStatusMessage(error) },
                        });
                    }
                },
            };
        }
        if (!this.goalsEnabled || parsed.commandName !== "goal")
            return null;
        const subcommand = parseGoalSubcommand(parsed.args);
        return {
            run: async ({ emit }) => {
                const text = formatOutOfBandStatusMessage(await this.executeGoalSubcommand(subcommand));
                emit({
                    type: "timeline",
                    provider: CODEX_PROVIDER,
                    item: { type: "assistant_message", text },
                });
            },
        };
    }
    async executeCompactCommand() {
        try {
            await this.connect();
            if (this.currentThreadId) {
                await this.ensureThreadLoaded();
            }
            else {
                await this.ensureThread();
            }
            if (!this.client || !this.currentThreadId) {
                throw new Error("Codex thread is not available");
            }
            this.pendingManualCompactionStarts += 1;
            try {
                await this.client.request("thread/compact/start", {
                    threadId: this.currentThreadId,
                });
            }
            catch (error) {
                this.pendingManualCompactionStarts = Math.max(0, this.pendingManualCompactionStarts - 1);
                throw error;
            }
            return null;
        }
        catch (error) {
            const message = error instanceof Error ? error.message : "unknown error";
            return `Failed to compact context: ${message}`;
        }
    }
    async executeGoalSubcommand(subcommand) {
        if (subcommand.kind === "usage") {
            return "Usage: /goal <objective>|pause|resume|clear";
        }
        try {
            await this.connect();
            if (this.currentThreadId) {
                await this.ensureThreadLoaded();
                // A goal runs turns of its own.
                await this.ensureHistoryIndexed();
            }
            else {
                await this.ensureThread();
            }
            if (!this.client || !this.currentThreadId) {
                throw new Error("Codex thread is not available");
            }
            switch (subcommand.kind) {
                case "set": {
                    await this.client.request("thread/goal/set", {
                        threadId: this.currentThreadId,
                        objective: subcommand.objective,
                        status: "active",
                    });
                    return `Goal set: ${subcommand.objective}`;
                }
                case "pause": {
                    await this.client.request("thread/goal/set", {
                        threadId: this.currentThreadId,
                        status: "paused",
                    });
                    return "Goal paused.";
                }
                case "resume": {
                    await this.client.request("thread/goal/set", {
                        threadId: this.currentThreadId,
                        status: "active",
                    });
                    return "Goal resumed.";
                }
                case "clear": {
                    await this.client.request("thread/goal/clear", {
                        threadId: this.currentThreadId,
                    });
                    return "Goal cleared.";
                }
            }
        }
        catch (error) {
            const message = error instanceof Error ? error.message : "unknown error";
            return `Failed to update goal: ${message}`;
        }
    }
    async resolveModelAndThinking() {
        if (!this.client) {
            throw new Error("Codex client is not initialized");
        }
        let configuredDefaults = {};
        let model = this.config.model;
        let thinkingOptionId = normalizeCodexThinkingOptionId(this.config.thinkingOptionId);
        if (!model || !thinkingOptionId) {
            configuredDefaults = await readCodexConfiguredDefaults(this.client, this.logger);
        }
        if (!model) {
            model = configuredDefaults.model;
        }
        if (!thinkingOptionId) {
            thinkingOptionId = configuredDefaults.thinkingOptionId;
        }
        if (!model || !thinkingOptionId) {
            const modelResponse = toObjectRecord(await this.client.request("model/list", {}));
            const modelData = Array.isArray(modelResponse?.data) ? modelResponse.data : [];
            const models = modelData
                .map((m) => {
                const record = toObjectRecord(m);
                return {
                    id: typeof record?.id === "string" ? record.id : "",
                    isDefault: !!record?.isDefault,
                    defaultReasoningEffort: typeof record?.defaultReasoningEffort === "string"
                        ? record.defaultReasoningEffort
                        : undefined,
                };
            })
                .filter((m) => m.id);
            const defaultModel = models.find((m) => m.isDefault) ?? models[0];
            if (!defaultModel) {
                throw new Error("No models available from Codex app-server");
            }
            const selectedModel = (model ? models.find((candidate) => candidate.id === model) : undefined) ?? defaultModel;
            if (!model) {
                model = selectedModel.id;
            }
            if (!thinkingOptionId) {
                thinkingOptionId = normalizeCodexThinkingOptionId(selectedModel.defaultReasoningEffort);
            }
        }
        if (!model) {
            throw new Error("Unable to resolve Codex model");
        }
        return { model, thinkingOptionId };
    }
    // A session without a model or effort runs on Codex's configured defaults, whether its
    // thread is new or resumed after a restart.
    async applyDefaultModelAndThinking() {
        const { model, thinkingOptionId } = await this.resolveModelAndThinking();
        this.config.model = model;
        this.config.thinkingOptionId = thinkingOptionId;
        this.refreshResolvedCollaborationMode();
        return model;
    }
    async ensureThread() {
        if (!this.client)
            return;
        if (this.currentThreadId)
            return;
        const model = await this.applyDefaultModelAndThinking();
        const { params, approvalPolicy, sandbox } = this.buildThreadStartRequest(model);
        const rawResponse = await this.client.request("thread/start", params);
        this.rememberResolvedSandboxPolicy(rawResponse);
        const response = toObjectRecord(rawResponse);
        const threadRecord = toObjectRecord(response?.thread);
        const threadId = typeof threadRecord?.id === "string" ? threadRecord.id : undefined;
        if (!threadId) {
            throw new Error("Codex app-server did not return thread id");
        }
        const responseApprovalsReviewer = typeof response?.approvalsReviewer === "string" ? response.approvalsReviewer : undefined;
        if (shouldPromoteThreadResponseToAutoReview({
            approvalsReviewer: responseApprovalsReviewer,
            approvalPolicy: approvalPolicy ?? String(this.providerOptions.approval_policy ?? ""),
            sandbox: sandbox ?? this.providerOptions.sandbox_mode ?? "",
        })) {
            this.currentMode = "auto-review";
            this.cachedRuntimeInfo = null;
        }
        this.currentThreadId = threadId;
    }
    buildThreadStartRequest(model) {
        const preset = MODE_PRESETS[this.currentMode] ?? MODE_PRESETS[DEFAULT_CODEX_MODE_ID];
        const approvalPolicy = this.hasWorkflowModeOverride ? preset.approvalPolicy : undefined;
        const sandbox = this.hasWorkflowModeOverride ? preset.sandbox : undefined;
        const innerConfig = this.buildCodexInnerConfig();
        const developerInstructions = composeSystemPromptParts(this.config.systemPrompt, this.config.daemonAppendSystemPrompt);
        const params = {
            model,
            cwd: this.config.cwd ?? null,
            ...(approvalPolicy && this.providerOptions.approval_policy === undefined
                ? { approvalPolicy }
                : {}),
            ...(sandbox && this.providerOptions.sandbox_mode === undefined ? { sandbox } : {}),
            ...(developerInstructions ? { developerInstructions } : {}),
            ...(innerConfig ? { config: innerConfig } : {}),
            ...(this.ephemeral ? { ephemeral: true } : {}),
        };
        if (this.hasWorkflowModeOverride) {
            params.approvalsReviewer = preset.approvalsReviewer;
        }
        return { params, approvalPolicy, sandbox };
    }
    buildCodexInnerConfig() {
        const innerConfig = {};
        const { service_tier: _tier, plan_mode: _plan, ...codexConfig } = this.providerOptions;
        Object.assign(innerConfig, codexConfig);
        if (this.deps.customCodexConfig) {
            Object.assign(innerConfig, this.deps.customCodexConfig);
        }
        if (this.config.mcpServers) {
            const mcpServers = {};
            for (const [name, serverConfig] of Object.entries(this.config.mcpServers)) {
                mcpServers[name] = toCodexMcpConfig(serverConfig);
            }
            innerConfig.mcp_servers = mcpServers;
        }
        const configured = applyCodexToolPolicy(innerConfig, this.config.toolPolicy);
        return Object.keys(configured).length > 0 ? configured : null;
    }
    async buildUserInput(prompt) {
        if (typeof prompt === "string") {
            return [toCodexTextInput(prompt)];
        }
        return await codexAppServerTurnInputFromPrompt(prompt, this.logger);
    }
    emitEvent(event) {
        if (this.historySubagentEvents && event.type === "provider_subagent") {
            this.historySubagentEvents.push(event);
            return;
        }
        this.notifySubscribers(event);
    }
    notifySubscribers(event) {
        const turnId = getAgentStreamEventTurnId(event) ?? this.activeForegroundTurnId;
        const tagged = turnId ? { ...event, turnId } : event;
        if (this.startupEvents) {
            this.startupEvents.push(tagged);
            return;
        }
        this.logger.trace({
            agentId: this.agentId,
            provider: CODEX_PROVIDER,
            sessionId: this.currentThreadId,
            turnId: getAgentStreamEventTurnId(tagged),
            event: tagged,
        }, "provider.codex.event_emit");
        for (const callback of this.subscribers) {
            try {
                callback(tagged);
            }
            catch (error) {
                this.logger.warn({ err: error }, "Subscriber callback threw");
            }
        }
    }
    createTurnId() {
        return `codex-turn-${this.turnNamespace}-${this.nextTurnOrdinal++}`;
    }
    handleNotification(method, params) {
        // An image must exist before its event, and completion must follow that event.
        // Transport callbacks cannot await: the session owns their ordered drain.
        const next = this.pendingNotifications.then(() => {
            return this.closed ? undefined : this.processNotification(method, params);
        });
        this.pendingNotifications = next.catch((error) => {
            this.handleUnexpectedTermination(error instanceof Error ? error : new Error(String(error)));
        });
        return this.pendingNotifications;
    }
    async processNotification(method, params) {
        const notificationParams = toObjectRecord(params);
        if (method === "serverRequest/resolved" && typeof notificationParams?.requestId === "number") {
            const requestId = this.mcpElicitationPermissionIds.get(notificationParams.requestId);
            if (requestId) {
                const pending = this.pendingPermissionHandlers.get(requestId);
                this.mcpElicitationPermissionIds.delete(notificationParams.requestId);
                if (!pending) {
                    return;
                }
                this.pendingPermissions.delete(requestId);
                this.pendingPermissionHandlers.delete(requestId);
                pending.resolve({ action: "cancel", content: null, _meta: null });
                this.emitEvent({
                    type: "permission_resolved",
                    provider: CODEX_PROVIDER,
                    requestId,
                    resolution: { behavior: "deny", interrupt: true },
                });
            }
            return;
        }
        const parsed = CodexNotificationSchema.parse({ method, params });
        this.traceParsedNotification(method, params, parsed);
        const route = this.resolveCodexThreadRoute(getCodexNotificationThreadId(parsed));
        if (route.kind === "pending_sub_agent") {
            this.bufferPendingSubAgentNotification(route.threadId, parsed);
            return;
        }
        if (route.kind === "sub_agent") {
            await this.dispatchSubAgentNotification(parsed, route.callId);
            return;
        }
        // Root model output means the stream is back. Steering messages and asynchronous
        // child activity arrive independently of the root stream, so neither proves recovery.
        const startedItemType = parsed.kind === "item_started" ? normalizeCodexThreadItemType(parsed.item.type) : null;
        if (parsed.kind === "agent_message_delta" ||
            parsed.kind === "reasoning_delta" ||
            (parsed.kind === "item_started" &&
                startedItemType !== "userMessage" &&
                startedItemType !== "subAgentActivity")) {
            this.settleStreamRetry({ status: "completed" });
        }
        this.observeBackgroundWork(parsed);
        await this.dispatchParsedNotification(parsed);
    }
    observeBackgroundWork(parsed) {
        const backgroundThread = getCodexNotificationThreadId(parsed) ?? this.currentThreadId;
        if (backgroundThread && parsed.kind === "turn_started")
            this.background.turnStarted(backgroundThread);
        else if (backgroundThread && parsed.kind === "turn_completed")
            this.background.turnSettled(backgroundThread);
        else if (backgroundThread && parsed.kind === "exec_command_started")
            this.background.commandStarted({ thread: backgroundThread, itemId: parsed.callId });
        else if (backgroundThread &&
            parsed.kind === "item_started" &&
            normalizeCodexThreadItemType(parsed.item.type) === "commandExecution")
            this.background.commandStarted({
                thread: backgroundThread,
                itemId: parsed.item.id ?? null,
                processId: typeof parsed.item.processId === "string" ? parsed.item.processId : undefined,
            });
        else if (parsed.kind === "exec_command_completed" ||
            ((parsed.kind === "item_started" || parsed.kind === "item_completed") &&
                normalizeCodexThreadItemType(parsed.item.type) === "commandExecution"))
            this.background.changed(this.currentThreadId);
    }
    async dispatchSubAgentNotification(parsed, callId) {
        switch (parsed.kind) {
            case "thread_started":
                this.emitSubAgentActivityUpdate(callId, "running", { reopen: true });
                return;
            case "turn_started":
            case "turn_completed":
            case "agent_message_delta":
            case "reasoning_delta":
            case "item_started":
            case "item_completed":
                await this.dispatchParsedNotification(parsed);
                return;
            case "exec_command_output_delta":
            case "file_change_output_delta":
                this.handleCodexDeltaNotification(parsed, callId);
                return;
            case "exec_command_started":
                this.handleExecCommandStartedNotification(parsed, callId);
                return;
            case "exec_command_completed":
                this.handleExecCommandCompletedNotification(parsed, callId);
                return;
            case "patch_apply_started":
                this.handlePatchApplyStartedNotification(parsed, callId);
                return;
            case "patch_apply_completed":
                this.handlePatchApplyCompletedNotification(parsed, callId);
                return;
            case "context_compacted":
                this.settleChildCompaction(parsed.threadId);
                return;
            default:
                // Aggregate child telemetry is redundant and must not leak into the
                // root timeline. Concrete legacy tools are projected above for Codex
                // versions that do not also emit canonical item lifecycle events.
                return;
        }
    }
    async dispatchParsedNotification(parsed) {
        if (isCodexDeltaNotification(parsed)) {
            this.handleCodexDeltaNotification(parsed);
            return;
        }
        if (this.handleThreadStateNotification(parsed)) {
            return;
        }
        switch (parsed.kind) {
            case "thread_started":
                this.handleThreadStartedNotification(parsed);
                return;
            case "turn_started":
                this.handleTurnStartedNotification(parsed);
                return;
            case "turn_completed":
                this.handleTurnCompletedNotification(parsed);
                return;
            case "turn_error":
                this.handleTurnErrorNotification(parsed);
                return;
            case "warning":
                this.handleWarningNotification(parsed);
                return;
            case "plan_updated":
                this.handlePlanUpdatedNotification(parsed);
                return;
            case "diff_updated":
                // NOTE: Codex app-server emits frequent `turn/diff/updated` notifications
                // containing a full accumulated unified diff for the *entire turn*.
                // This is not a concrete file-change tool call; it is progress telemetry.
                return;
            case "token_usage_updated":
                this.handleTokenUsageUpdatedNotification(parsed);
                return;
            case "exec_command_started":
                this.handleExecCommandStartedNotification(parsed);
                return;
            case "exec_command_completed":
                this.handleExecCommandCompletedNotification(parsed);
                return;
            case "terminal_interaction":
                this.handleTerminalInteractionNotification(parsed);
                return;
            case "patch_apply_started":
                this.handlePatchApplyStartedNotification(parsed);
                return;
            case "patch_apply_completed":
                this.handlePatchApplyCompletedNotification(parsed);
                return;
            case "item_completed":
                await this.handleItemCompletedNotification(parsed);
                return;
            case "item_started":
                await this.handleItemStartedNotification(parsed);
                return;
            case "invalid_payload":
                this.warnInvalidNotificationPayload(parsed.method, parsed.params);
                return;
            case "unknown_method":
                this.warnUnknownNotificationMethod(parsed.method, parsed.params);
                return;
            default:
                return;
        }
    }
    resolveCodexThreadRoute(threadId) {
        if (!threadId || !this.currentThreadId || threadId === this.currentThreadId) {
            return { kind: "root" };
        }
        const callId = this.subAgentCallIdByChildThreadId.get(threadId);
        return callId ? { kind: "sub_agent", callId } : { kind: "pending_sub_agent", threadId };
    }
    bufferPendingSubAgentNotification(threadId, parsed) {
        let pending = this.pendingSubAgentNotificationsByThreadId.get(threadId);
        if (!pending) {
            if (this.pendingSubAgentNotificationsByThreadId.size >= MAX_PENDING_SUB_AGENT_THREADS) {
                const oldestThreadId = this.pendingSubAgentNotificationsByThreadId.keys().next().value;
                if (typeof oldestThreadId === "string") {
                    this.pendingSubAgentNotificationsByThreadId.delete(oldestThreadId);
                }
            }
            pending = [];
            this.pendingSubAgentNotificationsByThreadId.set(threadId, pending);
        }
        if (pending.length >= MAX_PENDING_SUB_AGENT_NOTIFICATIONS_PER_THREAD) {
            pending.shift();
        }
        pending.push(parsed);
    }
    async replayPendingSubAgentNotifications(threadIds) {
        for (const threadId of threadIds) {
            const pending = this.pendingSubAgentNotificationsByThreadId.get(threadId);
            if (!pending) {
                continue;
            }
            this.pendingSubAgentNotificationsByThreadId.delete(threadId);
            const callId = this.subAgentCallIdByChildThreadId.get(threadId);
            if (!callId) {
                continue;
            }
            for (const parsed of pending) {
                await this.dispatchSubAgentNotification(parsed, callId);
            }
        }
    }
    handleThreadStateNotification(parsed) {
        switch (parsed.kind) {
            case "context_compacted":
                this.handleContextCompactedNotification(parsed);
                return true;
            case "thread_rolled_back":
                this.handleThreadRolledBackNotification(parsed);
                return true;
            default:
                return false;
        }
    }
    traceParsedNotification(method, params, parsed) {
        this.logger.trace({
            agentId: this.agentId,
            provider: CODEX_PROVIDER,
            sessionId: this.currentThreadId,
            turnId: this.activeForegroundTurnId ?? undefined,
            method,
            params,
            parsed,
        }, "provider.codex.parsed_event");
    }
    getSubAgentCallIdForThread(threadId) {
        if (!threadId || threadId === this.currentThreadId) {
            return null;
        }
        return this.subAgentCallIdByChildThreadId.get(threadId) ?? null;
    }
    registerSubAgentToolCall(params) {
        const { timelineItem, rawItem, parentCallId, parentSubagentId } = params;
        if (timelineItem.detail.type !== "sub_agent") {
            return [];
        }
        const existing = this.subAgentCallsByCallId.get(timelineItem.callId);
        const state = existing ??
            {
                callId: timelineItem.callId,
                toolCall: timelineItem,
                parentCallId,
                parentSubagentId,
                activityItemIds: new Set(),
                pendingCommandOutputDeltas: new Map(),
                pendingFileChangeOutputDeltas: new Map(),
                childThreadIds: new Set(),
            };
        state.toolCall = timelineItem;
        state.parentCallId ?? (state.parentCallId = parentCallId);
        state.parentSubagentId ?? (state.parentSubagentId = parentSubagentId);
        const activity = readCodexSubAgentActivity(rawItem);
        if (activity?.id) {
            state.activityItemIds.add(activity.id);
        }
        this.subAgentCallsByCallId.set(timelineItem.callId, state);
        const receiverThreadIds = Array.isArray(rawItem.receiverThreadIds)
            ? rawItem.receiverThreadIds.filter((value) => typeof value === "string")
            : [];
        const agentThreadId = typeof rawItem.agentThreadId === "string" && rawItem.agentThreadId.length > 0
            ? rawItem.agentThreadId
            : null;
        const childThreadIds = Array.from(new Set(agentThreadId ? [...receiverThreadIds, agentThreadId] : receiverThreadIds)).filter((threadId) => threadId !== this.currentThreadId);
        for (const receiverThreadId of childThreadIds) {
            const owningCallId = this.subAgentCallIdByChildThreadId.get(receiverThreadId);
            if (owningCallId && owningCallId !== timelineItem.callId) {
                continue;
            }
            this.subAgentCallIdByChildThreadId.set(receiverThreadId, timelineItem.callId);
            state.childThreadIds.add(receiverThreadId);
            this.emitProviderSubagentUpsert(receiverThreadId, state, timelineItem.status);
        }
        return childThreadIds;
    }
    handleRegisteredSubAgentActivity(rawItem) {
        const activity = readCodexSubAgentActivity(rawItem);
        if (!activity) {
            return false;
        }
        const callId = this.subAgentCallIdByChildThreadId.get(activity.agentThreadId);
        if (!callId) {
            return false;
        }
        const state = this.subAgentCallsByCallId.get(callId);
        if (!state) {
            return false;
        }
        if (activity.id && state.activityItemIds.has(activity.id)) {
            return true;
        }
        if (activity.id) {
            state.activityItemIds.add(activity.id);
        }
        const activityToolCall = mapCodexToolCallFromThreadItem(rawItem, {
            cwd: this.config.cwd ?? null,
        });
        const subAgentType = activityToolCall?.detail.type === "sub_agent"
            ? activityToolCall.detail.subAgentType
            : undefined;
        let nextStatus = "running";
        if (activity.kind === "interrupted") {
            nextStatus = "canceled";
        }
        else if (isTerminalSubAgentStatus(state.toolCall.status)) {
            nextStatus = undefined;
        }
        this.emitSubAgentActivityUpdate(callId, nextStatus, { subAgentType });
        return true;
    }
    handleCompletedContextCompactionItem(item) {
        if (!this.isContextCompactionItem(item)) {
            return false;
        }
        const consumedPendingCompaction = this.consumePendingRootCompaction(item.id);
        const hasDifferentPendingCompaction = this.pendingRootCompactionItemIds.size > 0 || this.pendingAnonymousRootCompactions > 0;
        const isLateCompletionForOlderItem = item.id !== undefined &&
            consumedPendingCompaction === undefined &&
            hasDifferentPendingCompaction;
        if (isLateCompletionForOlderItem) {
            return true;
        }
        if (this.unpairedCompactionNotificationCompletions > 0) {
            this.unpairedCompactionNotificationCompletions -= 1;
            return true;
        }
        this.emitEvent({
            type: "timeline",
            provider: CODEX_PROVIDER,
            item: this.createContextCompactionTimelineItem("completed", item.id),
        });
        this.unpairedCompactionItemCompletions += 1;
        return true;
    }
    handleCompletedSpecialItem(parsed, childSubAgentCallId) {
        if (childSubAgentCallId &&
            parsed.threadId &&
            this.handleSubAgentContextCompactionItem(childSubAgentCallId, parsed.threadId, parsed.item, "completed")) {
            return true;
        }
        return (this.handleCompletedContextCompactionItem(parsed.item) ||
            this.handleRegisteredSubAgentActivity(parsed.item));
    }
    emitCodexToolTimelineItem(timelineItem, subAgentCallId, childThreadId) {
        if (!subAgentCallId) {
            this.emitEvent({ type: "timeline", provider: CODEX_PROVIDER, item: timelineItem });
            return;
        }
        const state = this.subAgentCallsByCallId.get(subAgentCallId);
        if (state) {
            const targetThreadIds = childThreadId ? [childThreadId] : state.childThreadIds;
            for (const targetThreadId of targetThreadIds) {
                this.emitProviderSubagentTimeline(targetThreadId, timelineItem);
            }
        }
        this.emitSubAgentActivityUpdate(subAgentCallId, timelineItem.status === "running" ? "running" : undefined);
    }
    emitSubAgentActivityUpdate(callId, status, options) {
        const state = this.subAgentCallsByCallId.get(callId);
        if (!state || state.toolCall.detail.type !== "sub_agent") {
            return;
        }
        let resolvedStatus = status ?? state.toolCall.status;
        if (status === "running" &&
            !options?.reopen &&
            isTerminalSubAgentStatus(state.toolCall.status)) {
            resolvedStatus = state.toolCall.status;
        }
        const baseToolCall = options?.subAgentType
            ? {
                ...state.toolCall,
                detail: { ...state.toolCall.detail, subAgentType: options.subAgentType },
            }
            : state.toolCall;
        const nextToolCall = resolvedStatus === "failed"
            ? {
                ...baseToolCall,
                status: "failed",
                error: state.toolCall.error ?? { message: "Sub-agent failed" },
            }
            : {
                ...baseToolCall,
                status: resolvedStatus,
                error: null,
            };
        // The card is a launch marker: the child's activity streams to its own timeline, so the card
        // is re-sent only when its status or label changes.
        const cardChanged = !isDeepStrictEqual(nextToolCall, state.toolCall);
        state.toolCall = nextToolCall;
        for (const childThreadId of state.childThreadIds) {
            this.emitProviderSubagentUpsert(childThreadId, state, resolvedStatus);
        }
        if (!cardChanged || (state.parentCallId && state.parentCallId !== callId)) {
            return;
        }
        this.emitEvent({ type: "timeline", provider: CODEX_PROVIDER, item: nextToolCall });
    }
    emitProviderSubagentUpsert(childThreadId, state, status) {
        const detail = state.toolCall.detail;
        if (detail.type !== "sub_agent") {
            return;
        }
        let providerStatus = "running";
        if (status === "completed") {
            providerStatus = "completed";
        }
        else if (status === "failed") {
            providerStatus = "failed";
        }
        else if (status === "canceled") {
            providerStatus = "canceled";
        }
        this.emitEvent({
            type: "provider_subagent",
            provider: CODEX_PROVIDER,
            event: {
                type: "upsert",
                id: childThreadId,
                title: detail.subAgentType ?? "Codex subagent",
                description: detail.description ?? null,
                status: providerStatus,
                toolCallId: state.callId,
                parentSubagentId: state.parentSubagentId,
            },
        });
    }
    emitProviderSubagentTimeline(childThreadId, item, timestamp) {
        this.emitEvent({
            type: "provider_subagent",
            provider: CODEX_PROVIDER,
            event: {
                type: "timeline",
                id: childThreadId,
                item,
                ...(timestamp ? { timestamp } : {}),
            },
        });
    }
    emitCompletedProviderSubagentItem(parsed, timelineItem) {
        const itemId = parsed.item.id;
        if (!parsed.threadId)
            return;
        if (timelineItem.type === "assistant_message" && itemId) {
            const streamedText = this.pendingAgentMessages.get(itemId);
            if (streamedText !== undefined) {
                const suffix = this.buildMissingFinalTextSuffix(timelineItem, streamedText);
                if (suffix)
                    this.emitProviderSubagentTimeline(parsed.threadId, suffix);
                return;
            }
        }
        if (timelineItem.type === "reasoning" && itemId) {
            const streamedText = this.pendingReasoning.get(itemId)?.join("");
            if (streamedText !== undefined) {
                const suffix = this.buildMissingFinalTextSuffix(timelineItem, streamedText);
                if (suffix)
                    this.emitProviderSubagentTimeline(parsed.threadId, suffix);
                return;
            }
        }
        this.emitProviderSubagentTimeline(parsed.threadId, timelineItem);
    }
    emitStartedProviderSubagentItem(threadId, timelineItem) {
        if (threadId) {
            this.emitProviderSubagentTimeline(threadId, timelineItem);
        }
    }
    emitProviderSubagentTimelineItems(threadId, timelineItems) {
        if (!threadId) {
            return;
        }
        for (const timelineItem of timelineItems) {
            this.emitProviderSubagentTimeline(threadId, timelineItem);
        }
    }
    handleSubAgentChildItemCompleted(callId, itemId, timelineItem) {
        this.applyBufferedDeltaTextToTimelineItem(timelineItem, itemId);
        if (itemId) {
            this.pendingAgentMessages.delete(itemId);
            this.pendingReasoning.delete(itemId);
            this.pendingCommandOutputDeltas.delete(itemId);
            this.pendingFileChangeOutputDeltas.delete(itemId);
        }
        this.emitSubAgentActivityUpdate(callId);
    }
    handleSubAgentContextCompactionItem(callId, childThreadId, item, status) {
        if (!this.isContextCompactionItem(item)) {
            return false;
        }
        if (status === "loading") {
            this.pendingChildCompactionThreadIds.add(childThreadId);
            this.emitProviderSubagentTimeline(childThreadId, { type: "compaction", status });
        }
        else {
            this.settleChildCompaction(childThreadId);
        }
        this.emitSubAgentActivityUpdate(callId);
        return true;
    }
    settleChildCompaction(childThreadId) {
        if (this.pendingChildCompactionThreadIds.delete(childThreadId)) {
            this.emitProviderSubagentTimeline(childThreadId, { type: "compaction", status: "completed" });
        }
    }
    shouldSkipCompletedThreadItem(timelineItem, normalizedItemType, itemId) {
        // For commandExecution items, codex/event/exec_command_* is authoritative.
        if (timelineItem.type === "tool_call" && normalizedItemType === "commandExecution") {
            const callId = timelineItem.callId || itemId;
            return Boolean(callId && this.emittedExecCommandCompletedCallIds.has(callId));
        }
        return Boolean(itemId && this.emittedItemCompletedIds.has(itemId));
    }
    handleCodexDeltaNotification(parsed, routedSubAgentCallId = null) {
        if (parsed.kind === "agent_message_delta") {
            const prev = this.pendingAgentMessages.get(parsed.itemId) ?? "";
            const text = prev + parsed.delta;
            this.pendingAgentMessages.set(parsed.itemId, text);
            const subAgentCallId = this.getSubAgentCallIdForThread(parsed.threadId);
            if (subAgentCallId) {
                if (parsed.threadId) {
                    this.emitProviderSubagentTimeline(parsed.threadId, {
                        type: "assistant_message",
                        messageId: parsed.itemId,
                        text: parsed.delta,
                    });
                }
                this.emitSubAgentActivityUpdate(subAgentCallId, "running");
                return;
            }
            const isFirstDeltaForItem = prev.length === 0;
            this.emitEvent({
                type: "timeline",
                provider: CODEX_PROVIDER,
                item: {
                    type: "assistant_message",
                    messageId: parsed.itemId,
                    text: isFirstDeltaForItem && this.pendingAssistantMessageBoundary
                        ? `${ASSISTANT_MESSAGE_BOUNDARY_MARKDOWN}${parsed.delta}`
                        : parsed.delta,
                },
            });
            if (isFirstDeltaForItem) {
                this.pendingAssistantMessageBoundary = false;
            }
            return;
        }
        if (parsed.kind === "reasoning_delta") {
            const prev = this.pendingReasoning.get(parsed.itemId) ?? [];
            prev.push(parsed.delta);
            this.pendingReasoning.set(parsed.itemId, prev);
            const subAgentCallId = this.getSubAgentCallIdForThread(parsed.threadId);
            if (subAgentCallId) {
                if (parsed.threadId) {
                    this.emitProviderSubagentTimeline(parsed.threadId, {
                        type: "reasoning",
                        text: parsed.delta,
                    });
                }
                this.emitSubAgentActivityUpdate(subAgentCallId, "running");
                return;
            }
            this.emitEvent({
                type: "timeline",
                provider: CODEX_PROVIDER,
                item: { type: "reasoning", text: parsed.delta },
            });
            return;
        }
        if (parsed.kind === "exec_command_output_delta") {
            const outputDeltas = routedSubAgentCallId
                ? this.subAgentCallsByCallId.get(routedSubAgentCallId)?.pendingCommandOutputDeltas
                : this.pendingCommandOutputDeltas;
            if (!outputDeltas) {
                return;
            }
            this.appendOutputDeltaChunk(outputDeltas, parsed.callId, parsed.chunk, {
                decodeBase64: true,
            });
            return;
        }
        const outputDeltas = routedSubAgentCallId
            ? this.subAgentCallsByCallId.get(routedSubAgentCallId)?.pendingFileChangeOutputDeltas
            : this.pendingFileChangeOutputDeltas;
        if (outputDeltas) {
            this.appendOutputDeltaChunk(outputDeltas, parsed.itemId, parsed.delta);
        }
    }
    handleThreadStartedNotification(parsed) {
        this.currentThreadId = parsed.threadId;
        this.background.changed(this.currentThreadId);
        this.emitEvent({
            type: "thread_started",
            provider: CODEX_PROVIDER,
            sessionId: parsed.threadId,
        });
    }
    handleTurnStartedNotification(parsed) {
        const subAgentCallId = this.getSubAgentCallIdForThread(parsed.threadId);
        if (subAgentCallId) {
            this.emitSubAgentActivityUpdate(subAgentCallId, "running", { reopen: true });
            return;
        }
        this.markCurrentThreadSaved(parsed.threadId);
        this.currentTurnId = parsed.turnId;
        const pendingIdentification = this.pendingForegroundTurnIdentification;
        if (pendingIdentification &&
            pendingIdentification.foregroundTurnId === this.activeForegroundTurnId) {
            pendingIdentification.resolve(parsed.turnId);
            this.pendingForegroundTurnIdentification = null;
        }
        this.resetTurnTrackingState();
        this.emitEvent({ type: "turn_started", provider: CODEX_PROVIDER });
    }
    // Codex has written the rollout by the time it announces a thread's turn, including a
    // goal's or a compaction's. From here on the thread can be reported and resumed.
    markCurrentThreadSaved(threadId) {
        const current = this.currentThreadId;
        if (!current || (threadId !== null && threadId !== current))
            return;
        if (this.savedThreadId === current)
            return;
        this.savedThreadId = current;
        this.cachedRuntimeInfo = null;
        this.emitEvent({ type: "thread_started", provider: CODEX_PROVIDER, sessionId: current });
    }
    handleTurnCompletedNotification(parsed) {
        const subAgentCallId = this.getSubAgentCallIdForThread(parsed.threadId);
        if (subAgentCallId) {
            if (parsed.threadId)
                this.settleChildCompaction(parsed.threadId);
            let status = "completed";
            if (parsed.status === "failed") {
                status = "failed";
            }
            else if (parsed.status === "interrupted") {
                status = "canceled";
            }
            this.emitSubAgentActivityUpdate(subAgentCallId, status);
            return;
        }
        this.completePendingRootCompactions();
        // What a turn that was not interrupted leaves running, it left on purpose; no later
        // interrupt may end it.
        if (parsed.status !== "interrupted" && this.currentTurnId) {
            this.terminalProcessIdsByTurnId.delete(this.currentTurnId);
        }
        if (parsed.status === "failed") {
            const error = parsed.errorMessage ?? "Codex turn failed";
            this.settleStreamRetry({ status: "failed", error });
            this.emitEvent({ type: "turn_failed", provider: CODEX_PROVIDER, error });
        }
        else if (parsed.status === "interrupted") {
            this.settleStreamRetry({ status: "canceled" });
            this.dismissInterruptedAsyncQuestions();
            this.emitEvent({ type: "turn_canceled", provider: CODEX_PROVIDER, reason: "interrupted" });
        }
        else {
            this.settleStreamRetry({ status: "completed" });
            if (this.planModeEnabled && this.latestPlanResult?.text) {
                this.emitSyntheticPlanApprovalRequest(this.latestPlanResult.text);
            }
            this.emitEvent({
                type: "turn_completed",
                provider: CODEX_PROVIDER,
                usage: this.latestUsage,
            });
        }
        this.activeForegroundTurnId = null;
        this.activeClientMessageId = null;
        this.currentTurnId = null;
        this.pendingForegroundTurnIdentification?.resolve(null);
        this.pendingForegroundTurnIdentification = null;
        this.pendingSubAgentNotificationsByThreadId.clear();
        this.resetTurnTrackingState();
    }
    handleTurnErrorNotification(parsed) {
        // A final error is followed by `turn/completed`, which reports it as the turn's failure.
        if (!parsed.willRetry) {
            return;
        }
        this.activeStreamRetry = {
            callId: this.activeStreamRetry?.callId ?? `codex-retry:${randomUUID()}`,
            message: parsed.message,
            details: parsed.details,
        };
        this.emitEvent({
            type: "timeline",
            provider: CODEX_PROVIDER,
            item: mapCodexStreamRetryToStatusLine(this.activeStreamRetry, { status: "running" }),
        });
    }
    handleWarningNotification(parsed) {
        // Codex repeats some warnings on every turn, such as missing metadata for a custom model.
        if (this.shownWarningMessages.has(parsed.message)) {
            return;
        }
        this.shownWarningMessages.add(parsed.message);
        this.emitEvent({
            type: "timeline",
            provider: CODEX_PROVIDER,
            item: { type: "notification", level: "warning", message: parsed.message },
        });
    }
    settleStreamRetry(outcome) {
        const retry = this.activeStreamRetry;
        if (!retry) {
            return;
        }
        this.activeStreamRetry = null;
        this.emitEvent({
            type: "timeline",
            provider: CODEX_PROVIDER,
            item: mapCodexStreamRetryToStatusLine(retry, outcome),
        });
    }
    resetTurnTrackingState() {
        this.latestPlanResult = null;
        this.emittedItemStartedIds.clear();
        this.emittedItemCompletedIds.clear();
        this.emittedProviderSubagentUserMessageKeys.clear();
        this.emittedExecCommandStartedCallIds.clear();
        this.emittedExecCommandCompletedCallIds.clear();
        this.pendingAgentMessages.clear();
        this.pendingReasoning.clear();
        this.pendingCommandOutputDeltas.clear();
        this.pendingFileChangeOutputDeltas.clear();
        this.pendingToolCallDetails.clear();
        this.pendingAssistantMessageBoundary = false;
        this.warnedIncompleteEditToolCallIds.clear();
        this.pendingRootCompactionItemIds.clear();
        this.pendingAnonymousRootCompactions = 0;
        this.unpairedCompactionNotificationCompletions = 0;
        this.unpairedCompactionItemCompletions = 0;
    }
    handlePlanUpdatedNotification(parsed) {
        if (!this.planModeEnabled) {
            this.emitEvent({
                type: "timeline",
                provider: CODEX_PROVIDER,
                item: mapCodexPlanUpdateToTodo(parsed.plan),
            });
            return;
        }
        const timelineItem = mapCodexPlanToToolCall({
            callId: `plan:${this.currentTurnId ?? this.currentThreadId ?? "current"}`,
            text: planStepsToMarkdown(parsed.plan.map((entry) => ({
                step: entry.step ?? "",
                status: entry.status ?? "pending",
            }))),
        });
        if (timelineItem) {
            this.rememberPlanResult(timelineItem);
            // Older Codex app-server builds reported Plan-mode proposals through
            // turn/plan/updated. Retain that compatibility path only while Plan mode is active.
            return;
        }
    }
    handleTokenUsageUpdatedNotification(parsed) {
        this.latestUsage = toAgentUsage(parsed.tokenUsage);
        if (this.latestUsage) {
            this.notifySubscribers({
                type: "usage_updated",
                provider: CODEX_PROVIDER,
                usage: this.latestUsage,
            });
        }
    }
    resolveContextCompactionTrigger(itemId) {
        if (itemId) {
            const known = this.compactionTriggerByItemId.get(itemId);
            if (known) {
                return known;
            }
        }
        if (this.pendingManualCompactionStarts > 0) {
            this.pendingManualCompactionStarts -= 1;
            return "manual";
        }
        return undefined;
    }
    trackPendingRootCompaction(itemId) {
        if (itemId) {
            this.pendingRootCompactionItemIds.add(itemId);
            return;
        }
        this.pendingAnonymousRootCompactions += 1;
    }
    consumePendingRootCompaction(itemId) {
        if (itemId) {
            if (this.pendingRootCompactionItemIds.delete(itemId)) {
                return { itemId };
            }
            if (this.pendingRootCompactionItemIds.size === 0 &&
                this.pendingAnonymousRootCompactions > 0) {
                this.pendingAnonymousRootCompactions -= 1;
                return {};
            }
            return undefined;
        }
        const pendingItemId = this.pendingRootCompactionItemIds.values().next().value;
        if (typeof pendingItemId === "string") {
            this.pendingRootCompactionItemIds.delete(pendingItemId);
            return { itemId: pendingItemId };
        }
        if (this.pendingAnonymousRootCompactions > 0) {
            this.pendingAnonymousRootCompactions -= 1;
            return {};
        }
        return undefined;
    }
    completePendingRootCompactions() {
        // Some Codex builds end a turn without completing the contextCompaction
        // item. Close every loading timeline row before emitting the terminal turn.
        for (const itemId of this.pendingRootCompactionItemIds) {
            this.emitEvent({
                type: "timeline",
                provider: CODEX_PROVIDER,
                item: this.createContextCompactionTimelineItem("completed", itemId),
            });
        }
        for (let index = 0; index < this.pendingAnonymousRootCompactions; index += 1) {
            this.emitEvent({
                type: "timeline",
                provider: CODEX_PROVIDER,
                item: this.createContextCompactionTimelineItem("completed"),
            });
        }
        this.pendingRootCompactionItemIds.clear();
        this.pendingAnonymousRootCompactions = 0;
    }
    createContextCompactionTimelineItem(status, itemId) {
        const trigger = this.resolveContextCompactionTrigger(itemId);
        if (itemId && trigger) {
            if (status === "loading") {
                this.compactionTriggerByItemId.set(itemId, trigger);
            }
            else {
                this.compactionTriggerByItemId.delete(itemId);
            }
        }
        return {
            type: "compaction",
            status,
            ...(trigger ? { trigger } : {}),
        };
    }
    isContextCompactionItem(item) {
        return (normalizeCodexThreadItemType(typeof item.type === "string" ? item.type : undefined) ===
            CODEX_CONTEXT_COMPACTION_TYPE);
    }
    isUserMessageItem(item) {
        return (normalizeCodexThreadItemType(typeof item.type === "string" ? item.type : undefined) ===
            "userMessage");
    }
    handleThreadRolledBackNotification(parsed) {
        this.truncateCodexUserMessageTurns(parsed.numTurns);
    }
    handleContextCompactedNotification(parsed) {
        if (parsed.threadId !== this.currentThreadId) {
            return;
        }
        if (this.unpairedCompactionItemCompletions > 0) {
            this.unpairedCompactionItemCompletions -= 1;
            return;
        }
        const pendingItemId = this.consumePendingRootCompaction()?.itemId;
        this.unpairedCompactionNotificationCompletions += 1;
        this.emitEvent({
            type: "timeline",
            provider: CODEX_PROVIDER,
            item: this.createContextCompactionTimelineItem("completed", pendingItemId),
            ...(parsed.turnId ? { turnId: parsed.turnId } : {}),
        });
    }
    handleExecCommandStartedNotification(parsed, subAgentCallId = null) {
        const outputDeltas = subAgentCallId
            ? this.subAgentCallsByCallId.get(subAgentCallId)?.pendingCommandOutputDeltas
            : this.pendingCommandOutputDeltas;
        if (!outputDeltas) {
            return;
        }
        if (parsed.callId && !subAgentCallId) {
            this.emittedExecCommandStartedCallIds.add(parsed.callId);
        }
        if (parsed.callId) {
            outputDeltas.delete(parsed.callId);
        }
        const timelineItem = mapCodexExecNotificationToToolCall({
            callId: parsed.callId,
            command: parsed.command,
            cwd: parsed.cwd ?? this.config.cwd ?? null,
            running: true,
        });
        if (timelineItem) {
            this.emitCodexToolTimelineItem(timelineItem, subAgentCallId, parsed.threadId);
        }
    }
    handleExecCommandCompletedNotification(parsed, subAgentCallId = null) {
        const outputDeltas = subAgentCallId
            ? this.subAgentCallsByCallId.get(subAgentCallId)?.pendingCommandOutputDeltas
            : this.pendingCommandOutputDeltas;
        if (!outputDeltas) {
            return;
        }
        const bufferedOutput = this.consumeOutputDelta(outputDeltas, parsed.callId);
        const resolvedOutput = parsed.output ?? bufferedOutput;
        if (!subAgentCallId) {
            this.rememberTerminalProcessForCommand(parsed.command, resolvedOutput);
        }
        const timelineItem = mapCodexExecNotificationToToolCall({
            callId: parsed.callId,
            command: parsed.command,
            cwd: parsed.cwd ?? this.config.cwd ?? null,
            output: resolvedOutput,
            exitCode: parsed.exitCode,
            success: parsed.success,
            stderr: parsed.stderr,
            running: false,
        });
        if (timelineItem) {
            if (!subAgentCallId) {
                this.emittedExecCommandCompletedCallIds.add(timelineItem.callId);
            }
            this.emitCodexToolTimelineItem(timelineItem, subAgentCallId, parsed.threadId);
        }
    }
    handleTerminalInteractionNotification(parsed) {
        const interactionKey = [parsed.processId ?? "", parsed.stdin ?? ""].join("\u0000");
        if (!this.shouldEmitTerminalInteractionKey(interactionKey)) {
            return;
        }
        const command = (parsed.processId ? this.terminalCommandByProcessId.get(parsed.processId) : undefined) ??
            null;
        const callId = this.createTerminalInteractionCallId(parsed.processId, parsed.callId);
        if (!command && parsed.processId) {
            const pendingInteractions = this.pendingUnlabeledTerminalInteractions.get(parsed.processId) ?? [];
            pendingInteractions.push({ callId, stdin: parsed.stdin });
            this.pendingUnlabeledTerminalInteractions.set(parsed.processId, pendingInteractions);
        }
        const timelineItem = mapCodexTerminalInteractionToToolCall({
            callId,
            processId: parsed.processId,
            command,
            stdin: parsed.stdin,
        });
        this.emitEvent({ type: "timeline", provider: CODEX_PROVIDER, item: timelineItem });
    }
    handlePatchApplyStartedNotification(parsed, subAgentCallId = null) {
        const outputDeltas = subAgentCallId
            ? this.subAgentCallsByCallId.get(subAgentCallId)?.pendingFileChangeOutputDeltas
            : this.pendingFileChangeOutputDeltas;
        if (!outputDeltas) {
            return;
        }
        if (parsed.callId) {
            outputDeltas.delete(parsed.callId);
        }
        const timelineItem = mapCodexPatchNotificationToToolCall({
            callId: parsed.callId,
            changes: parsed.changes,
            cwd: this.config.cwd ?? null,
            running: true,
        });
        if (timelineItem) {
            this.warnOnIncompleteEditToolCall(timelineItem, "patch_apply_started", {
                callId: parsed.callId,
                changes: parsed.changes,
            });
            this.emitCodexToolTimelineItem(timelineItem, subAgentCallId, parsed.threadId);
        }
    }
    handlePatchApplyCompletedNotification(parsed, subAgentCallId = null) {
        const outputDeltas = subAgentCallId
            ? this.subAgentCallsByCallId.get(subAgentCallId)?.pendingFileChangeOutputDeltas
            : this.pendingFileChangeOutputDeltas;
        if (!outputDeltas) {
            return;
        }
        const bufferedOutput = this.consumeOutputDelta(outputDeltas, parsed.callId);
        const timelineItem = mapCodexPatchNotificationToToolCall({
            callId: parsed.callId,
            changes: parsed.changes,
            cwd: this.config.cwd ?? null,
            stdout: parsed.stdout ?? bufferedOutput,
            stderr: parsed.stderr,
            success: parsed.success,
            running: false,
        });
        if (timelineItem) {
            this.warnOnIncompleteEditToolCall(timelineItem, "patch_apply_completed", {
                callId: parsed.callId,
                changes: parsed.changes,
                stdout: parsed.stdout,
            });
            this.emitCodexToolTimelineItem(timelineItem, subAgentCallId, parsed.threadId);
        }
    }
    receiveAsyncQuestion(threadId, item) {
        if (threadId !== this.currentThreadId)
            return;
        const request = this.asyncQuestions.receive(item);
        if (request)
            this.emitEvent({ type: "permission_requested", provider: CODEX_PROVIDER, request });
    }
    dismissInterruptedAsyncQuestions() {
        const resolution = { behavior: "deny", message: "Interrupted" };
        for (const request of this.asyncQuestions.pending()) {
            const prepared = this.asyncQuestions.prepareResponse(request.id, resolution);
            this.emitEvent({ type: "timeline", provider: CODEX_PROVIDER, item: prepared.complete() });
            this.emitEvent({
                type: "permission_resolved",
                provider: CODEX_PROVIDER,
                requestId: request.id,
                resolution,
            });
        }
    }
    reconcileAsyncQuestionsAfterRewind(timeline) {
        const retainedIds = new Set(timeline.flatMap(({ item }) => item.type === "tool_call" && item.name === "request_user_input_async" ? [item.callId] : []));
        for (const requestId of this.asyncQuestions.retain(retainedIds)) {
            this.emitEvent({
                type: "permission_resolved",
                provider: CODEX_PROVIDER,
                requestId,
                resolution: { behavior: "deny", message: "Removed by rewind" },
            });
        }
    }
    ownsNotificationTurn(turnId) {
        return !this.closed && this.activeForegroundTurnId === turnId;
    }
    async handleItemCompletedNotification(parsed) {
        this.recordTurnTerminalProcess(parsed);
        // Codex emits mirrored lifecycle notifications via both `codex/event/item_*`
        // and canonical `item/*`. Render ordinary items only from the canonical
        // channel, but accept a legacy-only child announcement so it can establish
        // the provider-subagent route.
        if (shouldIgnoreMirroredLifecycleItem(parsed.source, parsed.item)) {
            return;
        }
        if (parsed.item.id) {
            this.pendingToolCallDetails.delete(parsed.item.id);
        }
        this.receiveAsyncQuestion(parsed.threadId, parsed.item);
        if (this.isUserMessageItem(parsed.item)) {
            await this.handleUserMessageItem(parsed);
            return;
        }
        const childSubAgentCallId = this.getSubAgentCallIdForThread(parsed.threadId);
        if (this.handleCompletedSpecialItem(parsed, childSubAgentCallId)) {
            return;
        }
        const foregroundTurnId = this.activeForegroundTurnId;
        const timelineItem = await threadItemToTimeline(parsed.item, {
            includeUserMessage: false,
            cwd: this.config.cwd ?? null,
        });
        if (!timelineItem) {
            return;
        }
        const imageItems = await mcpToolResultImagesToTimeline(parsed.item);
        if (!this.ownsNotificationTurn(foregroundTurnId))
            return;
        const registeredChildThreadIds = timelineItem.type === "tool_call"
            ? this.registerSubAgentToolCall({
                timelineItem,
                rawItem: parsed.item,
                parentCallId: childSubAgentCallId,
                parentSubagentId: resolveCodexParentSubagentId(childSubAgentCallId, parsed.threadId),
            })
            : [];
        if (childSubAgentCallId) {
            this.emitCompletedProviderSubagentItem(parsed, timelineItem);
            this.handleSubAgentChildItemCompleted(childSubAgentCallId, parsed.item.id, timelineItem);
            this.emitProviderSubagentTimelineItems(parsed.threadId, imageItems);
            await this.replayPendingSubAgentNotifications(registeredChildThreadIds);
            return;
        }
        const normalizedItemType = normalizeCodexThreadItemType(typeof parsed.item.type === "string" ? parsed.item.type : undefined);
        const itemId = parsed.item.id;
        if (this.shouldSkipCompletedThreadItem(timelineItem, normalizedItemType, itemId)) {
            await this.replayPendingSubAgentNotifications(registeredChildThreadIds);
            return;
        }
        if (this.consumeStreamedTextCompletion(timelineItem, itemId)) {
            if (timelineItem.type === "assistant_message") {
                this.pendingAssistantMessageBoundary = true;
            }
            this.emittedItemCompletedIds.add(itemId);
            this.emittedItemStartedIds.delete(itemId);
            await this.replayPendingSubAgentNotifications(registeredChildThreadIds);
            return;
        }
        this.applyBufferedDeltaTextToTimelineItem(timelineItem, itemId);
        if (timelineItem.type === "tool_call") {
            if (timelineItem.detail.type === "plan") {
                this.rememberPlanResult(timelineItem);
                // Codex can surface plans both as turn/plan updates and as completed
                // thread items. In plan mode, approval owns the visible plan card.
                if (this.planModeEnabled) {
                    return;
                }
            }
            this.warnOnIncompleteEditToolCall(timelineItem, "item_completed", parsed.item);
        }
        this.emitEvent({ type: "timeline", provider: CODEX_PROVIDER, item: timelineItem });
        if (timelineItem.type === "assistant_message") {
            this.pendingAssistantMessageBoundary = true;
        }
        for (const imageItem of imageItems) {
            this.emitEvent({ type: "timeline", provider: CODEX_PROVIDER, item: imageItem });
            this.pendingAssistantMessageBoundary = true;
        }
        if (itemId) {
            this.emittedItemCompletedIds.add(itemId);
            this.emittedItemStartedIds.delete(itemId);
            this.pendingCommandOutputDeltas.delete(itemId);
            this.pendingFileChangeOutputDeltas.delete(itemId);
        }
        await this.replayPendingSubAgentNotifications(registeredChildThreadIds);
    }
    consumeStreamedTextCompletion(timelineItem, itemId) {
        if (!itemId) {
            return false;
        }
        if (timelineItem.type === "assistant_message" && this.pendingAgentMessages.has(itemId)) {
            const streamedText = this.pendingAgentMessages.get(itemId) ?? "";
            this.pendingAgentMessages.delete(itemId);
            this.emitMissingFinalTextSuffix(timelineItem, streamedText);
            return true;
        }
        if (timelineItem.type === "reasoning" && this.pendingReasoning.has(itemId)) {
            const streamedText = this.pendingReasoning.get(itemId)?.join("") ?? "";
            this.pendingReasoning.delete(itemId);
            this.emitMissingFinalTextSuffix(timelineItem, streamedText);
            return true;
        }
        return false;
    }
    emitMissingFinalTextSuffix(timelineItem, streamedText) {
        const item = this.buildMissingFinalTextSuffix(timelineItem, streamedText);
        if (item)
            this.emitEvent({ type: "timeline", provider: CODEX_PROVIDER, item });
    }
    buildMissingFinalTextSuffix(timelineItem, streamedText) {
        if (!timelineItem.text.startsWith(streamedText))
            return timelineItem;
        const suffix = timelineItem.text.slice(streamedText.length);
        if (!suffix)
            return null;
        return timelineItem.type === "assistant_message"
            ? {
                type: timelineItem.type,
                text: suffix,
                ...(timelineItem.messageId ? { messageId: timelineItem.messageId } : {}),
            }
            : { type: timelineItem.type, text: suffix };
    }
    applyBufferedDeltaTextToTimelineItem(timelineItem, itemId) {
        if (!itemId) {
            return;
        }
        if (timelineItem.type === "assistant_message") {
            const buffered = this.pendingAgentMessages.get(itemId);
            if (buffered && buffered.length > 0) {
                if (!timelineItem.text.startsWith(buffered))
                    timelineItem.text = buffered;
            }
            return;
        }
        if (timelineItem.type === "reasoning") {
            const buffered = this.pendingReasoning.get(itemId);
            if (buffered && buffered.length > 0) {
                const streamedText = buffered.join("");
                if (!timelineItem.text.startsWith(streamedText))
                    timelineItem.text = streamedText;
            }
        }
    }
    async handleItemStartedNotification(parsed) {
        this.recordTurnTerminalProcess(parsed);
        if (shouldIgnoreMirroredLifecycleItem(parsed.source, parsed.item)) {
            return;
        }
        if (this.isUserMessageItem(parsed.item)) {
            await this.handleUserMessageItem(parsed);
            return;
        }
        const childSubAgentCallId = this.getSubAgentCallIdForThread(parsed.threadId);
        if (childSubAgentCallId &&
            parsed.threadId &&
            this.handleSubAgentContextCompactionItem(childSubAgentCallId, parsed.threadId, parsed.item, "loading")) {
            return;
        }
        if (this.isContextCompactionItem(parsed.item)) {
            this.trackPendingRootCompaction(parsed.item.id);
            this.emitEvent({
                type: "timeline",
                provider: CODEX_PROVIDER,
                item: this.createContextCompactionTimelineItem("loading", parsed.item.id),
            });
            return;
        }
        if (this.handleRegisteredSubAgentActivity(parsed.item)) {
            return;
        }
        const timelineItem = await threadItemToTimeline(parsed.item, {
            includeUserMessage: false,
            cwd: this.config.cwd ?? null,
        });
        if (!timelineItem || timelineItem.type !== "tool_call") {
            return;
        }
        this.pendingToolCallDetails.set(timelineItem.callId, timelineItem.detail);
        const registeredChildThreadIds = this.registerSubAgentToolCall({
            timelineItem,
            rawItem: parsed.item,
            parentCallId: childSubAgentCallId,
            parentSubagentId: resolveCodexParentSubagentId(childSubAgentCallId, parsed.threadId),
        });
        if (childSubAgentCallId) {
            this.emitStartedProviderSubagentItem(parsed.threadId, timelineItem);
            this.emitSubAgentActivityUpdate(childSubAgentCallId, "running");
            await this.replayPendingSubAgentNotifications(registeredChildThreadIds);
            return;
        }
        const normalizedItemType = normalizeCodexThreadItemType(typeof parsed.item.type === "string" ? parsed.item.type : undefined);
        const itemId = parsed.item.id;
        if (normalizedItemType === "commandExecution") {
            this.rememberTerminalProcessForItem(parsed.item);
            const callId = timelineItem.callId || itemId;
            if (callId && this.emittedExecCommandStartedCallIds.has(callId)) {
                return;
            }
        }
        if (itemId && this.emittedItemStartedIds.has(itemId)) {
            return;
        }
        this.warnOnIncompleteEditToolCall(timelineItem, "item_started", parsed.item);
        this.emitEvent({ type: "timeline", provider: CODEX_PROVIDER, item: timelineItem });
        if (itemId) {
            this.emittedItemStartedIds.add(itemId);
            this.pendingCommandOutputDeltas.delete(itemId);
            this.pendingFileChangeOutputDeltas.delete(itemId);
        }
        await this.replayPendingSubAgentNotifications(registeredChildThreadIds);
    }
    async handleUserMessageItem(parsed) {
        const itemId = parsed.item.id;
        const timelineItem = await threadItemToTimeline(parsed.item, {
            includeUserMessage: true,
            cwd: this.config.cwd ?? null,
        });
        if (!timelineItem || timelineItem.type !== "user_message") {
            return;
        }
        const childSubAgentCallId = this.getSubAgentCallIdForThread(parsed.threadId);
        if (childSubAgentCallId) {
            const childMessageId = itemId ?? timelineItem.messageId;
            if (!childMessageId) {
                return;
            }
            const childMessageKey = `${parsed.threadId ?? childSubAgentCallId}:${childMessageId}`;
            if (this.emittedProviderSubagentUserMessageKeys.has(childMessageKey)) {
                return;
            }
            this.emittedProviderSubagentUserMessageKeys.add(childMessageKey);
            if (parsed.threadId) {
                this.emitProviderSubagentTimeline(parsed.threadId, timelineItem);
            }
            this.emitSubAgentActivityUpdate(childSubAgentCallId, "running");
            return;
        }
        if (!this.rememberCodexUserMessageTurn(timelineItem.messageId, parsed.turnId)) {
            return;
        }
        const clientMessageId = timelineItem.clientMessageId ?? this.activeClientMessageId;
        const item = clientMessageId ? { ...timelineItem, clientMessageId } : timelineItem;
        this.activeClientMessageId = null;
        this.emitEvent({ type: "timeline", provider: CODEX_PROVIDER, item });
    }
    warnUnknownNotificationMethod(method, params) {
        if (this.warnedUnknownNotificationMethods.has(method)) {
            return;
        }
        this.warnedUnknownNotificationMethods.add(method);
        this.logger.trace({
            agentId: this.agentId,
            provider: CODEX_PROVIDER,
            sessionId: this.currentThreadId,
            turnId: this.activeForegroundTurnId ?? undefined,
            method,
            params,
        }, "provider.codex.event_unhandled");
    }
    warnInvalidNotificationPayload(method, params) {
        const key = method;
        if (this.warnedInvalidNotificationPayloads.has(key)) {
            return;
        }
        this.warnedInvalidNotificationPayloads.add(key);
        this.logger.warn({ method, params }, "Invalid Codex app-server notification payload");
    }
    appendOutputDeltaChunk(store, id, chunk, options) {
        if (!id || !chunk) {
            return;
        }
        const normalized = options?.decodeBase64 ? decodeCodexOutputDeltaChunk(chunk) : chunk;
        if (!normalized.length) {
            return;
        }
        const prev = store.get(id) ?? [];
        prev.push(normalized);
        store.set(id, prev);
    }
    consumeOutputDelta(store, id) {
        if (!id) {
            return null;
        }
        const buffered = store.get(id);
        if (!buffered || buffered.length === 0) {
            return null;
        }
        store.delete(id);
        return buffered.join("");
    }
    rememberTerminalProcessForCommand(command, output) {
        const processId = extractCodexTerminalSessionId(output ?? undefined);
        if (processId) {
            this.rememberTerminalProcess(processId, command);
        }
    }
    rememberTerminalProcessForItem(item) {
        if (typeof item.processId === "string" && item.processId) {
            this.rememberTerminalProcess(item.processId, item.command);
        }
    }
    rememberTerminalProcess(processId, command) {
        const displayCommand = normalizeCommandExecutionCommand(command);
        if (!displayCommand) {
            return;
        }
        this.terminalCommandByProcessId.set(processId, displayCommand);
        if (!this.pendingUnlabeledTerminalInteractions.has(processId)) {
            return;
        }
        const pendingInteractions = this.pendingUnlabeledTerminalInteractions.get(processId) ?? [];
        this.pendingUnlabeledTerminalInteractions.delete(processId);
        for (const pendingInteraction of pendingInteractions) {
            this.emitEvent({
                type: "timeline",
                provider: CODEX_PROVIDER,
                item: mapCodexTerminalInteractionToToolCall({
                    callId: pendingInteraction.callId,
                    processId,
                    command: displayCommand,
                    stdin: pendingInteraction.stdin,
                }),
            });
        }
    }
    createTerminalInteractionCallId(processId, fallbackCallId) {
        const baseCallId = processId
            ? `terminal-session-${processId}`
            : (nonEmptyString(fallbackCallId ?? undefined) ?? "terminal-interaction");
        this.nextTerminalInteractionOrdinal += 1;
        return `${baseCallId}-${this.nextTerminalInteractionOrdinal}`;
    }
    shouldEmitTerminalInteractionKey(key) {
        if (this.emittedTerminalInteractionKeys.has(key)) {
            return false;
        }
        this.emittedTerminalInteractionKeys.add(key);
        return true;
    }
    warnOnIncompleteEditToolCall(item, source, payload) {
        if (!isEditToolCallWithoutContent(item)) {
            return;
        }
        const warnKey = `${source}:${item.callId}`;
        if (this.warnedIncompleteEditToolCallIds.has(warnKey)) {
            return;
        }
        this.warnedIncompleteEditToolCallIds.add(warnKey);
        this.logger.warn({
            source,
            callId: item.callId,
            status: item.status,
            name: item.name,
            detail: item.detail,
            payload,
        }, "Codex edit tool call is missing diff/content fields");
    }
    handleCommandApprovalRequest(params) {
        const parsed = z
            .object({
            itemId: z.string(),
            // Set when several approval callbacks belong to one item; it names the callback.
            approvalId: z.string().nullable().optional(),
            threadId: z.string(),
            turnId: z.string(),
            command: z.string().nullable().optional(),
            cwd: z.string().nullable().optional(),
            reason: z.string().nullable().optional(),
        })
            .parse(params);
        const commandPreview = mapCodexExecNotificationToToolCall({
            callId: parsed.itemId,
            command: parsed.command,
            cwd: parsed.cwd ?? this.config.cwd ?? null,
            running: true,
        });
        const requestId = `permission-${parsed.approvalId ?? parsed.itemId}`;
        const title = parsed.command ? `Run command: ${parsed.command}` : "Run command";
        const request = {
            id: requestId,
            provider: CODEX_PROVIDER,
            name: "CodexBash",
            kind: "tool",
            title,
            description: parsed.reason ?? undefined,
            input: {
                command: parsed.command ?? undefined,
                cwd: parsed.cwd ?? undefined,
            },
            detail: commandPreview?.detail ?? {
                type: "unknown",
                input: {
                    command: parsed.command ?? null,
                    cwd: parsed.cwd ?? null,
                },
                output: null,
            },
            metadata: {
                itemId: parsed.itemId,
                threadId: parsed.threadId,
                turnId: parsed.turnId,
            },
        };
        this.pendingPermissions.set(requestId, request);
        this.emitEvent({ type: "permission_requested", provider: CODEX_PROVIDER, request });
        return new Promise((resolve) => {
            this.pendingPermissionHandlers.set(requestId, { resolve, kind: "command" });
        });
    }
    async handleFileChangeApprovalRequest(params) {
        await this.pendingNotifications;
        const parsed = z
            .object({
            itemId: z.string(),
            threadId: z.string(),
            turnId: z.string(),
            reason: z.string().nullable().optional(),
        })
            .parse(params);
        const requestId = `permission-${parsed.itemId}`;
        const detail = this.pendingToolCallDetails.get(parsed.itemId) ?? {
            type: "unknown",
            input: { reason: parsed.reason ?? null },
            output: null,
        };
        const title = detail.type === "edit" && detail.filePath
            ? `Apply file changes: ${detail.filePath}`
            : "Apply file changes";
        const request = {
            id: requestId,
            provider: CODEX_PROVIDER,
            name: "CodexFileChange",
            kind: "tool",
            title,
            description: parsed.reason ?? undefined,
            detail,
            metadata: {
                itemId: parsed.itemId,
                threadId: parsed.threadId,
                turnId: parsed.turnId,
            },
        };
        this.pendingPermissions.set(requestId, request);
        this.emitEvent({ type: "permission_requested", provider: CODEX_PROVIDER, request });
        return new Promise((resolve) => {
            this.pendingPermissionHandlers.set(requestId, { resolve, kind: "file" });
        });
    }
    handleToolApprovalRequest(params) {
        const parsed = z
            .object({
            itemId: z.string(),
            threadId: z.string(),
            turnId: z.string(),
            questions: z.array(z.unknown()),
        })
            .parse(params);
        const requestId = `permission-${parsed.itemId}`;
        const questions = normalizeCodexQuestionPrompts(parsed.questions);
        const request = {
            id: requestId,
            provider: CODEX_PROVIDER,
            name: "request_user_input",
            kind: "question",
            title: "Question",
            description: undefined,
            detail: {
                type: "plain_text",
                text: formatCodexQuestionPrompts(questions),
                icon: "brain",
            },
            input: { questions },
            metadata: {
                itemId: parsed.itemId,
                threadId: parsed.threadId,
                turnId: parsed.turnId,
                questions,
            },
        };
        this.pendingPermissions.set(requestId, request);
        this.emitEvent({
            type: "timeline",
            provider: CODEX_PROVIDER,
            item: mapCodexQuestionRequestToToolCall({
                callId: parsed.itemId,
                questions,
                status: "running",
            }),
        });
        this.emitEvent({ type: "permission_requested", provider: CODEX_PROVIDER, request });
        return new Promise((resolve) => {
            this.pendingPermissionHandlers.set(requestId, {
                resolve,
                kind: "question",
                questions,
            });
        });
    }
    handleMcpElicitationRequest(params, serverRequestId) {
        const parsed = z
            .object({
            threadId: z.string(),
            turnId: z.string().nullable().optional(),
            serverName: z.string(),
            mode: z.enum(["form", "openai/form", "url"]),
            message: z.string(),
            requestedSchema: z.unknown().optional(),
            url: z.string().optional(),
            elicitationId: z.string().optional(),
        })
            .parse(params);
        if (parsed.mode === "url") {
            return Promise.resolve({ action: "decline", content: null, _meta: null });
        }
        const requiredFields = toObjectRecord(parsed.requestedSchema)?.required;
        if (Array.isArray(requiredFields) && requiredFields.length > 0) {
            return Promise.resolve({ action: "decline", content: null, _meta: null });
        }
        const requestId = `permission-${randomUUID()}`;
        const request = {
            id: requestId,
            provider: CODEX_PROVIDER,
            name: "CodexMcpElicitation",
            kind: "tool",
            title: `MCP approval: ${parsed.serverName}`,
            description: parsed.message,
            input: {
                mode: parsed.mode,
                requestedSchema: parsed.requestedSchema ?? null,
                url: parsed.url ?? null,
            },
            metadata: {
                threadId: parsed.threadId,
                turnId: parsed.turnId ?? null,
                serverName: parsed.serverName,
                elicitationId: parsed.elicitationId ?? null,
            },
        };
        this.pendingPermissions.set(requestId, request);
        this.mcpElicitationPermissionIds.set(serverRequestId, requestId);
        this.emitEvent({ type: "permission_requested", provider: CODEX_PROVIDER, request });
        return new Promise((resolve) => {
            this.pendingPermissionHandlers.set(requestId, { resolve, kind: "mcp_elicitation" });
        });
    }
}
export class CodexAppServerAgentClient {
    constructor(logger, runtimeSettings, deps = {}) {
        this.logger = logger;
        this.runtimeSettings = runtimeSettings;
        this.deps = deps;
        this.provider = CODEX_PROVIDER;
        this.capabilities = CODEX_APP_SERVER_CAPABILITIES;
        this.goalsEnabledPromise = null;
        this.autoReviewEnabledPromise = null;
    }
    sessionDeps(launchEnv) {
        return {
            ...this.deps,
            codexHome: resolveCodexHomeDir(buildCodexAppServerEnv(this.runtimeSettings, launchEnv)),
            customCodexConfig: this.customProviderConfig(),
        };
    }
    customProviderConfig() {
        return buildCodexCustomProviderConfig(this.runtimeSettings, this.deps.customProvider);
    }
    resolveGoalsEnabled() {
        if (!this.goalsEnabledPromise) {
            this.goalsEnabledPromise = (async () => {
                try {
                    const launchPrefix = await resolveCodexLaunchPrefix(this.runtimeSettings);
                    const versionOutput = await resolveBinaryVersion(launchPrefix.command);
                    const enabled = codexVersionAtLeast(versionOutput, CODEX_GOALS_MIN_VERSION);
                    this.logger.trace({
                        provider: CODEX_PROVIDER,
                        versionOutput,
                        enabled,
                    }, "provider.codex.config.goals_resolved");
                    return enabled;
                }
                catch (error) {
                    this.logger.warn({ err: error }, "Failed to probe codex version for goals gate");
                    return false;
                }
            })();
        }
        return this.goalsEnabledPromise;
    }
    resolveAutoReviewEnabled(signal) {
        if (signal)
            return this.probeAutoReviewEnabled(signal);
        if (!this.autoReviewEnabledPromise) {
            this.autoReviewEnabledPromise = this.probeAutoReviewEnabled();
        }
        return this.autoReviewEnabledPromise;
    }
    async probeAutoReviewEnabled(signal) {
        try {
            const launchPrefix = await resolveCodexLaunchPrefix(this.runtimeSettings);
            signal?.throwIfAborted();
            const versionOutput = await resolveBinaryVersion(launchPrefix.command, signal);
            signal?.throwIfAborted();
            const enabled = codexVersionAtLeast(versionOutput, CODEX_AUTO_REVIEW_MIN_VERSION);
            this.logger.trace({ provider: CODEX_PROVIDER, versionOutput, enabled }, "provider.codex.config.auto_review_resolved");
            return enabled;
        }
        catch (error) {
            if (signal?.aborted)
                throw signal.reason;
            this.logger.warn({ err: error }, "Failed to probe codex version for auto-review gate");
            return false;
        }
    }
    async prepareAppServerSpawn(options) {
        const processPort = this.deps.appServerProcess ?? defaultCodexAppServerProcess;
        const launchPrefix = await processPort.resolvePrefix({ runtimeSettings: this.runtimeSettings });
        const args = [...launchPrefix.args, "app-server"];
        if (options.goalsEnabled) {
            args.push("--enable", "goals");
        }
        return () => {
            this.logger.trace({
                agentId: options.agentId,
                provider: CODEX_PROVIDER,
                launchPrefix,
                goalsEnabled: options.goalsEnabled === true,
            }, "provider.codex.spawn");
            const child = processPort.spawn({
                command: launchPrefix.command,
                args,
                options: {
                    detached: process.platform !== "win32",
                    stdio: ["pipe", "pipe", "pipe"],
                    baseEnv: options.environment,
                },
            });
            assertChildWithPipes(child);
            return child;
        };
    }
    async startAppServer(options = {}) {
        const environment = buildCodexAppServerEnv(this.runtimeSettings);
        const spawn = await this.prepareAppServerSpawn({ environment });
        const { client } = await codexAppServerStartup.start({
            stateDirKey: codexStartupStateDirKey(environment),
            spawn,
            createClient: (child) => new CodexAppServerClient(child, this.logger),
            initializeParams: buildCodexAppServerInitializeParams(),
            logger: this.logger,
            signal: options.signal,
        });
        return client;
    }
    async createSession(config, launchContext, options) {
        if (options?.persistSession === false) {
            this.logger.debug("Codex app-server does not expose an ephemeral-session option; persistSession=false is currently a no-op");
            // TODO: Honor persistSession=false if app-server adds support, or route
            // utility generations through `codex exec --ephemeral` in a larger change.
        }
        const sessionConfig = { ...config, provider: CODEX_PROVIDER };
        const goalsEnabled = await this.resolveGoalsEnabled();
        const autoReviewEnabled = await this.resolveAutoReviewEnabled();
        const environment = buildCodexAppServerEnv(this.runtimeSettings, launchContext?.env);
        const spawn = await this.prepareAppServerSpawn({
            environment,
            goalsEnabled,
            agentId: launchContext?.agentId,
        });
        const session = new CodexAppServerAgentSession(sessionConfig, null, this.logger, async () => spawn(), { ...this.sessionDeps(launchContext?.env), environment }, options?.persistSession === false, goalsEnabled, autoReviewEnabled, launchContext?.agentId, "interactive");
        await session.connect();
        return session;
    }
    async resumeSession(handle, overrides, launchContext, options) {
        const storedConfig = (handle.metadata ?? {});
        const merged = {
            ...storedConfig,
            ...overrides,
            provider: CODEX_PROVIDER,
            cwd: overrides?.cwd ?? storedConfig.cwd ?? process.cwd(),
        };
        const goalsEnabled = await this.resolveGoalsEnabled();
        const autoReviewEnabled = await this.resolveAutoReviewEnabled();
        const environment = buildCodexAppServerEnv(this.runtimeSettings, launchContext?.env);
        const spawn = await this.prepareAppServerSpawn({
            environment,
            goalsEnabled,
            agentId: launchContext?.agentId,
        });
        const session = new CodexAppServerAgentSession(merged, handle, this.logger, async () => spawn(), { ...this.sessionDeps(launchContext?.env), environment }, false, goalsEnabled, autoReviewEnabled, launchContext?.agentId, options?.purpose ?? "interactive");
        await session.connect();
        return session;
    }
    async listImportableSessions(options) {
        const environment = buildCodexAppServerEnv(this.runtimeSettings);
        const spawn = await this.prepareAppServerSpawn({ environment });
        const { client } = await codexAppServerStartup.start({
            stateDirKey: codexStartupStateDirKey(environment),
            spawn,
            createClient: (child) => this.deps._createCodexClient?.(child, this.logger, () => ({})) ??
                new CodexAppServerClient(child, this.logger),
            initializeParams: buildCodexAppServerInitializeParams(),
            logger: this.logger,
        });
        try {
            const limit = options?.limit ?? 20;
            const scanLimit = Math.min(options?.scanLimit ?? limit, 500);
            // thread/list returns the cheap `cwd` field. Fetch a wider window when
            // filtering since most threads will be from other cwds, then keep the
            // local realpath-aware filter for symlink-equivalent workspace paths.
            const listLimit = options?.cwd ? Math.max(scanLimit, 50) : scanLimit;
            // Codex records each thread under the model provider that ran it and,
            // unless told otherwise, lists only the provider its own config selects.
            // Include legacy custom ids, but keep Codex built-in sessions separate.
            const customProviderConfig = this.customProviderConfig();
            let modelProviders;
            if (customProviderConfig && this.deps.customProvider) {
                modelProviders = [customProviderConfig.model_provider];
                if (!CODEX_BUILTIN_MODEL_PROVIDER_IDS.has(this.deps.customProvider.id)) {
                    modelProviders.push(this.deps.customProvider.id);
                }
            }
            const allThreads = await readCodexThreadWindow(client, this.logger, {
                limit: listLimit,
                cwd: options?.cwd,
                ...(modelProviders ? { modelProviders } : {}),
            });
            const threads = filterCodexThreadsByCwd(allThreads, options?.cwd);
            return threads.slice(0, limit).map((thread) => {
                const threadId = typeof thread.id === "string" ? thread.id : "";
                const cwd = typeof thread.cwd === "string" ? thread.cwd : process.cwd();
                const preview = typeof thread.preview === "string" ? thread.preview : null;
                const title = typeof thread.name === "string" && thread.name.trim() ? thread.name : preview;
                return {
                    providerHandleId: threadId,
                    cwd,
                    title,
                    firstPromptPreview: preview,
                    lastPromptPreview: preview,
                    lastActivityAt: new Date(((typeof thread.updatedAt === "number" ? thread.updatedAt : undefined) ??
                        (typeof thread.createdAt === "number" ? thread.createdAt : undefined) ??
                        0) * 1000),
                };
            });
        }
        finally {
            await client.dispose();
        }
    }
    async importSession(input, context) {
        return importSessionFromPersistence({
            provider: CODEX_PROVIDER,
            request: input,
            context,
            resumeSession: this.resumeSession.bind(this),
        });
    }
    async getCatalogCacheKey(_options) {
        // This client discovers through host configuration, independent of project cwd.
        return "host";
    }
    async fetchCatalog(_options, context) {
        const [models, autoReviewEnabled] = await Promise.all([
            this.fetchModelsFromAppServer(context),
            runProviderRefreshActivity(context, "version", () => this.resolveAutoReviewEnabled(context?.signal)),
        ]);
        return {
            models,
            defaultModeId: autoReviewEnabled ? "auto-review" : DEFAULT_CODEX_MODE_ID,
            modes: autoReviewEnabled
                ? CODEX_MODES
                : CODEX_MODES.filter((mode) => mode.id !== "auto-review"),
        };
    }
    async resolveDefaultModeId(input) {
        return (await this.resolveAutoReviewEnabled(input.signal))
            ? "auto-review"
            : DEFAULT_CODEX_MODE_ID;
    }
    async fetchModelsFromAppServer(context) {
        // Codex model/list is global to the app server in this flow; cwd/force are intentionally ignored.
        let client;
        let disposePromise;
        const dispose = () => {
            if (!client)
                return Promise.resolve();
            disposePromise ?? (disposePromise = client.dispose());
            return disposePromise;
        };
        const handleAbort = () => void dispose().catch(() => undefined);
        context?.signal.addEventListener("abort", handleAbort, { once: true });
        try {
            await runProviderRefreshActivity(context, "app-server.start", async () => {
                client = await this.startAppServer({ signal: context?.signal });
                if (context?.signal.aborted)
                    await dispose();
            });
            if (!client)
                throw new Error("Codex app-server did not start");
            const rawResponse = await runProviderRefreshActivity(context, "model/list", () => client.request("model/list", {}));
            const parsedResponse = CodexModelListResponseSchema.safeParse(rawResponse);
            const models = parsedResponse.success ? (parsedResponse.data.data ?? []) : [];
            const configuredDefaults = await runProviderRefreshActivity(context, "config/read", () => readCodexConfiguredDefaults(client, this.logger));
            const configuredDefaultModelId = configuredDefaults.model;
            const configuredDefaultThinkingOptionId = configuredDefaults.thinkingOptionId;
            const hasConfiguredDefaultModel = typeof configuredDefaultModelId === "string"
                ? models.some((model) => model?.id === configuredDefaultModelId)
                : false;
            return models.map((model) => buildCodexModelDefinition(model, {
                configuredDefaultModelId,
                configuredDefaultThinkingOptionId,
                hasConfiguredDefaultModel,
            }));
        }
        finally {
            context?.signal.removeEventListener("abort", handleAbort);
            await dispose();
        }
    }
    async archiveNativeSession(handle) {
        await this.updateNativeThreadArchiveState(handle, "archive");
    }
    async unarchiveNativeSession(handle) {
        await this.updateNativeThreadArchiveState(handle, "restore");
    }
    async updateNativeThreadArchiveState(handle, state) {
        const threadId = handle.nativeHandle ?? handle.sessionId;
        if (!threadId)
            return;
        const client = await this.startAppServer();
        try {
            if (state === "archive") {
                await client.request("thread/archive", { threadId });
                return;
            }
            try {
                await client.request("thread/unarchive", { threadId });
            }
            catch (error) {
                if (!isCodexAlreadyUnarchivedError(error, threadId))
                    throw error;
                try {
                    await client.request("thread/read", { threadId });
                }
                catch {
                    throw error;
                }
            }
        }
        finally {
            await client.dispose();
        }
    }
    async isAvailable() {
        const launch = await resolveCodexLaunch(this.runtimeSettings);
        const availability = await checkCodexLaunchAvailable(launch);
        return availability.available;
    }
    async getDiagnostic() {
        try {
            const launch = await resolveCodexLaunch(this.runtimeSettings);
            const availability = await checkCodexLaunchAvailable(launch);
            const entries = [
                ...(await buildCommandResolutionDiagnosticRows(launch, {
                    knownBinaryNames: ["codex"],
                })),
                ...(await buildBinaryDiagnosticRows(launch, availability)),
            ];
            return {
                diagnostic: formatProviderDiagnostic("Codex", entries),
            };
        }
        catch (error) {
            return {
                diagnostic: formatProviderDiagnosticError("Codex", error),
            };
        }
    }
}
function buildCodexModelDefinition(model, ctx) {
    const defaultReasoningEffort = normalizeCodexThinkingOptionId(typeof model.defaultReasoningEffort === "string" ? model.defaultReasoningEffort : null);
    const resolvedDefaultReasoningEffort = ctx.configuredDefaultThinkingOptionId ?? defaultReasoningEffort;
    const thinkingById = buildCodexThinkingOptionMap(model.supportedReasoningEfforts, resolvedDefaultReasoningEffort, ctx.configuredDefaultThinkingOptionId);
    const thinkingOptions = Array.from(thinkingById.values()).map((option) => Object.assign({}, option, {
        isDefault: option.id === resolvedDefaultReasoningEffort,
    }));
    const defaultThinkingOptionId = resolvedDefaultReasoningEffort ??
        thinkingOptions.find((option) => option.isDefault)?.id ??
        thinkingOptions[0]?.id;
    const isDefaultModel = ctx.hasConfiguredDefaultModel
        ? model.id === ctx.configuredDefaultModelId
        : model.isDefault;
    return {
        provider: CODEX_PROVIDER,
        id: model.id,
        label: normalizeCodexModelLabel(model.displayName ?? ""),
        description: model.description,
        isDefault: isDefaultModel,
        thinkingOptions: thinkingOptions.length > 0 ? thinkingOptions : undefined,
        defaultThinkingOptionId,
        metadata: {
            model: model.model,
            defaultReasoningEffort: model.defaultReasoningEffort,
            supportedReasoningEfforts: model.supportedReasoningEfforts,
        },
    };
}
function buildCodexThinkingOptionMap(supportedReasoningEfforts, resolvedDefaultReasoningEffort, configuredDefaultThinkingOptionId) {
    const thinkingById = new Map();
    if (Array.isArray(supportedReasoningEfforts)) {
        for (const entry of supportedReasoningEfforts) {
            const id = normalizeCodexThinkingOptionId(typeof entry?.reasoningEffort === "string" ? entry.reasoningEffort : null);
            if (!id)
                continue;
            const description = typeof entry?.description === "string" && entry.description.trim().length > 0
                ? entry.description
                : undefined;
            thinkingById.set(id, { id, label: id, description });
        }
    }
    if (resolvedDefaultReasoningEffort && !thinkingById.has(resolvedDefaultReasoningEffort)) {
        thinkingById.set(resolvedDefaultReasoningEffort, {
            id: resolvedDefaultReasoningEffort,
            label: resolvedDefaultReasoningEffort,
            description: configuredDefaultThinkingOptionId === resolvedDefaultReasoningEffort
                ? "Configured default reasoning effort"
                : "Model default reasoning effort",
        });
    }
    return thinkingById;
}
function resolveSkillDescription(skill) {
    if (typeof skill.description === "string") {
        return skill.description;
    }
    if (typeof skill.shortDescription === "string") {
        return skill.shortDescription;
    }
    return "Skill";
}
//# sourceMappingURL=codex-app-server-agent.js.map