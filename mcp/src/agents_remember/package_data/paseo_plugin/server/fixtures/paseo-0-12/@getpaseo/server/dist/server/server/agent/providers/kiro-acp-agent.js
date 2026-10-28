// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/agent/providers/kiro-acp-agent.js.
import { GenericACPAgentClient } from "./generic-acp-agent.js";
export class KiroACPAgentClient extends GenericACPAgentClient {
    label() {
        return "kiro";
    }
}
