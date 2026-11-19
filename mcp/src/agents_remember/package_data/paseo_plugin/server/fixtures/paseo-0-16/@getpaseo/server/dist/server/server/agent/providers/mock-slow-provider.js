export const MOCK_SLOW_PROVIDER_ID = "mock-slow";
const CAPABILITIES = {
    supportsStreaming: false,
    supportsSessionPersistence: false,
    supportsDynamicModes: false,
    supportsMcpServers: false,
    supportsReasoningStream: false,
    supportsToolInvocations: false,
    supportsRewindConversation: false,
    supportsRewindFiles: false,
    supportsRewindBoth: false,
};
export class MockSlowProviderClient {
    constructor() {
        this.provider = MOCK_SLOW_PROVIDER_ID;
        this.capabilities = CAPABILITIES;
    }
    async isAvailable() {
        return process.env.PASEO_ENABLE_MOCK_SLOW === "true";
    }
    async fetchCatalog(_options, context) {
        return await (context?.runActivity("mock.catalog", () => new Promise((_resolve, reject) => {
            context.signal.addEventListener("abort", () => reject(context.signal.reason), {
                once: true,
            });
        })) ?? new Promise(() => { }));
    }
    async getDiagnostic() {
        return {
            diagnostic: "Mock slow provider: dev-only. fetchCatalog() never resolves so the snapshot manager will time out.",
        };
    }
    createSession(_config, _launchContext) {
        throw new Error("Mock slow provider is dev-only; sessions are not supported.");
    }
    resumeSession(_handle, _overrides, _launchContext) {
        throw new Error("Mock slow provider is dev-only; sessions are not supported.");
    }
}
//# sourceMappingURL=mock-slow-provider.js.map