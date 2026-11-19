import { isDefaultAgentCreateConfigUnattended, resolveDefaultAgentCreateConfig, } from "./create-agent-mode.js";
import { normalizeAgentModelDefinition } from "./agent-sdk-types.js";
import { runProviderRefreshActivity } from "./provider-refresh-deadline.js";
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
import { convertLegacyOptions } from "./option-controls.js";
import { ToolPolicyUnsupportedError, mergeProviderOptions } from "./provider-options.js";
import { AGENT_PROVIDER_DEFINITIONS, BUILTIN_PROVIDER_IDS, DEV_AGENT_PROVIDER_DEFINITIONS, getAgentProviderDefinition, } from "@getpaseo/protocol/provider-manifest";
function isNonEmptyStringArray(value) {
    return value.length > 0;
}
export { AGENT_PROVIDER_DEFINITIONS, getAgentProviderDefinition };
const PROVIDER_CONTRACTS = {
    claude: { supportsExactMcpPreapproval: true },
    codex: { supportsExactMcpPreapproval: true },
    opencode: { supportsExactMcpPreapproval: true },
};
const UNSUPPORTED_PROVIDER_CONTRACT = {
    supportsExactMcpPreapproval: false,
};
const HUB_E2E_PROVIDER_ID = "hub-e2e";
const HUB_E2E_MCP_SERVER = "hub";
const HUB_E2E_TOOL_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/u;
// The cross-repository Hub harness owns this synthetic provider ID. It exercises the production
// registry path without extending exact-preapproval support to user-defined ACP providers.
const HUB_E2E_PROVIDER_CONTRACT = {
    supportsExactMcpPreapproval: true,
    applyToolPolicy: (provider, toolPolicy) => {
        for (const grant of toolPolicy.preapproved) {
            if (grant.kind !== "mcp" ||
                grant.server !== HUB_E2E_MCP_SERVER ||
                !HUB_E2E_TOOL_NAME.test(grant.tool)) {
                throw new ToolPolicyUnsupportedError(provider, `Provider '${provider}' accepts only exact MCP tool grants for the injected '${HUB_E2E_MCP_SERVER}' server`);
            }
        }
        return {
            preapproved: toolPolicy.preapproved.map((grant) => ({ ...grant })),
        };
    },
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
    if (runtimeSettings?.command?.mode === "replace" &&
        isNonEmptyStringArray(runtimeSettings.command.argv)) {
        return runtimeSettings.command.argv;
    }
    return ["cursor-agent", "acp"];
}
function getProviderClientFactory(provider) {
    const factory = PROVIDER_CLIENT_FACTORIES[provider];
    if (!factory) {
        throw new Error(`No provider client factory registered for '${provider}'`);
    }
    return factory;
}
function toRuntimeSettings(override) {
    if (!override?.command && !override?.env && !override?.disallowedTools) {
        return undefined;
    }
    return {
        command: override.command
            ? {
                mode: "replace",
                argv: override.command,
            }
            : undefined,
        env: override.env,
        disallowedTools: override.disallowedTools,
    };
}
function mergeRuntimeSettings(base, override) {
    if (!base && !override) {
        return undefined;
    }
    return {
        command: override?.command ?? base?.command,
        env: base?.env || override?.env
            ? {
                ...base?.env,
                ...override?.env,
            }
            : undefined,
        disallowedTools: base?.disallowedTools || override?.disallowedTools
            ? [...(base?.disallowedTools ?? []), ...(override?.disallowedTools ?? [])]
            : undefined,
    };
}
function applyOverrideToDefinition(definition, override) {
    if (!override) {
        return definition;
    }
    return {
        ...definition,
        label: override.label ?? definition.label,
        description: override.description ?? definition.description,
    };
}
function createDerivedDefinition(providerId, baseDefinition, override) {
    if (!override.label) {
        throw new Error(`Custom provider '${providerId}' requires a label`);
    }
    return {
        ...baseDefinition,
        id: providerId,
        label: override.label,
        description: override.description ?? baseDefinition.description,
    };
}
function mapPersistenceHandle(provider, handle) {
    if (!handle) {
        return null;
    }
    return {
        ...handle,
        provider,
    };
}
function mapRuntimeInfo(provider, runtimeInfo) {
    return {
        ...runtimeInfo,
        provider,
    };
}
function mapStreamEvent(provider, event) {
    return {
        ...event,
        provider,
    };
}
function mapModel(provider, model) {
    return normalizeAgentModelDefinition({ ...model, provider });
}
function resolveConfiguredModels(provider, client, models) {
    return models.map((model) => {
        const mapped = mapModel(provider, model);
        return client.resolveConfiguredModel?.(mapped) ?? mapped;
    });
}
function mergeModels(provider, profileModels, additionalModels, runtimeModels, options) {
    const baseModels = runtimeModels.map((model) => mapModel(provider, model));
    if (profileModels.length > 0 && options?.profileModelsAreAdditive !== true) {
        return mergeModelAdditions(provider, profileModels.map((model) => mapModel(provider, model)), additionalModels);
    }
    return mergeModelAdditions(provider, baseModels, [...profileModels, ...additionalModels]);
}
function mergeModelAdditions(provider, baseModels, modelAdditions) {
    if (modelAdditions.length === 0) {
        return baseModels;
    }
    const mergedModels = [...baseModels];
    let hasAdditionalDefault = false;
    for (const model of modelAdditions) {
        const additionalModel = mapModel(provider, model);
        hasAdditionalDefault || (hasAdditionalDefault = additionalModel.isDefault === true);
        const existingIndex = mergedModels.findIndex((candidate) => candidate.id === model.id);
        if (existingIndex === -1) {
            mergedModels.push(additionalModel);
            continue;
        }
        const existingModel = mergedModels[existingIndex];
        const explicitlyEnablesCompatibilityModel = existingModel?.isSelectable === false && additionalModel.isSelectable === undefined;
        mergedModels[existingIndex] = {
            ...existingModel,
            ...additionalModel,
            ...(explicitlyEnablesCompatibilityModel ? { isSelectable: true } : {}),
        };
    }
    if (!hasAdditionalDefault) {
        return mergedModels;
    }
    const additionalDefaultIds = new Set(modelAdditions.filter((model) => model.isDefault === true).map((model) => model.id));
    return mergedModels.map((model) => additionalDefaultIds.has(model.id) ? model : Object.assign({}, model, { isDefault: false }));
}
export function wrapSessionProvider(provider, inner) {
    return {
        provider,
        get id() {
            return inner.id;
        },
        get capabilities() {
            return inner.capabilities;
        },
        get initialTimeline() {
            return inner.initialTimeline;
        },
        get controls() {
            return inner.controls;
        },
        get openOptionsPatch() {
            return inner.openOptionsPatch;
        },
        usageSession: inner.usageSession?.bind(inner),
        run: (prompt, options) => inner.run(prompt, options),
        startTurn: (prompt, options) => inner.startTurn(prompt, options),
        steerActiveTurn: inner.steerActiveTurn?.bind(inner),
        subscribe: (callback) => inner.subscribe((event) => callback(mapStreamEvent(provider, event))),
        async *streamHistory() {
            for await (const event of inner.streamHistory()) {
                yield mapStreamEvent(provider, event);
            }
        },
        getRuntimeInfo: async () => mapRuntimeInfo(provider, await inner.getRuntimeInfo()),
        getAvailableModes: () => inner.getAvailableModes(),
        getCurrentMode: () => inner.getCurrentMode(),
        setMode: (modeId) => inner.setMode(modeId),
        getPendingPermissions: () => inner.getPendingPermissions(),
        respondToPermission: (requestId, response) => inner.respondToPermission(requestId, response),
        describePersistence: () => mapPersistenceHandle(provider, inner.describePersistence()),
        interrupt: () => inner.interrupt(),
        stopBackgroundWork: inner.stopBackgroundWork?.bind(inner),
        close: () => inner.close(),
        canStopRuntime: inner.canStopRuntime?.bind(inner),
        listCommands: inner.listCommands?.bind(inner),
        setModel: inner.setModel?.bind(inner),
        setThinkingOption: inner.setThinkingOption?.bind(inner),
        applyOptions: inner.applyOptions?.bind(inner),
        revertConversation: inner.revertConversation?.bind(inner),
        revertFiles: inner.revertFiles?.bind(inner),
        revertBoth: inner.revertBoth?.bind(inner),
        tryHandleOutOfBand: inner.tryHandleOutOfBand?.bind(inner),
    };
}
function wrapClientProvider(provider, inner, profileModels, additionalModels, profileModelsAreAdditive, configuredOptions) {
    const listImportableSessions = inner.listImportableSessions?.bind(inner);
    const importSession = inner.importSession?.bind(inner);
    const listControls = inner.listControls?.bind(inner);
    const archiveNativeSession = inner.archiveNativeSession?.bind(inner);
    const unarchiveNativeSession = inner.unarchiveNativeSession?.bind(inner);
    const configuredModelIds = [...profileModels, ...additionalModels].map((model) => model.id);
    // Converted where it is used, so a malformed configured default fails the call that reads it
    // rather than the registry that holds every provider.
    const configuredDefaults = () => convertLegacyOptions({ provider: inner.provider, options: configuredOptions });
    const withDefaults = (own) => mergeProviderOptions(configuredDefaults(), own);
    return {
        provider,
        get capabilities() {
            return inner.capabilities;
        },
        shutdown: inner.shutdown?.bind(inner),
        createSession: async (config, launchContext, options) => wrapSessionProvider(provider, await inner.createSession({
            ...config,
            provider: inner.provider,
            providerOptions: withDefaults(config.providerOptions),
        }, launchContext, { ...options, configuredModelIds })),
        resumeSession: async (handle, overrides, launchContext, options) => wrapSessionProvider(provider, await inner.resumeSession({
            ...handle,
            provider: inner.provider,
        }, {
            ...overrides,
            provider: inner.provider,
            providerOptions: withDefaults(overrides?.providerOptions),
        }, launchContext, { ...options, configuredModelIds })),
        fetchCatalog: async (options, context) => {
            const catalog = await inner.fetchCatalog({ ...options, providerOptions: configuredDefaults() }, context);
            return {
                ...catalog,
                models: mergeModels(provider, profileModels, additionalModels, catalog.models, {
                    profileModelsAreAdditive,
                }),
                modes: catalog.modes,
            };
        },
        resolveDefaultModeId: inner.resolveDefaultModeId
            ? async ({ config, env, signal }) => await inner.resolveDefaultModeId?.({
                config: {
                    ...config,
                    provider: inner.provider,
                    providerOptions: withDefaults(config.providerOptions),
                },
                env,
                signal,
            })
            : undefined,
        resolveCreateConfig: inner.resolveCreateConfig?.bind(inner),
        resolveConfiguredModel: inner.resolveConfiguredModel?.bind(inner),
        isCreateConfigUnattended: inner.isCreateConfigUnattended?.bind(inner),
        effectiveOptions: (own) => withDefaults(own) ?? {},
        listControls: listControls
            ? async (config) => await listControls({
                ...config,
                provider: inner.provider,
                providerOptions: withDefaults(config.providerOptions),
            })
            : undefined,
        listImportableSessions: listImportableSessions
            ? async (options) => await listImportableSessions({ ...options, providerOptions: configuredDefaults() })
            : undefined,
        importSession: importSession
            ? async (input, context) => {
                const imported = await importSession(input, {
                    ...context,
                    config: {
                        ...context.config,
                        provider: inner.provider,
                        providerOptions: withDefaults(context.config.providerOptions),
                    },
                    storedConfig: {
                        ...context.storedConfig,
                        provider: inner.provider,
                    },
                });
                const persistence = mapPersistenceHandle(provider, imported.persistence);
                if (!persistence) {
                    throw new Error(`Provider '${provider}' import did not return persistence`);
                }
                return {
                    ...imported,
                    session: wrapSessionProvider(provider, imported.session),
                    config: {
                        ...imported.config,
                        provider,
                        providerOptions: context.storedConfig.providerOptions,
                    },
                    persistence,
                };
            }
            : undefined,
        archiveNativeSession: archiveNativeSession
            ? async (handle) => await archiveNativeSession({ ...handle, provider: inner.provider })
            : undefined,
        unarchiveNativeSession: unarchiveNativeSession
            ? async (handle) => await unarchiveNativeSession({ ...handle, provider: inner.provider })
            : undefined,
        getCatalogCacheKey: inner.getCatalogCacheKey?.bind(inner),
        isAvailable: (signal, options) => inner.isAvailable(signal, options),
        getDiagnostic: inner.getDiagnostic?.bind(inner),
    };
}
function createRegistryEntry(logger, provider, resolved) {
    const modelClient = createResolvedProviderClient(logger, provider, resolved);
    const profileModels = resolveConfiguredModels(provider, modelClient, resolved.profileModels);
    const additionalModels = resolveConfiguredModels(provider, modelClient, resolved.additionalModels);
    const hasReplacementModels = profileModels.length > 0 && !resolved.profileModelsAreAdditive;
    const replacementModels = hasReplacementModels
        ? profileModels.map((model) => mapModel(provider, model))
        : [];
    const decorateModes = (modes) => modes.map((mode) => {
        if (mode.icon && mode.colorTier)
            return mode;
        const definitionMode = resolved.definition.modes.find((d) => d.id === mode.id);
        if (!definitionMode)
            return mode;
        return Object.assign({}, mode, {
            icon: mode.icon ?? definitionMode.icon,
            colorTier: mode.colorTier ?? definitionMode.colorTier,
        });
    });
    const hasStaticModes = resolved.definition.modes.length > 0;
    const { createBaseClient: _createBaseClient, contract: _contract, ...configuration } = resolved;
    return {
        ...resolved.definition,
        configuration,
        enabled: resolved.enabled,
        derivedFromProviderId: resolved.derivedFromProviderId,
        supportsExactMcpPreapproval: resolved.contract.supportsExactMcpPreapproval,
        applyToolPolicy: (config, toolPolicy) => {
            if (toolPolicy && !resolved.contract.supportsExactMcpPreapproval) {
                throw new ToolPolicyUnsupportedError(provider);
            }
            return {
                ...config,
                toolPolicy: toolPolicy
                    ? (resolved.contract.applyToolPolicy?.(provider, toolPolicy) ?? toolPolicy)
                    : undefined,
            };
        },
        createClient: (providerLogger) => createResolvedProviderClient(providerLogger, provider, resolved),
        resolveCreateConfig: modelClient.resolveCreateConfig ?? resolveDefaultAgentCreateConfig,
        isCreateConfigUnattended: modelClient.isCreateConfigUnattended ?? isDefaultAgentCreateConfigUnattended,
        fetchCatalog: async (options, client, context) => {
            const catalogClient = client ?? modelClient;
            if (hasReplacementModels) {
                // Replacement models skip runtime model discovery, but additionalModels
                // must still be merged on top. If modes are dynamic, probe for modes via
                // the single catalog API; otherwise use static/empty modes with no runtime.
                const models = mergeModelAdditions(provider, replacementModels, additionalModels);
                if (hasStaticModes) {
                    const defaultModeId = await runProviderRefreshActivity(context, "default-mode", async () => await catalogClient.resolveDefaultModeId?.({
                        config: {
                            provider,
                            cwd: options.scope === "workspace" ? options.cwd : process.cwd(),
                        },
                        signal: context?.signal,
                    }));
                    return {
                        models,
                        modes: decorateModes(resolved.definition.modes),
                        defaultModeId,
                    };
                }
                const catalog = await catalogClient.fetchCatalog(options, context);
                return { ...catalog, models, modes: decorateModes(catalog.modes) };
            }
            const catalog = await catalogClient.fetchCatalog(options, context);
            return {
                ...catalog,
                models: mergeModels(provider, profileModels, additionalModels, catalog.models, {
                    profileModelsAreAdditive: resolved.profileModelsAreAdditive,
                }),
                modes: decorateModes(catalog.modes),
            };
        },
    };
}
function createResolvedProviderClient(logger, provider, resolved) {
    const inner = resolved.createBaseClient(logger);
    const profileModels = resolveConfiguredModels(provider, inner, resolved.profileModels);
    const additionalModels = resolveConfiguredModels(provider, inner, resolved.additionalModels);
    return wrapClientProvider(provider, inner, profileModels, additionalModels, resolved.profileModelsAreAdditive, resolved.providerOptions);
}
function resolveRegisteredProvider(input) {
    const { definition, override, contract } = input;
    const runtimeSettings = mergeRuntimeSettings(input.runtimeSettings, toRuntimeSettings(override));
    return {
        definition: applyOverrideToDefinition(definition, override),
        runtimeSettings,
        profileModels: override?.models ?? [],
        additionalModels: override?.additionalModels ?? [],
        profileModelsAreAdditive: false,
        enabled: override?.enabled ?? definition.enabledByDefault ?? true,
        derivedFromProviderId: null,
        providerOptions: configuredProviderOptions(override),
        createBaseClient: (logger) => input.createClient(logger, runtimeSettings),
        contract,
    };
}
function buildResolvedBuiltinProviders(providerOverrides, runtimeSettings, options, isDev) {
    const resolvedProviders = new Map();
    const definitions = isDev
        ? [...AGENT_PROVIDER_DEFINITIONS, ...DEV_AGENT_PROVIDER_DEFINITIONS]
        : AGENT_PROVIDER_DEFINITIONS;
    for (const definition of definitions) {
        const override = providerOverrides[definition.id];
        const factory = getProviderClientFactory(definition.id);
        resolvedProviders.set(definition.id, resolveRegisteredProvider({
            definition,
            override,
            runtimeSettings: runtimeSettings?.[definition.id],
            createClient: (logger, settings) => factory(logger, settings, {
                workspaceGitService: options.workspaceGitService,
                managedProcesses: options.managedProcesses,
                ompRuntime: options.ompRuntime,
                openCodeBridge: options.openCodeBridge,
            }),
            contract: PROVIDER_CONTRACTS[definition.id] ?? UNSUPPORTED_PROVIDER_CONTRACT,
        }));
    }
    return resolvedProviders;
}
function addDerivedProviders(resolvedProviders, providerOverrides, options) {
    for (const [providerId, override] of Object.entries(providerOverrides)) {
        if (resolvedProviders.has(providerId) || BUILTIN_PROVIDER_IDS.includes(providerId)) {
            continue;
        }
        if (!override.extends) {
            // Plugin overrides can precede registration (including disabled or removed plugins).
            continue;
        }
        if (override.extends === "acp") {
            if (!override.command || !isNonEmptyStringArray(override.command)) {
                throw new Error(`ACP provider '${providerId}' requires a command`);
            }
            // Capture command in const for closure - TypeScript can't track type refinement inside closures
            const command = override.command;
            resolvedProviders.set(providerId, {
                definition: createDerivedDefinition(providerId, {
                    id: providerId,
                    label: override.label ?? providerId,
                    description: override.description ?? "Custom ACP provider",
                    defaultModeId: null,
                    modes: [],
                }, override),
                runtimeSettings: toRuntimeSettings(override),
                profileModels: override.models ?? [],
                additionalModels: override.additionalModels ?? [],
                profileModelsAreAdditive: false,
                enabled: override.enabled !== false,
                derivedFromProviderId: null,
                providerOptions: configuredProviderOptions(override),
                createBaseClient: (logger) => {
                    const acpOptions = {
                        logger,
                        command,
                        env: override.env,
                        providerId,
                        label: override.label ?? providerId,
                    };
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
                contract: providerId === HUB_E2E_PROVIDER_ID
                    ? HUB_E2E_PROVIDER_CONTRACT
                    : UNSUPPORTED_PROVIDER_CONTRACT,
            });
            continue;
        }
        const baseProviderId = override.extends;
        const baseProvider = resolvedProviders.get(baseProviderId);
        if (!baseProvider) {
            throw new Error(`Custom provider '${providerId}' extends unknown provider '${baseProviderId}'`);
        }
        const mergedRuntimeSettings = mergeRuntimeSettings(baseProvider.runtimeSettings, toRuntimeSettings(override));
        const baseDefinition = baseProvider.definition;
        const baseFactory = getProviderClientFactory(baseProviderId);
        const providerOptions = configuredProviderOptions(override) ?? baseProvider.providerOptions;
        resolvedProviders.set(providerId, {
            definition: createDerivedDefinition(providerId, baseDefinition, override),
            runtimeSettings: mergedRuntimeSettings,
            profileModels: override.models ?? [],
            additionalModels: override.additionalModels ?? [],
            profileModelsAreAdditive: false,
            enabled: override.enabled !== false,
            derivedFromProviderId: baseProviderId,
            providerOptions,
            createBaseClient: (logger) => baseFactory(logger, mergedRuntimeSettings, {
                managedProcesses: options.managedProcesses,
                openCodeBridge: options.openCodeBridge,
                customProvider: {
                    id: providerId,
                    label: override.label ?? providerId,
                    extends: baseProviderId,
                },
            }),
            contract: baseProvider.contract,
        });
    }
}
export function buildProviderRegistry(logger, options) {
    const runtimeSettings = options?.runtimeSettings;
    const providerOverrides = options?.providerOverrides ?? {};
    const resolvedProviders = buildResolvedBuiltinProviders(providerOverrides, runtimeSettings, {
        workspaceGitService: options?.workspaceGitService,
        managedProcesses: options?.managedProcesses,
        ompRuntime: options?.ompRuntime,
        openCodeBridge: options?.openCodeBridge,
    }, options?.isDev === true);
    for (const [provider, definition] of Object.entries(options?.pluginProviders ?? {})) {
        if (resolvedProviders.has(provider)) {
            throw new Error(`Plugin provider '${provider}' conflicts with a built-in provider`);
        }
        const override = providerOverrides[provider];
        if (definition.requiresConfiguration && !override)
            continue;
        if (override?.extends) {
            logger.warn({ provider }, "Plugin provider shadowed by configured provider");
            continue;
        }
        resolvedProviders.set(provider, resolveRegisteredProvider({
            definition,
            override,
            runtimeSettings: runtimeSettings?.[provider],
            createClient: definition.createClient,
            contract: {
                supportsExactMcpPreapproval: definition.supportsExactMcpPreapproval,
            },
        }));
    }
    addDerivedProviders(resolvedProviders, providerOverrides, {
        managedProcesses: options?.managedProcesses,
        openCodeBridge: options?.openCodeBridge,
    });
    return Object.fromEntries([...resolvedProviders.entries()].map(([provider, resolved]) => [
        provider,
        createRegistryEntry(logger, provider, resolved),
    ]));
}
export function getProviderIds(registry) {
    return Object.keys(registry);
}
// Deprecated: Use buildProviderRegistry instead
export const PROVIDER_REGISTRY = null;
export function createAllClients(logger, options) {
    return createClientsFromRegistry(buildProviderRegistry(logger, options), logger);
}
export function createClientsFromRegistry(registry, logger) {
    return Object.fromEntries(Object.entries(registry).map(([provider, definition]) => [
        provider,
        definition.createClient(logger),
    ]));
}
export async function shutdownProviders(logger, options) {
    const clients = createAllClients(logger, options);
    await shutdownAgentClients(Object.values(clients), logger);
}
export async function shutdownAgentClients(clients, logger) {
    await Promise.all(Array.from(clients).map(async (client) => {
        if (!client.shutdown)
            return;
        try {
            await client.shutdown();
        }
        catch (error) {
            logger.warn({ err: error, provider: client.provider }, "Provider client shutdown failed");
        }
    }));
}
function configuredProviderOptions(override) {
    // COMPAT(providerParams): added in v0.10.0, remove after 2027-03-30 once configs use options.
    // options wins as a whole record when both spellings are supplied.
    return override?.options ?? override?.params;
}
//# sourceMappingURL=provider-registry.js.map