// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/agent/provider-registry.js: the shapes rule
// 1 (MIK-R216) reads, written by hand, not copied source.
import { isDefaultAgentCreateConfigUnattended, resolveDefaultAgentCreateConfig, } from "./create-agent-mode.js";
import { ClaudeAgentClient } from "./providers/claude/agent.js";
import { CodexAppServerAgentClient } from "./providers/codex-app-server-agent.js";
import { CopilotACPAgentClient } from "./providers/copilot-acp-agent.js";
import { CursorACPAgentClient } from "./providers/cursor-acp-agent.js";
import { GenericACPAgentClient } from "./providers/generic-acp-agent.js";
import { KimiACPAgentClient } from "./providers/kimi-acp-agent.js";
import { KiroACPAgentClient } from "./providers/kiro-acp-agent.js";
import { OpenCodeRuntimeClient } from "./providers/opencode/runtime-client.js";
import { OmpAgentClient } from "./providers/omp/agent.js";
import { PiRpcAgentClient } from "./providers/pi/agent.js";
import { TraeACPAgentClient } from "./providers/trae-acp-agent.js";
import { MockLoadTestAgentClient } from "./providers/mock-load-test-agent.js";
import { MockSlowProviderClient } from "./providers/mock-slow-provider.js";
import { AGENT_PROVIDER_DEFINITIONS, BUILTIN_PROVIDER_IDS, } from "@getpaseo/protocol/provider-manifest";
export { AGENT_PROVIDER_DEFINITIONS };
const PROVIDER_CONTRACTS = {
    claude: { supportsExactMcpPreapproval: true },
    codex: { supportsExactMcpPreapproval: true },
    opencode: { supportsExactMcpPreapproval: true },
};
const PROVIDER_CLIENT_FACTORIES = {
    claude: (logger, runtimeSettings) => new ClaudeAgentClient({
        logger,
        runtimeSettings,
    }),
    codex: (logger, runtimeSettings, options) => new CodexAppServerAgentClient(logger, runtimeSettings, {
        workspaceGitService: options?.workspaceGitService,
        customProvider: options?.customProvider,
    }),
    copilot: (logger, runtimeSettings) => new CopilotACPAgentClient({
        logger,
        runtimeSettings,
    }),
    cursor: (logger, runtimeSettings) => new CursorACPAgentClient({
        logger,
        command: getCursorACPCommand(runtimeSettings),
        env: runtimeSettings?.env,
    }),
    opencode: (logger, runtimeSettings, options) => new OpenCodeRuntimeClient(logger, runtimeSettings, {
        managedProcesses: options?.managedProcesses,
        bridge: options?.openCodeBridge,
    }),
    pi: (logger, runtimeSettings) => new PiRpcAgentClient({
        logger,
        runtimeSettings,
    }),
    omp: (logger, runtimeSettings, options) => new OmpAgentClient({
        logger,
        runtimeSettings,
        runtime: options?.ompRuntime,
    }),
    mock: (logger) => new MockLoadTestAgentClient(logger),
    "mock-slow": () => new MockSlowProviderClient(),
};
function getCursorACPCommand(runtimeSettings) {
    if (runtimeSettings?.command?.mode === "replace") {
        return runtimeSettings.command.argv;
    }
    return ["cursor-agent", "acp"];
}
export function getProviderClientFactory(provider) {
    const factory = PROVIDER_CLIENT_FACTORIES[provider];
    if (!factory) {
        throw new Error(`No provider client factory registered for '${provider}'`);
    }
    return factory;
}
// addDerivedProviders builds the client for a configured provider: the generic ACP
// client, or the one its id is special-cased to.
function addDerivedProviders(resolvedProviders, providerOverrides) {
    for (const [providerId, override] of Object.entries(providerOverrides)) {
        if (resolvedProviders.has(providerId) || BUILTIN_PROVIDER_IDS.includes(providerId)) {
            continue;
        }
        if (!override.extends) {
            continue;
        }
        if (override.extends === "acp") {
            resolvedProviders.set(providerId, {
                createBaseClient: (logger) => {
                    const acpOptions = { logger, command: override.command, providerId };
                    if (providerId === "cursor") {
                        return new CursorACPAgentClient(acpOptions);
                    }
                    if (providerId === "kimi") {
                        return new KimiACPAgentClient(acpOptions);
                    }
                    if (providerId === "kiro") {
                        return new KiroACPAgentClient(acpOptions);
                    }
                    if (providerId === "traecli") {
                        return new TraeACPAgentClient(acpOptions);
                    }
                    return new GenericACPAgentClient(acpOptions);
                },
            });
            continue;
        }
        const baseProviderId = override.extends;
        const baseProvider = resolvedProviders.get(baseProviderId);
        if (!baseProvider) {
            throw new Error(`Custom provider '${providerId}' extends unknown provider '${baseProviderId}'`);
        }
        const baseFactory = getProviderClientFactory(baseProviderId);
        resolvedProviders.set(providerId, {
            derivedFromProviderId: baseProviderId,
            createBaseClient: (logger) => baseFactory(logger, baseProvider.runtimeSettings, {}),
        });
    }
}
export function buildProviderRegistry(logger, options) {
    const resolvedProviders = new Map();
    const providerOverrides = options?.providerOverrides ?? {};
    addDerivedProviders(resolvedProviders, providerOverrides);
    for (const [provider, definition] of Object.entries(options?.pluginProviders ?? {})) {
        resolvedProviders.set(provider, definition);
    }
    return Object.fromEntries([...resolvedProviders.entries()].map(([provider, resolved]) => [
        provider,
        resolved,
    ]));
}
export function getProviderIds(registry) {
    return Object.keys(registry);
}
export { isDefaultAgentCreateConfigUnattended, resolveDefaultAgentCreateConfig };
