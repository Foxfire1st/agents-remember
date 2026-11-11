// Synthetic stand-in for Paseo 0.15.4's
// @getpaseo/server/dist/server/server/agent/providers/generic-acp-agent.js: the
// class a configured provider that extends `acp` is built with.
import { ACPAgentClient, DEFAULT_ACP_CAPABILITIES, } from "./acp-agent.js";
export class GenericACPAgentClient extends ACPAgentClient {
    capabilities() {
        return DEFAULT_ACP_CAPABILITIES;
    }
}
