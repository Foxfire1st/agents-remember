// Synthetic stand-in for Paseo 0.12.0's @getpaseo/protocol/dist/provider-manifest.js:
// the shapes rule 1 (MIK-R216) reads, written by hand, not copied source.
//
// Each definition carries its own `modes` array, and those members have `id`s of
// their own. They are not provider ids, and the discovery's depth scan must not
// take them for any: `default`, `auto`, `auto-review`, `full`, `build` and the
// Copilot session-mode URL below are all mode ids.
export const AGENT_PROVIDER_DEFINITIONS = [
    {
        id: "claude",
        label: "Claude",
        description: "Anthropic's multi-tool assistant with MCP support",
        defaultModeId: "auto",
        modes: [
            { id: "default", label: "Default", isUnattended: false },
            { id: "auto", label: "Auto", isUnattended: true },
        ],
        voice: {
            enabled: true,
            defaultModeId: "default",
            defaultModel: "haiku",
        },
    },
    {
        id: "codex",
        label: "Codex",
        defaultModeId: "auto-review",
        modes: [
            { id: "auto-review", label: "Auto Review" },
            { id: "full-auto", label: "Full Auto" },
        ],
    },
    {
        id: "copilot",
        label: "Copilot",
        description: "GitHub Copilot via Agent Client Protocol",
        defaultModeId: "https://agentclientprotocol.com/protocol/session-modes#agent",
        modes: [
            /* The session-mode URL is an id the manifest carries, and not a provider. */
            { id: "https://agentclientprotocol.com/protocol/session-modes#agent", label: "Agent" },
        ],
    },
    {
        id: "opencode",
        label: "OpenCode",
        // No static default: OpenCode users can rename any agent, "build" included.
        defaultModeId: null,
        modes: [{ id: "build", label: "Build" }],
    },
    {
        id: "pi",
        label: "Pi",
        defaultModeId: null,
        modes: [],
    },
    {
        id: "omp",
        label: "Oh My Pi",
        enabledByDefault: false,
        defaultModeId: "full",
        modes: [{ id: "full", label: "Full", isUnattended: true }],
    },
];
export const DEV_AGENT_PROVIDER_DEFINITIONS = [
    {
        id: "mock",
        label: "Mock Load Test",
        defaultModeId: "load-test",
        modes: [{ id: "load-test", label: "Load Test" }],
    },
];
export const BUILTIN_PROVIDER_IDS = AGENT_PROVIDER_DEFINITIONS.map((d) => d.id);
export const AGENT_PROVIDER_IDS = BUILTIN_PROVIDER_IDS;
export function getAgentProviderDefinition(provider, definitions = [
    ...AGENT_PROVIDER_DEFINITIONS,
    ...DEV_AGENT_PROVIDER_DEFINITIONS,
]) {
    const definition = definitions.find((entry) => entry.id === provider);
    if (!definition) {
        throw new Error(`Unknown agent provider: ${provider}`);
    }
    return definition;
}
