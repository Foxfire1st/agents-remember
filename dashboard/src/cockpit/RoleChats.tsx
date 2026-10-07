import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { css, cva } from "../../styled-system/css";
import type { SeriesNode, TaskDocNode } from "../types/projection";
import { sameTaskDocumentRef } from "../data/taskIdentity";
import { RoleExecutionStatus, RoleReviveControl } from "./RoleExecutionStatus";
import { readRoleReport, type RoleReportContent } from "../data/roleReport";
import { NotesReaderViewer } from "../panels/notes-reader/NotesReaderViewer";
import {
  EMPTY_ROLE_EFFORTS,
  EMPTY_ROLE_MODELS,
  LAUNCHER_ROLES,
  isTasklessRole,
  isUncertainRoleExecution,
  launchChoiceProblem,
  launchSelectionComplete,
  masterOptionsForSprint,
  mergeRoleAgentInventory,
  optionIndex,
  roleAgentOverrideFor,
  roleDocumentScope,
  roleOptionsScope,
  readTasklessActiveRequests,
  refAtOptionIndex,
  roleDefaultsCacheKey,
  roleNeedsMaster,
  roleNeedsSprint,
  roleNeedsTask,
  sameRoleDocumentScope,
  sameRoleLaunchSelection,
  sameRoleOptionsScope,
  sprintOptionsForDocs,
  tasklessRequestStorageKey,
  taskOptionsForMaster,
  taskRefIdentity,
  type RoleAction,
  type RoleAgentChoice,
  type RoleAgentInventory,
  type RoleDocumentScope,
  type RoleExecutionReceipt,
  type RoleLaunchSelection,
  type RoleLauncherOptions,
  type RoleOptionsScope,
  type LauncherRole,
  type RoleDefaults,
  type RoleScopedExecution,
  type RoleTasklessActiveRequest,
} from "./roleLaunchModel";
import { PaseoChatFrame } from "./PaseoChatFrame";
import { paseoAgentTarget } from "./paseoFrameModel";

const rolePane = css({
  display: "flex",
  flex: "1",
  flexDirection: "column",
  minHeight: "0",
  minWidth: "0",
  overflow: "hidden",
  border: "1px solid var(--grid)",
  background: "var(--bg-panel)",
});
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
function RoleLauncher({
  taskDocuments,
  series,
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
  const canStart = !choiceProblem && (isTasklessRole(role)
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
    <div className={roleLauncherGrid} data-testid="role-launcher">
      <label className={`${roleLauncherField} ${roleLauncherRoleField}`}>
        Role
        <select
          className={roleLauncherSelect}
          id="launcher-role"
          title={LAUNCHER_ROLES.find((option) => option.id === role)?.label ?? role}
          value={role}
          disabled={busy || optionsLoading || (!isTasklessRole(role) && currentScopedExecution?.canRetry === true)}
          onChange={(event) => onSelectionChange({ role: event.target.value as LauncherRole })}
        >
          {LAUNCHER_ROLES.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
      </label>
      {roleNeedsSprint(role) ? (
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
      {roleNeedsMaster(role) && sprintDocumentRef ? (
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
      {roleNeedsTask(role) && masterDocumentRef ? (
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
        <button className={roleLauncherButton({ tone: "primary" })} type="button" aria-label="Start role" title="Start role" disabled={!canStart} onClick={() => void onLaunch(launchSelection)}>Start</button>
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

// How soon the options are read again after the backend answered that a launch is in progress.
const LAUNCH_REREAD_MS = 1500;

export function RoleChatsPane({
  active,
  taskDocuments,
  series,
}: {
  active: boolean;
  taskDocuments: TaskDocNode[];
  series: SeriesNode[];
}) {
  const [report, setReport] = useState<RoleReportContent | null>(null);
  const [selection, setSelection] = useState<RoleLaunchSelection>({ role: "architect" });
  const [optionsRefresh, setOptionsRefresh] = useState(0);
  const [catalogRefresh, setCatalogRefresh] = useState(0);
  const [optionsState, setOptionsState] = useState<{ scope: RoleOptionsScope; value: RoleLauncherOptions } | null>(null);
  const [agentInventory, setAgentInventory] = useState<RoleAgentInventory | null>(null);
  const [roleDefaultsCache, setRoleDefaultsCache] = useState<Record<string, RoleDefaults>>({});
  const [optionsLoadingScope, setOptionsLoadingScope] = useState<RoleOptionsScope | null>(null);
  const [optionsErrorState, setOptionsErrorState] = useState<{ scope: RoleOptionsScope; message: string } | null>(null);
  const [executionState, setExecutionState] = useState<{ scope: RoleDocumentScope; value: RoleExecutionReceipt } | null>(null);
  const [executionErrorState, setExecutionErrorState] = useState<{ scope: RoleDocumentScope; message: string; sticky?: boolean } | null>(null);
  const [busyScope, setBusyScope] = useState<RoleLaunchSelection | null>(null);
  const rereadTimer = useRef<number | undefined>(undefined);
  const failureExecutionRef = useRef<{ scope: RoleDocumentScope; value: RoleExecutionReceipt } | null>(null);
  const [tasklessActiveRequests, setTasklessActiveRequests] = useState(readTasklessActiveRequests);
  const sentCatalogRefresh = useRef(0);
  const explicitOptionsRefresh = useRef(false);
  const currentSelectionRef = useRef(selection);
  currentSelectionRef.current = selection;
  const tasklessActiveRequestsRef = useRef(tasklessActiveRequests);
  tasklessActiveRequestsRef.current = tasklessActiveRequests;
  const currentDocumentScope = useMemo(
    () => roleDocumentScope(selection),
    [
      selection.role,
      selection.sprintDocumentRef?.repository,
      selection.sprintDocumentRef?.path,
      selection.masterDocumentRef?.repository,
      selection.masterDocumentRef?.path,
      selection.taskDocumentRef?.repository,
      selection.taskDocumentRef?.path,
    ],
  );
  const currentOptionsScope = useMemo(
    () => roleOptionsScope(selection),
    [currentDocumentScope, selection.agentOverride?.agentId],
  );
  const isCurrentOptionsScope = useCallback(
    (scope: RoleOptionsScope) => sameRoleOptionsScope(roleOptionsScope(currentSelectionRef.current), scope),
    [],
  );
  const isCurrentExecutionTarget = useCallback((scope: RoleDocumentScope, requestId?: string) => {
    if (!sameRoleDocumentScope(roleDocumentScope(currentSelectionRef.current), scope)) return false;
    return !isTasklessRole(scope.role) || tasklessActiveRequestsRef.current[scope.role]?.requestId === requestId;
  }, []);
  const updateTasklessActiveRequest = useCallback((
    role: LauncherRole,
    request: RoleTasklessActiveRequest | undefined,
  ) => {
    if (!isTasklessRole(role)) return;
    const current = tasklessActiveRequestsRef.current;
    const next = { ...current };
    if (request) {
      next[role] = request;
      sessionStorage.setItem(tasklessRequestStorageKey(role), JSON.stringify(request));
    } else {
      delete next[role];
      sessionStorage.removeItem(tasklessRequestStorageKey(role));
    }
    tasklessActiveRequestsRef.current = next;
    setTasklessActiveRequests(next);
    if (current[role]?.requestId !== request?.requestId) {
      setExecutionErrorState((error) => error?.scope.role === role ? null : error);
    }
  }, []);
  const options = optionsState && sameRoleOptionsScope(optionsState.scope, currentOptionsScope)
    ? optionsState.value
    : null;
  const optionsLoading = Boolean(optionsLoadingScope && sameRoleOptionsScope(optionsLoadingScope, currentOptionsScope));
  const busy = Boolean(busyScope && sameRoleDocumentScope(roleDocumentScope(busyScope), currentDocumentScope));
  // A launch holds the backend's launch lock for as long as it runs, and the options and result
  // routes answer that with a mark of their own. That is no error of the selection shown: the
  // launcher reads again shortly, until the launch has answered.
  const waitForLaunch = useCallback(() => {
    window.clearTimeout(rereadTimer.current);
    rereadTimer.current = window.setTimeout(() => setOptionsRefresh((current) => current + 1), LAUNCH_REREAD_MS);
  }, []);
  useEffect(() => () => window.clearTimeout(rereadTimer.current), []);
  const optionsError = optionsErrorState && sameRoleOptionsScope(optionsErrorState.scope, currentOptionsScope)
    ? optionsErrorState.message
    : null;
  const activeTasklessRequest = isTasklessRole(selection.role)
    ? tasklessActiveRequests[selection.role]
    : undefined;
  const savedTasklessExecution = isTasklessRole(selection.role) && activeTasklessRequest
    ? options?.executions?.find((execution) => execution.requestId === activeTasklessRequest.requestId)
    : undefined;
  const executionReceipt = executionState && sameRoleDocumentScope(executionState.scope, currentDocumentScope) &&
    (!isTasklessRole(selection.role) || executionState.value.requestId === activeTasklessRequest?.requestId)
    ? executionState.value
    : null;
  const pendingTasklessExecution = activeTasklessRequest?.pending && !executionReceipt && !savedTasklessExecution
    ? {
        status: busy ? "starting" : "unknown",
        detail: busy ? undefined : "The saved request has not returned a receipt yet.",
        requestId: activeTasklessRequest.requestId,
        retryPayload: activeTasklessRequest.retryPayload,
        canStart: false,
        canRevive: false,
        canRetry: !busy && Boolean(activeTasklessRequest.retryPayload),
      }
    : undefined;
  const selectedTasklessExecution = executionReceipt ?? savedTasklessExecution;
  const executionError = executionErrorState && sameRoleDocumentScope(executionErrorState.scope, currentDocumentScope)
    ? executionErrorState.message
    : null;
  const currentScopedExecution = !optionsLoading && isTasklessRole(selection.role)
    ? selectedTasklessExecution
      ? {
          ...selectedTasklessExecution,
          ...(selectedTasklessExecution.retryPayload ?? activeTasklessRequest?.retryPayload
            ? { retryPayload: selectedTasklessExecution.retryPayload ?? activeTasklessRequest?.retryPayload }
            : {}),
        }
      : pendingTasklessExecution
    : !optionsLoading ? executionReceipt ?? options?.execution ?? undefined : undefined;
  const lastFailureExecution = failureExecutionRef.current;
  const failureExecution = lastFailureExecution && isCurrentExecutionTarget(lastFailureExecution.scope, lastFailureExecution.value.requestId)
    ? lastFailureExecution.value : currentScopedExecution;
  const retryRequestId = currentScopedExecution?.canRetry &&
    (!isTasklessRole(selection.role) || Boolean(currentScopedExecution.retryPayload))
    ? currentScopedExecution.requestId
    : undefined;
  const selectionComplete = launchSelectionComplete(selection);

  // The frame follows the launcher's selected execution. Its host fields are
  // read from the receipt itself (for a taskless role the options answer puts the saved
  // execution of the active request there), else from the options answer of a task-bound role.
  const frameScope = useMemo(() => JSON.stringify(currentDocumentScope), [currentDocumentScope]);
  const frameTarget = paseoAgentTarget(executionReceipt ?? options?.execution);

  useEffect(() => {
    if (!active || !selectionComplete) return;
    const requestScope = currentOptionsScope;
    const retryOptions = explicitOptionsRefresh.current;
    explicitOptionsRefresh.current = false;
    const refreshAgentCatalog = catalogRefresh > sentCatalogRefresh.current;
    if (refreshAgentCatalog) sentCatalogRefresh.current = catalogRefresh;
    const controller = new AbortController();
    const body = {
      ...currentDocumentScope,
      ...(requestScope.agentId ? { agentId: requestScope.agentId } : {}),
      ...(refreshAgentCatalog ? { refreshCatalog: true } : {}),
    };
    setOptionsLoadingScope(requestScope);
    setExecutionState((current) =>
      current && sameRoleDocumentScope(current.scope, currentDocumentScope) ? null : current,
    );
    void fetch("/api/role-launch/options", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) {
        const value = (await response.json().catch(() => ({}))) as { detail?: unknown; launchInProgress?: unknown };
        if (isCurrentOptionsScope(requestScope) && value.launchInProgress === true) {
          waitForLaunch();
        } else if (isCurrentOptionsScope(requestScope)) {
          setOptionsErrorState({
            scope: requestScope,
            message: typeof value.detail === "string"
              ? "Could not load role launch options (HTTP " + response.status + "): " + value.detail
              : "Could not load role launch options (HTTP " + response.status + ").",
          });
        }
        return;
      }
      const value = (await response.json()) as RoleLauncherOptions;
      if (!isCurrentOptionsScope(requestScope)) return;
      setOptionsErrorState(null);
      if (value.execution) failureExecutionRef.current = { scope: currentDocumentScope, value: value.execution };
      setOptionsState({ scope: requestScope, value });
      setExecutionErrorState((current) =>
        refreshAgentCatalog || retryOptions || !current?.sticky ? null : current,
      );
      setRoleDefaultsCache((current) => ({
        ...current,
        [roleDefaultsCacheKey(currentDocumentScope)]: value.roleDefaults,
      }));
      setAgentInventory((current) => mergeRoleAgentInventory(
        current,
        value,
        requestScope.agentId ?? value.roleDefaults.agent,
        refreshAgentCatalog,
      ));
      if (isTasklessRole(requestScope.role)) {
        const role = requestScope.role;
        const executions = value.executions ?? [];
        const activeRequest = tasklessActiveRequestsRef.current[role];
        const savedExecution = activeRequest
          ? executions.find((execution) => execution.requestId === activeRequest?.requestId)
          : undefined;
        if (savedExecution?.requestId) {
          const pending = isUncertainRoleExecution(savedExecution);
          updateTasklessActiveRequest(role, pending
            ? {
                requestId: savedExecution.requestId,
                pending: true,
                ...(activeRequest?.retryPayload ?? savedExecution.retryPayload
                  ? { retryPayload: activeRequest?.retryPayload ?? savedExecution.retryPayload }
                  : {}),
              }
            : { requestId: savedExecution.requestId });
          rememberExecution(requestScope, savedExecution);
        }
        const activeRequestId = tasklessActiveRequestsRef.current[role]?.requestId;
        if (activeRequestId) void fetchExecutionResult({ role }, false, activeRequestId);
      }
    }).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (isCurrentOptionsScope(requestScope)) {
        setOptionsErrorState({
          scope: requestScope,
          message: "Could not load role launch options. Check the dashboard connection and retry.",
        });
      }
    }).finally(() => {
      if (!controller.signal.aborted && isCurrentOptionsScope(requestScope)) setOptionsLoadingScope(null);
    });
    return () => controller.abort();
  }, [active, selectionComplete, currentDocumentScope, currentOptionsScope, optionsRefresh, catalogRefresh, isCurrentOptionsScope, waitForLaunch]);

  function rememberExecution(scope: RoleDocumentScope, value: RoleExecutionReceipt): void {
    // Controls may clear their receipt on refresh; failure rendering keeps the last scoped answer.
    failureExecutionRef.current = { scope, value };
    setExecutionState({ scope, value });
  }

  async function fetchExecutionResult(
    requestScope: RoleDocumentScope,
    reportNotFound: boolean,
    requestId?: string,
  ): Promise<RoleExecutionReceipt | undefined> {
    if (isTasklessRole(requestScope.role) && !requestId) return;
    try {
      const response = await fetch("/api/role-launch/result", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...requestScope, ...(requestId ? { requestId } : {}) }),
      });
      if (response.status === 404) {
        if (!isCurrentExecutionTarget(requestScope, requestId)) return;
        const activeRequest = isTasklessRole(requestScope.role)
          ? tasklessActiveRequestsRef.current[requestScope.role]
          : undefined;
        if (activeRequest?.pending && activeRequest.requestId === requestId) {
          const retryPayload = activeRequest.retryPayload;
          rememberExecution(requestScope, {
              status: "unknown",
              detail: "No saved role execution receipt was found for this request yet.",
              requestId,
              ...(retryPayload ? { retryPayload } : {}),
              canStart: false,
              canRevive: false,
              canRetry: Boolean(retryPayload),
          });
        }
        if (reportNotFound) {
          setExecutionErrorState({
            scope: requestScope,
            message: activeRequest?.pending
              ? "No receipt was found yet. Retry will reuse the same saved request."
              : "No saved role result is available for this request yet.",
          });
        }
        return;
      }
      const value = (await response.json()) as RoleExecutionReceipt | { detail?: unknown; launchInProgress?: unknown };
      if (!isCurrentExecutionTarget(requestScope, requestId)) return;
      if (!response.ok && "launchInProgress" in value && value.launchInProgress === true) {
        if (reportNotFound) setExecutionErrorState({
          scope: requestScope,
          message: "The report could not be opened while a role launch or result check is in progress. Wait for it to finish, then press Result again.",
        });
        waitForLaunch();
        return;
      }
      if (!response.ok) {
        setExecutionErrorState({
          scope: requestScope,
          message: typeof value.detail === "string"
            ? "Could not read the role result (HTTP " + response.status + "): " + value.detail
            : "Could not read the role result (HTTP " + response.status + ").",
        });
        return;
    }
    if (!("status" in value) || typeof value.status !== "string") {
      setExecutionErrorState({ scope: requestScope, message: "The role result did not contain an execution status." });
      return;
      }
      const receipt = value as RoleExecutionReceipt;
      if (requestId && receipt.requestId !== requestId) {
        setExecutionErrorState({ scope: requestScope, message: "The role result did not match the selected request." });
        return;
      }
      rememberExecution(requestScope, receipt);
      if (isTasklessRole(requestScope.role) && receipt.requestId) {
        const current = tasklessActiveRequestsRef.current[requestScope.role];
        const pending = isUncertainRoleExecution(receipt);
        updateTasklessActiveRequest(requestScope.role, pending
          ? {
              requestId: receipt.requestId,
              pending: true,
              ...(receipt.retryPayload ?? current?.retryPayload
                ? { retryPayload: receipt.retryPayload ?? current?.retryPayload }
                : {}),
            }
          : { requestId: receipt.requestId });
      }
      // Automatic reads retain a refused dispatch; a successful explicit action clears it.
      setExecutionErrorState((current) => (current?.sticky ? current : null));
      return receipt;
    } catch {
      if (isCurrentExecutionTarget(requestScope, requestId)) {
        setExecutionErrorState({
          scope: requestScope,
          message: "Could not read the role result. Check the dashboard connection and refresh it.",
        });
      }
    }
  }

  function refreshOptions(): void {
    explicitOptionsRefresh.current = true;
    setOptionsRefresh((current) => current + 1);
  }

  function refreshCatalog(): void {
    setCatalogRefresh((current) => current + 1);
  }

  async function openExecutionReport(requestScope: RoleDocumentScope, requestId: string | undefined, receipt: RoleExecutionReceipt | undefined): Promise<void> {
    if (!receipt || !isCurrentExecutionTarget(requestScope, requestId)) return;
    try {
      if (!receipt.requestId) throw new Error("The execution has no recorded request ID for its report. Refresh and retry.");
      const content = await readRoleReport(requestScope, receipt.requestId);
      if (isCurrentExecutionTarget(requestScope, receipt.requestId)) {
        setExecutionErrorState(null);
        setReport(content);
      }
    } catch (error) {
      if (isCurrentExecutionTarget(requestScope, requestId)) setExecutionErrorState({
        scope: requestScope,
        sticky: true,
        message: error instanceof Error ? error.message : "The report could not be opened. Refresh and retry.",
      });
    }
  }

  async function refreshResult(): Promise<void> {
    if (!selectionComplete || busy || optionsLoading) return;
    const selected = selection;
    const requestScope = roleDocumentScope(selected);
    const requestId = isTasklessRole(selected.role)
      ? tasklessActiveRequestsRef.current[selected.role]?.requestId
      : currentScopedExecution?.requestId;
    if (isTasklessRole(selected.role) && !requestId) return;
    setBusyScope(selected);
    try {
      await openExecutionReport(requestScope, requestId, await fetchExecutionResult(requestScope, true, requestId));
    } finally {
      if (sameRoleLaunchSelection(currentSelectionRef.current, selected)) setBusyScope(null);
    }
  }

  async function dispatch(
    action: RoleAction,
    launchSelection: RoleLaunchSelection,
    replayRequestId?: string,
  ): Promise<void> {
    if (!active || busy || !options || !launchSelectionComplete(launchSelection)) return;
    const tasklessRole = isTasklessRole(launchSelection.role);
    const activeTasklessRequest = tasklessRole
      ? tasklessActiveRequestsRef.current[launchSelection.role]
      : undefined;
    const currentStatus = currentScopedExecution?.status.toLowerCase();
    if (action === "start" && tasklessRole) {
      if (replayRequestId) {
        if (activeTasklessRequest?.requestId !== replayRequestId || currentScopedExecution?.canRetry !== true) return;
      } else if (["starting", "unknown"].includes(currentStatus ?? "")) {
        return;
      }
    }
    if (action === "start" && !tasklessRole && currentScopedExecution?.canStart === false && !replayRequestId) return;
    if (action === "revive" && currentScopedExecution?.canRevive !== true) return;
    if (action === "revive" && tasklessRole && (!replayRequestId || currentScopedExecution?.requestId !== replayRequestId)) return;
    const selected = launchSelection;
    const requestScope = roleDocumentScope(selected);
    const requestId = replayRequestId ?? crypto.randomUUID();
    const savedLaunchSelection: RoleLaunchSelection = {
      ...requestScope,
      ...(selected.agentOverride ? { agentOverride: selected.agentOverride } : {}),
    };
    if (tasklessRole && action === "start" && !replayRequestId) {
      updateTasklessActiveRequest(selected.role, {
        requestId,
        pending: true,
        retryPayload: savedLaunchSelection,
      });
    }
    const requestBody = {
      requestId,
      action,
      ...requestScope,
      ...(selected.agentOverride && !(tasklessRole && action === "revive")
        ? { agentOverride: selected.agentOverride }
        : {}),
    };
    setBusyScope(selected);
    setExecutionErrorState(null);
    try {
      const response = await fetch("/api/role-launch/dispatch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      const value = (await response.json()) as RoleExecutionReceipt | { detail?: unknown };
      if (!sameRoleDocumentScope(roleDocumentScope(currentSelectionRef.current), requestScope)) return;
      if (response.status === 409 && "detail" in value && typeof value.detail === "string") {
        setExecutionErrorState({
          scope: requestScope,
          message: "The role launch was not accepted (HTTP 409): " + value.detail,
          sticky: true,
        });
      } else if ("status" in value && typeof value.status === "string") {
        const receipt = value as RoleExecutionReceipt;
        if (tasklessRole && receipt.requestId !== requestId) {
          setExecutionErrorState({ scope: requestScope, message: "The role execution receipt did not match this request." });
          return;
        }
        rememberExecution(requestScope, receipt);
        if (tasklessRole && receipt.requestId) {
          const pending = isUncertainRoleExecution(receipt);
          updateTasklessActiveRequest(selected.role, pending
            ? {
                requestId: receipt.requestId,
                pending: true,
                ...(receipt.retryPayload ?? tasklessActiveRequestsRef.current[selected.role]?.retryPayload
                  ? { retryPayload: receipt.retryPayload ?? tasklessActiveRequestsRef.current[selected.role]?.retryPayload }
                  : {}),
              }
            : { requestId: receipt.requestId });
        }
        setExecutionErrorState(
          response.ok || ["accepted", "completed"].includes(receipt.status.toLowerCase())
            ? null
            : {
                scope: requestScope,
                sticky: true,
                message: typeof value.detail === "string"
                  ? "The role handover was not accepted (HTTP " + response.status + "): " + value.detail
                  : "The role handover was not accepted (HTTP " + response.status + ").",
              },
        );
      } else {
        const status = response.status >= 500 ? "unknown" : "rejected";
        rememberExecution(requestScope, {
            status,
            detail: "HTTP " + response.status,
            ...(tasklessRole
              ? {
                  requestId,
                  retryPayload: savedLaunchSelection,
                  canRetry: status === "unknown",
                }
              : {}),
            canStart: false,
            canRevive: false,
        });
        setExecutionErrorState({
          scope: requestScope,
          message: response.status >= 500
            ? "The role launch returned HTTP " + response.status + "; execution state is unknown. Refresh the AR result before retrying."
            : "The role handover was rejected (HTTP " + response.status + ").",
        });
      }
      await fetchExecutionResult(requestScope, false, tasklessRole ? requestId : undefined);
    } catch {
      if (!sameRoleDocumentScope(roleDocumentScope(currentSelectionRef.current), requestScope)) return;
      rememberExecution(requestScope, {
          status: "unknown",
          detail: "No dispatch receipt was returned.",
          requestId,
          retryPayload: savedLaunchSelection,
          canStart: false,
          canRevive: false,
          canRetry: true,
      });
      setExecutionErrorState({
        scope: requestScope,
        message: "The role launch request did not return. Its state is unknown; refresh the AR result before retrying.",
      });
      await fetchExecutionResult(requestScope, false, tasklessRole ? requestId : undefined);
    } finally {
      if (sameRoleDocumentScope(roleDocumentScope(currentSelectionRef.current), requestScope)) {
        setBusyScope(null);
        setOptionsRefresh((current) => current + 1);
      }
    }
  }

  const onSelectionChange = useCallback((next: RoleLaunchSelection) => {
    setSelection(next);
  }, []);
  const roleDefaults = options?.roleDefaults ?? roleDefaultsCache[roleDefaultsCacheKey(currentDocumentScope)] ?? {};
  const agents = agentInventory && agentInventory.catalogOrigin === options?.catalogOrigin
    ? agentInventory.agents
    : options?.agents ?? agentInventory?.agents ?? [];
  const optionsReady = Boolean(options && !optionsLoading);
  const onLaunch = (launchSelection: RoleLaunchSelection) => dispatch("start", launchSelection);
  const onRevive = (launchSelection: RoleLaunchSelection) => dispatch(
    "revive",
    launchSelection,
    isTasklessRole(launchSelection.role) ? currentScopedExecution?.requestId : undefined,
  );
  const onRetry = (launchSelection: RoleLaunchSelection, requestId: string) =>
    dispatch("start", launchSelection, requestId);

  return (
    <>
    <section aria-label="Role chats" className={rolePane} data-testid="role-chats-pane" style={report ? { display: "none" } : undefined}>
      <RoleLauncher
        taskDocuments={taskDocuments}
        series={series}
        roleDefaults={roleDefaults}
        agents={agents}
        selection={selection}
        currentScopedExecution={currentScopedExecution}
        failureExecution={failureExecution}
        optionsReady={optionsReady}
        optionsLoading={optionsLoading}
        busy={busy}
        retryRequestId={retryRequestId}
        optionsError={optionsError}
        executionError={executionError}
        onSelectionChange={onSelectionChange}
        onLaunch={onLaunch}
        onRevive={onRevive}
        onRetry={onRetry}
        onRefreshOptions={refreshOptions}
        onRefreshCatalog={refreshCatalog}
        onRefreshResult={refreshResult}
      />
      <PaseoChatFrame active={active} scope={frameScope} target={frameTarget} />
    </section>
    {report ? <NotesReaderViewer kind="role-report" report={report} onBack={() => setReport(null)} /> : null}
    </>
  );
}
