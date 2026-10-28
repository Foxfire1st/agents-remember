// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/agent/providers/copilot-acp-agent.js: the
// one built-in provider whose class is built straight on ACPAgentClient.
import { ACPAgentClient, } from "./acp-agent.js";
export class CopilotACPAgentClient extends ACPAgentClient {
    label() {
        return "copilot";
    }
}
