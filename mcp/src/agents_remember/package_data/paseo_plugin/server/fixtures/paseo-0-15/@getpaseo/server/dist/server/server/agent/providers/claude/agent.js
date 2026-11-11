// Synthetic stand-in for Paseo 0.15.4's
// @getpaseo/server/dist/server/server/agent/providers/claude/agent.js: a client
// with no base class, so it is not on an ACP connection.
export class ClaudeAgentClient {
    constructor(options) {
        this.options = options;
    }
}
