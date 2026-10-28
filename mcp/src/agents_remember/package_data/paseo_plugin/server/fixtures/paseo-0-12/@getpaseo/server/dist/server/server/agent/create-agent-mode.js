// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/agent/create-agent-mode.js. The registry
// imports it; the discovery never follows it, because no provider class extends a
// class declared here.
export function resolveDefaultAgentCreateConfig(definition) {
    return { modeId: definition.defaultModeId };
}
export function isDefaultAgentCreateConfigUnattended() {
    return false;
}
