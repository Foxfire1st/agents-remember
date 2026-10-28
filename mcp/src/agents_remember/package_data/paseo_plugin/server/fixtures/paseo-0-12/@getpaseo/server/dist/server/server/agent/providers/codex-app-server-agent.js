// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/agent/providers/codex-app-server-agent.js:
// an app-server client with no base class, so not on an ACP connection.
export class CodexAppServerAgentSession {
}
export class CodexAppServerAgentClient {
    constructor(logger, runtimeSettings, options) {
        this.logger = logger;
        this.runtimeSettings = runtimeSettings;
        this.options = options;
    }
}
