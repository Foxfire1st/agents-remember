import { useEffect } from "react";
import { css } from "../../../styled-system/css";
import { launcherButton, launcherChip, launcherSelect } from "../../grammar/viewTabLook";
import type { SeriesNode, TaskDocNode } from "../../types/projection";
import { sameTaskDocumentRef } from "../../data/taskIdentity";
import { RoleExecutionStatus, RoleReviveControl } from "../RoleExecutionStatus";
import { RoleLaunchDefaults, RoleLaunchFeatures } from "../RoleLaunchFeatures";


import { EMPTY_ROLE_EFFORTS, EMPTY_ROLE_MODELS, LAUNCHER_ROLES, isTasklessRole, usesRequestReceipts, launchChoiceProblem, launchSelectionComplete, masterOptionsForSprint, optionIndex, roleAgentOverrideFor, roleDocumentScope, refAtOptionIndex, roleNeedsMaster, roleNeedsSprint, roleNeedsTask, sameRoleDocumentScope, sprintOptionsForDocs, taskOptionsForMaster, taskRefIdentity, type RoleAgentChoice, type RoleLaunchSelection, type LauncherRole, type RoleDefaults, type RoleScopedExecution, type RoleTaskOption } from "../roleLaunchModel";
import type { TaskDocumentRef } from "../../types/terminalCatalog";



const roleLauncherGrid = css({
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  width: "100%",
  minWidth: "0",
  columnGap: "0.15rem",
  rowGap: "0.5rem",
  padding: "0.65rem 0.5rem",
  borderBottom: "1px solid var(--grid)",
  background: "var(--bg-panel)",
  // Rule 8 and the overfull-feature recovery: the row wraps by whole controls
  // at any width whenever its controls cannot fit; it never clips or hides
  // them and offers no overflow menu.
});
// A selector's wrapper: no visible title (each selector keeps its accessible
// name and carries a hover text that says what it selects). It sizes to its
// value up to its cap and yields below it, so the full row still lands on one
// line; only a longer value ends in an ellipsis, whole in the hover text.
const roleLauncherField = css({
  display: "grid",
  flexGrow: "0",
  flexBasis: "auto",
  minWidth: "6rem",
});
// Selectors give way first: short-valued wrappers yield little, the
// default-valued wrappers most, each down to its floor; a chip or button never
// yields. The document caps keep the ordinary eleven-control row on one line
// at 1440 px; whatever still does not fit wraps by whole controls.
const roleLauncherRoleField = css({ maxWidth: "9rem", flexShrink: 0.5, "@media (min-width: 90rem)": { minWidth: "5.5rem" } });
const roleLauncherSprintField = css({ maxWidth: "6rem", flexShrink: 0.5, "@media (min-width: 90rem)": { minWidth: "5rem" } });
const roleLauncherMasterField = css({ maxWidth: "6rem", flexShrink: 0.5, "@media (min-width: 90rem)": { minWidth: "5rem" } });
const roleLauncherTaskField = css({ maxWidth: "6rem", flexShrink: 0.5, "@media (min-width: 90rem)": { minWidth: "5rem" } });
const roleLauncherAgentField = css({ maxWidth: "10rem", flexShrink: 1.25, "@media (min-width: 90rem)": { minWidth: "4.5rem" } });
const roleLauncherModelField = css({ maxWidth: "10rem", flexShrink: 1.25, "@media (min-width: 90rem)": { minWidth: "4.5rem" } });
const roleLauncherEffortField = css({ maxWidth: "10rem", flexShrink: 1.25, "@media (min-width: 90rem)": { minWidth: "4.5rem" } });
// Every selector of the row is the one shared selector in
// ../../grammar/viewTabLook (imported as `launcherSelect` above): the row
// holds no copy of the tabs' look. Buttons likewise use the shared
// `launcherButton` (Start the selected-tab look, the rest the unselected one).
const roleLauncherActions = css({
  display: "flex",
  flex: "0 0 auto",
  alignItems: "center",
  flexWrap: "wrap",
  gap: "0.35rem",
  minWidth: "0",
});
const roleLauncherMeta = css({
  flex: "1 0 100%",
  minWidth: "0",
  color: "muted",
  fontSize: "0.64rem",
  lineHeight: "1.35",
  overflowWrap: "anywhere",
});
function boundRoleLabel(label: string, selected: boolean, bound: boolean, status: string | undefined): string {
  return bound && selected && status === "running" ? label + " (running)" : label;
}

function boundCanStart(canStart: boolean, bound: boolean, liveOccupant: boolean): boolean {
  return canStart && !(bound && liveOccupant);
}

// Where a default comes from lives in the hover text of its control, in the
// form "<key>: <value> · <source>".
function defaultSourceTitle(key: string, choices: RoleDefaults["choices"], fallbackValue: string): string {
  const choice = choices?.[key];
  const value = choice?.value === undefined || choice?.value === null ? fallbackValue : String(choice.value);
  return `${key}: ${value} · ${choice?.source ?? "host default"}`;
}

// The hover text of a document selector: the complete rendered label it
// currently shows, what it selects, and the document identity behind it.
function docSelectTitle(options: RoleTaskOption[], ref: TaskDocumentRef | undefined, what: string, emptyText: string): string {
  const value = optionIndex(options, ref);
  if (value === "") return what;
  const selected = options[Number(value)];
  const label = selected ? selected.doc.title || selected.doc.id : emptyText;
  const identity = (selected && selected.ref ? `${selected.ref.repository}/${selected.ref.path}` : undefined) ?? taskRefIdentity(ref);
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
  return { startLabel: progress ?? "Start" };
}

export function RoleLauncher({
  taskDocuments,
  series,
  boundRoles,
  progress,
  roleDefaults,
  agents,
  selection,
  currentScopedExecution,
  failureExecution,
  optionsReady,
  optionsLoading,
  busy,
  retryRequestId,
  optionsError,
  executionError,
  onSelectionChange,
  onLaunch,
  onRevive,
  onRetry,
  onRefreshOptions,
  onRefreshCatalog,
  onRefreshResult,
}: {
  taskDocuments: TaskDocNode[];
  series: SeriesNode[];
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
}) {
  const { role, sprintDocumentRef, masterDocumentRef, taskDocumentRef } = selection;
  const fields = referenceFields(Boolean(boundRoles), selection);
  const row = launcherRow(progress);
  const sprintOptions = sprintOptionsForDocs(taskDocuments);
  const selectedSprint = sprintOptions.find((option) => sameTaskDocumentRef(option.ref, sprintDocumentRef))?.doc;
  const masterOptions = masterOptionsForSprint(taskDocuments, selectedSprint);
  const selectedMaster = masterOptions.find((option) => sameTaskDocumentRef(option.ref, masterDocumentRef))?.doc;
  const taskOptions = taskOptionsForMaster(taskDocuments, series, selectedMaster);

  useEffect(() => {
    const currentSprints = sprintOptionsForDocs(taskDocuments);
    if (sprintDocumentRef && !currentSprints.some((option) => sameTaskDocumentRef(option.ref, sprintDocumentRef))) {
      onSelectionChange({ role });
      return;
    }
    const currentSprint = currentSprints.find((option) => sameTaskDocumentRef(option.ref, sprintDocumentRef))?.doc;
    const currentMasters = masterOptionsForSprint(taskDocuments, currentSprint);
    if (masterDocumentRef && !currentMasters.some((option) => sameTaskDocumentRef(option.ref, masterDocumentRef))) {
      onSelectionChange({ role, ...(sprintDocumentRef ? { sprintDocumentRef } : {}) });
      return;
    }
    const currentMaster = currentMasters.find((option) => sameTaskDocumentRef(option.ref, masterDocumentRef))?.doc;
    const currentTasks = taskOptionsForMaster(taskDocuments, series, currentMaster);
    if (taskDocumentRef && !currentTasks.some((option) => sameTaskDocumentRef(option.ref, taskDocumentRef))) {
      onSelectionChange({
        role,
        ...(sprintDocumentRef ? { sprintDocumentRef } : {}),
        ...(masterDocumentRef ? { masterDocumentRef } : {}),
      });
    }
  }, [taskDocuments, series, role, sprintDocumentRef, masterDocumentRef, taskDocumentRef, onSelectionChange]);

  const selectedAgentId = selection.agentOverride?.agentId ?? roleDefaults.agent ?? "";
  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId);
  const models = selectedAgent?.models ?? EMPTY_ROLE_MODELS;
  const defaultModel = models.find((model) => model.id === roleDefaults.model);
  const selectedModelId = selection.agentOverride?.modelId ?? defaultModel?.id ?? "";
  const selectedModel = models.find((model) => model.id === selectedModelId);
  const efforts = selectedModel?.efforts ?? EMPTY_ROLE_EFFORTS;
  const defaultEffort = efforts.find((effort) => effort.id === (roleDefaults.effort ?? selectedModel?.defaultEffort));
  const selectedAgentLabel = agents.find((agent) => agent.id === roleDefaults.agent)?.label ?? roleDefaults.agent;
  // The complete labels the override selectors currently show: the chosen
  // option when an override is set, else the default first option. Each mirrors
  // its default-option JSX below.
  const agentDisplayed = selection.agentOverride?.agentId
    ? agents.find((agent) => agent.id === selection.agentOverride?.agentId)?.label ?? selection.agentOverride.agentId
    : selectedAgentLabel ? selectedAgentLabel + " · default" : "Role default";
  const modelDisplayed = selection.agentOverride?.modelId
    ? models.find((model) => model.id === selection.agentOverride?.modelId)?.label ?? selection.agentOverride.modelId
    : !selectedAgentId
      ? "Load agent default"
      : !selectedAgent
        ? "Agent capabilities loading"
        : models.length
          ? defaultModel ? defaultModel.label + " · default" : selection.agentOverride ? "Agent default" : "Role default"
          : "No model choices";
  const effortDisplayed = selection.agentOverride?.effortId
    ? efforts.find((effort) => effort.id === selection.agentOverride?.effortId)?.label ?? selection.agentOverride.effortId
    : defaultEffort ? defaultEffort.label + " · default" : selection.agentOverride ? "Model default" : "Role default";
  const launchSelection: RoleLaunchSelection = {
    ...roleDocumentScope(selection),
    ...(selection.agentOverride ? { agentOverride: selection.agentOverride } : {}),
  };
  const complete = launchSelectionComplete(launchSelection);
  const status = currentScopedExecution?.status.toLowerCase();
  const liveOccupant = ["accepted", "unknown", "running", "starting"].includes(status ?? "");
  const canRetry = optionsReady && complete && !busy && currentScopedExecution?.canRetry === true && Boolean(retryRequestId);
  const retrySelection = currentScopedExecution?.retryPayload ?? launchSelection;
  const canRetrySelection = sameRoleDocumentScope(roleDocumentScope(retrySelection), roleDocumentScope(launchSelection));
  const choiceProblem = optionsReady ? launchChoiceProblem(roleDefaults, agents, selection.agentOverride) : null;
  const canStart = !choiceProblem && (usesRequestReceipts(selection)
    ? optionsReady && complete && !busy && !["starting", "unknown"].includes(status ?? "")
    : optionsReady && complete && !busy && !liveOccupant && !canRetry && currentScopedExecution?.canStart !== false);
  // Every refresh re-reads the agent, so Result is offered for any execution, closed ones included.
  const canRefreshResult = complete && Boolean(status);

  useEffect(() => {
    if (!optionsReady) return;
    const activeScope = roleDocumentScope(selection);
    if (selection.agentOverride?.agentId && !agents.some((agent) => agent.id === selection.agentOverride?.agentId)) {
      onSelectionChange({ ...activeScope, agentOverride: undefined });
      return;
    }
    if (selection.agentOverride?.modelId && !models.some((model) => model.id === selection.agentOverride?.modelId)) {
      onSelectionChange({
        ...activeScope,
        agentOverride: { agentId: selection.agentOverride.agentId },
      });
      return;
    }
    if (selection.agentOverride?.effortId && !efforts.some((effort) => effort.id === selection.agentOverride?.effortId)) {
      onSelectionChange({
        ...activeScope,
        agentOverride: {
          agentId: selection.agentOverride.agentId,
          ...(selection.agentOverride.modelId ? { modelId: selection.agentOverride.modelId } : {}),
        },
      });
    }
  }, [optionsReady, agents, models, efforts, selection, onSelectionChange]);

  return (
    <div className={roleLauncherGrid} data-testid="role-launcher">
      <label className={`${roleLauncherField} ${roleLauncherRoleField}`}>
        <select
          className={launcherSelect}
          id="launcher-role"
          aria-label="Role"
          title={`${boundRoleLabel(LAUNCHER_ROLES.find((option) => option.id === role)?.label ?? role, true, Boolean(boundRoles), status)} — Role`}
          value={role}
          disabled={busy || optionsLoading || (!usesRequestReceipts(selection) && currentScopedExecution?.canRetry === true)}
          onChange={(event) => onSelectionChange(boundRoles ? { ...roleDocumentScope(selection), role: event.target.value as LauncherRole } : { role: event.target.value as LauncherRole })}
        >
          {LAUNCHER_ROLES.filter((option) => !boundRoles || boundRoles.includes(option.id)).map((option) => <option key={option.id} value={option.id}>{boundRoleLabel(option.label, option.id === role, Boolean(boundRoles), status)}</option>)}
        </select>
      </label>
      {fields.sprint ? (
        <label className={`${roleLauncherField} ${roleLauncherSprintField}`}>
          <select
            className={launcherSelect}
            aria-label="AR sprint"
            value={optionIndex(sprintOptions, sprintDocumentRef)}
            title={docSelectTitle(sprintOptions, sprintDocumentRef, "Sprint", sprintOptions.length ? "Select sprint…" : "No sprint documents")}
            disabled={busy || optionsLoading || currentScopedExecution?.canRetry === true}
            onChange={(event) => {
              const selected = refAtOptionIndex(sprintOptions, event.target.value);
              onSelectionChange({ role, ...(selected ? { sprintDocumentRef: selected } : {}) });
            }}
          >
            <option value="">{sprintOptions.length ? "Select sprint…" : "No sprint documents"}</option>
            {sprintOptions.map((option, index) => (
              <option key={index} value={index} title={taskRefIdentity(option.ref)}>
                {option.doc.title || option.doc.id}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {fields.master ? (
        <label className={`${roleLauncherField} ${roleLauncherMasterField}`}>
          <select
            className={launcherSelect}
            aria-label="AR master"
            value={optionIndex(masterOptions, masterDocumentRef)}
            title={docSelectTitle(masterOptions, masterDocumentRef, "Master", masterOptions.length ? "Select master…" : "No linked masters")}
            disabled={busy || optionsLoading || currentScopedExecution?.canRetry === true}
            onChange={(event) => {
              const selected = refAtOptionIndex(masterOptions, event.target.value);
              onSelectionChange({
                role,
                sprintDocumentRef,
                ...(selected ? { masterDocumentRef: selected } : {}),
              });
            }}
          >
            <option value="">{masterOptions.length ? "Select master…" : "No linked masters"}</option>
            {masterOptions.map((option, index) => (
              <option key={index} value={index} title={taskRefIdentity(option.ref)}>
                {option.doc.title || option.doc.id}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {fields.task ? (
        <label className={`${roleLauncherField} ${roleLauncherTaskField}`}>
          <select
            className={launcherSelect}
            aria-label="AR task"
            value={optionIndex(taskOptions, taskDocumentRef)}
            title={docSelectTitle(taskOptions, taskDocumentRef, "Task", taskOptions.length ? "Select task…" : "No leaf tasks")}
            disabled={busy || optionsLoading || currentScopedExecution?.canRetry === true}
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
            <option value="">{taskOptions.length ? "Select task…" : "No leaf tasks"}</option>
            {taskOptions.map((option, index) => (
              <option key={index} value={index} title={taskRefIdentity(option.ref)}>
                {option.doc.title || option.doc.id}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <label className={`${roleLauncherField} ${roleLauncherAgentField}`}>
        <select
          className={launcherSelect}
          aria-label="Role agent override"
          title={`${agentDisplayed} — ${defaultSourceTitle("agent", roleDefaults.choices, selectedAgentLabel ?? roleDefaults.agent ?? "unset")}`}
          value={selection.agentOverride?.agentId ?? ""}
          disabled={!optionsReady || !agents.length || optionsLoading || busy || currentScopedExecution?.canRetry === true}
          onChange={(event) => {
            const agentId = event.target.value;
            onSelectionChange({
              ...launchSelection,
              agentOverride: agentId ? { agentId } : undefined,
            });
          }}
        >
          <option value="" title="Use the agent configured for this AR role">
            {selectedAgentLabel ? selectedAgentLabel + " · default" : "Role default"}
          </option>
          {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.label}</option>)}
        </select>
      </label>
      <label className={`${roleLauncherField} ${roleLauncherModelField}`}>
        <select
          className={launcherSelect}
          aria-label="Role model override"
          title={`${modelDisplayed} — ${defaultSourceTitle("model", roleDefaults.choices, defaultModel?.label ?? roleDefaults.model ?? "unset")}`}
          value={selection.agentOverride?.modelId ?? ""}
          disabled={!optionsReady || !selectedAgent || !models.length || optionsLoading || busy || currentScopedExecution?.canRetry === true}
          onChange={(event) => {
            const modelId = event.target.value;
            const agentId = selection.agentOverride?.agentId ?? selectedAgentId;
            onSelectionChange({
              ...launchSelection,
              agentOverride: roleAgentOverrideFor(agentId, modelId || undefined, undefined, roleDefaults, selection.agentOverride?.featureValues),
            });
          }}
        >
          <option value="">
            {!selectedAgentId
              ? "Load agent default"
              : !selectedAgent
                ? "Agent capabilities loading"
                : models.length
                  ? defaultModel ? defaultModel.label + " · default" : selection.agentOverride ? "Agent default" : "Role default"
                  : "No model choices"}
          </option>
          {models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
        </select>
      </label>
      {selectedModel && efforts.length ? (
        <label className={`${roleLauncherField} ${roleLauncherEffortField}`}>
          <select
            className={launcherSelect}
            aria-label="Role effort override"
            title={`${effortDisplayed} — ${defaultSourceTitle("effort", roleDefaults.choices, defaultEffort?.label ?? roleDefaults.effort ?? "unset")}`}
            value={selection.agentOverride?.effortId ?? ""}
            disabled={busy || currentScopedExecution?.canRetry === true}
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
              {defaultEffort ? defaultEffort.label + " · default" : selection.agentOverride ? "Model default" : "Role default"}
            </option>
            {efforts.map((effort) => <option key={effort.id} value={effort.id}>{effort.label}</option>)}
          </select>
        </label>
      ) : null}
      <RoleLaunchDefaults ready={optionsReady} defaults={roleDefaults} selectedAgentId={selectedAgentId}
        modeLabel={selectedAgent?.modes?.find((mode) => mode.id === roleDefaults.mode)?.label}
        modeDescription={selectedAgent?.modes?.find((mode) => mode.id === roleDefaults.mode)?.description} />
      <RoleLaunchFeatures features={(selectedModel ?? models.find((model) => model.isDefault))?.features ?? []}
        defaults={selectedAgentId === roleDefaults.agent ? roleDefaults : {}}
        override={selection.agentOverride} disabled={!optionsReady || optionsLoading || busy || currentScopedExecution?.canRetry === true}
        onChange={(agentOverride) => onSelectionChange({ ...roleDocumentScope(selection), agentOverride })} />
      <div className={roleLauncherActions}>
        {roleDefaults.serviceTier ? (
          <span data-testid="role-service-tier" className={launcherChip} title="The role service tier applies on its default agent, including model overrides; a different agent uses its native default.">
            {"Speed: " + (selectedAgentId === roleDefaults.agent ? (roleDefaults.serviceTier === "fast" ? "Fast" : roleDefaults.serviceTier) : "provider default")}
          </span>
        ) : null}
        <button className={launcherButton({ selected: true })} type="button" aria-label="Start role" title="Start role" disabled={!boundCanStart(canStart, Boolean(boundRoles) && (!usesRequestReceipts(selection) || isTasklessRole(selection)), liveOccupant)} onClick={() => void onLaunch(launchSelection)}>{row.startLabel}</button>
        {canRetry && canRetrySelection && retryRequestId ? (
          <button className={launcherButton({ selected: false })} type="button" aria-label="Retry role launch" title="Retry the same saved launch request" disabled={busy} onClick={() => void onRetry(retrySelection, retryRequestId)}>Retry</button>
        ) : null}
        <RoleReviveControl execution={currentScopedExecution} enabled={optionsReady && complete && !busy} className={launcherButton({ selected: false })} onRevive={() => void onRevive(launchSelection)} />
        {canRefreshResult ? <button className={launcherButton({ selected: false })} type="button" aria-label="Refresh role result" title="Refresh role result" disabled={busy || optionsLoading} onClick={onRefreshResult}>Result</button> : null}
        {optionsError ? <button className={launcherButton({ selected: false })} type="button" disabled={optionsLoading} onClick={onRefreshOptions}>Retry launch options</button> : null}
        <button className={launcherButton({ selected: false })} type="button" aria-label="Refresh role agents" title="Refresh agent catalog" disabled={(!optionsReady && !optionsError) || optionsLoading || busy} onClick={onRefreshCatalog}>Refresh</button>
      </div>
      <RoleExecutionStatus
        execution={failureExecution}
        failure={[choiceProblem, selectedAgent?.listingError ? "Models of " + selectedAgent.label + " could not be listed: " + selectedAgent.listingError : null, optionsError, executionError].filter(Boolean).join(" ")}
        failureTestId={choiceProblem || selectedAgent?.listingError ? "role-choice-problem" : "role-launch-error"}
        className={roleLauncherMeta}
      />
    </div>
  );

}
