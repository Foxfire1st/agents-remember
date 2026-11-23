import { GenericACPAgentClient } from "./generic-acp-agent.js";
const TRAE_INITIAL_COMMANDS_WAIT_TIMEOUT_MS = 10000;
export class TraeACPAgentClient extends GenericACPAgentClient {
    constructor(options) {
        super({
            logger: options.logger,
            command: options.command,
            env: options.env,
            providerId: options.providerId,
            label: options.label,
            // traecli publishes slash commands and skills asynchronously via available_commands_update.
            waitForInitialCommands: true,
            initialCommandsWaitTimeoutMs: TRAE_INITIAL_COMMANDS_WAIT_TIMEOUT_MS,
        });
    }
}
//# sourceMappingURL=trae-acp-agent.js.map