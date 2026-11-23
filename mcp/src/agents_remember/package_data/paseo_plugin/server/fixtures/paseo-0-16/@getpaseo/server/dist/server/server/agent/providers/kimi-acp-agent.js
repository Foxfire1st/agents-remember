import { deriveSelectorOptions, findSelectConfigOption, } from "./acp-agent.js";
import { toDiagnosticErrorMessage } from "./diagnostic-utils.js";
import { GenericACPAgentClient } from "./generic-acp-agent.js";
// Kimi exposes thinking options only for the selected model. Keep its model-switching
// discovery here: other providers can supply a read-only catalog instead.
export async function resolveKimiCatalogModels({ connection, sessionId, models, configOptions, runRequest, transformConfigOptions, logger, provider, }) {
    if (models.length <= 1) {
        return models;
    }
    const modelOption = findSelectConfigOption({ configOptions, category: "model" });
    if (!modelOption) {
        return models;
    }
    const resolved = [];
    for (const model of models) {
        try {
            const response = await runRequest(() => connection.setSessionConfigOption({
                sessionId,
                configId: modelOption.id,
                value: model.id,
            }));
            const modelConfigOptions = transformConfigOptions(response.configOptions ?? []);
            const thinkingOptions = deriveSelectorOptions(modelConfigOptions, "thought_level");
            resolved.push({
                ...model,
                thinkingOptions: thinkingOptions.length > 0 ? thinkingOptions : undefined,
                defaultThinkingOptionId: thinkingOptions.find((option) => option.isDefault)?.id ?? undefined,
            });
        }
        catch (error) {
            const errorMessage = toDiagnosticErrorMessage(error);
            if (model.isDefault) {
                logger.warn({ modelId: model.id, error: errorMessage }, `${provider} catalog probe could not refresh thinking options for current model "${model.id}"; keeping session options`);
                resolved.push(model);
                continue;
            }
            logger.warn({ modelId: model.id, error: errorMessage }, `${provider} catalog probe could not resolve thinking options for model "${model.id}"; omitting thinking options`);
            resolved.push({
                ...model,
                thinkingOptions: undefined,
                defaultThinkingOptionId: undefined,
            });
        }
    }
    return resolved;
}
export class KimiACPAgentClient extends GenericACPAgentClient {
    constructor(options) {
        super({
            logger: options.logger,
            command: options.command,
            env: options.env,
            providerId: options.providerId,
            label: options.label,
            catalogModelResolver: resolveKimiCatalogModels,
        });
    }
}
//# sourceMappingURL=kimi-acp-agent.js.map