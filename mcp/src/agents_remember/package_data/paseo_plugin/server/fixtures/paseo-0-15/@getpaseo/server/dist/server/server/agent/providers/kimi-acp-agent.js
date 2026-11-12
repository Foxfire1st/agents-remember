// Synthetic stand-in for Paseo 0.15.4's
// @getpaseo/server/dist/server/server/agent/providers/kimi-acp-agent.js.
import { GenericACPAgentClient } from "./generic-acp-agent.js";
export class KimiACPAgentClient extends GenericACPAgentClient {
    label() {
        return "kimi";
    }
}
