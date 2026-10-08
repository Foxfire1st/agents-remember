import { useEffect } from "react";
import { css, cva } from "../../../styled-system/css";
import type { SeriesNode, TaskDocNode } from "../../types/projection";
import { sameTaskDocumentRef } from "../../data/taskIdentity";
import { RoleExecutionStatus, RoleReviveControl } from "../RoleExecutionStatus";


import { EMPTY_ROLE_EFFORTS, EMPTY_ROLE_MODELS, LAUNCHER_ROLES, isTasklessRole, usesRequestReceipts, launchChoiceProblem, launchSelectionComplete, masterOptionsForSprint, optionIndex, roleAgentOverrideFor, roleDocumentScope, refAtOptionIndex, roleNeedsMaster, roleNeedsSprint, roleNeedsTask, sameRoleDocumentScope, sprintOptionsForDocs, taskOptionsForMaster, taskRefIdentity, type RoleAgentChoice, type RoleLaunchSelection, type LauncherRole, type RoleDefaults, type RoleScopedExecution } from "../roleLaunchModel";



const roleLauncherGrid = css({
  display: "flex",
  flexWrap: "wrap",
  alignItems: "flex-end",
  width: "100%",
  minWidth: "0",
  columnGap: "0.55rem",
  rowGap: "0.5rem",
  padding: "0.65rem 0.75rem",
  borderBottom: "1px solid var(--grid)",
  background: "var(--bg-panel)",
});
const roleLauncherField = css({
  display: "grid",
  flex: "0 0 auto",
  gap: "0.22rem",
  minWidth: "0",
  color: "muted",
  fontSize: "0.75rem",
  letterSpacing: "0.035em",
  textTransform: "uppercase",
});
const roleLauncherRoleField = css({ width: "8rem" });
const roleLauncherSprintField = css({ width: "8rem" });
const roleLauncherMasterField = css({ width: "8.75rem" });
const roleLauncherTaskField = css({ width: "9.5rem" });
const roleLauncherAgentField = css({ width: "9.5rem" });
const roleLauncherModelField = css({ width: "10rem" });
const roleLauncherEffortField = css({ width: "8rem" });
const roleLauncherSelect = css({
  appearance: "none",
  width: "100%",
  minWidth: "0",
  maxWidth: "100%",
  height: "2.55rem",
  minHeight: "2.55rem",
  paddingInline: "0.58rem",
  paddingRight: "2rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  backgroundColor: "bg",
  backgroundImage: "linear-gradient(45deg, transparent 50%, var(--muted) 50%), linear-gradient(135deg, var(--muted) 50%, transparent 50%)",
  backgroundPosition: "calc(100% - 14px) 50%, calc(100% - 9px) 50%",
  backgroundSize: "5px 5px, 5px 5px",
  backgroundRepeat: "no-repeat",
  color: "ink",
  font: "inherit",
  fontSize: "0.86rem",
  letterSpacing: "0",
  textTransform: "none",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  transition: "border-color 120ms ease, box-shadow 120ms ease",
  _hover: { borderColor: "cyan" },
  _focusVisible: {
    outlineWidth: "2px",
    outlineStyle: "solid",
    outlineColor: "amber",
    outlineOffset: "1px",
    borderColor: "amber",
  },
  _disabled: { opacity: "0.58", cursor: "not-allowed" },
});
const roleLauncherButton = cva({
  base: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    minWidth: "0",
    height: "2.55rem",
    minHeight: "2.55rem",
    flexShrink: "0",
    paddingInline: "0.62rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderRadius: "3px",
    font: "inherit",
    fontSize: "0.77rem",
    fontWeight: "600",
    lineHeight: "1.2",
    whiteSpace: "nowrap",
    cursor: "pointer",
    transition: "background-color 120ms ease, border-color 120ms ease, color 120ms ease",
    _focusVisible: { outlineWidth: "2px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "2px" },
    _disabled: { opacity: "0.5", cursor: "not-allowed" },
  },
  variants: {
    tone: {
      primary: {
        background: "amber",
        color: "bg",
        borderColor: "amber",
        _hover: { background: "gold", borderColor: "gold" },
      },
      secondary: {
        background: "transparent",
        color: "cyan",
        borderColor: "cyan",
        _hover: { background: "oklch(0.85 0.13 200 / 0.08)", color: "ink" },
      },
      quiet: {
        background: "transparent",
        color: "muted",
        borderColor: "grid",
        _hover: { color: "ink", borderColor: "amber", background: "oklch(0.82 0.16 75 / 0.06)" },
      },
    },
  },
});
const roleLauncherActions = css({
  display: "flex",
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

function referenceFields(bound: boolean, selection: RoleLaunchSelection) {
  return {
    sprint: !bound && roleNeedsSprint(selection.role),
    master: !bound && roleNeedsMaster(selection.role) && Boolean(selection.sprintDocumentRef),
    task: !bound && roleNeedsTask(selection.role) && Boolean(selection.masterDocumentRef),
  };
}

function launcherRow(bound: boolean, progress?: string) {
  return { style: bound ? { flexWrap: "nowrap" as const, overflowX: "auto" as const } : undefined, startLabel: progress ?? "Start" };
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
  const row = launcherRow(Boolean(boundRoles), progress);
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
    if (selection.agentOverride && !agents.some((agent) => agent.id === selection.agentOverride?.agentId)) {
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
    <div className={roleLauncherGrid} style={row.style} data-testid="role-launcher">
      <label className={`${roleLauncherField} ${roleLauncherRoleField}`}>
        Role
        <select
          className={roleLauncherSelect}
          id="launcher-role"
          title={LAUNCHER_ROLES.find((option) => option.id === role)?.label ?? role}
          value={role}
          disabled={busy || optionsLoading || (!usesRequestReceipts(selection) && currentScopedExecution?.canRetry === true)}
          onChange={(event) => onSelectionChange(boundRoles ? { ...roleDocumentScope(selection), role: event.target.value as LauncherRole } : { role: event.target.value as LauncherRole })}
        >
          {LAUNCHER_ROLES.filter((option) => !boundRoles || boundRoles.includes(option.id)).map((option) => <option key={option.id} value={option.id}>{boundRoleLabel(option.label, option.id === role, Boolean(boundRoles), status)}</option>)}
        </select>
      </label>
      {fields.sprint ? (
        <label className={`${roleLauncherField} ${roleLauncherSprintField}`}>
          Sprint
          <select
            className={roleLauncherSelect}
            aria-label="AR sprint"
            value={optionIndex(sprintOptions, sprintDocumentRef)}
            title={taskRefIdentity(sprintDocumentRef)}
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
          Master
          <select
            className={roleLauncherSelect}
            aria-label="AR master"
            value={optionIndex(masterOptions, masterDocumentRef)}
            title={taskRefIdentity(masterDocumentRef)}
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
          Task
          <select
            className={roleLauncherSelect}
            aria-label="AR task"
            value={optionIndex(taskOptions, taskDocumentRef)}
            title={taskRefIdentity(taskDocumentRef)}
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
        Agent override
        <select
          className={roleLauncherSelect}
          aria-label="Role agent override"
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
        Model override
        <select
          className={roleLauncherSelect}
          aria-label="Role model override"
          title={selection.agentOverride ? "Use the selected agent's default model" : "Use the model configured for this AR role"}
          value={selection.agentOverride?.modelId ?? ""}
          disabled={!optionsReady || !selectedAgent || !models.length || optionsLoading || busy || currentScopedExecution?.canRetry === true}
          onChange={(event) => {
            const modelId = event.target.value;
            const agentId = selection.agentOverride?.agentId ?? selectedAgentId;
            onSelectionChange({
              ...launchSelection,
              agentOverride: roleAgentOverrideFor(agentId, modelId || undefined, undefined, roleDefaults),
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
          Effort override
          <select
            className={roleLauncherSelect}
            aria-label="Role effort override"
            title={selection.agentOverride ? "Use the selected model's default effort" : "Use the effort configured for this AR role or model"}
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
      <div className={roleLauncherActions}>
        {roleDefaults.serviceTier ? (
          <span data-testid="role-service-tier" title="The role service tier applies on its default agent, including model overrides; a different agent uses its native default.">
            {"Speed: " + (selectedAgentId === roleDefaults.agent ? (roleDefaults.serviceTier === "fast" ? "Fast" : roleDefaults.serviceTier) : "provider default")}
          </span>
        ) : null}
        <button className={roleLauncherButton({ tone: "primary" })} type="button" aria-label="Start role" title="Start role" disabled={!boundCanStart(canStart, Boolean(boundRoles) && (!usesRequestReceipts(selection) || isTasklessRole(selection)), liveOccupant)} onClick={() => void onLaunch(launchSelection)}>{row.startLabel}</button>
        {canRetry && canRetrySelection && retryRequestId ? (
          <button className={roleLauncherButton({ tone: "secondary" })} type="button" aria-label="Retry role launch" title="Retry the same saved launch request" disabled={busy} onClick={() => void onRetry(retrySelection, retryRequestId)}>Retry</button>
        ) : null}
        <RoleReviveControl execution={currentScopedExecution} enabled={optionsReady && complete && !busy} className={roleLauncherButton({ tone: "secondary" })} onRevive={() => void onRevive(launchSelection)} />
        {canRefreshResult ? <button className={roleLauncherButton({ tone: "quiet" })} type="button" aria-label="Refresh role result" title="Refresh role result" disabled={busy || optionsLoading} onClick={onRefreshResult}>Result</button> : null}
        {optionsError ? <button className={roleLauncherButton({ tone: "quiet" })} type="button" disabled={optionsLoading} onClick={onRefreshOptions}>Retry launch options</button> : null}
        <button className={roleLauncherButton({ tone: "quiet" })} type="button" aria-label="Refresh role agents" title="Refresh agent catalog" disabled={(!optionsReady && !optionsError) || optionsLoading || busy} onClick={onRefreshCatalog}>Refresh</button>
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
