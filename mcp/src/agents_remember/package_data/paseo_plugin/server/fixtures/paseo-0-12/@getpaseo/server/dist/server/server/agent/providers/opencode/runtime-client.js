// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/agent/providers/opencode/runtime-client.js:
// a runtime client with no base class, so not on an ACP connection.
export class OpenCodeRuntimeClient {
    constructor(logger, runtimeSettings, options) {
        this.logger = logger;
        this.runtimeSettings = runtimeSettings;
        this.options = options;
    }
}
