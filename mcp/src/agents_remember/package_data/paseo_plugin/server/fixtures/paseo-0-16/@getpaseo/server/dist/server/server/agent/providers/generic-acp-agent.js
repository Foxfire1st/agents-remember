import { checkProviderLaunchAvailable, resolveProviderLaunch } from "../provider-launch-config.js";
import { ACPAgentClient, DEFAULT_ACP_CAPABILITIES, } from "./acp-agent.js";
import { buildBinaryDiagnosticRows, formatProviderDiagnostic, toDiagnosticErrorMessage, } from "./diagnostic-utils.js";
export class GenericACPAgentClient extends ACPAgentClient {
    constructor(options) {
        super({
            provider: "acp",
            usageProvider: options.usageProvider,
            logger: options.logger,
            runtimeSettings: {
                env: options.env,
            },
            defaultCommand: options.command,
            capabilities: DEFAULT_ACP_CAPABILITIES,
            // ACP agents advertise slash commands with available_commands_update after
            // session/new, so the first listCommands() waits for that batch.
            waitForInitialCommands: options.waitForInitialCommands ?? true,
            initialCommandsWaitTimeoutMs: options.initialCommandsWaitTimeoutMs,
            clientCapabilityMeta: options.clientCapabilityMeta,
            configFeatureOptions: options.configFeatureOptions,
            extensionCommandsParser: options.extensionCommandsParser,
            catalogModelResolver: options.catalogModelResolver,
            now: options.now,
        });
        this.command = options.command;
        this.providerId = options.providerId;
        this.label = options.label;
        this.diagnosticPhaseTimeoutMs = options.diagnosticPhaseTimeoutMs;
    }
    async resolveLaunchCommand() {
        return {
            command: this.command[0],
            args: this.command.slice(1),
        };
    }
    async isAvailable() {
        const launch = await this.resolveConfiguredLaunch();
        const availability = await checkProviderLaunchAvailable(launch);
        return availability.available;
    }
    async getDiagnostic() {
        const providerName = formatProviderName(this.label, this.providerId);
        const entries = [
            { label: "Provider ID", value: this.providerId ?? "unknown" },
            { label: "Configured command", value: this.command.join(" ") },
        ];
        const versionProbe = buildVersionProbeCommand(this.command);
        try {
            const launch = await this.resolveConfiguredLaunch();
            const availability = await checkProviderLaunchAvailable(launch);
            entries.push(...(await buildBinaryDiagnosticRows(launch, availability, {
                binaryLabel: "Launcher binary",
                versionCommand: {
                    command: versionProbe.command,
                    args: versionProbe.args,
                    env: this.runtimeSettings?.env,
                },
            })));
        }
        catch (error) {
            entries.push({
                label: "Launcher binary",
                value: `error: ${toDiagnosticErrorMessage(error)}`,
            });
        }
        entries.push({
            label: "Version command",
            value: formatCommand(versionProbe.command, versionProbe.args),
        }, ...(await this.getACPProbeRowsForDiagnostic()));
        return {
            diagnostic: formatProviderDiagnostic(providerName, entries),
        };
    }
    async resolveConfiguredLaunch() {
        return resolveProviderLaunch({
            commandConfig: { mode: "replace", argv: this.command },
            defaultBinary: this.command[0],
        });
    }
    async getACPProbeRowsForDiagnostic() {
        try {
            return await this.buildACPProbeDiagnosticRows({
                phaseTimeoutMs: this.diagnosticPhaseTimeoutMs,
            });
        }
        catch (error) {
            return [
                {
                    label: "ACP probe",
                    value: `error: ${toDiagnosticErrorMessage(error)}`,
                },
            ];
        }
    }
}
function formatProviderName(label, providerId) {
    if (label) {
        return `${label} (ACP)`;
    }
    if (providerId) {
        return `${providerId} (ACP)`;
    }
    return "Custom ACP";
}
function formatCommand(command, args) {
    return [command, ...args].join(" ");
}
export function buildVersionProbeCommand(command) {
    const [launcher, ...args] = command;
    if (isPackageRunner(launcher)) {
        return {
            command: launcher,
            args: [...takePackageRunnerPrefix(args), "--version"],
        };
    }
    return {
        command: launcher,
        args: ["--version"],
    };
}
function isPackageRunner(command) {
    return ["npx", "bunx", "pnpm", "uvx"].includes(command);
}
function takePackageRunnerPrefix(args) {
    if (args.length === 0) {
        return [];
    }
    if (args[0] === "dlx") {
        return ["dlx", ...takePackageSpecPrefix(args.slice(1))];
    }
    return takePackageSpecPrefix(args);
}
function takePackageSpecPrefix(args) {
    const prefix = [];
    for (let index = 0; index < args.length; index += 1) {
        const arg = args[index];
        prefix.push(arg);
        if (arg === "--package" || arg === "-p") {
            if (args[index + 1]) {
                prefix.push(args[index + 1]);
                index += 1;
            }
            continue;
        }
        if (!arg.startsWith("-")) {
            break;
        }
    }
    return prefix;
}
//# sourceMappingURL=generic-acp-agent.js.map