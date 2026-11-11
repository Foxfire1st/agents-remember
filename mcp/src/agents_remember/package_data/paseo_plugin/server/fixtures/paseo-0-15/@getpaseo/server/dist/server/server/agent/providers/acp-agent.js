// Synthetic stand-in for Paseo 0.15.4's
// @getpaseo/server/dist/server/server/agent/providers/acp-agent.js. This is the
// base the ACP discovery tests a class chain against; the real file is far larger.
export const DEFAULT_ACP_CAPABILITIES = {
    loadSession: true,
    sessionCapabilities: {},
};
class ACPRequestError extends Error {
}
export class ACPAgentClient {
    constructor(options) {
        this.options = options;
    }
}
export class ACPAgentSession {
    constructor(client) {
        this.client = client;
    }
}
