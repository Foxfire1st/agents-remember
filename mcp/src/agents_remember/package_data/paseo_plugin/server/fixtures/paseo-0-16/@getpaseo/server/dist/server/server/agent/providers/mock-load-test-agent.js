import { randomUUID } from "node:crypto";
import { importSessionFromPersistence } from "../provider-session-import.js";
import { getAgentProviderDefinition } from "@getpaseo/protocol/provider-manifest";
export const MOCK_LOAD_TEST_PROVIDER_ID = "mock";
export const MOCK_LOAD_TEST_DEFAULT_MODEL_ID = "five-minute-stream";
const MOCK_LOAD_TEST_COMMAND = {
    name: "mock",
    description: "Reply without starting a turn",
    argumentHint: "handled-command",
    kind: "command",
};
export const MOCK_LOAD_TEST_HANDLED_COMMAND = `/${MOCK_LOAD_TEST_COMMAND.name} ${MOCK_LOAD_TEST_COMMAND.argumentHint}`;
const MOCK_LOAD_TEST_MODE_ID = "load-test";
const MOCK_LOAD_TEST_DURATION_MS = 5 * 60 * 1000;
const MOCK_LOAD_TEST_INTERVAL_MS = 40;
function getPositiveFeatureInteger(value) {
    return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : 0;
}
const ONE_PIXEL_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl4Kj8AAAAASUVORK5CYII=";
const CAPABILITIES = {
    supportsStreaming: true,
    supportsSessionPersistence: true,
    supportsSessionListing: true,
    supportsDynamicModes: false,
    supportsMcpServers: false,
    supportsReasoningStream: true,
    supportsToolInvocations: true,
    supportsRewindConversation: true,
    supportsRewindFiles: true,
    supportsRewindBoth: true,
};
const MODELS = [
    {
        provider: MOCK_LOAD_TEST_PROVIDER_ID,
        id: MOCK_LOAD_TEST_DEFAULT_MODEL_ID,
        aliases: ["legacy-five-minute-stream"],
        label: "Five minute stream",
        description: "Realistic agent flow streamed as sub-word tokens for five minutes (good for scroll/coalesce debugging).",
        isDefault: true,
        thinkingOptions: [
            { id: "low", label: "Low", isDefault: true },
            { id: "medium", label: "Medium" },
            { id: "high", label: "High" },
        ],
        defaultThinkingOptionId: "low",
        metadata: {
            durationMs: MOCK_LOAD_TEST_DURATION_MS,
            intervalMs: MOCK_LOAD_TEST_INTERVAL_MS,
        },
    },
    {
        provider: MOCK_LOAD_TEST_PROVIDER_ID,
        id: "thirty-minute-stream",
        label: "Thirty minute stream",
        description: "Long-running realistic stream for extended scroll-anchor debugging.",
        metadata: {
            durationMs: 30 * 60 * 1000,
            intervalMs: 50,
        },
    },
    {
        provider: MOCK_LOAD_TEST_PROVIDER_ID,
        id: "legacy-five-minute-stream",
        label: "Legacy five minute stream",
        isSelectable: false,
        thinkingOptions: [
            { id: "low", label: "Low", isDefault: true },
            { id: "medium", label: "Medium" },
            { id: "high", label: "High" },
        ],
        defaultThinkingOptionId: "low",
        metadata: {
            durationMs: MOCK_LOAD_TEST_DURATION_MS,
            intervalMs: MOCK_LOAD_TEST_INTERVAL_MS,
        },
    },
    {
        provider: MOCK_LOAD_TEST_PROVIDER_ID,
        id: "one-minute-stream",
        label: "One minute stream",
        description: "Shorter realistic stream for quick manual checks.",
        metadata: {
            durationMs: 60000,
            intervalMs: 40,
        },
    },
    {
        provider: MOCK_LOAD_TEST_PROVIDER_ID,
        id: "bursty-stream",
        label: "Bursty stream",
        description: "Emits tokens in uneven bursts separated by idle gaps, reproducing the lumpy arrival pattern real models produce. Use this to measure streaming smoothness.",
        metadata: {
            durationMs: 60000,
            intervalMs: 0,
            burstMinTokens: 1,
            burstMaxTokens: 40,
            burstGapMs: 90,
        },
    },
    {
        provider: MOCK_LOAD_TEST_PROVIDER_ID,
        id: "ten-second-stream",
        label: "Ten second stream",
        description: "Fast realistic stream for tests and smoke checks.",
        thinkingOptions: [
            { id: "low", label: "Low", isDefault: true },
            { id: "medium", label: "Medium" },
            { id: "high", label: "High" },
        ],
        defaultThinkingOptionId: "low",
        metadata: {
            durationMs: 10000,
            intervalMs: 5,
        },
    },
    {
        provider: MOCK_LOAD_TEST_PROVIDER_ID,
        id: "max-only-thinking-stream",
        label: "Max-only thinking stream",
        description: "Fast realistic stream with one supported thinking level.",
        thinkingOptions: [{ id: "max", label: "Max", isDefault: true }],
        defaultThinkingOptionId: "max",
        metadata: {
            durationMs: 10000,
            intervalMs: 5,
        },
    },
    {
        provider: MOCK_LOAD_TEST_PROVIDER_ID,
        id: "e2e-fast-stream",
        label: "E2E fast stream",
        description: "Short deterministic stream for browser tests.",
        isSelectable: false,
        metadata: {
            durationMs: 2000,
            intervalMs: 20,
        },
    },
];
function nextBurstSize(profile, sequence) {
    // Deterministic hash of the burst index; no Math.random so runs are repeatable.
    let hash = (sequence + 1) * 2654435761;
    hash ^= hash >>> 15;
    hash = Math.imul(hash, 2246822519);
    hash ^= hash >>> 13;
    const span = profile.maxTokens - profile.minTokens + 1;
    return profile.minTokens + ((hash >>> 0) % span);
}
function shouldEmitPlanApprovalPrompt(prompt) {
    return /emit\s+(?:a\s+)?synthetic\s+plan\s+approval/i.test(promptToText(prompt));
}
function shouldEmitTurnFailure(prompt) {
    return /emit\s+(?:a\s+)?synthetic\s+turn\s+failure/i.test(promptToText(prompt));
}
function parseSteeringReplayShape(prompt) {
    const match = /replay a (claude|codex)-shaped foreground shell tool call/i.exec(promptToText(prompt));
    return match?.[1] === "claude" || match?.[1] === "codex" ? match[1] : null;
}
function parseSettledAssistantImageMarkdown(prompt) {
    const match = /^emit settled assistant image markdown:\s*(!\[[^\]\r\n]*\]\(.+\))\s*$/i.exec(promptToText(prompt));
    return match?.[1] ?? null;
}
const ROLLOUT_FLAG_PREVIEW = [
    "Ship it dark behind the new-surface flag, then turn it on for internal users before everyone else.",
    "",
    'if (flags.enabled("new-surface")) {',
    "  render(<NewSurface />);",
    "}",
    ...Array.from({ length: 40 }, (_, i) => `// rollout step ${i + 1}`),
    "// LAST PREVIEW LINE",
].join("\n");
function parseMockQuestionPrompt(prompt) {
    const text = promptToText(prompt);
    if (!/emit\s+(?:a\s+)?synthetic\s+questions?/i.test(text)) {
        return null;
    }
    if (/with\s+links/i.test(text)) {
        return {
            questions: [
                {
                    question: "Sign in at https://example.com/login, then follow [the setup guide](https://example.com/setup). Done?",
                    header: "signIn",
                    options: [
                        {
                            label: "Done",
                            description: "Logs are at https://example.com/logs if sign-in fails.",
                        },
                        { label: "Not yet, see [status](https://example.com/status)" },
                    ],
                    multiSelect: false,
                },
                {
                    question: "Which checks should run?",
                    header: "checks",
                    options: [
                        { label: "Lint", description: "Rules: https://example.com/lint" },
                        { label: "Typecheck" },
                    ],
                    multiSelect: true,
                },
            ],
        };
    }
    if (/free[-\s]?write|freeform|text[-\s]?only/i.test(text)) {
        return {
            questions: [
                {
                    question: "What is the GitHub private repo URL to push to?",
                    header: "repoUrl",
                    options: [],
                    multiSelect: false,
                    placeholder: "git@github.com:user/repo.git",
                },
                {
                    question: "What should the first commit message be?",
                    header: "commitMessage",
                    options: [],
                    multiSelect: false,
                    placeholder: "Initial commit",
                },
            ],
        };
    }
    return {
        questions: [
            {
                question: "Which surface should this apply to?",
                header: "surface",
                options: [{ label: "App" }, { label: "Desktop" }],
                multiSelect: false,
            },
            {
                question: "Which rollout should we use?",
                header: "rollout",
                options: [
                    { label: "Immediately" },
                    { label: "Behind feature flag", preview: ROLLOUT_FLAG_PREVIEW },
                ],
                multiSelect: false,
            },
            {
                question: "What success criteria should we use?",
                header: "success",
                options: [],
                multiSelect: false,
                placeholder: "Describe success...",
            },
        ],
    };
}
function resolveModelProfile(modelId) {
    const model = MODELS.find((entry) => entry.id === modelId) ?? MODELS[0];
    const metadata = model.metadata ?? {};
    const burstMinTokens = typeof metadata.burstMinTokens === "number" ? metadata.burstMinTokens : null;
    const burstMaxTokens = typeof metadata.burstMaxTokens === "number" ? metadata.burstMaxTokens : null;
    return {
        modelId: model.id,
        durationMs: typeof metadata.durationMs === "number" ? metadata.durationMs : MOCK_LOAD_TEST_DURATION_MS,
        intervalMs: typeof metadata.intervalMs === "number" ? metadata.intervalMs : MOCK_LOAD_TEST_INTERVAL_MS,
        burst: burstMinTokens !== null && burstMaxTokens !== null
            ? {
                minTokens: Math.max(1, burstMinTokens),
                maxTokens: Math.max(Math.max(1, burstMinTokens), burstMaxTokens),
                gapMs: typeof metadata.burstGapMs === "number" ? metadata.burstGapMs : 90,
            }
            : null,
    };
}
function promptToText(prompt) {
    if (typeof prompt === "string") {
        return prompt;
    }
    return prompt
        .flatMap((block) => (block.type === "text" ? [block.text] : []))
        .join("\n")
        .trim();
}
function parseUserMessageDelayMs(prompt) {
    const match = /delay synthetic user message by (\d+)ms/i.exec(promptToText(prompt));
    const delayMs = Number(match?.[1] ?? 0);
    return Number.isSafeInteger(delayMs) ? Math.min(delayMs, 2000) : 0;
}
function shouldWithholdUserMessageUntilInterrupt(prompt) {
    return /withhold synthetic user message until interrupted/i.test(promptToText(prompt));
}
function shouldEmitUserMessageBeforeTurnAcceptance(prompt) {
    return /emit synthetic user message before accepting turn/i.test(promptToText(prompt));
}
function parseAssistantMessagesBeforeUserMessage(prompt) {
    const match = /emit (\d+) assistant messages before synthetic user message/i.exec(promptToText(prompt));
    const count = Number(match?.[1]);
    return Number.isSafeInteger(count) && count > 0 ? Math.min(count, 500) : null;
}
function parseLargeAgentStreamPayloadPrompt(prompt) {
    const text = promptToText(prompt);
    const match = /emit\s+(\d+)\s+(?:byte\s+)?(?:large\s+)?(diff|file|image)\s+agent stream (?:update|payload)/i.exec(text);
    if (!match) {
        return null;
    }
    const bytes = Number(match[1]);
    if (!Number.isFinite(bytes) || bytes <= 0) {
        return null;
    }
    const kindValue = match[2]?.toLowerCase();
    if (kindValue !== "diff" && kindValue !== "file" && kindValue !== "image") {
        return null;
    }
    return {
        bytes: Math.min(bytes, 1000000),
        kind: kindValue,
    };
}
function parseAgentStreamStressPrompt(prompt) {
    const text = promptToText(prompt);
    const match = /emit\s+(\d+)\s+(coalesced\s+|tool\s+)?agent stream updates/i.exec(text);
    if (!match) {
        return null;
    }
    const count = Number(match[1]);
    if (!Number.isFinite(count) || count <= 0) {
        return null;
    }
    return {
        count: Math.min(count, 5000),
        coalesced: match[2]?.trim() === "coalesced",
        tools: match[2]?.trim() === "tool",
    };
}
function parseStructuredBranchNamePrompt(prompt) {
    const text = promptToText(prompt);
    const hasBranchNamePrompt = text.includes("Generate a title and a git branch name for a coding agent") &&
        (text.includes("Return JSON only with fields 'title' and 'branch'.") ||
            text.includes('"title"') ||
            text.includes('"branch"'));
    if (!hasBranchNamePrompt &&
        !(text.includes("You must respond with JSON only that matches this JSON Schema") &&
            text.includes('"title"') &&
            text.includes('"branch"'))) {
        return null;
    }
    const seed = text.match(/<user-prompt>\n([\s\S]*?)\n<\/user-prompt>/)?.[1]?.trim() ??
        text.match(/<attachments>\n([\s\S]*?)\n<\/attachments>/)?.[1]?.trim() ??
        "";
    const firstLine = seed
        .split("\n")
        .find((line) => line.trim().length > 0)
        ?.trim() ?? "Mock task";
    const title = firstLine
        .replace(/^["'`]+|["'`]+$/g, "")
        .replace(/\s+/g, " ")
        .slice(0, 80)
        .trim();
    const branch = title
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 100) || "mock-task";
    return { title: title || "Mock task", branch };
}
function buildRepeatedPayload(bytes, prefix) {
    const line = `${prefix} ${"x".repeat(96)}\n`;
    let output = "";
    while (output.length < bytes) {
        output += line;
    }
    return output.slice(0, bytes);
}
function tokenize(text) {
    const tokens = [];
    const regex = /(\s*)(\S+)|(\s+)/g;
    let match;
    while ((match = regex.exec(text)) !== null) {
        const [, leadingWs, word, lonelyWs] = match;
        if (lonelyWs !== undefined) {
            tokens.push(lonelyWs);
            continue;
        }
        const ws = leadingWs ?? "";
        const w = word ?? "";
        if (w.length <= 5) {
            tokens.push(ws + w);
            continue;
        }
        tokens.push(ws + w.slice(0, 4));
        for (let i = 4; i < w.length; i += 4) {
            tokens.push(w.slice(i, i + 4));
        }
    }
    return tokens;
}
function buildIntroParagraph(cycle) {
    return [
        `## Cycle ${cycle}`,
        "",
        "I'll take a look at the scroll anchor behavior you described. Let me start by walking through how the conversation list currently handles streaming updates and where the auto-scroll logic actually lives. My instinct is that the anchor is supposed to pin to the bottom only when the user is already there, but I want to confirm that against the code rather than guess. This kind of behavior is usually a thin layout effect over a ref, and the bugs tend to come from event ordering rather than the math itself, so the first useful step is to read the relevant files.",
    ].join("\n");
}
function buildReasoningText() {
    return "Need to find the scroll container, the layout effect that watches for new messages, and any gesture handler that might fight with programmatic scrolling. Probably a ref on the FlatList plus a near-bottom threshold.";
}
function buildMidParagraph() {
    return [
        "Now I have a clearer picture. The auto-scroll uses a ref on the FlatList and tracks whether the user has scrolled away from the bottom by comparing the offset against the content size. There are a few subtle issues worth flagging before we change anything:",
        "",
        "- The threshold for 'near the bottom' is hardcoded at 80px, which feels too tight on dense content where headers and tool calls take up a lot of vertical space.",
        "- We rely on `onContentSizeChange` to detect new content, but that fires after layout, not as the streaming delta arrives, so we end up scrolling one frame late on fast streams.",
        "- The gesture handler does not pause scroll-to-bottom while the user is actively dragging, which means a drag in progress can be visually overridden mid-frame.",
        "- Coalescing happens upstream, so the FlatList sees fewer updates than the wire — but each batch can still cause a relayout.",
        "",
        "Let me make a small adjustment to the threshold and add a flag for active gestures, then run a quick command to confirm the order of events when streaming is fast.",
    ].join("\n");
}
function buildClosingParagraph() {
    return "The change should keep scroll-to-bottom working when the user is at the bottom while not yanking the viewport when they are reading earlier messages. The bash output confirms the `userIsAtBottom` flag flips correctly during a simulated streaming burst, and the gesture flag suppresses scroll while a drag is active. If you want, I can follow up with a regression test that drives the FlatList with synthetic deltas at high frequency to lock in the behavior. For now, I'll stop here so you can take a look.";
}
function buildEditDiff(filePath) {
    return [
        `diff --git a/${filePath} b/${filePath}`,
        `--- a/${filePath}`,
        `+++ b/${filePath}`,
        "@@ -42,7 +42,9 @@",
        "   const ref = useRef<FlatList>(null);",
        "   const [userIsAtBottom, setUserIsAtBottom] = useState(true);",
        "-  const NEAR_BOTTOM_PX = 80;",
        "+  const NEAR_BOTTOM_PX = 160;",
        "+  const isDraggingRef = useRef(false);",
        "",
        "-  if (userIsAtBottom) ref.current?.scrollToEnd({ animated: true });",
        "+  if (userIsAtBottom && !isDraggingRef.current) {",
        "+    ref.current?.scrollToEnd({ animated: true });",
        "+  }",
    ].join("\n");
}
function buildCycleQueue(turnId, cycle) {
    const queue = [];
    for (const tok of tokenize(buildIntroParagraph(cycle))) {
        queue.push({ kind: "assistant_token", text: tok });
    }
    for (const tok of tokenize(buildReasoningText())) {
        queue.push({ kind: "reasoning_token", text: tok });
    }
    const readDetail = {
        type: "read",
        filePath: "packages/app/src/components/conversation-list.tsx",
    };
    const readId = `${turnId}:read:${cycle}`;
    queue.push({ kind: "tool_running", callId: readId, name: "read", detail: readDetail });
    queue.push({
        kind: "tool_completed",
        callId: readId,
        name: "read",
        detail: {
            ...readDetail,
            content: "export function ConversationList() {\n  const ref = useRef<FlatList>(null);\n  // ...\n}",
        },
    });
    const grepDetail = {
        type: "search",
        query: "scrollToEnd",
        toolName: "grep",
        mode: "files_with_matches",
    };
    const grepId = `${turnId}:grep:${cycle}`;
    queue.push({ kind: "tool_running", callId: grepId, name: "grep", detail: grepDetail });
    queue.push({
        kind: "tool_completed",
        callId: grepId,
        name: "grep",
        detail: {
            ...grepDetail,
            filePaths: [
                "packages/app/src/components/conversation-list.tsx",
                "packages/app/src/hooks/use-scroll-anchor.ts",
            ],
            numFiles: 2,
            numMatches: 5,
        },
    });
    for (const tok of tokenize(buildMidParagraph())) {
        queue.push({ kind: "assistant_token", text: tok });
    }
    const editFile = "packages/app/src/hooks/use-scroll-anchor.ts";
    const editDetail = {
        type: "edit",
        filePath: editFile,
        oldString: "const NEAR_BOTTOM_PX = 80;",
        newString: "const NEAR_BOTTOM_PX = 160;",
        unifiedDiff: buildEditDiff(editFile),
    };
    const editId = `${turnId}:edit:${cycle}`;
    queue.push({ kind: "tool_running", callId: editId, name: "edit", detail: editDetail });
    queue.push({ kind: "tool_completed", callId: editId, name: "edit", detail: editDetail });
    const shellDetail = {
        type: "shell",
        command: "node scripts/simulate-stream-burst.mjs",
        cwd: "/tmp/paseo-mock-load",
        output: "[burst] tick 1 userIsAtBottom=true\n[burst] tick 2 userIsAtBottom=true\n[burst] drag-start isDragging=true\n[burst] tick 3 suppressed\n[burst] drag-end isDragging=false\n",
        exitCode: 0,
    };
    const shellId = `${turnId}:bash:${cycle}`;
    queue.push({ kind: "tool_running", callId: shellId, name: "bash", detail: shellDetail });
    queue.push({ kind: "tool_completed", callId: shellId, name: "bash", detail: shellDetail });
    for (const tok of tokenize(buildClosingParagraph())) {
        queue.push({ kind: "assistant_token", text: tok });
    }
    queue.push({ kind: "usage" });
    return queue;
}
function buildBurstyStreamQueue(cycle) {
    return tokenize([buildIntroParagraph(cycle), buildMidParagraph(), buildClosingParagraph()].join("\n\n")).map((text) => ({ kind: "assistant_token", text }));
}
function createToolCall(input) {
    return {
        type: "tool_call",
        callId: input.callId,
        name: input.name,
        status: input.status,
        error: null,
        detail: input.detail,
    };
}
export class MockLoadTestAgentClient {
    constructor(logger) {
        this.logger = logger;
        this.provider = MOCK_LOAD_TEST_PROVIDER_ID;
        this.capabilities = CAPABILITIES;
    }
    async createSession(config, _launchContext) {
        return new MockLoadTestAgentSession({
            config,
            sessionId: randomUUID(),
            logger: this.logger,
        });
    }
    async resumeSession(handle, overrides, _launchContext) {
        const metadata = (handle.metadata ?? {});
        return new MockLoadTestAgentSession({
            config: {
                cwd: metadata.cwd ?? overrides?.cwd ?? process.cwd(),
                ...metadata,
                ...overrides,
                provider: MOCK_LOAD_TEST_PROVIDER_ID,
            },
            sessionId: handle.sessionId,
            logger: this.logger,
        });
    }
    async fetchCatalog(_options) {
        return {
            models: MODELS,
            modes: getAgentProviderDefinition(MOCK_LOAD_TEST_PROVIDER_ID).modes,
        };
    }
    async listImportableSessions() {
        return [];
    }
    async importSession(input, context) {
        return importSessionFromPersistence({
            provider: MOCK_LOAD_TEST_PROVIDER_ID,
            request: input,
            context,
            resumeSession: this.resumeSession.bind(this),
        });
    }
    async isAvailable() {
        return true;
    }
    async getDiagnostic() {
        return {
            diagnostic: "Mock load-test provider is available in development builds.",
        };
    }
}
function getConfiguredAssistantResponses(value) {
    return Array.isArray(value) && value.every((response) => typeof response === "string")
        ? [...value]
        : [];
}
export class MockLoadTestAgentSession {
    constructor(options) {
        this.provider = MOCK_LOAD_TEST_PROVIDER_ID;
        this.capabilities = CAPABILITIES;
        this.controls = [];
        this.usageSessionKey = randomUUID();
        this.listeners = new Set();
        this.history = [];
        this.activeTurn = null;
        this.pendingPermissions = new Map();
        this.id = options.sessionId;
        this.logger = options.logger;
        this.modeId = options.config.modeId ?? MOCK_LOAD_TEST_MODE_ID;
        this.modelId = options.config.model ?? MOCK_LOAD_TEST_DEFAULT_MODEL_ID;
        this.assistantResponse =
            typeof options.config.providerOptions?.mockAssistantResponse === "string"
                ? options.config.providerOptions.mockAssistantResponse
                : null;
        this.assistantResponses = getConfiguredAssistantResponses(options.config.providerOptions?.mockAssistantResponses);
        this.streamingAssistantResponse =
            typeof options.config.providerOptions?.mockStreamingAssistantResponse === "string"
                ? options.config.providerOptions.mockStreamingAssistantResponse
                : null;
        const requestedStreamingInterval = options.config.providerOptions?.mockStreamingAssistantIntervalMs;
        this.streamingAssistantIntervalMs =
            typeof requestedStreamingInterval === "number" &&
                Number.isFinite(requestedStreamingInterval) &&
                requestedStreamingInterval >= 1
                ? Math.min(requestedStreamingInterval, 1000)
                : MOCK_LOAD_TEST_INTERVAL_MS;
        this.rewindError =
            typeof options.config.providerOptions?.mockRewindError === "string"
                ? options.config.providerOptions.mockRewindError
                : null;
        const requestedPromptRejections = options.config.providerOptions?.mockPromptRejections;
        this.remainingPromptRejections =
            typeof requestedPromptRejections === "number" &&
                Number.isSafeInteger(requestedPromptRejections) &&
                requestedPromptRejections > 0
                ? requestedPromptRejections
                : 0;
        this.remainingSteerFailures = getPositiveFeatureInteger(options.config.providerOptions?.mockSteerAmbiguousFailures);
    }
    usageSession() {
        return {
            provider: this.provider,
            model: this.modelId ?? undefined,
            env: {},
            sessionKey: this.usageSessionKey,
        };
    }
    async run(prompt, options) {
        const { turnId } = await this.startTurn(prompt, options);
        const turn = this.activeTurn;
        if (!turn || turn.turnId !== turnId) {
            throw new Error("Mock load-test turn did not start");
        }
        return turn.completed;
    }
    async startTurn(prompt, options) {
        if (this.activeTurn) {
            throw new Error("Mock load-test provider already has an active turn");
        }
        if (this.remainingPromptRejections > 0) {
            this.remainingPromptRejections -= 1;
            throw new Error("Requested mock prompt rejection");
        }
        const profile = resolveModelProfile(this.modelId);
        const turnId = randomUUID();
        const assistantMessageId = randomUUID();
        let resolve;
        const completed = new Promise((promiseResolve) => {
            resolve = promiseResolve;
        });
        const turn = {
            turnId,
            assistantMessageId,
            prompt,
            startedAt: Date.now(),
            cycle: 0,
            durationMs: profile.durationMs,
            intervalMs: profile.intervalMs,
            timer: null,
            resolve,
            completed,
            queue: [],
            emittedTokens: 0,
            turnStarted: false,
            burst: profile.burst,
            burstIndex: 0,
        };
        this.activeTurn = turn;
        const largePayload = parseLargeAgentStreamPayloadPrompt(prompt);
        const stress = parseAgentStreamStressPrompt(prompt);
        const questionPrompt = parseMockQuestionPrompt(prompt);
        const structuredBranchName = parseStructuredBranchNamePrompt(prompt);
        const settledAssistantImageMarkdown = parseSettledAssistantImageMarkdown(prompt);
        const steeringReplayShape = parseSteeringReplayShape(prompt);
        const scheduleTurn = () => {
            if (shouldEmitTurnFailure(prompt)) {
                this.scheduleFailedTurn(turn);
            }
            else if (steeringReplayShape) {
                this.scheduleSteeringReplayTurn(turn, steeringReplayShape);
            }
            else if (this.streamingAssistantResponse !== null) {
                this.scheduleStreamingAssistantTurn(turn, this.streamingAssistantResponse);
            }
            else if (this.assistantResponses.length > 0 || this.assistantResponse !== null) {
                this.scheduleSettledAssistantTurn(turn, (this.assistantResponses.shift() ?? this.assistantResponse));
            }
            else if (structuredBranchName) {
                this.scheduleSettledAssistantTurn(turn, JSON.stringify(structuredBranchName));
            }
            else if (settledAssistantImageMarkdown) {
                this.scheduleSettledAssistantTurn(turn, settledAssistantImageMarkdown);
            }
            else if (shouldEmitPlanApprovalPrompt(prompt)) {
                this.schedulePlanApprovalTurn(turn);
            }
            else if (questionPrompt) {
                this.scheduleQuestionPromptTurn(turn, questionPrompt);
            }
            else if (largePayload) {
                this.scheduleLargePayloadTurn(turn, largePayload);
            }
            else if (stress) {
                this.scheduleStressTurn(turn, stress);
            }
            else {
                this.schedule(turn, 0);
            }
        };
        const emitUserMessage = () => {
            if (this.activeTurn?.turnId !== turnId) {
                return;
            }
            this.emitTurnStarted(turn);
            this.emit({
                type: "timeline",
                provider: this.provider,
                turnId,
                item: {
                    type: "user_message",
                    text: promptToText(prompt),
                    messageId: randomUUID(),
                    ...(options?.clientMessageId ? { clientMessageId: options.clientMessageId } : {}),
                },
            });
        };
        if (shouldEmitUserMessageBeforeTurnAcceptance(prompt)) {
            emitUserMessage();
            scheduleTurn();
            return { turnId };
        }
        if (shouldWithholdUserMessageUntilInterrupt(prompt)) {
            return { turnId };
        }
        const assistantMessagesBeforeUserMessage = parseAssistantMessagesBeforeUserMessage(prompt);
        if (assistantMessagesBeforeUserMessage !== null) {
            turn.timer = setTimeout(async () => {
                if (this.activeTurn !== turn)
                    return;
                this.emitTurnStarted(turn);
                for (let index = 0; index < assistantMessagesBeforeUserMessage; index += 1) {
                    this.emitTimeline(turnId, {
                        type: "assistant_message",
                        text: `Synthetic pre-echo message ${index + 1}`,
                        messageId: `${turn.assistantMessageId}-${index + 1}`,
                    });
                    await new Promise((resolvePromise) => setImmediate(resolvePromise));
                    if (this.activeTurn !== turn)
                        return;
                }
                emitUserMessage();
                await new Promise((resolvePromise) => setImmediate(resolvePromise));
                if (this.activeTurn !== turn)
                    return;
                this.finishTurnWithText(turn, "Synthetic pre-echo stream complete");
            }, 0);
            turn.timer.unref?.();
            return { turnId };
        }
        const userMessageDelayMs = parseUserMessageDelayMs(prompt);
        const userMessageTimer = setTimeout(() => {
            emitUserMessage();
            if (userMessageDelayMs > 0)
                scheduleTurn();
        }, userMessageDelayMs);
        userMessageTimer.unref?.();
        if (userMessageDelayMs === 0)
            scheduleTurn();
        return { turnId };
    }
    async listCommands() {
        return [MOCK_LOAD_TEST_COMMAND];
    }
    tryHandleOutOfBand(prompt) {
        if (prompt !== MOCK_LOAD_TEST_HANDLED_COMMAND)
            return null;
        return {
            run: async ({ emit }) => {
                emit({
                    type: "timeline",
                    provider: this.provider,
                    item: { type: "assistant_message", text: "Mock command handled" },
                });
            },
        };
    }
    subscribe(callback) {
        this.listeners.add(callback);
        return () => {
            this.listeners.delete(callback);
        };
    }
    async *streamHistory() {
        for (const event of this.history) {
            yield event;
        }
    }
    async getRuntimeInfo() {
        return {
            provider: this.provider,
            sessionId: this.id,
            model: this.modelId,
            modeId: this.modeId,
        };
    }
    async getAvailableModes() {
        return getAgentProviderDefinition(MOCK_LOAD_TEST_PROVIDER_ID).modes;
    }
    async getCurrentMode() {
        return this.modeId;
    }
    async setMode(modeId) {
        this.modeId = modeId;
    }
    getPendingPermissions() {
        return Array.from(this.pendingPermissions.values());
    }
    async respondToPermission(requestId, response) {
        const request = this.pendingPermissions.get(requestId);
        if (!request) {
            return undefined;
        }
        this.pendingPermissions.delete(requestId);
        const turn = this.activeTurn;
        this.emit({
            type: "permission_resolved",
            provider: this.provider,
            requestId,
            resolution: response,
            ...(turn ? { turnId: turn.turnId } : {}),
        });
        if (turn) {
            this.finishTurnWithText(turn, request.kind === "question"
                ? "Synthetic questions resolved"
                : "Synthetic plan approval resolved");
        }
        return undefined;
    }
    describePersistence() {
        return {
            provider: this.provider,
            sessionId: this.id,
            metadata: {
                model: this.modelId,
                modeId: this.modeId,
            },
        };
    }
    async interrupt() {
        const turn = this.activeTurn;
        if (!turn) {
            return;
        }
        this.clearTurnTimer(turn);
        this.activeTurn = null;
        const event = {
            type: "turn_canceled",
            provider: this.provider,
            reason: "Interrupted",
            turnId: turn.turnId,
        };
        this.emit(event);
        turn.resolve({
            sessionId: this.id,
            finalText: "",
            timeline: [],
            canceled: true,
        });
    }
    async steerActiveTurn(_prompt, options) {
        if (this.activeTurn?.turnId !== options.expectedTurnId) {
            return { status: "unavailable" };
        }
        if (this.remainingSteerFailures > 0) {
            this.remainingSteerFailures -= 1;
            throw new Error("Requested mock steer transport failure");
        }
        return { status: "accepted" };
    }
    async close() {
        await this.interrupt();
        this.listeners.clear();
    }
    async revertConversation(input) {
        this.failConfiguredRewind();
        this.validateRewindTarget(input.messageId);
        this.keepFirstUserMessageHistory();
    }
    async revertFiles(input) {
        this.failConfiguredRewind();
        this.validateRewindTarget(input.messageId);
    }
    async revertBoth(input) {
        this.failConfiguredRewind();
        this.validateRewindTarget(input.messageId);
        this.keepFirstUserMessageHistory();
    }
    async setModel(modelId) {
        this.modelId = modelId ?? MOCK_LOAD_TEST_DEFAULT_MODEL_ID;
    }
    schedule(turn, delayMs) {
        turn.timer = setTimeout(() => {
            this.tick(turn);
        }, delayMs);
        turn.timer.unref?.();
    }
    emitTurnStarted(turn) {
        if (turn.turnStarted) {
            return;
        }
        turn.turnStarted = true;
        this.emit({
            type: "turn_started",
            provider: this.provider,
            turnId: turn.turnId,
        });
    }
    failConfiguredRewind() {
        if (this.rewindError) {
            throw new Error(this.rewindError);
        }
    }
    validateRewindTarget(messageId) {
        const isKnownUserMessage = this.history.some((event) => event.type === "timeline" &&
            event.item.type === "user_message" &&
            event.item.messageId === messageId);
        if (!isKnownUserMessage) {
            throw new Error(`Mock rewind target ${messageId} was not found in session history`);
        }
    }
    scheduleLargePayloadTurn(turn, largePayload) {
        turn.timer = setTimeout(() => {
            this.emitLargePayloadTurn(turn, largePayload);
        }, 0);
        turn.timer.unref?.();
    }
    scheduleFailedTurn(turn) {
        turn.timer = setTimeout(() => {
            if (this.activeTurn !== turn) {
                return;
            }
            this.clearTurnTimer(turn);
            this.emitTurnStarted(turn);
            this.activeTurn = null;
            this.emit({
                type: "turn_failed",
                provider: this.provider,
                turnId: turn.turnId,
                error: "Requested mock provider failure",
            });
            turn.resolve({
                sessionId: this.id,
                finalText: "",
                timeline: [],
                canceled: false,
            });
        }, 0);
        turn.timer.unref?.();
    }
    scheduleSteeringReplayTurn(turn, shape) {
        turn.timer = setTimeout(() => {
            if (this.activeTurn !== turn)
                return;
            this.clearTurnTimer(turn);
            this.emitTurnStarted(turn);
            if (shape === "codex") {
                this.emitTimeline(turn.turnId, {
                    type: "assistant_message",
                    text: "Running the foreground command.",
                    messageId: turn.assistantMessageId,
                });
            }
            const callId = `${turn.turnId}:steering-replay-shell`;
            const detail = {
                type: "shell",
                command: "sleep 5",
                cwd: "/tmp/paseo-mock-load",
            };
            this.emitTimeline(turn.turnId, createToolCall({ callId, name: "bash", status: "running", detail }));
            turn.timer = setTimeout(() => {
                if (this.activeTurn !== turn)
                    return;
                this.clearTurnTimer(turn);
                this.emitTimeline(turn.turnId, createToolCall({
                    callId,
                    name: "bash",
                    status: "completed",
                    detail: { ...detail, output: "", exitCode: 0 },
                }));
                this.emitTimeline(turn.turnId, {
                    type: "assistant_message",
                    text: "Foreground command completed after steering.",
                    messageId: turn.assistantMessageId,
                });
                this.finishTurnWithText(turn, "Foreground command completed after steering.");
            }, 5000);
            turn.timer.unref?.();
        }, 0);
        turn.timer.unref?.();
    }
    scheduleStressTurn(turn, stress) {
        turn.timer = setTimeout(() => {
            this.emitStressTurn(turn, stress);
        }, 0);
        turn.timer.unref?.();
    }
    schedulePlanApprovalTurn(turn) {
        turn.timer = setTimeout(() => {
            this.emitPlanApprovalTurn(turn);
        }, 0);
        turn.timer.unref?.();
    }
    scheduleQuestionPromptTurn(turn, questionPrompt) {
        turn.timer = setTimeout(() => {
            this.emitQuestionPromptTurn(turn, questionPrompt);
        }, 0);
        turn.timer.unref?.();
    }
    scheduleSettledAssistantTurn(turn, finalText) {
        turn.timer = setTimeout(() => {
            this.emitSettledAssistantTurn(turn, finalText);
        }, 0);
        turn.timer.unref?.();
    }
    scheduleStreamingAssistantTurn(turn, finalText) {
        const tokens = tokenize(finalText);
        const emitNext = () => {
            if (this.activeTurn !== turn) {
                return;
            }
            this.clearTurnTimer(turn);
            this.emitTurnStarted(turn);
            const token = tokens.shift();
            if (token === undefined) {
                this.finishTurnWithText(turn, finalText);
                return;
            }
            turn.emittedTokens += 1;
            this.emitTimeline(turn.turnId, {
                type: "assistant_message",
                text: token,
                messageId: turn.assistantMessageId,
            });
            turn.timer = setTimeout(emitNext, this.streamingAssistantIntervalMs);
            turn.timer.unref?.();
        };
        turn.timer = setTimeout(emitNext, 0);
        turn.timer.unref?.();
    }
    emitSettledAssistantTurn(turn, finalText) {
        if (this.activeTurn !== turn) {
            return;
        }
        this.clearTurnTimer(turn);
        this.emitTurnStarted(turn);
        this.emitTimeline(turn.turnId, {
            type: "assistant_message",
            text: finalText,
            messageId: turn.assistantMessageId,
        });
        this.activeTurn = null;
        this.emit({
            type: "turn_completed",
            provider: this.provider,
            turnId: turn.turnId,
        });
        turn.resolve({
            sessionId: this.id,
            finalText,
            timeline: [
                {
                    type: "assistant_message",
                    text: finalText,
                    messageId: turn.assistantMessageId,
                },
            ],
            canceled: false,
        });
    }
    emitPlanApprovalTurn(turn) {
        if (this.activeTurn !== turn) {
            return;
        }
        this.clearTurnTimer(turn);
        this.emitTurnStarted(turn);
        const request = {
            id: `mock-plan-${turn.turnId}`,
            provider: this.provider,
            name: "MockPlanApproval",
            kind: "plan",
            title: "Plan",
            description: "Review the proposed plan before implementation starts.",
            input: {
                // The (c), the quoted flag and the --- are load-bearing: they trip
                // three different markdown-it typographer rules, and
                // plan-card-markdown.spec.ts asserts all three render verbatim. That
                // is the only guard against the plan card's parser prop going missing.
                plan: '1. Add the (c) README note.\n2. Run --name="my repo".\n3. Verify ---buzz in the diff.',
            },
            actions: [
                {
                    id: "implement",
                    label: "Implement",
                    behavior: "allow",
                    variant: "primary",
                    intent: "implement",
                },
                {
                    id: "dismiss",
                    label: "Dismiss",
                    behavior: "deny",
                    variant: "secondary",
                    intent: "dismiss",
                },
            ],
            metadata: {
                source: "mock_plan_approval",
            },
        };
        this.pendingPermissions.set(request.id, request);
        this.emit({
            type: "permission_requested",
            provider: this.provider,
            request,
            turnId: turn.turnId,
        });
    }
    emitQuestionPromptTurn(turn, questionPrompt) {
        if (this.activeTurn !== turn) {
            return;
        }
        this.clearTurnTimer(turn);
        this.emitTurnStarted(turn);
        const request = {
            id: `mock-questions-${turn.turnId}`,
            provider: this.provider,
            name: "MockQuestions",
            kind: "question",
            title: "Questions",
            input: {
                questions: questionPrompt.questions,
            },
            metadata: {
                source: "mock_questions",
            },
        };
        this.pendingPermissions.set(request.id, request);
        this.emit({
            type: "permission_requested",
            provider: this.provider,
            request,
            turnId: turn.turnId,
        });
    }
    emitStressTurn(turn, stress) {
        if (this.activeTurn !== turn) {
            return;
        }
        this.clearTurnTimer(turn);
        this.emitTurnStarted(turn);
        for (let index = 0; index < stress.count; index += 1) {
            let item;
            if (stress.tools) {
                item = {
                    type: "tool_call",
                    callId: `${turn.turnId}:stress:${index}`,
                    name: "Bash",
                    status: "completed",
                    error: null,
                    detail: { type: "shell", command: `echo stress-${index}`, output: `stress-${index}` },
                };
            }
            else if (stress.coalesced) {
                item = {
                    type: "assistant_message",
                    text: `stress-update-${index}`,
                    messageId: turn.assistantMessageId,
                };
            }
            else {
                item = {
                    type: "todo",
                    items: [{ text: `stress-update-${index}`, completed: index % 2 === 0 }],
                };
            }
            this.emitTimeline(turn.turnId, item);
        }
        if (stress.tools)
            this.emitTimeline(turn.turnId, {
                type: "assistant_message",
                text: `Completed ${stress.count} commands`,
                messageId: turn.assistantMessageId,
            });
        this.activeTurn = null;
        const usage = {
            inputTokens: 1,
            outputTokens: stress.count,
            contextWindowUsedTokens: stress.count,
            contextWindowMaxTokens: 128000,
        };
        this.emit({
            type: "turn_completed",
            provider: this.provider,
            turnId: turn.turnId,
            usage,
        });
        turn.resolve({
            sessionId: this.id,
            finalText: "Synthetic agent stream stress complete",
            usage,
            timeline: [],
            canceled: false,
        });
    }
    emitLargePayloadTurn(turn, largePayload) {
        if (this.activeTurn !== turn) {
            return;
        }
        this.clearTurnTimer(turn);
        this.emitTurnStarted(turn);
        const payload = buildRepeatedPayload(largePayload.bytes, largePayload.kind);
        if (largePayload.kind === "diff") {
            this.emitTimeline(turn.turnId, createToolCall({
                callId: `${turn.turnId}:edit:large`,
                name: "edit",
                status: "completed",
                detail: {
                    type: "edit",
                    filePath: "src/large-diff.ts",
                    unifiedDiff: `diff --git a/src/large-diff.ts b/src/large-diff.ts\n${payload}`,
                },
            }));
        }
        else if (largePayload.kind === "file") {
            this.emitTimeline(turn.turnId, createToolCall({
                callId: `${turn.turnId}:read:large`,
                name: "read",
                status: "completed",
                detail: {
                    type: "read",
                    filePath: "src/large-file.txt",
                    content: payload,
                },
            }));
        }
        else {
            const imageBytes = Buffer.from(ONE_PIXEL_PNG_BASE64, "base64");
            const imagePayload = Buffer.concat([
                imageBytes,
                Buffer.alloc(Math.max(0, largePayload.bytes - imageBytes.length)),
            ]).toString("base64");
            this.emitTimeline(turn.turnId, {
                type: "assistant_message",
                text: `![Synthetic image](data:image/png;base64,${imagePayload})`,
                messageId: turn.assistantMessageId,
            });
        }
        this.activeTurn = null;
        const usage = {
            inputTokens: 1,
            outputTokens: largePayload.bytes,
            contextWindowUsedTokens: largePayload.bytes,
            contextWindowMaxTokens: 128000,
        };
        this.emit({
            type: "turn_completed",
            provider: this.provider,
            turnId: turn.turnId,
            usage,
        });
        turn.resolve({
            sessionId: this.id,
            finalText: "Synthetic large payload complete",
            usage,
            timeline: [],
            canceled: false,
        });
    }
    tick(turn) {
        if (this.activeTurn !== turn) {
            return;
        }
        this.clearTurnTimer(turn);
        this.emitTurnStarted(turn);
        const elapsedMs = Date.now() - turn.startedAt;
        if (elapsedMs >= turn.durationMs) {
            this.finishTurn(turn);
            return;
        }
        const eventsThisTick = turn.burst ? nextBurstSize(turn.burst, turn.burstIndex) : 1;
        turn.burstIndex += 1;
        for (let emitted = 0; emitted < eventsThisTick; emitted += 1) {
            if (turn.queue.length === 0) {
                turn.cycle += 1;
                turn.queue = turn.burst
                    ? buildBurstyStreamQueue(turn.cycle)
                    : buildCycleQueue(turn.turnId, turn.cycle);
            }
            const event = turn.queue.shift();
            if (!event) {
                break;
            }
            this.dispatchCycleEvent(turn, event);
        }
        this.schedule(turn, turn.burst ? turn.burst.gapMs : turn.intervalMs);
    }
    dispatchCycleEvent(turn, event) {
        switch (event.kind) {
            case "assistant_token": {
                turn.emittedTokens += 1;
                this.emitTimeline(turn.turnId, {
                    type: "assistant_message",
                    text: event.text,
                    messageId: turn.assistantMessageId,
                });
                return;
            }
            case "reasoning_token": {
                turn.emittedTokens += 1;
                this.emitTimeline(turn.turnId, {
                    type: "reasoning",
                    text: event.text,
                });
                return;
            }
            case "tool_running":
            case "tool_completed": {
                this.emitTimeline(turn.turnId, createToolCall({
                    callId: event.callId,
                    name: event.name,
                    status: event.kind === "tool_running" ? "running" : "completed",
                    detail: event.detail,
                }));
                return;
            }
            case "usage": {
                this.emit({
                    type: "usage_updated",
                    provider: this.provider,
                    turnId: turn.turnId,
                    usage: {
                        inputTokens: turn.cycle * 32,
                        outputTokens: turn.emittedTokens,
                        contextWindowUsedTokens: turn.emittedTokens * 2,
                        contextWindowMaxTokens: 128000,
                    },
                });
                return;
            }
        }
    }
    finishTurn(turn) {
        this.emitTimeline(turn.turnId, {
            type: "assistant_message",
            text: "\n\n_(end of synthetic stream)_\n",
            messageId: turn.assistantMessageId,
        });
        this.finishTurnWithText(turn, "Synthetic load test complete");
    }
    finishTurnWithText(turn, finalText) {
        this.activeTurn = null;
        const usage = {
            inputTokens: turn.cycle * 32,
            outputTokens: turn.emittedTokens,
            contextWindowUsedTokens: turn.emittedTokens * 2,
            contextWindowMaxTokens: 128000,
        };
        this.emit({
            type: "turn_completed",
            provider: this.provider,
            turnId: turn.turnId,
            usage,
        });
        turn.resolve({
            sessionId: this.id,
            finalText,
            usage,
            timeline: [],
            canceled: false,
        });
    }
    emitTimeline(turnId, item) {
        this.emit({
            type: "timeline",
            provider: this.provider,
            turnId,
            item,
        });
    }
    emit(event) {
        this.remember(event);
        for (const listener of this.listeners) {
            try {
                listener(event);
            }
            catch (error) {
                this.logger?.warn({ err: error }, "Mock load-test listener failed");
            }
        }
    }
    remember(event) {
        this.history.push(event);
    }
    keepFirstUserMessageHistory() {
        const nextHistory = [];
        for (const event of this.history) {
            if (event.type === "timeline" && event.item.type === "user_message") {
                nextHistory.push(event);
                break;
            }
        }
        this.history.length = 0;
        this.history.push(...nextHistory);
    }
    clearTurnTimer(turn) {
        if (!turn.timer) {
            return;
        }
        clearTimeout(turn.timer);
        turn.timer = null;
    }
}
//# sourceMappingURL=mock-load-test-agent.js.map