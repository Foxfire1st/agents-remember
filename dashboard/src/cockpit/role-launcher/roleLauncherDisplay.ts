import {
  EMPTY_ROLE_EFFORTS,
  EMPTY_ROLE_MODELS,
  matchesChoice,
  type RoleAgentChoice,
  type RoleDefaults,
  type RoleEffortChoice,
  type RoleLaunchSelection,
  type RoleModeChoice,
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
  usesRoleEffort: boolean;
  selectedAgentLabel: string | undefined;
}

function resolveAgent(
  agents: RoleAgentChoice[],
  roleDefaults: RoleDefaults,
  selection: RoleLaunchSelection,
): Pick<ResolvedAgentChoice, "selectedAgentId" | "selectedAgent" | "models" | "selectedAgentLabel"> {
  const selectedAgentId = selection.agentOverride?.agentId ?? roleDefaults.agent ?? "";
  const selectedAgent = agents.find((agent) => matchesChoice(agent, selectedAgentId));
  const selectedAgentLabel = agents.find((agent) => matchesChoice(agent, roleDefaults.agent))?.label ?? roleDefaults.agent;
  return { selectedAgentId, selectedAgent, models: selectedAgent?.models ?? EMPTY_ROLE_MODELS, selectedAgentLabel };
}

function resolveModel(
  agent: ReturnType<typeof resolveAgent>,
  roleDefaults: RoleDefaults,
  selection: RoleLaunchSelection,
): Pick<ResolvedAgentChoice, "defaultModel" | "selectedModelId" | "selectedModel"> {
  const { models, selectedAgent } = agent;
  const roleModel = selectedAgent && matchesChoice(selectedAgent, roleDefaults.agent) ? roleDefaults.model : undefined;
  const defaultModel = models.find((model) => matchesChoice(model, roleModel));
  const nativeDefault = models.find((model) => model.isDefault);
  const selectedModelId = selection.agentOverride?.modelId ?? defaultModel?.id ?? roleModel ?? nativeDefault?.id ?? "";
  return { defaultModel, selectedModelId, selectedModel: models.find((model) => matchesChoice(model, selectedModelId)) };
}

function resolveEffort(
  { defaultModel, selectedModel }: ReturnType<typeof resolveModel>,
  roleDefaults: RoleDefaults,
): Pick<ResolvedAgentChoice, "efforts" | "defaultEffort" | "usesRoleEffort"> {
  const efforts = selectedModel?.efforts ?? EMPTY_ROLE_EFFORTS;
  // The configured effort applies only to its role's provider and model.
  const configuredEffort = defaultModel && defaultModel === selectedModel ? roleDefaults.effort : undefined;
  const defaultEffort = efforts.find(
    (effort) => matchesChoice(effort, configuredEffort ?? selectedModel?.defaultEffort),
  );
  return { efforts, defaultEffort, usesRoleEffort: Boolean(configuredEffort) };
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
  const model = resolveModel(agent, roleDefaults, selection);
  const effort = resolveEffort(model, roleDefaults);
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

function unavailableModelsLabel(choice: ResolvedAgentChoice): string {
  if (choice.selectedAgent?.discovery?.models?.source === "unknown") return `${choice.selectedModelId || "Configured model"} · unchecked`;
  return "No model choices";
}

export function displayedModelLabel(
  selection: RoleLaunchSelection,
  choice: ResolvedAgentChoice,
): string {
  if (selection.agentOverride?.modelId) {
    return choice.models.find((model) => model.id === selection.agentOverride?.modelId)?.label ??
      selection.agentOverride.modelId;
  }
  if (!choice.selectedAgentId) return "Pick an agent first";
  if (!choice.selectedAgent) return "Agent capabilities loading";
  if (!choice.models.length) return unavailableModelsLabel(choice);
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

export interface ResolvedModeChoice {
  modes: RoleModeChoice[];
  defaultModeId: string;
  defaultSource: string;
}

// Where a role's resolved mode came from, as the launcher names it.
function roleModeSource(roleDefaults: RoleDefaults): string {
  return roleDefaults.choices?.mode?.source ?? "host default";
}

// The modes the selected agent's harness offers and the mode this row starts in when none is
// chosen: the row's own mode when the selected agent is the harness that row names, else the
// role's resolved mode on its own agent, else the host's own default for that agent. The start
// applies the same rule, so the shown default and the launched mode cannot disagree.
function modesOf(choice: ResolvedAgentChoice): RoleModeChoice[] {
  return choice.selectedAgent?.modes ?? [];
}

// The settings row's own mode, when the selected agent is the harness that row names.
function rowModeDefault(
  choice: ResolvedAgentChoice,
  roleDefaults: RoleDefaults,
): ResolvedModeChoice | null {
  const rowMode = roleDefaults.choices?.rowMode as
    | { harness?: string | null; value?: string | null }
    | undefined;
  if (!choice.selectedAgent || rowMode?.harness !== choice.selectedAgent.id || !rowMode.value) {
    return null;
  }
  return { modes: modesOf(choice), defaultModeId: rowMode.value, defaultSource: "our settings" };
}

// The mode resolution already made for the role's own agent.
function roleModeDefault(
  choice: ResolvedAgentChoice,
  roleDefaults: RoleDefaults,
): ResolvedModeChoice | null {
  if (!choice.selectedAgent || !matchesChoice(choice.selectedAgent, roleDefaults.agent)) return null;
  return {
    modes: modesOf(choice),
    defaultModeId: roleDefaults.mode ?? "",
    defaultSource: roleModeSource(roleDefaults),
  };
}

// The host profile's mode, which the start applies before the settings row on the host's own
// agent. It must win here too, or the row would show the row's mode where the start sends this.
function hostRoleDefault(
  choice: ResolvedAgentChoice,
  roleDefaults: RoleDefaults,
): ResolvedModeChoice | null {
  if (!choice.selectedAgent || !matchesChoice(choice.selectedAgent, roleDefaults.agent)) return null;
  if (roleModeSource(roleDefaults) !== "host role") return null;
  return {
    modes: modesOf(choice),
    defaultModeId: roleDefaults.mode ?? "",
    defaultSource: "host role",
  };
}

export function resolveModeChoice(
  choice: ResolvedAgentChoice,
  roleDefaults: RoleDefaults,
): ResolvedModeChoice {
  return (
    hostRoleDefault(choice, roleDefaults) ??
    rowModeDefault(choice, roleDefaults) ??
    roleModeDefault(choice, roleDefaults) ?? {
      modes: modesOf(choice),
      defaultModeId: choice.selectedAgent?.defaultModeId ?? "",
      defaultSource: "host default",
    }
  );
}
