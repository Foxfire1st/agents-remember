// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/agent/providers/generic-acp-agent.js: the
// class a configured provider that extends `acp` is built with (MIK-R216 rule 1).
import { ACPAgentClient, DEFAULT_ACP_CAPABILITIES, } from "./acp-agent.js";
export class GenericACPAgentClient extends ACPAgentClient {
    capabilities() {
        return DEFAULT_ACP_CAPABILITIES;
    }
}
