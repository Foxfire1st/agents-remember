import { css } from "../../styled-system/css";
import { launcherChip, launcherSelect } from "../grammar/viewTabLook";
import { matchesChoice, type RoleAgentOverride, type RoleDefaults, type RoleFeatureChoice } from "./roleLaunchModel";

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
  return options.find((option) => matchesChoice(option, String(current)))?.label ?? String(current ?? "host default");
}

// The selectable values of a feature: a toggle offers On/Off, a choice its options.
function featureOptionChoices(feature: RoleFeatureChoice): Array<{ id: string; label: string }> {
  if (feature.type === "toggle") return [{ id: "true", label: "On" }, { id: "false", label: "Off" }];
  return feature.options ?? [];
}

// The hover text's shown value: the override's label when set, else the default marker.
function chosenFeatureText(
  feature: RoleFeatureChoice,
  choices: Array<{ id: string; label: string }>,
  overrideValue?: FeatureValue,
): string | undefined {
  if (overrideValue === undefined) return undefined;
  if (feature.type === "toggle") return overrideValue === true || overrideValue === "true" ? "On" : "Off";
  return choices.find((option) => matchesChoice(option, String(overrideValue)))?.label ?? String(overrideValue);
}

// A permission-bearing feature is read-only: its value is set in the user's settings.
function PermissionFeatureNote({ feature, defaultValue }: {
  feature: RoleFeatureChoice;
  defaultValue?: FeatureValue | null;
}) {
  const valueText = featureValueText(feature, defaultValue);
  return (
    <span className={launcherChip} title={`${feature.label} ${valueText}: set in the user's settings`}>
      {feature.label} {valueText}
    </span>
  );
}

function chooseFeatureValue(feature: RoleFeatureChoice, value: string): FeatureValue | undefined {
  if (value === "") return undefined;
  return feature.type === "toggle" ? value === "true" : value;
}

// An ordinary feature: a selector between its choices, defaulting to the role default.
function FeatureOverrideSelect({ feature, defaultValue, overrideValue, source, disabled, onChange }: {
  feature: RoleFeatureChoice;
  defaultValue?: FeatureValue | null;
  overrideValue?: FeatureValue;
  source?: string;
  disabled: boolean;
  onChange: (value: FeatureValue | undefined) => void;
}) {
  const choices = featureOptionChoices(feature);
  const displayed = chosenFeatureText(feature, choices, overrideValue) ?? `${feature.label} · default`;
  return (
    <label className={featureField}>
      <select
        className={launcherSelect}
        aria-label={feature.label + " override"}
        title={`${displayed} — ${feature.id}: ${String(defaultValue ?? "unset")} · ${source ?? "host default"}`}
        disabled={disabled}
        value={String(overrideValue ?? "")}
        onChange={(event) => onChange(chooseFeatureValue(feature, event.target.value))}
      >
        <option value="">{feature.label} · default</option>
        {choices.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select>
    </label>
  );
}

function FeatureControl({ feature, defaultValue, overrideValue, source, disabled, onChange }: {
  feature: RoleFeatureChoice;
  defaultValue?: FeatureValue | null;
  overrideValue?: FeatureValue;
  source?: string;
  disabled: boolean;
  onChange: (value: FeatureValue | undefined) => void;
}) {
  if (feature.permissionBearing) return <PermissionFeatureNote feature={feature} defaultValue={defaultValue} />;
  return (
    <FeatureOverrideSelect
      feature={feature}
      defaultValue={defaultValue}
      overrideValue={overrideValue}
      source={source}
      disabled={disabled}
      onChange={onChange}
    />
  );
}

function withFeatureValue(
  override: RoleAgentOverride | undefined,
  id: string,
  value: FeatureValue | undefined,
): RoleAgentOverride | undefined {
  const values = { ...override?.featureValues };
  if (value === undefined) delete values[id];
  else values[id] = value;
  const next = { ...override, featureValues: values };
  return Object.keys(values).length || next.agentId || next.modelId || next.effortId ? next : undefined;
}

export function RoleLaunchFeatures({ features, defaults, override, disabled, onChange, basePreview = false }: {
  features: RoleFeatureChoice[];
  defaults: RoleDefaults;
  override?: RoleAgentOverride;
  disabled: boolean;
  onChange: (override: RoleAgentOverride | undefined) => void;
  basePreview?: boolean;
}) {
  if (basePreview) return features.map((feature) => (
    <span key={feature.id} className={launcherChip}
      title="Base launch preview; not applied to the stepped-up execution. Permission choices remain set in the user's settings.">
      Base · {feature.label} {featureValueText(feature, defaults.featureValues?.[feature.id] ??
        (feature.id === "service_tier" ? defaults.serviceTier : undefined))}
    </span>
  ));
  const featureChoices = defaults.choices?.featureValues as
    | Record<string, { value?: unknown; source?: string }>
    | undefined;
  return features.map((feature) => <FeatureControl key={feature.id} feature={feature}
    defaultValue={defaults.featureValues?.[feature.id] ?? feature.value} overrideValue={override?.featureValues?.[feature.id]}
    source={featureChoices?.[feature.id]?.source}
    disabled={disabled} onChange={(value) => onChange(withFeatureValue(override, feature.id, value))} />);
}

// The applied mode as one chip: the resolved mode on the role's own agent, else a pointer
// that the mode resolves for the selected agent at start.
function ModeChip({ defaults, onRoleAgent, modeLabel, modeDescription }: {
  defaults: RoleDefaults;
  onRoleAgent: boolean;
  modeLabel?: string;
  modeDescription?: string;
}) {
  if (!onRoleAgent) {
    return (
      <span className={launcherChip} title="The mode is resolved for the selected agent at start.">At start</span>
    );
  }
  const modeSource = defaults.choices?.mode?.source ?? "user settings";
  const modeTitle = modeDescription ? `${modeSource} · ${modeDescription}` : modeSource;
  return (
    <span className={launcherChip} title={modeTitle}>{modeLabel ?? defaults.mode ?? "none stated"}</span>
  );
}

// The user-instructions chip, present only when the defaults carry instructions.
function InstructionsChip({ defaults }: { defaults: RoleDefaults }) {
  const notes = defaults.choices?.notes;
  if (!notes?.present) return null;
  return (
    <span
      className={launcherChip}
      title={`User instructions (${notes.source ?? "user settings"}): ${notes.baselineReplaced ? "baseline replaced" : "added to baseline"}`}
    >
      Instructions
    </span>
  );
}

// What the row said in words lives on: the applied mode is one chip whose
// hover text says where the mode comes from and holds the host's description,
// and an instruction text is one chip whose hover text names its source.
export function RoleLaunchDefaults({ defaults, selectedAgentId, modeLabel, modeDescription, ready, basePreview = false }: {
  defaults: RoleDefaults;
  selectedAgentId: string;
  modeLabel?: string;
  modeDescription?: string;
  ready: boolean;
  basePreview?: boolean;
}) {
  if (!ready) return null;
  if (basePreview) return (
    <>
      <span className={launcherChip} title="Base launch preview; not applied to the stepped-up execution.">
        Base · {selectedAgentId === defaults.agent ? modeLabel ?? defaults.mode ?? "none stated" : "mode at start"}
      </span>
      {defaults.choices?.notes?.present ? <span className={launcherChip}
        title="Base user instructions preview; not applied here as a claim about the stepped-up execution.">Base · Instructions</span> : null}
    </>
  );
  return (
    <>
      <ModeChip
        defaults={defaults}
        onRoleAgent={selectedAgentId === defaults.agent}
        modeLabel={modeLabel}
        modeDescription={modeDescription}
      />
      <InstructionsChip defaults={defaults} />
    </>
  );
}
