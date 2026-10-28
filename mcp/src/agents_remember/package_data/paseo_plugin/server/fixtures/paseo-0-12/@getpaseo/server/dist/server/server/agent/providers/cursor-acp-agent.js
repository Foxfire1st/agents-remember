// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/agent/providers/cursor-acp-agent.js: the id
// `addDerivedProviders` special-cases away from the generic client.
import { deriveSelectorOptions, } from "./acp-agent.js";
import { GenericACPAgentClient } from "./generic-acp-agent.js";
export class CursorACPAgentClient extends GenericACPAgentClient {
    selectorOptions() {
        return deriveSelectorOptions(this.options);
    }
}
