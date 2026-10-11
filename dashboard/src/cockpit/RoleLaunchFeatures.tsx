import { css } from "../../styled-system/css";
import { launcherChip, launcherSelect } from "../grammar/viewTabLook";
import type { RoleAgentOverride, RoleDefaults, RoleFeatureChoice } from "./roleLaunchModel";

type FeatureValue = boolean | string;

// A feature control sizes to its own selector, up to the same cap family as
// the row's selectors, and yields with them; only a longer value ends in an
// ellipsis.
const featureField = css({
  display: "grid",
  flexGrow: "0",
  flexShrink: 1.25,
  flexBasis: "auto",
  minWidth: "6rem",
  maxWidth: "10rem",
  "@media (min-width: 90rem)": { minWidth: "4.5rem" },
});

function featureValueText(feature: RoleFeatureChoice, defaultValue?: FeatureValue | null): string {
  if (feature.type === "toggle") return (defaultValue ?? feature.value) === true ? "On" : "Off";
  const options = feature.options ?? [];
  const current = defaultValue ?? feature.value;
  return options.find((option) => option.id === String(current))?.label ?? String(current ?? "host default");
}

function FeatureControl({ feature, defaultValue, overrideValue, source, disabled, onChange }: {
  feature: RoleFeatureChoice;
  defaultValue?: FeatureValue | null;
  overrideValue?: FeatureValue;
  source?: string;
  disabled: boolean;
  onChange: (value: FeatureValue | undefined) => void;
}) {
  if (feature.permissionBearing) {
    const valueText = featureValueText(feature, defaultValue);
    return (
      <span className={launcherChip} title={`${feature.label} ${valueText}: set in the user's settings`}>
        {feature.label} {valueText}
      </span>
    );
  }
  function choose(value: string) {
    if (value === "") onChange(undefined);
    else onChange(feature.type === "toggle" ? value === "true" : value);
  }
  const choices = feature.type === "toggle" ? [{ id: "true", label: "On" }, { id: "false", label: "Off" }] : feature.options ?? [];
  const chosen = overrideValue === undefined ? undefined
    : feature.type === "toggle"
      ? (overrideValue === true || overrideValue === "true") ? "On" : "Off"
      : choices.find((option) => option.id === String(overrideValue))?.label ?? String(overrideValue);
  const displayed = chosen ?? `${feature.label} · default`;
  return (
    <label className={featureField}>
      <select
        className={launcherSelect}
        aria-label={feature.label + " override"}
        title={`${displayed} — ${feature.id}: ${String(defaultValue ?? "unset")} · ${source ?? "host default"}`}
        disabled={disabled}
        value={String(overrideValue ?? "")}
        onChange={(event) => choose(event.target.value)}
      >
        <option value="">{feature.label} · default</option>
        {choices.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select>
    </label>
  );
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
  const featureChoices = defaults.choices?.featureValues as
    | Record<string, { value?: unknown; source?: string }>
    | undefined;
  return features.map((feature) => <FeatureControl key={feature.id} feature={feature}
    defaultValue={defaults.featureValues?.[feature.id] ?? feature.value} overrideValue={override?.featureValues?.[feature.id]}
    source={featureChoices?.[feature.id]?.source}
    disabled={disabled} onChange={(value) => choose(feature.id, value)} />);
}

// What the row said in words lives on: the applied mode is one chip whose
// hover text says where the mode comes from and holds the host's description,
// and an instruction text is one chip whose hover text names its source.
export function RoleLaunchDefaults({ defaults, selectedAgentId, modeLabel, modeDescription, ready }: {
  defaults: RoleDefaults;
  selectedAgentId: string;
  modeLabel?: string;
  modeDescription?: string;
  ready: boolean;
}) {
  if (!ready) return null;
  const onRoleAgent = selectedAgentId === defaults.agent;
  const modeSource = defaults.choices?.mode?.source ?? "user settings";
  const modeTitle = modeDescription ? `${modeSource} · ${modeDescription}` : modeSource;
  const notes = defaults.choices?.notes;
  return (
    <>
      {onRoleAgent ? (
        <span className={launcherChip} title={modeTitle}>{modeLabel ?? defaults.mode ?? "none stated"}</span>
      ) : (
        <span className={launcherChip} title="The mode is resolved for the selected agent at start.">At start</span>
      )}
      {notes?.present ? (
        <span
          className={launcherChip}
          title={`User instructions (${notes.source ?? "user settings"}): ${notes.baselineReplaced ? "baseline replaced" : "added to baseline"}`}
        >
          Instructions
        </span>
      ) : null}
    </>
  );
}
