import {
  EMPTY_ROLE_EFFORTS,
  EMPTY_ROLE_MODELS,
  type RoleAgentChoice,
  type RoleDefaults,
  type RoleEffortChoice,
  type RoleLaunchSelection,
  type RoleModelChoice,
} from "../roleLaunchModel";

export interface ResolvedAgentChoice {
  selectedAgentId: string;
  selectedAgent: RoleAgentChoice | undefined;
  models: RoleModelChoice[];
  defaultModel: RoleModelChoice | undefined;
  selectedModelId: string;
  selectedModel: RoleModelChoice | undefined;
  efforts: RoleEffortChoice[];
  defaultEffort: RoleEffortChoice | undefined;
  selectedAgentLabel: string | undefined;
}

function resolveAgent(
  agents: RoleAgentChoice[],
  roleDefaults: RoleDefaults,
  selection: RoleLaunchSelection,
): Pick<ResolvedAgentChoice, "selectedAgentId" | "selectedAgent" | "models" | "selectedAgentLabel"> {
  const selectedAgentId = selection.agentOverride?.agentId ?? roleDefaults.agent ?? "";
  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId);
  const selectedAgentLabel = agents.find((agent) => agent.id === roleDefaults.agent)?.label ?? roleDefaults.agent;
  return { selectedAgentId, selectedAgent, models: selectedAgent?.models ?? EMPTY_ROLE_MODELS, selectedAgentLabel };
}

function resolveModel(
  models: RoleModelChoice[],
  roleDefaults: RoleDefaults,
  selection: RoleLaunchSelection,
): Pick<ResolvedAgentChoice, "defaultModel" | "selectedModelId" | "selectedModel"> {
  const defaultModel = models.find((model) => model.id === roleDefaults.model);
  const selectedModelId = selection.agentOverride?.modelId ?? defaultModel?.id ?? "";
  return { defaultModel, selectedModelId, selectedModel: models.find((model) => model.id === selectedModelId) };
}

function resolveEffort(
  selectedModel: RoleModelChoice | undefined,
  roleDefaults: RoleDefaults,
): Pick<ResolvedAgentChoice, "efforts" | "defaultEffort"> {
  const efforts = selectedModel?.efforts ?? EMPTY_ROLE_EFFORTS;
  const defaultEffort = efforts.find(
    (effort) => effort.id === (roleDefaults.effort ?? selectedModel?.defaultEffort),
  );
  return { efforts, defaultEffort };
}

// The agent behind the row's override selectors: the chosen agent with its models,
// the chosen model with its efforts, and each level's default. Straight lookups,
// no selection change.
export function resolveAgentChoice(
  agents: RoleAgentChoice[],
  roleDefaults: RoleDefaults,
  selection: RoleLaunchSelection,
): ResolvedAgentChoice {
  const agent = resolveAgent(agents, roleDefaults, selection);
  const model = resolveModel(agent.models, roleDefaults, selection);
  const effort = resolveEffort(model.selectedModel, roleDefaults);
  return { ...agent, ...model, ...effort };
}

// The complete labels the override selectors currently show: the chosen option when an
// override is set, else the default first option. Each mirrors its default-option JSX.
export function displayedAgentLabel(
  selection: RoleLaunchSelection,
  agents: RoleAgentChoice[],
  selectedAgentLabel: string | undefined,
): string {
  if (selection.agentOverride?.agentId) {
    return agents.find((agent) => agent.id === selection.agentOverride?.agentId)?.label ??
      selection.agentOverride.agentId;
  }
  return selectedAgentLabel ? selectedAgentLabel + " · default" : "Role default";
}

export function displayedModelLabel(
  selection: RoleLaunchSelection,
  choice: ResolvedAgentChoice,
): string {
  if (selection.agentOverride?.modelId) {
    return choice.models.find((model) => model.id === selection.agentOverride?.modelId)?.label ??
      selection.agentOverride.modelId;
  }
  if (!choice.selectedAgentId) return "Load agent default";
  if (!choice.selectedAgent) return "Agent capabilities loading";
  if (!choice.models.length) return "No model choices";
  if (choice.defaultModel) return choice.defaultModel.label + " · default";
  return selection.agentOverride ? "Agent default" : "Role default";
}

export function displayedEffortLabel(
  selection: RoleLaunchSelection,
  choice: ResolvedAgentChoice,
): string {
  if (selection.agentOverride?.effortId) {
    return choice.efforts.find((effort) => effort.id === selection.agentOverride?.effortId)?.label ??
      selection.agentOverride.effortId;
  }
  if (choice.defaultEffort) return choice.defaultEffort.label + " · default";
  return selection.agentOverride ? "Model default" : "Role default";
}
