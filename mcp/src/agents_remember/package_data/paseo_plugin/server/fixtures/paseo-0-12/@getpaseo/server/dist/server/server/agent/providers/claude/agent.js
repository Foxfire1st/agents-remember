// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/agent/providers/claude/agent.js: a client
// with no base class, so it is not on an ACP connection (MIK-R216 rule 1).
export class ClaudeAgentClient {
    constructor(options) {
        this.options = options;
    }
}
