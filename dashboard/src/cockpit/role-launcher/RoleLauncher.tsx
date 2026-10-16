import { css } from '../../../styled-system/css';
import { launcherButton, launcherChip, launcherSelect } from '../../grammar/viewTabLook';
import type { TaskEntry, TaskDocumentRef } from '../../types/projection';
import { sameTaskDocumentRef } from '../../data/taskIdentity';
import { RoleExecutionStatus, RoleReviveControl } from '../RoleExecutionStatus';
import { RoleLaunchDefaults, RoleLaunchFeatures } from '../RoleLaunchFeatures';
import { StepUpControl, StepUpStatus } from './StepUpControl';

import {
  LAUNCHER_ROLES,
  isTasklessRole,
  usesRequestReceipts,
  roleAgentOverrideFor,
  roleDocumentScope,
  masterOptionsForSprint,
  optionIndex,
  refAtOptionIndex,
  roleNeedsMaster,
  roleNeedsSprint,
  roleNeedsTask,
  sprintOptionsForEntries,
  taskOptionsForMaster,
  taskRefIdentity,
  type RoleAgentChoice,
  type RoleLaunchSelection,
  type LauncherRole,
  type RoleDefaults,
  type RoleFeatureChoice,
  type RoleScopedExecution,
  type RoleTaskOption,
} from '../roleLaunchModel';
import {
  displayedAgentLabel,
  displayedEffortLabel,
  displayedModelLabel,
  resolveAgentChoice,
  type ResolvedAgentChoice,
} from './roleLauncherDisplay';
import { resolveLaunchReadiness, type LaunchReadiness } from './roleLauncherReadiness';
import { usePruneSeededSelection, usePruneStaleOverride } from './useLauncherSelectionRepair';

const roleLauncherGrid = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  width: '100%',
  minWidth: '0',
  columnGap: '0.15rem',
  rowGap: '0.5rem',
  padding: '0.65rem 0.5rem',
  borderBottom: '1px solid var(--grid)',
  background: 'var(--bg-panel)',
  // Rule 8 and the overfull-feature recovery: the row wraps by whole controls
  // at any width whenever its controls cannot fit; it never clips or hides
  // them and offers no overflow menu.
});
// A selector's wrapper: no visible title (each selector keeps its accessible
// name and carries a hover text that says what it selects). It sizes to its
// value up to its cap and yields below it, so the full row still lands on one
// line; only a longer value ends in an ellipsis, whole in the hover text.
const roleLauncherField = css({
  display: 'grid',
  flexGrow: '0',
  flexBasis: 'auto',
  minWidth: '6rem',
});
// Selectors give way first: short-valued wrappers yield little, the
// default-valued wrappers most, each down to its floor; a chip or button never
// yields. The document caps keep the ordinary eleven-control row on one line
// at 1440 px; whatever still does not fit wraps by whole controls.
const roleLauncherRoleField = css({
  maxWidth: '9rem',
  flexShrink: 0.5,
  '@media (min-width: 90rem)': { minWidth: '5.5rem' },
});
const roleLauncherSprintField = css({
  maxWidth: '6rem',
  flexShrink: 0.5,
  '@media (min-width: 90rem)': { minWidth: '5rem' },
});
const roleLauncherMasterField = css({
  maxWidth: '6rem',
  flexShrink: 0.5,
  '@media (min-width: 90rem)': { minWidth: '5rem' },
});
const roleLauncherTaskField = css({
  maxWidth: '6rem',
  flexShrink: 0.5,
  '@media (min-width: 90rem)': { minWidth: '5rem' },
});
const roleLauncherAgentField = css({
  maxWidth: '10rem',
  flexShrink: 1.25,
  '@media (min-width: 90rem)': { minWidth: '4.5rem' },
});
const roleLauncherModelField = css({
  maxWidth: '10rem',
  flexShrink: 1.25,
  '@media (min-width: 90rem)': { minWidth: '4.5rem' },
});
const roleLauncherEffortField = css({
  maxWidth: '10rem',
  flexShrink: 1.25,
  '@media (min-width: 90rem)': { minWidth: '4.5rem' },
});
// Every selector of the row is the one shared selector in
// ../../grammar/viewTabLook (imported as `launcherSelect` above): the row
// holds no copy of the tabs' look. Buttons likewise use the shared
// `launcherButton` (Start the selected-tab look, the rest the unselected one).
const roleLauncherActions = css({
  display: 'flex',
  flex: '0 0 auto',
  alignItems: 'center',
  flexWrap: 'wrap',
  gap: '0.35rem',
  minWidth: '0',
});
const roleLauncherMeta = css({
  flex: '1 0 100%',
  minWidth: '0',
  color: 'muted',
  fontSize: '0.64rem',
  lineHeight: '1.35',
  overflowWrap: 'anywhere',
});
function boundRoleLabel(
  label: string,
  selected: boolean,
  bound: boolean,
  status: string | undefined,
): string {
  if (!bound || !selected) return label;
  // A held seat is called held and not running, idle or failed (MIK-R100 rule 5).
  if (status === 'held') return label + ' (held)';
  return status === 'running' ? label + ' (running)' : label;
}

function boundCanStart(canStart: boolean, bound: boolean, liveOccupant: boolean): boolean {
  return canStart && !(bound && liveOccupant);
}

// Where a default comes from lives in the hover text of its control, in the
// form "<key>: <value> · <source>".
function defaultSourceTitle(
  key: string,
  choices: RoleDefaults['choices'],
  fallbackValue: string,
): string {
  const choice = choices?.[key];
  const value =
    choice?.value === undefined || choice?.value === null ? fallbackValue : String(choice.value);
  return `${key}: ${value} · ${choice?.source ?? 'host default'}`;
}

// The hover text of a document selector: the complete rendered label it
// currently shows, what it selects, and the document identity behind it.
function docSelectTitle(
  options: RoleTaskOption[],
  ref: TaskDocumentRef | undefined,
  what: string,
  emptyText: string,
): string {
  const value = optionIndex(options, ref);
  if (value === '') return what;
  const selected = options[Number(value)];
  const label = selected ? selected.entry.title || selected.entry.id : emptyText;
  const identity =
    (selected && selected.ref ? `${selected.ref.repository}/${selected.ref.path}` : undefined) ??
    taskRefIdentity(ref);
  return identity ? `${label} — ${what}: ${identity}` : `${label} — ${what}`;
}

function referenceFields(bound: boolean, selection: RoleLaunchSelection) {
  return {
    sprint: !bound && roleNeedsSprint(selection.role),
    master: !bound && roleNeedsMaster(selection.role) && Boolean(selection.sprintDocumentRef),
    task: !bound && roleNeedsTask(selection.role) && Boolean(selection.masterDocumentRef),
  };
}

function launcherRow(progress?: string) {
  return { startLabel: progress ?? 'Start' };
}

interface DocumentFields {
  sprintOptions: RoleTaskOption[];
  masterOptions: RoleTaskOption[];
  taskOptions: RoleTaskOption[];
}

// The role selector: the launcher role itself, limited to the bound roles on a bound seat.
function RoleSelect({
  role,
  boundRoles,
  status,
  selection,
  busy,
  optionsLoading,
  retryLocked,
  onSelectionChange,
}: {
  role: LauncherRole;
  boundRoles: LauncherRole[] | undefined;
  status: string | undefined;
  selection: RoleLaunchSelection;
  busy: boolean;
  optionsLoading: boolean;
  retryLocked: boolean;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}) {
  return (
    <label className={`${roleLauncherField} ${roleLauncherRoleField}`}>
      <select
        className={launcherSelect}
        id="launcher-role"
        aria-label="Role"
        title={`${boundRoleLabel(LAUNCHER_ROLES.find((option) => option.id === role)?.label ?? role, true, Boolean(boundRoles), status)} — Role`}
        value={role}
        disabled={busy || optionsLoading || (!usesRequestReceipts(selection) && retryLocked)}
        onChange={(event) =>
          onSelectionChange(
            boundRoles
              ? { ...roleDocumentScope(selection), role: event.target.value as LauncherRole }
              : { role: event.target.value as LauncherRole },
          )
        }
      >
        {LAUNCHER_ROLES.filter((option) => !boundRoles || boundRoles.includes(option.id)).map(
          (option) => (
            <option key={option.id} value={option.id}>
              {boundRoleLabel(option.label, option.id === role, Boolean(boundRoles), status)}
            </option>
          ),
        )}
      </select>
    </label>
  );
}

// One document selector: sprint, master or task, each listing its catalog level and
// pruning the selection back to the role when its level is cleared.
function SprintSelect({
  sprintOptions,
  sprintDocumentRef,
  role,
  busy,
  optionsLoading,
  retryLocked,
  onSelectionChange,
}: {
  sprintOptions: RoleTaskOption[];
  sprintDocumentRef: TaskDocumentRef | undefined;
  role: LauncherRole;
  busy: boolean;
  optionsLoading: boolean;
  retryLocked: boolean;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}) {
  return (
    <label className={`${roleLauncherField} ${roleLauncherSprintField}`}>
      <select
        className={launcherSelect}
        aria-label="AR sprint"
        value={optionIndex(sprintOptions, sprintDocumentRef)}
        title={docSelectTitle(
          sprintOptions,
          sprintDocumentRef,
          'Sprint',
          sprintOptions.length ? 'Select sprint…' : 'No sprint documents',
        )}
        disabled={busy || optionsLoading || retryLocked}
        onChange={(event) => {
          const selected = refAtOptionIndex(sprintOptions, event.target.value);
          onSelectionChange({ role, ...(selected ? { sprintDocumentRef: selected } : {}) });
        }}
      >
        <option value="">{sprintOptions.length ? 'Select sprint…' : 'No sprint documents'}</option>
        {sprintOptions.map((option, index) => (
          <option key={index} value={index} title={taskRefIdentity(option.ref)}>
            {option.entry.title || option.entry.id}
          </option>
        ))}
      </select>
    </label>
  );
}

function MasterSelect({
  masterOptions,
  masterDocumentRef,
  role,
  sprintDocumentRef,
  busy,
  optionsLoading,
  retryLocked,
  onSelectionChange,
}: {
  masterOptions: RoleTaskOption[];
  masterDocumentRef: TaskDocumentRef | undefined;
  role: LauncherRole;
  sprintDocumentRef: TaskDocumentRef | undefined;
  busy: boolean;
  optionsLoading: boolean;
  retryLocked: boolean;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}) {
  return (
    <label className={`${roleLauncherField} ${roleLauncherMasterField}`}>
      <select
        className={launcherSelect}
        aria-label="AR master"
        value={optionIndex(masterOptions, masterDocumentRef)}
        title={docSelectTitle(
          masterOptions,
          masterDocumentRef,
          'Master',
          masterOptions.length ? 'Select master…' : 'No linked masters',
        )}
        disabled={busy || optionsLoading || retryLocked}
        onChange={(event) => {
          const selected = refAtOptionIndex(masterOptions, event.target.value);
          onSelectionChange({
            role,
            sprintDocumentRef,
            ...(selected ? { masterDocumentRef: selected } : {}),
          });
        }}
      >
        <option value="">{masterOptions.length ? 'Select master…' : 'No linked masters'}</option>
        {masterOptions.map((option, index) => (
          <option key={index} value={index} title={taskRefIdentity(option.ref)}>
            {option.entry.title || option.entry.id}
          </option>
        ))}
      </select>
    </label>
  );
}

function TaskSelect({
  taskOptions,
  taskDocumentRef,
  role,
  sprintDocumentRef,
  masterDocumentRef,
  busy,
  optionsLoading,
  retryLocked,
  onSelectionChange,
}: {
  taskOptions: RoleTaskOption[];
  taskDocumentRef: TaskDocumentRef | undefined;
  role: LauncherRole;
  sprintDocumentRef: TaskDocumentRef | undefined;
  masterDocumentRef: TaskDocumentRef | undefined;
  busy: boolean;
  optionsLoading: boolean;
  retryLocked: boolean;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}) {
  return (
    <label className={`${roleLauncherField} ${roleLauncherTaskField}`}>
      <select
        className={launcherSelect}
        aria-label="AR task"
        value={optionIndex(taskOptions, taskDocumentRef)}
        title={docSelectTitle(
          taskOptions,
          taskDocumentRef,
          'Task',
          taskOptions.length ? 'Select task…' : 'No leaf tasks',
        )}
        disabled={busy || optionsLoading || retryLocked}
        onChange={(event) => {
          const selected = refAtOptionIndex(taskOptions, event.target.value);
          onSelectionChange({
            role,
            ...(sprintDocumentRef ? { sprintDocumentRef } : {}),
            masterDocumentRef,
            ...(selected ? { taskDocumentRef: selected } : {}),
          });
        }}
      >
        <option value="">{taskOptions.length ? 'Select task…' : 'No leaf tasks'}</option>
        {taskOptions.map((option, index) => (
          <option key={index} value={index} title={taskRefIdentity(option.ref)}>
            {option.entry.title || option.entry.id}
          </option>
        ))}
      </select>
    </label>
  );
}

// The document half of the row: the role selector with the sprint, master and task
// selectors its role needs.
function LauncherDocumentFields({
  fields,
  role,
  boundRoles,
  status,
  selection,
  documents,
  sprintDocumentRef,
  masterDocumentRef,
  taskDocumentRef,
  busy,
  optionsLoading,
  retryLocked,
  onSelectionChange,
}: {
  fields: { sprint: boolean; master: boolean; task: boolean };
  role: LauncherRole;
  boundRoles: LauncherRole[] | undefined;
  status: string | undefined;
  selection: RoleLaunchSelection;
  documents: DocumentFields;
  sprintDocumentRef: TaskDocumentRef | undefined;
  masterDocumentRef: TaskDocumentRef | undefined;
  taskDocumentRef: TaskDocumentRef | undefined;
  busy: boolean;
  optionsLoading: boolean;
  retryLocked: boolean;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}) {
  return (
    <>
      <RoleSelect
        role={role}
        boundRoles={boundRoles}
        status={status}
        selection={selection}
        busy={busy}
        optionsLoading={optionsLoading}
        retryLocked={retryLocked}
        onSelectionChange={onSelectionChange}
      />
      {fields.sprint ? (
        <SprintSelect
          sprintOptions={documents.sprintOptions}
          sprintDocumentRef={sprintDocumentRef}
          role={role}
          busy={busy}
          optionsLoading={optionsLoading}
          retryLocked={retryLocked}
          onSelectionChange={onSelectionChange}
        />
      ) : null}
      {fields.master ? (
        <MasterSelect
          masterOptions={documents.masterOptions}
          masterDocumentRef={masterDocumentRef}
          role={role}
          sprintDocumentRef={sprintDocumentRef}
          busy={busy}
          optionsLoading={optionsLoading}
          retryLocked={retryLocked}
          onSelectionChange={onSelectionChange}
        />
      ) : null}
      {fields.task ? (
        <TaskSelect
          taskOptions={documents.taskOptions}
          taskDocumentRef={taskDocumentRef}
          role={role}
          sprintDocumentRef={sprintDocumentRef}
          masterDocumentRef={masterDocumentRef}
          busy={busy}
          optionsLoading={optionsLoading}
          retryLocked={retryLocked}
          onSelectionChange={onSelectionChange}
        />
      ) : null}
    </>
  );
}

// The agent override selector: the host agent behind the role, defaulting to the role default.
function AgentOverrideSelect({
  selection,
  agents,
  roleDefaults,
  choice,
  agentDisplayed,
  launchSelection,
  optionsReady,
  optionsLoading,
  busy,
  retryLocked,
  onSelectionChange,
}: {
  selection: RoleLaunchSelection;
  agents: RoleAgentChoice[];
  roleDefaults: RoleDefaults;
  choice: ResolvedAgentChoice;
  agentDisplayed: string;
  launchSelection: RoleLaunchSelection;
  optionsReady: boolean;
  optionsLoading: boolean;
  busy: boolean;
  retryLocked: boolean;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}) {
  const defaultLabel = choice.selectedAgentLabel
    ? choice.selectedAgentLabel + ' · default'
    : 'Role default';
  return (
    <label className={`${roleLauncherField} ${roleLauncherAgentField}`}>
      <select
        className={launcherSelect}
        aria-label="Role agent override"
        title={`${agentDisplayed} — ${defaultSourceTitle('agent', roleDefaults.choices, choice.selectedAgentLabel ?? roleDefaults.agent ?? 'unset')}`}
        value={selection.agentOverride?.agentId ?? ''}
        disabled={!optionsReady || !agents.length || optionsLoading || busy || retryLocked}
        onChange={(event) => {
          const agentId = event.target.value;
          onSelectionChange({
            ...launchSelection,
            agentOverride: agentId ? { agentId } : undefined,
          });
        }}
      >
        <option value="" title="Use the agent configured for this AR role">
          {defaultLabel}
        </option>
        {agents.map((agent) => (
          <option key={agent.id} value={agent.id}>
            {agent.label}
          </option>
        ))}
      </select>
    </label>
  );
}

// The model selector is inert until its agent resolves with offered models.
function modelSelectDisabled(
  choice: ResolvedAgentChoice,
  optionsReady: boolean,
  optionsLoading: boolean,
  busy: boolean,
  retryLocked: boolean,
): boolean {
  return (
    !optionsReady ||
    !choice.selectedAgent ||
    !choice.models.length ||
    optionsLoading ||
    busy ||
    retryLocked
  );
}

// The model's default option names the load state until an agent default resolves.
function modelDefaultOptionText(
  choice: ResolvedAgentChoice,
  selectedAgentId: string,
  selection: RoleLaunchSelection,
): string {
  if (!selectedAgentId) return 'Load agent default';
  if (!choice.selectedAgent) return 'Agent capabilities loading';
  if (!choice.models.length) return 'No model choices';
  if (choice.defaultModel) return choice.defaultModel.label + ' · default';
  return selection.agentOverride ? 'Agent default' : 'Role default';
}

// The model override selector: the host model behind the agent, defaulting to the agent default.
function ModelOverrideSelect({
  selection,
  roleDefaults,
  choice,
  modelDisplayed,
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
  modelDisplayed: string;
  launchSelection: RoleLaunchSelection;
  selectedAgentId: string;
  optionsReady: boolean;
  optionsLoading: boolean;
  busy: boolean;
  retryLocked: boolean;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}) {
  return (
    <label className={`${roleLauncherField} ${roleLauncherModelField}`}>
      <select
        className={launcherSelect}
        aria-label="Role model override"
        title={`${modelDisplayed} — ${defaultSourceTitle('model', roleDefaults.choices, choice.defaultModel?.label ?? roleDefaults.model ?? 'unset')}`}
        value={selection.agentOverride?.modelId ?? ''}
        disabled={modelSelectDisabled(choice, optionsReady, optionsLoading, busy, retryLocked)}
        onChange={(event) => {
          const modelId = event.target.value;
          const agentId = selection.agentOverride?.agentId ?? selectedAgentId;
          onSelectionChange({
            ...launchSelection,
            agentOverride: roleAgentOverrideFor(
              agentId,
              modelId || undefined,
              undefined,
              roleDefaults,
              selection.agentOverride?.featureValues,
            ),
          });
        }}
      >
        <option value="">{modelDefaultOptionText(choice, selectedAgentId, selection)}</option>
        {choice.models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.label}
          </option>
        ))}
      </select>
    </label>
  );
}

// The effort override selector, shown only while the model offers efforts.
function EffortOverrideSelect({
  selection,
  roleDefaults,
  choice,
  effortDisplayed,
  launchSelection,
  selectedAgentId,
  busy,
  retryLocked,
  onSelectionChange,
}: {
  selection: RoleLaunchSelection;
  roleDefaults: RoleDefaults;
  choice: ResolvedAgentChoice;
  effortDisplayed: string;
  launchSelection: RoleLaunchSelection;
  selectedAgentId: string;
  busy: boolean;
  retryLocked: boolean;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
}) {
  return (
    <label className={`${roleLauncherField} ${roleLauncherEffortField}`}>
      <select
        className={launcherSelect}
        aria-label="Role effort override"
        title={`${effortDisplayed} — ${defaultSourceTitle('effort', roleDefaults.choices, choice.defaultEffort?.label ?? roleDefaults.effort ?? 'unset')}`}
        value={selection.agentOverride?.effortId ?? ''}
        disabled={busy || retryLocked}
        onChange={(event) => {
          const agentId = selection.agentOverride?.agentId ?? selectedAgentId;
          onSelectionChange({
            ...launchSelection,
            agentOverride: roleAgentOverrideFor(
              agentId,
              selection.agentOverride?.modelId,
              event.target.value || undefined,
              roleDefaults,
              selection.agentOverride?.featureValues,
            ),
          });
        }}
      >
        <option value="">
          {choice.defaultEffort
            ? choice.defaultEffort.label + ' · default'
            : selection.agentOverride
              ? 'Model default'
              : 'Role default'}
        </option>
        {choice.efforts.map((effort) => (
          <option key={effort.id} value={effort.id}>
            {effort.label}
          </option>
        ))}
      </select>
    </label>
  );
}

// The feature controls behind the selected model: the model's own features, or the
// default model's while no model is chosen yet.
function overrideFeatures(choice: ResolvedAgentChoice): RoleFeatureChoice[] {
  return (choice.selectedModel ?? choice.models.find((model) => model.isDefault))?.features ?? [];
}

// The defaults the feature controls disclose: the role defaults on the role's own
// agent, else an empty disclosure for the foreign agent.
function overrideDefaults(selectedAgentId: string, roleDefaults: RoleDefaults): RoleDefaults {
  return selectedAgentId === roleDefaults.agent ? roleDefaults : {};
}

// The effort selector shows only while the selected model offers efforts.
function effortSelectShown(choice: ResolvedAgentChoice): boolean {
  return Boolean(choice.selectedModel && choice.efforts.length);
}

// The mode disclosure behind the row: the selected agent's label and description for
// the role's mode.
function modeDisclosures(
  choice: ResolvedAgentChoice,
  roleDefaults: RoleDefaults,
): {
  label: string | undefined;
  description: string | undefined;
} {
  const mode = choice.selectedAgent?.modes?.find((candidate) => candidate.id === roleDefaults.mode);
  return { label: mode?.label, description: mode?.description };
}

// The override half of the row: the agent, model and effort selectors with the mode,
// feature and instruction disclosures they resolve.
type LauncherOverrideProps = {
  selection: RoleLaunchSelection;
  agents: RoleAgentChoice[];
  roleDefaults: RoleDefaults;
  choice: ResolvedAgentChoice;
  launchSelection: RoleLaunchSelection;
  selectedAgentId: string;
  agentDisplayed: string;
  modelDisplayed: string;
  effortDisplayed: string;
  optionsReady: boolean;
  optionsLoading: boolean;
  busy: boolean;
  retryLocked: boolean;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
  basePreview: boolean;
};

function LauncherOverrideFields(props: LauncherOverrideProps) {
  const {
    selection,
    roleDefaults,
    choice,
    selectedAgentId,
    optionsReady,
    optionsLoading,
    busy,
    retryLocked,
    onSelectionChange,
    basePreview,
  } = props;
  const mode = modeDisclosures(choice, roleDefaults);
  return (
    <>
      <AgentOverrideSelect {...props} />
      <ModelOverrideSelect {...props} />
      {effortSelectShown(choice) ? <EffortOverrideSelect {...props} /> : null}
      <RoleLaunchDefaults
        ready={optionsReady}
        defaults={roleDefaults}
        selectedAgentId={selectedAgentId}
        basePreview={basePreview}
        modeLabel={mode.label}
        modeDescription={mode.description}
      />
      <RoleLaunchFeatures
        features={overrideFeatures(choice)}
        basePreview={basePreview}
        defaults={overrideDefaults(selectedAgentId, roleDefaults)}
        override={selection.agentOverride}
        disabled={!optionsReady || optionsLoading || busy || retryLocked}
        onChange={(agentOverride) =>
          onSelectionChange({ ...roleDocumentScope(selection), agentOverride })
        }
      />
    </>
  );
}

// The one actionable failure line behind the row: the choice problem, an unloadable
// agent's listing error, and the options and execution errors, in that order.
function failureLine(
  choiceProblem: string | null,
  selectedAgent: RoleAgentChoice | undefined,
  optionsError: string | null,
  executionError: string | null,
): string {
  return [
    choiceProblem,
    selectedAgent?.listingError
      ? 'Models of ' + selectedAgent.label + ' could not be listed: ' + selectedAgent.listingError
      : null,
    optionsError,
    executionError,
  ]
    .filter(Boolean)
    .join(' ');
}

function failureTestId(
  choiceProblem: string | null,
  selectedAgent: RoleAgentChoice | undefined,
): string {
  return choiceProblem || selectedAgent?.listingError ? 'role-choice-problem' : 'role-launch-error';
}

// The service-tier chip text: the tier on the role's own agent, else the provider
// default. Absent when the role states no tier.
function serviceTierText(roleDefaults: RoleDefaults, selectedAgentId: string): string | undefined {
  if (!roleDefaults.serviceTier) return undefined;
  if (selectedAgentId !== roleDefaults.agent) return 'Speed: provider default';
  return 'Speed: ' + (roleDefaults.serviceTier === 'fast' ? 'Fast' : roleDefaults.serviceTier);
}

// The agent-catalog refresh stays offered once the options answered or failed.
function refreshAgentsDisabled(
  optionsReady: boolean,
  optionsError: string | null,
  optionsLoading: boolean,
  busy: boolean,
): boolean {
  return (!optionsReady && !optionsError) || optionsLoading || busy;
}

// Start stays disabled while its readiness is missing or a bound seat's occupant runs.
function startButtonDisabled(
  readiness: LaunchReadiness,
  selection: RoleLaunchSelection,
  boundRoles: LauncherRole[] | undefined,
): boolean {
  return !boundCanStart(
    readiness.canStart,
    Boolean(boundRoles) && (!usesRequestReceipts(selection) || isTasklessRole(selection)),
    readiness.liveOccupant,
  );
}

// The retry the row offers: the saved request's selection with its id, while the
// receipt leaves a retry and the request still matches the row's selection.
function retryOffered(
  readiness: LaunchReadiness,
  retryRequestId: string | undefined,
):
  | {
      selection: RoleLaunchSelection;
      requestId: string;
    }
  | undefined {
  if (readiness.canRetry && readiness.canRetrySelection && retryRequestId) {
    return { selection: readiness.retrySelection, requestId: retryRequestId };
  }
  return undefined;
}

// The retry button for the saved launch request, shown only while offered.
function RetryButton({
  offered,
  busy,
  onRetry,
}: {
  offered: { selection: RoleLaunchSelection; requestId: string } | undefined;
  busy: boolean;
  onRetry: (selection: RoleLaunchSelection, requestId: string) => Promise<void>;
}) {
  if (!offered) return null;
  return (
    <button
      className={launcherButton({ selected: false })}
      type="button"
      aria-label="Retry role launch"
      title="Retry the same saved launch request"
      disabled={busy}
      onClick={() => void onRetry(offered.selection, offered.requestId)}
    >
      Retry
    </button>
  );
}

// The result button, offered for any execution once the selection is complete.
function ResultButton({
  shown,
  disabled,
  onRefreshResult,
}: {
  shown: boolean;
  disabled: boolean;
  onRefreshResult: () => void;
}) {
  if (!shown) return null;
  return (
    <button
      className={launcherButton({ selected: false })}
      type="button"
      aria-label="Refresh role result"
      title="Refresh role result"
      disabled={disabled}
      onClick={onRefreshResult}
    >
      Result
    </button>
  );
}

// The options retry, shown only while the options route failed.
function OptionsRetryButton({
  optionsError,
  optionsLoading,
  onRefreshOptions,
}: {
  optionsError: string | null;
  optionsLoading: boolean;
  onRefreshOptions: () => void;
}) {
  if (!optionsError) return null;
  return (
    <button
      className={launcherButton({ selected: false })}
      type="button"
      disabled={optionsLoading}
      onClick={onRefreshOptions}
    >
      Retry launch options
    </button>
  );
}
function ServiceTierChip({ roleDefaults, selectedAgentId, execution }: {
  roleDefaults: RoleDefaults;
  selectedAgentId: string;
  execution: RoleScopedExecution | undefined;
}) {
  const tierText = serviceTierText(roleDefaults, selectedAgentId);
  if (!tierText) return null;
  const basePreview = Boolean(execution?.stepUp);
  return (
    <span data-testid="role-service-tier" className={launcherChip} title={basePreview
      ? "Base service tier preview; not applied to the stepped-up execution."
      : "The role service tier applies on its default agent, including model overrides; a different agent uses its native default."}>
      {basePreview ? "Base · " : ""}{tierText}
    </span>
  );
}

type LauncherActionProps = {
  roleDefaults: RoleDefaults;
  selectedAgentId: string;
  readiness: LaunchReadiness;
  selection: RoleLaunchSelection;
  boundRoles: LauncherRole[] | undefined;
  currentScopedExecution: RoleScopedExecution | undefined;
  failureExecution: RoleScopedExecution | undefined;
  busy: boolean;
  optionsLoading: boolean;
  optionsReady: boolean;
  optionsError: string | null;
  executionError: string | null;
  selectedAgent: RoleAgentChoice | undefined;
  retryRequestId: string | undefined;
  startLabel: string;
  onLaunch: (selection: RoleLaunchSelection) => Promise<void>;
  onRevive: (selection: RoleLaunchSelection) => Promise<void>;
  onRetry: (selection: RoleLaunchSelection, requestId: string) => Promise<void>;
  onRefreshOptions: () => void;
  onRefreshCatalog: () => void;
  onRefreshResult: () => void;
};

function LauncherButtons({
  roleDefaults,
  selectedAgentId,
  readiness,
  selection,
  boundRoles,
  currentScopedExecution,
  busy,
  optionsLoading,
  optionsReady,
  optionsError,
  retryRequestId,
  startLabel,
  onLaunch,
  onRevive,
  onRetry,
  onRefreshOptions,
  onRefreshCatalog,
  onRefreshResult,
}: LauncherActionProps) {
  const { launchSelection, complete, canRefreshResult } = readiness;
  const retry = retryOffered(readiness, retryRequestId);
  return (
    <div className={roleLauncherActions}>
      <ServiceTierChip roleDefaults={roleDefaults} selectedAgentId={selectedAgentId} execution={currentScopedExecution} />
      <button
        className={launcherButton({ selected: true })}
        type="button"
        aria-label="Start role"
        title="Start role"
        disabled={startButtonDisabled(readiness, selection, boundRoles)}
        onClick={() => void onLaunch(launchSelection)}
      >
        {startLabel}
      </button>
      <RetryButton offered={retry} busy={busy} onRetry={onRetry} />
      <StepUpControl defaults={roleDefaults} selection={selection} disabled={!complete || !optionsReady || busy || optionsLoading || Boolean(retry)} onLaunch={onLaunch} />
      <StepUpStatus execution={currentScopedExecution} />
      <RoleReviveControl
        execution={currentScopedExecution}
        enabled={optionsReady && complete && !busy}
        className={launcherButton({ selected: false })}
        onRevive={() => void onRevive(launchSelection)}
      />
      <ResultButton
        shown={canRefreshResult}
        disabled={busy || optionsLoading}
        onRefreshResult={onRefreshResult}
      />
      <OptionsRetryButton
        optionsError={optionsError}
        optionsLoading={optionsLoading}
        onRefreshOptions={onRefreshOptions}
      />
      <button
        className={launcherButton({ selected: false })}
        type="button"
        aria-label="Refresh role agents"
        title="Refresh agent catalog"
        disabled={refreshAgentsDisabled(optionsReady, optionsError, optionsLoading, busy)}
        onClick={onRefreshCatalog}
      >
        Refresh
      </button>
    </div>
  );
}

function LauncherActionBar(props: LauncherActionProps) {
  return (
    <>
      <LauncherButtons {...props} />
      <RoleExecutionStatus
        execution={props.failureExecution}
        showStepUp={false}
        failure={failureLine(
          props.readiness.choiceProblem,
          props.selectedAgent,
          props.optionsError,
          props.executionError,
        )}
        failureTestId={failureTestId(props.readiness.choiceProblem, props.selectedAgent)}
        className={roleLauncherMeta}
      />
    </>
  );
}

export interface RoleLauncherProps {
  tasks: TaskEntry[];
  boundRoles?: LauncherRole[];
  progress?: string;
  roleDefaults: RoleDefaults;
  agents: RoleAgentChoice[];
  selection: RoleLaunchSelection;
  currentScopedExecution?: RoleScopedExecution;
  failureExecution?: RoleScopedExecution;
  optionsReady: boolean;
  optionsLoading: boolean;
  busy: boolean;
  retryRequestId?: string;
  optionsError: string | null;
  executionError: string | null;
  onSelectionChange: (selection: RoleLaunchSelection) => void;
  onLaunch: (selection: RoleLaunchSelection) => Promise<void>;
  onRevive: (selection: RoleLaunchSelection) => Promise<void>;
  onRetry: (selection: RoleLaunchSelection, requestId: string) => Promise<void>;
  onRefreshOptions: () => void;
  onRefreshCatalog: () => void;
  onRefreshResult: () => void;
}

function launcherDocuments(
  tasks: TaskEntry[],
  sprintDocumentRef: TaskDocumentRef | undefined,
  masterDocumentRef: TaskDocumentRef | undefined,
) {
  const sprintOptions = sprintOptionsForEntries(tasks);
  const selectedSprint = sprintOptions.find((option) =>
    sameTaskDocumentRef(option.ref, sprintDocumentRef),
  )?.entry;
  const masterOptions = masterOptionsForSprint(tasks, selectedSprint);
  const selectedMaster = masterOptions.find((option) =>
    sameTaskDocumentRef(option.ref, masterDocumentRef),
  )?.entry;
  const taskOptions = taskOptionsForMaster(tasks, selectedMaster);
  return { sprintOptions, masterOptions, taskOptions };
}

export function RoleLauncher(props: RoleLauncherProps) {
  const { tasks, boundRoles, progress, roleDefaults, agents, selection } = props;
  const { currentScopedExecution, failureExecution, optionsReady, busy, retryRequestId } = props;
  const { onSelectionChange } = props;
  const { role, sprintDocumentRef, masterDocumentRef, taskDocumentRef } = selection;
  const fields = referenceFields(Boolean(boundRoles), selection);
  const row = launcherRow(progress);
  const documents = launcherDocuments(tasks, sprintDocumentRef, masterDocumentRef);

  usePruneSeededSelection({
    tasks,
    role,
    sprintDocumentRef,
    masterDocumentRef,
    taskDocumentRef,
    onSelectionChange,
  });

  const choice = resolveAgentChoice(agents, roleDefaults, selection);
  usePruneStaleOverride({
    optionsReady,
    agents,
    models: choice.models,
    efforts: choice.efforts,
    selection,
    onSelectionChange,
  });

  const readiness = resolveLaunchReadiness({
    selection,
    roleDefaults,
    agents,
    currentScopedExecution,
    optionsReady,
    busy,
    retryRequestId,
  });
  const agentDisplayed = displayedAgentLabel(selection, agents, choice.selectedAgentLabel);
  const modelDisplayed = displayedModelLabel(selection, choice);
  const effortDisplayed = displayedEffortLabel(selection, choice);
  const retryLocked = currentScopedExecution?.canRetry === true;

  return (
    <div className={roleLauncherGrid} data-testid="role-launcher">
      <LauncherDocumentFields
        {...props}
        boundRoles={boundRoles}
        fields={fields}
        role={role}
        status={readiness.status}
        documents={documents}
        sprintDocumentRef={sprintDocumentRef}
        masterDocumentRef={masterDocumentRef}
        taskDocumentRef={taskDocumentRef}
        retryLocked={retryLocked}
      />
      <LauncherOverrideFields
        {...props}
        choice={choice}
        launchSelection={readiness.launchSelection}
        selectedAgentId={choice.selectedAgentId}
        agentDisplayed={agentDisplayed}
        modelDisplayed={modelDisplayed}
        effortDisplayed={effortDisplayed}
        retryLocked={retryLocked}
        basePreview={Boolean(currentScopedExecution?.stepUp)}
      />
      <LauncherActionBar
        {...props}
        boundRoles={boundRoles}
        selectedAgentId={choice.selectedAgentId}
        readiness={readiness}
        currentScopedExecution={currentScopedExecution}
        failureExecution={failureExecution}
        selectedAgent={choice.selectedAgent}
        retryRequestId={retryRequestId}
        startLabel={row.startLabel}
      />
    </div>
  );
}
