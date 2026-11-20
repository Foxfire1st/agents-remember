import { launcherSelect } from '../../grammar/viewTabLook';
import {
  roleAgentOverrideFor,
  type RoleDefaults,
  type RoleLaunchSelection,
} from '../roleLaunchModel';
import { resolveModeChoice, type ResolvedAgentChoice, type ResolvedModeChoice } from './roleLauncherDisplay';
import { roleLauncherField, roleLauncherModeField } from './roleLauncherFields';

// The mode selector's first option: the resolved mode's own label with the source it came
// from, or the host's silence said plainly.
export function defaultModeLabel(mode: ResolvedModeChoice): string {
  if (!mode.defaultModeId) return 'No mode · ' + mode.defaultSource;
  const label = mode.modes.find((row) => row.id === mode.defaultModeId)?.label ?? mode.defaultModeId;
  return label + ' · ' + mode.defaultSource;
}

// The mode selector: the permission modes the chosen agent's harness offers, its first option
// the mode this start takes anyway, named with its source. It stands beside the effort selector.
// A chosen mode the harness no longer lists stays in the list under its own name: the row keeps
// showing and sending exactly what is chosen, and the host stays the judge of whether it applies.
export function ModeOverrideSelect({
  selection,
  roleDefaults,
  choice,
  launchSelection,
  selectedAgentId,
  optionsReady,
  optionsLoading,
  busy,
  retryLocked,
  onSelectionChange,
}: {
  selection: RoleLaunchSelection;
  roleDefaults: RoleDefaults;
  choice: ResolvedAgentChoice;
  launchSelection: RoleLaunchSelection;
  selectedAgentId: string;
  optionsReady: boolean;
  optionsLoading: boolean;
  busy: boolean;
  retryLocked: boolean;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}) {
  const mode = resolveModeChoice(choice, roleDefaults);
  const defaultLabel = defaultModeLabel(mode);
  const chosenId = selection.agentOverride?.modeId;
  const chosenMissing = Boolean(chosenId) && !mode.modes.some((row) => row.id === chosenId);
  return (
    <label className={`${roleLauncherField} ${roleLauncherModeField}`}>
      <select
        className={launcherSelect}
        aria-label="Role mode override"
        title={`The first option, ${defaultLabel}, is what this start takes when you choose no mode.`}
        value={chosenId ?? ''}
        disabled={!optionsReady || !choice.selectedAgent || optionsLoading || busy || retryLocked}
        onChange={(event) => {
          const agentId = selection.agentOverride?.agentId ?? selectedAgentId;
          onSelectionChange({
            ...launchSelection,
            agentOverride: roleAgentOverrideFor(
              {
                agentId,
                modelId: selection.agentOverride?.modelId,
                effortId: selection.agentOverride?.effortId,
                modeId: event.target.value || undefined,
                featureValues: selection.agentOverride?.featureValues,
              },
              roleDefaults,
            ),
          });
        }}
      >
        <option value="">{defaultLabel}</option>
        {mode.modes.map((row) => (
          <option key={row.id} value={row.id}>
            {row.label}
          </option>
        ))}
        {chosenId && chosenMissing ? (
          <option value={chosenId}>{chosenId} · no longer listed</option>
        ) : null}
      </select>
    </label>
  );
}
