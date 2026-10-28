// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/agent/providers/omp/agent.js: a CLI runtime
// client with no base class, so not on an ACP connection.
export class OmpAgentSession {
}
export class OmpAgentClient {
    constructor(options) {
        this.options = options;
    }
}
