// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/agent/providers/pi/agent.js: an RPC client
// with no base class, so not on an ACP connection.
export class PiRpcAgentSession {
}
export class PiRpcAgentClient {
    constructor(options) {
        this.options = options;
    }
}
