function listModes(modes) {
    if (modes === undefined) {
        return "unknown";
    }
    return modes.length > 0 ? modes.join(", ") : "(none)";
}
function isUnattendedCreateConfigParent(parent) {
    return parent.isUnattended;
}
function formatCreateConfigParentMode(parent) {
    return parent.modeId ?? "<none>";
}
function formatCreateConfigParentSource(parent) {
    return `caller (provider '${parent.provider}')`;
}
export function resolveAndValidateCreateAgentMode(input) {
    const { requestedMode, targetProvider, parent, availableModes } = input;
    if (requestedMode !== undefined) {
        if (availableModes !== undefined && !availableModes.includes(requestedMode)) {
            throw new Error(`Invalid mode '${requestedMode}' for provider '${targetProvider}'. Available modes: ${listModes(availableModes)}`);
        }
        return requestedMode;
    }
    if (!parent) {
        if (input.unattended && input.targetUnattendedMode !== undefined) {
            return input.targetUnattendedMode;
        }
        return undefined;
    }
    if (parent.provider === targetProvider) {
        return parent.modeId ?? undefined;
    }
    if ((input.unattended || isUnattendedCreateConfigParent(parent)) &&
        input.targetUnattendedMode !== undefined) {
        return input.targetUnattendedMode;
    }
    if (availableModes?.length === 0) {
        return undefined;
    }
    throw new Error(`cannot inherit mode '${formatCreateConfigParentMode(parent)}' from ${formatCreateConfigParentSource(parent)} for new agent (provider '${targetProvider}'). Pass an explicit mode in settings.modeId. Available modes for '${targetProvider}': ${listModes(availableModes)}`);
}
export function resolveDefaultAgentCreateConfig(input) {
    const availableModeIds = input.availableModes?.map((mode) => mode.id);
    return {
        modeId: resolveAndValidateCreateAgentMode({
            requestedMode: input.requestedMode,
            targetProvider: input.provider,
            parent: input.parent,
            unattended: input.unattended,
            availableModes: availableModeIds,
            targetUnattendedMode: input.availableModes?.find(isUnattendedMode)?.id,
        }),
        providerOptions: input.providerOptions,
    };
}
export function isDefaultAgentCreateConfigUnattended(input) {
    if (input.modeId === null) {
        return false;
    }
    return input.availableModes.some((mode) => mode.id === input.modeId && isUnattendedMode(mode));
}
function isUnattendedMode(mode) {
    return mode.isUnattended === true;
}
//# sourceMappingURL=create-agent-mode.js.map