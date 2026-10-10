import type { RoleAgentOverride, RoleDefaults, RoleFeatureChoice } from "./roleLaunchModel";

type FeatureValue = boolean | string;

function FeatureControl({ feature, defaultValue, overrideValue, disabled, onChange }: {
  feature: RoleFeatureChoice;
  defaultValue?: FeatureValue | null;
  overrideValue?: FeatureValue;
  disabled: boolean;
  onChange: (value: FeatureValue | undefined) => void;
}) {
  if (feature.permissionBearing) {
    return <span>{feature.label}: {String(defaultValue ?? "host default")} · configure in user settings</span>;
  }
  function choose(value: string) {
    if (value === "") onChange(undefined);
    else onChange(feature.type === "toggle" ? value === "true" : value);
  }
  const choices = feature.type === "toggle" ? [{ id: "true", label: "On" }, { id: "false", label: "Off" }] : feature.options ?? [];
  return <label>
    {feature.label}
    <select aria-label={feature.label + " override"} disabled={disabled} value={String(overrideValue ?? "")}
      onChange={(event) => choose(event.target.value)}>
      <option value="">Role default ({String(defaultValue ?? "unset")})</option>
      {choices.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
    </select>
  </label>;
}

export function RoleLaunchFeatures({ features, defaults, override, disabled, onChange }: {
  features: RoleFeatureChoice[];
  defaults: RoleDefaults;
  override?: RoleAgentOverride;
  disabled: boolean;
  onChange: (override: RoleAgentOverride | undefined) => void;
}) {
  function choose(id: string, value: FeatureValue | undefined) {
    const values = { ...override?.featureValues };
    if (value === undefined) delete values[id];
    else values[id] = value;
    const next = { ...override, featureValues: values };
    onChange(Object.keys(values).length || next.agentId || next.modelId || next.effortId ? next : undefined);
  }
  return features.map((feature) => <FeatureControl key={feature.id} feature={feature}
    defaultValue={defaults.featureValues?.[feature.id] ?? feature.value} overrideValue={override?.featureValues?.[feature.id]}
    disabled={disabled} onChange={(value) => choose(feature.id, value)} />);
}


export function RoleLaunchDefaults({ defaults, selectedAgentId, modeDescription, ready }: {
  defaults: RoleDefaults;
  selectedAgentId: string;
  modeDescription?: string;
  ready: boolean;
}) {
  if (!ready) return null;
  const onRoleAgent = selectedAgentId === defaults.agent;
  const mode = onRoleAgent ? `Mode: ${defaults.mode ?? "none stated"} · ${defaults.choices?.mode?.source ?? "user settings"}` : "Mode resolved for the selected agent at start";
  return <span title={modeDescription}>
    {mode}
    {defaults.choices ? <span data-testid="role-default-sources"> · Role defaults: {["agent", "model", "effort"].map((key) => {
      const choice = defaults.choices?.[key];
      return `${key}: ${String(choice?.value ?? "host default")} (${choice?.source ?? "host default"})`;
    }).join(" · ")}</span> : null}
    <FeatureSources defaults={defaults} />
    <InstructionSource defaults={defaults} />
  </span>;
}

function FeatureSources({ defaults }: { defaults: RoleDefaults }) {
  const features = defaults.choices?.featureValues;
  if (!features) return null;
  return <span> · Features: {Object.entries(features).map(([key, choice]) => {
    const value = choice as { value?: unknown; source?: string };
    return `${key}: ${String(value.value ?? "host default")} (${value.source ?? "host default"})`;
  }).join(" · ")}</span>;
}

function InstructionSource({ defaults }: { defaults: RoleDefaults }) {
  const notes = defaults.choices?.notes;
  if (!notes?.present) return null;
  return <span> · User instructions ({notes.source}): {notes.baselineReplaced ? "baseline replaced" : "added to baseline"}</span>;
}
