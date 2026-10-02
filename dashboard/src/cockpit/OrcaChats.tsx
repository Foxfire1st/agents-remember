import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { css, cva } from "../../styled-system/css";
import { SessionsView } from "../panels/session-cockpit/sessions-view/SessionsView";
import type { SeriesNode, TaskDocNode } from "../types/projection";
import { sameTaskDocumentRef } from "../data/taskIdentity";
import { OrcaExecutionStatus, OrcaReviveControl } from "./OrcaExecutionStatus";
import {
  EMPTY_ORCA_EFFORTS,
  EMPTY_ORCA_MODELS,
  ORCA_ROLES,
  isTasklessOrcaRole,
  isUncertainOrcaExecution,
  launchChoiceProblem,
  launchSelectionComplete,
  masterOptionsForSprint,
  mergeOrcaAgentInventory,
  optionIndex,
  orcaAgentOverrideFor,
  orcaDocumentScope,
  orcaOptionsScope,
  readTasklessActiveRequests,
  refAtOptionIndex,
  roleDefaultsCacheKey,
  roleNeedsMaster,
  roleNeedsSprint,
  roleNeedsTask,
  sameOrcaDocumentScope,
  sameOrcaLaunchSelection,
  sameOrcaOptionsScope,
  sprintOptionsForDocs,
  tasklessRequestStorageKey,
  taskOptionsForMaster,
  taskRefIdentity,
  type OrcaAction,
  type OrcaAgentChoice,
  type OrcaAgentInventory,
  type OrcaDocumentScope,
  type OrcaExecutionReceipt,
  type OrcaLaunchSelection,
  type OrcaLauncherOptions,
  type OrcaOptionsScope,
  type OrcaRole,
  type OrcaRoleDefaults,
  type OrcaScopedExecution,
  type OrcaTasklessActiveRequest,
} from "./orcaLaunchModel";
import { PaseoChatFrame } from "./PaseoChatFrame";
import { paseoAgentTarget } from "./paseoFrameModel";

const chatsModeTabs = css({
  display: "flex",
  flexShrink: 0,
  gap: "0.25rem",
  paddingBottom: "0.35rem",
});
const chatsModeTab = cva({
  base: {
    font: "inherit",
    fontSize: "0.7rem",
    letterSpacing: "0.06em",
    paddingInline: "0.55rem",
    paddingBlock: "0.2rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    cursor: "pointer",
    background: "transparent",
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  },
  variants: {
    selected: {
      true: { color: "amber", borderColor: "amber" },
      false: { color: "muted", borderColor: "grid" },
    },
  },
});
const chatsModePanel = css({
  display: "flex",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
});
const orcaPane = css({
  display: "flex",
  flex: "1",
  flexDirection: "column",
  minHeight: "0",
  minWidth: "0",
  overflow: "hidden",
  border: "1px solid var(--grid)",
  background: "var(--bg-panel)",
});
const orcaLauncherGrid = css({
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
const orcaLauncherField = css({
  display: "grid",
  flex: "0 0 auto",
  gap: "0.22rem",
  minWidth: "0",
  color: "muted",
  fontSize: "0.75rem",
  letterSpacing: "0.035em",
  textTransform: "uppercase",
});
const orcaLauncherRoleField = css({ width: "8rem" });
const orcaLauncherSprintField = css({ width: "8rem" });
const orcaLauncherMasterField = css({ width: "8.75rem" });
const orcaLauncherTaskField = css({ width: "9.5rem" });
const orcaLauncherAgentField = css({ width: "9.5rem" });
const orcaLauncherModelField = css({ width: "10rem" });
const orcaLauncherEffortField = css({ width: "8rem" });
const orcaLauncherSelect = css({
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
const orcaLauncherButton = cva({
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
const orcaLauncherActions = css({
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  gap: "0.35rem",
  minWidth: "0",
});
const orcaLauncherMeta = css({
  flex: "1 0 100%",
  minWidth: "0",
  color: "muted",
  fontSize: "0.64rem",
  lineHeight: "1.35",
  overflowWrap: "anywhere",
});
function OrcaRoleLauncher({
  taskDocuments,
  series,
  roleDefaults,
  agents,
  selection,
  currentScopedExecution,
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
  roleDefaults: OrcaRoleDefaults;
  agents: OrcaAgentChoice[];
  selection: OrcaLaunchSelection;
  currentScopedExecution?: OrcaScopedExecution;
  optionsReady: boolean;
  optionsLoading: boolean;
  busy: boolean;
  retryRequestId?: string;
  optionsError: string | null;
  executionError: string | null;
  onSelectionChange: (selection: OrcaLaunchSelection) => void;
  onLaunch: (selection: OrcaLaunchSelection) => Promise<void>;
  onRevive: (selection: OrcaLaunchSelection) => Promise<void>;
  onRetry: (selection: OrcaLaunchSelection, requestId: string) => Promise<void>;
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
  const models = selectedAgent?.models ?? EMPTY_ORCA_MODELS;
  const defaultModel = models.find((model) => model.id === roleDefaults.model);
  const selectedModelId = selection.agentOverride?.modelId ?? defaultModel?.id ?? "";
  const selectedModel = models.find((model) => model.id === selectedModelId);
  const efforts = selectedModel?.efforts ?? EMPTY_ORCA_EFFORTS;
  const defaultEffort = efforts.find((effort) => effort.id === (roleDefaults.effort ?? selectedModel?.defaultEffort));
  const selectedAgentLabel = agents.find((agent) => agent.id === roleDefaults.agent)?.label ?? roleDefaults.agent;
  const launchSelection: OrcaLaunchSelection = {
    ...orcaDocumentScope(selection),
    ...(selection.agentOverride ? { agentOverride: selection.agentOverride } : {}),
  };
  const complete = launchSelectionComplete(launchSelection);
  const status = currentScopedExecution?.status.toLowerCase();
  const liveOccupant = ["accepted", "unknown", "running", "starting"].includes(status ?? "");
  const canRetry = optionsReady && complete && !busy && currentScopedExecution?.canRetry === true && Boolean(retryRequestId);
  const retrySelection = currentScopedExecution?.retryPayload ?? launchSelection;
  const canRetrySelection = sameOrcaDocumentScope(orcaDocumentScope(retrySelection), orcaDocumentScope(launchSelection));
  const choiceProblem = optionsReady ? launchChoiceProblem(roleDefaults, agents, selection.agentOverride) : null;
  const canStart = !choiceProblem && (isTasklessOrcaRole(role)
    ? optionsReady && complete && !busy && !["starting", "unknown"].includes(status ?? "")
    : optionsReady && complete && !busy && !liveOccupant && !canRetry && currentScopedExecution?.canStart !== false);
  // Every refresh re-reads the agent, so Result is offered for any execution, closed ones included.
  const canRefreshResult = complete && Boolean(status);

  useEffect(() => {
    if (!optionsReady) return;
    const activeScope = orcaDocumentScope(selection);
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
    <div className={orcaLauncherGrid} data-testid="orca-role-launcher">
      <label className={`${orcaLauncherField} ${orcaLauncherRoleField}`}>
        Role
        <select
          className={orcaLauncherSelect}
          id="orca-role"
          title={ORCA_ROLES.find((option) => option.id === role)?.label ?? role}
          value={role}
          disabled={busy || optionsLoading || (!isTasklessOrcaRole(role) && currentScopedExecution?.canRetry === true)}
          onChange={(event) => onSelectionChange({ role: event.target.value as OrcaRole })}
        >
          {ORCA_ROLES.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
        </select>
      </label>
      {roleNeedsSprint(role) ? (
        <label className={`${orcaLauncherField} ${orcaLauncherSprintField}`}>
          Sprint
          <select
            className={orcaLauncherSelect}
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
        <label className={`${orcaLauncherField} ${orcaLauncherMasterField}`}>
          Master
          <select
            className={orcaLauncherSelect}
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
        <label className={`${orcaLauncherField} ${orcaLauncherTaskField}`}>
          Task
          <select
            className={orcaLauncherSelect}
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
      <label className={`${orcaLauncherField} ${orcaLauncherAgentField}`}>
        Agent override
        <select
          className={orcaLauncherSelect}
          aria-label="Orca agent override"
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
      <label className={`${orcaLauncherField} ${orcaLauncherModelField}`}>
        Model override
        <select
          className={orcaLauncherSelect}
          aria-label="Orca model override"
          title={selection.agentOverride ? "Use the selected agent's default model" : "Use the model configured for this AR role"}
          value={selection.agentOverride?.modelId ?? ""}
          disabled={!optionsReady || !selectedAgent || !models.length || optionsLoading || busy || currentScopedExecution?.canRetry === true}
          onChange={(event) => {
            const modelId = event.target.value;
            const agentId = selection.agentOverride?.agentId ?? selectedAgentId;
            onSelectionChange({
              ...launchSelection,
              agentOverride: orcaAgentOverrideFor(agentId, modelId || undefined, undefined, roleDefaults),
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
        <label className={`${orcaLauncherField} ${orcaLauncherEffortField}`}>
          Effort override
          <select
            className={orcaLauncherSelect}
            aria-label="Orca effort override"
            title={selection.agentOverride ? "Use the selected model's default effort" : "Use the effort configured for this AR role or model"}
            value={selection.agentOverride?.effortId ?? ""}
            disabled={busy || currentScopedExecution?.canRetry === true}
            onChange={(event) => {
              const agentId = selection.agentOverride?.agentId ?? selectedAgentId;
              onSelectionChange({
                ...launchSelection,
                agentOverride: orcaAgentOverrideFor(
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
      <div className={orcaLauncherActions}>
        <button className={orcaLauncherButton({ tone: "primary" })} type="button" aria-label="Start Orca" title="Start Orca" disabled={!canStart} onClick={() => void onLaunch(launchSelection)}>Start</button>
        {canRetry && canRetrySelection && retryRequestId ? (
          <button className={orcaLauncherButton({ tone: "secondary" })} type="button" aria-label="Retry Orca" title="Retry the same saved Orca request" disabled={busy} onClick={() => void onRetry(retrySelection, retryRequestId)}>Retry</button>
        ) : null}
        <OrcaReviveControl execution={currentScopedExecution} enabled={optionsReady && complete && !busy} className={orcaLauncherButton({ tone: "secondary" })} onRevive={() => void onRevive(launchSelection)} />
        {canRefreshResult ? <button className={orcaLauncherButton({ tone: "quiet" })} type="button" aria-label="Refresh Orca result" title="Refresh Orca result" disabled={busy || optionsLoading} onClick={onRefreshResult}>Result</button> : null}
        <button className={orcaLauncherButton({ tone: "quiet" })} type="button" aria-label="Refresh Orca agents" title="Refresh agent catalog" disabled={(!optionsReady && !optionsError) || optionsLoading || busy} onClick={onRefreshCatalog}>Refresh</button>
      </div>
      {selection.agentOverride && selectedAgent ? (
        <div className={orcaLauncherMeta} role="status" data-testid="orca-capability-summary">
          {"Using " + selectedAgent.label +
            (selectedModel ? " · " + selectedModel.label : roleDefaults.model ? " · role model " + roleDefaults.model : "") +
            (selectedModel
              ? selectedModel.efforts?.length
                ? " · " + (defaultEffort?.label ?? selectedModel.efforts.length + " effort choices")
                : " · no effort choices"
              : "")}
        </div>
      ) : null}
      {choiceProblem || selectedAgent?.listingError ? (
        <div className={orcaLauncherMeta} role="alert" data-testid="orca-choice-problem" style={{ color: "var(--alarm)" }}>
          {[choiceProblem, selectedAgent?.listingError ? "Models of " + selectedAgent.label + " could not be listed: " + selectedAgent.listingError : null].filter(Boolean).join(" ")}
        </div>
      ) : null}
      {currentScopedExecution ? <OrcaExecutionStatus execution={currentScopedExecution} className={orcaLauncherMeta} /> : null}
      {optionsError ? (
        <div className={orcaLauncherMeta} role="alert" style={{ color: "var(--alarm)" }}>
          {optionsError} <button className={orcaLauncherButton({ tone: "quiet" })} type="button" disabled={optionsLoading} onClick={onRefreshOptions}>Retry launch options</button>
        </div>
      ) : null}
      {executionError ? (
        <div className={orcaLauncherMeta} role="alert" style={{ color: "var(--alarm)" }}>{executionError}</div>
      ) : null}
      {optionsLoading ? <div className={orcaLauncherMeta} role="status">Loading Orca launch options…</div> : null}
    </div>
  );

}

function OrcaChatsPane({
  active,
  taskDocuments,
  series,
}: {
  active: boolean;
  taskDocuments: TaskDocNode[];
  series: SeriesNode[];
}) {
  const [selection, setSelection] = useState<OrcaLaunchSelection>({ role: "architect" });
  const [optionsRefresh, setOptionsRefresh] = useState(0);
  const [catalogRefresh, setCatalogRefresh] = useState(0);
  const [optionsState, setOptionsState] = useState<{ scope: OrcaOptionsScope; value: OrcaLauncherOptions } | null>(null);
  const [agentInventory, setAgentInventory] = useState<OrcaAgentInventory | null>(null);
  const [roleDefaultsCache, setRoleDefaultsCache] = useState<Record<string, OrcaRoleDefaults>>({});
  const [optionsLoadingScope, setOptionsLoadingScope] = useState<OrcaOptionsScope | null>(null);
  const [optionsErrorState, setOptionsErrorState] = useState<{ scope: OrcaOptionsScope; message: string } | null>(null);
  const [executionState, setExecutionState] = useState<{ scope: OrcaDocumentScope; value: OrcaExecutionReceipt } | null>(null);
  const [executionErrorState, setExecutionErrorState] = useState<{ scope: OrcaDocumentScope; message: string; sticky?: boolean } | null>(null);
  const [busyScope, setBusyScope] = useState<OrcaLaunchSelection | null>(null);
  const [tasklessActiveRequests, setTasklessActiveRequests] = useState(readTasklessActiveRequests);
  const sentCatalogRefresh = useRef(0);
  const currentSelectionRef = useRef(selection);
  currentSelectionRef.current = selection;
  const tasklessActiveRequestsRef = useRef(tasklessActiveRequests);
  tasklessActiveRequestsRef.current = tasklessActiveRequests;
  const currentDocumentScope = useMemo(
    () => orcaDocumentScope(selection),
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
    () => orcaOptionsScope(selection),
    [currentDocumentScope, selection.agentOverride?.agentId],
  );
  const isCurrentOptionsScope = useCallback(
    (scope: OrcaOptionsScope) => sameOrcaOptionsScope(orcaOptionsScope(currentSelectionRef.current), scope),
    [],
  );
  const isCurrentExecutionTarget = useCallback((scope: OrcaDocumentScope, requestId?: string) => {
    if (!sameOrcaDocumentScope(orcaDocumentScope(currentSelectionRef.current), scope)) return false;
    return !isTasklessOrcaRole(scope.role) || tasklessActiveRequestsRef.current[scope.role]?.requestId === requestId;
  }, []);
  const updateTasklessActiveRequest = useCallback((
    role: OrcaRole,
    request: OrcaTasklessActiveRequest | undefined,
  ) => {
    if (!isTasklessOrcaRole(role)) return;
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
  const options = optionsState && sameOrcaOptionsScope(optionsState.scope, currentOptionsScope)
    ? optionsState.value
    : null;
  const optionsLoading = Boolean(optionsLoadingScope && sameOrcaOptionsScope(optionsLoadingScope, currentOptionsScope));
  const busy = Boolean(busyScope && sameOrcaDocumentScope(orcaDocumentScope(busyScope), currentDocumentScope));
  const optionsError = optionsErrorState && sameOrcaOptionsScope(optionsErrorState.scope, currentOptionsScope)
    ? optionsErrorState.message
    : null;
  const activeTasklessRequest = isTasklessOrcaRole(selection.role)
    ? tasklessActiveRequests[selection.role]
    : undefined;
  const savedTasklessExecution = isTasklessOrcaRole(selection.role) && activeTasklessRequest
    ? options?.executions?.find((execution) => execution.requestId === activeTasklessRequest.requestId)
    : undefined;
  const executionReceipt = executionState && sameOrcaDocumentScope(executionState.scope, currentDocumentScope) &&
    (!isTasklessOrcaRole(selection.role) || executionState.value.requestId === activeTasklessRequest?.requestId)
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
  const executionError = executionErrorState && sameOrcaDocumentScope(executionErrorState.scope, currentDocumentScope)
    ? executionErrorState.message
    : null;
  const currentScopedExecution = !optionsLoading && isTasklessOrcaRole(selection.role)
    ? selectedTasklessExecution
      ? {
          ...selectedTasklessExecution,
          ...(selectedTasklessExecution.retryPayload ?? activeTasklessRequest?.retryPayload
            ? { retryPayload: selectedTasklessExecution.retryPayload ?? activeTasklessRequest?.retryPayload }
            : {}),
        }
      : pendingTasklessExecution
    : !optionsLoading && executionReceipt
    ? executionReceipt
    : !optionsLoading ? options?.execution ?? undefined : undefined;
  const retryRequestId = currentScopedExecution?.canRetry &&
    (!isTasklessOrcaRole(selection.role) || Boolean(currentScopedExecution.retryPayload))
    ? currentScopedExecution.requestId
    : undefined;
  const selectionComplete = launchSelectionComplete(selection);

  // The frame follows the execution the launcher bar displays status for. Its host fields are
  // read from the receipt itself (for a taskless role the options answer puts the saved
  // execution of the active request there), else from the options answer of a task-bound role.
  const frameScope = useMemo(() => JSON.stringify(currentDocumentScope), [currentDocumentScope]);
  const frameTarget = paseoAgentTarget(executionReceipt ?? options?.execution);

  useEffect(() => {
    if (!active || !selectionComplete) return;
    const requestScope = currentOptionsScope;
    const refreshAgentCatalog = catalogRefresh > sentCatalogRefresh.current;
    if (refreshAgentCatalog) sentCatalogRefresh.current = catalogRefresh;
    const controller = new AbortController();
    const body = {
      ...currentDocumentScope,
      ...(requestScope.agentId ? { agentId: requestScope.agentId } : {}),
      ...(refreshAgentCatalog ? { refreshCatalog: true } : {}),
    };
    setOptionsErrorState(null);
    setOptionsLoadingScope(requestScope);
    setExecutionState((current) =>
      current && sameOrcaDocumentScope(current.scope, currentDocumentScope) ? null : current,
    );
    void fetch("/api/orca/launcher/options", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) {
        const value = (await response.json().catch(() => ({}))) as { detail?: unknown };
        if (isCurrentOptionsScope(requestScope)) {
          setOptionsErrorState({
            scope: requestScope,
            message: typeof value.detail === "string"
              ? "Could not load Orca launch options (HTTP " + response.status + "): " + value.detail
              : "Could not load Orca launch options (HTTP " + response.status + ").",
          });
        }
        return;
      }
      const value = (await response.json()) as OrcaLauncherOptions;
      if (!isCurrentOptionsScope(requestScope)) return;
      setOptionsState({ scope: requestScope, value });
      setRoleDefaultsCache((current) => ({
        ...current,
        [roleDefaultsCacheKey(currentDocumentScope)]: value.roleDefaults,
      }));
      setAgentInventory((current) => mergeOrcaAgentInventory(
        current,
        value,
        requestScope.agentId ?? value.roleDefaults.agent,
        refreshAgentCatalog,
      ));
      if (isTasklessOrcaRole(requestScope.role)) {
        const role = requestScope.role;
        const executions = value.executions ?? [];
        const activeRequest = tasklessActiveRequestsRef.current[role];
        const savedExecution = activeRequest
          ? executions.find((execution) => execution.requestId === activeRequest?.requestId)
          : undefined;
        if (savedExecution?.requestId) {
          const pending = isUncertainOrcaExecution(savedExecution);
          updateTasklessActiveRequest(role, pending
            ? {
                requestId: savedExecution.requestId,
                pending: true,
                ...(activeRequest?.retryPayload ?? savedExecution.retryPayload
                  ? { retryPayload: activeRequest?.retryPayload ?? savedExecution.retryPayload }
                  : {}),
              }
            : { requestId: savedExecution.requestId });
          setExecutionState({ scope: requestScope, value: savedExecution });
        }
        const activeRequestId = tasklessActiveRequestsRef.current[role]?.requestId;
        if (activeRequestId) void fetchExecutionResult({ role }, false, activeRequestId);
      }
    }).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      if (isCurrentOptionsScope(requestScope)) {
        setOptionsErrorState({
          scope: requestScope,
          message: "Could not load Orca launch options. Check the dashboard connection and retry.",
        });
      }
    }).finally(() => {
      if (!controller.signal.aborted && isCurrentOptionsScope(requestScope)) setOptionsLoadingScope(null);
    });
    return () => controller.abort();
  }, [active, selectionComplete, currentDocumentScope, currentOptionsScope, optionsRefresh, catalogRefresh, isCurrentOptionsScope]);

  async function fetchExecutionResult(
    requestScope: OrcaDocumentScope,
    reportNotFound: boolean,
    requestId?: string,
  ): Promise<void> {
    if (isTasklessOrcaRole(requestScope.role) && !requestId) return;
    try {
      const response = await fetch("/api/orca/result", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...requestScope, ...(requestId ? { requestId } : {}) }),
      });
      if (response.status === 404) {
        if (!isCurrentExecutionTarget(requestScope, requestId)) return;
        const activeRequest = isTasklessOrcaRole(requestScope.role)
          ? tasklessActiveRequestsRef.current[requestScope.role]
          : undefined;
        if (activeRequest?.pending && activeRequest.requestId === requestId) {
          const retryPayload = activeRequest.retryPayload;
          setExecutionState({
            scope: requestScope,
            value: {
              status: "unknown",
              detail: "No saved Orca receipt was found for this request yet.",
              requestId,
              ...(retryPayload ? { retryPayload } : {}),
              canStart: false,
              canRevive: false,
              canRetry: Boolean(retryPayload),
            },
          });
        }
        if (reportNotFound) {
          setExecutionErrorState({
            scope: requestScope,
            message: activeRequest?.pending
              ? "No receipt was found yet. Retry will reuse the same saved request."
              : "No saved Orca result is available for this request yet.",
          });
        }
        return;
      }
      const value = (await response.json()) as OrcaExecutionReceipt | { detail?: unknown };
      if (!isCurrentExecutionTarget(requestScope, requestId)) return;
      if (!response.ok) {
        setExecutionErrorState({
          scope: requestScope,
          message: typeof value.detail === "string"
            ? "Could not read the Orca result (HTTP " + response.status + "): " + value.detail
            : "Could not read the Orca result (HTTP " + response.status + ").",
        });
        return;
    }
    if (!("status" in value) || typeof value.status !== "string") {
      setExecutionErrorState({ scope: requestScope, message: "The Orca result did not contain an execution status." });
      return;
      }
      const receipt = value as OrcaExecutionReceipt;
      if (requestId && receipt.requestId !== requestId) {
        setExecutionErrorState({ scope: requestScope, message: "The Orca result did not match the selected request." });
        return;
      }
      setExecutionState({ scope: requestScope, value: receipt });
      if (isTasklessOrcaRole(requestScope.role) && receipt.requestId) {
        const current = tasklessActiveRequestsRef.current[requestScope.role];
        const pending = isUncertainOrcaExecution(receipt);
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
      // A refused dispatch stays on screen next to the execution it was refused for.
      setExecutionErrorState((current) => (current?.sticky ? current : null));
    } catch {
      if (isCurrentExecutionTarget(requestScope, requestId)) {
        setExecutionErrorState({
          scope: requestScope,
          message: "Could not read the Orca result. Check the dashboard connection and refresh it.",
        });
      }
    }
  }

  function refreshOptions(): void {
    setOptionsRefresh((current) => current + 1);
  }

  function refreshCatalog(): void {
    setCatalogRefresh((current) => current + 1);
  }

  async function refreshResult(): Promise<void> {
    if (!selectionComplete || busy || optionsLoading) return;
    const selected = selection;
    const requestScope = orcaDocumentScope(selected);
    const requestId = isTasklessOrcaRole(selected.role)
      ? tasklessActiveRequestsRef.current[selected.role]?.requestId
      : currentScopedExecution?.requestId;
    if (isTasklessOrcaRole(selected.role) && !requestId) return;
    setBusyScope(selected);
    setExecutionErrorState(null);
    try {
      await fetchExecutionResult(requestScope, true, requestId);
    } finally {
      if (sameOrcaLaunchSelection(currentSelectionRef.current, selected)) setBusyScope(null);
    }
  }

  async function dispatch(
    action: OrcaAction,
    launchSelection: OrcaLaunchSelection,
    replayRequestId?: string,
  ): Promise<void> {
    if (!active || busy || !options || !launchSelectionComplete(launchSelection)) return;
    const tasklessRole = isTasklessOrcaRole(launchSelection.role);
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
    const requestScope = orcaDocumentScope(selected);
    const requestId = replayRequestId ?? crypto.randomUUID();
    const savedLaunchSelection: OrcaLaunchSelection = {
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
      const response = await fetch("/api/orca/dispatch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      const value = (await response.json()) as OrcaExecutionReceipt | { detail?: unknown };
      if (!sameOrcaDocumentScope(orcaDocumentScope(currentSelectionRef.current), requestScope)) return;
      if (response.status === 409 && "detail" in value && typeof value.detail === "string") {
        setExecutionErrorState({
          scope: requestScope,
          message: "Orca could not accept this launch (HTTP 409): " + value.detail,
          sticky: true,
        });
      } else if ("status" in value && typeof value.status === "string") {
        const receipt = value as OrcaExecutionReceipt;
        if (tasklessRole && receipt.requestId !== requestId) {
          setExecutionErrorState({ scope: requestScope, message: "The Orca receipt did not match this request." });
          return;
        }
        setExecutionState({ scope: requestScope, value: receipt });
        if (tasklessRole && receipt.requestId) {
          const pending = isUncertainOrcaExecution(receipt);
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
                message: typeof value.detail === "string"
                  ? "Orca could not accept this handover (HTTP " + response.status + "): " + value.detail
                  : "Orca could not accept this handover (HTTP " + response.status + ").",
              },
        );
      } else {
        const status = response.status >= 500 ? "unknown" : "rejected";
        setExecutionState({
          scope: requestScope,
          value: {
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
          },
        });
        setExecutionErrorState({
          scope: requestScope,
          message: response.status >= 500
            ? "Orca returned HTTP " + response.status + "; execution state is unknown. Refresh the AR result before retrying."
            : "Orca rejected the handover (HTTP " + response.status + ").",
        });
      }
      await fetchExecutionResult(requestScope, false, tasklessRole ? requestId : undefined);
    } catch {
      if (!sameOrcaDocumentScope(orcaDocumentScope(currentSelectionRef.current), requestScope)) return;
      setExecutionState({
        scope: requestScope,
        value: {
          status: "unknown",
          detail: "No dispatch receipt was returned.",
          requestId,
          retryPayload: savedLaunchSelection,
          canStart: false,
          canRevive: false,
          canRetry: true,
        },
      });
      setExecutionErrorState({
        scope: requestScope,
        message: "The Orca request did not return. Its state is unknown; refresh the AR result before retrying.",
      });
      await fetchExecutionResult(requestScope, false, tasklessRole ? requestId : undefined);
    } finally {
      if (sameOrcaDocumentScope(orcaDocumentScope(currentSelectionRef.current), requestScope)) {
        setBusyScope(null);
        setOptionsRefresh((current) => current + 1);
      }
    }
  }

  const onSelectionChange = useCallback((next: OrcaLaunchSelection) => {
    setSelection(next);
  }, []);
  const roleDefaults = options?.roleDefaults ?? roleDefaultsCache[roleDefaultsCacheKey(currentDocumentScope)] ?? {};
  const agents = agentInventory && agentInventory.catalogOrigin === options?.catalogOrigin
    ? agentInventory.agents
    : options?.agents ?? agentInventory?.agents ?? [];
  const optionsReady = Boolean(options && !optionsLoading);
  const onLaunch = (launchSelection: OrcaLaunchSelection) => dispatch("start", launchSelection);
  const onRevive = (launchSelection: OrcaLaunchSelection) => dispatch(
    "revive",
    launchSelection,
    isTasklessOrcaRole(launchSelection.role) ? currentScopedExecution?.requestId : undefined,
  );
  const onRetry = (launchSelection: OrcaLaunchSelection, requestId: string) =>
    dispatch("start", launchSelection, requestId);

  return (
    <section aria-label="Native Orca chats" className={orcaPane} data-testid="orca-chats-pane">
      <OrcaRoleLauncher
        taskDocuments={taskDocuments}
        series={series}
        roleDefaults={roleDefaults}
        agents={agents}
        selection={selection}
        currentScopedExecution={currentScopedExecution}
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
  );
}

export function ChatsModePanels({
  active,
  selectedLifecycleId,
  selectedLeafKey,
  taskDocuments,
  series,
  contextMaster,
}: {
  active: boolean;
  selectedLifecycleId: string | undefined;
  selectedLeafKey: string | undefined;
  taskDocuments: TaskDocNode[];
  series: SeriesNode[];
  contextMaster: string | undefined;
}) {
  const [chatsMode, setChatsMode] = useState<"orca" | "ar">("orca");
  return (
    <>
      <div role="tablist" aria-label="Chats mode" className={chatsModeTabs}>
        <button
          id="chats-mode-orca"
          type="button"
          role="tab"
          aria-selected={chatsMode === "orca"}
          aria-controls="chats-panel-orca"
          className={chatsModeTab({ selected: chatsMode === "orca" })}
          onClick={() => setChatsMode("orca")}
        >
          Orca
        </button>
        <button
          id="chats-mode-ar"
          type="button"
          role="tab"
          aria-selected={chatsMode === "ar"}
          aria-controls="chats-panel-ar"
          className={chatsModeTab({ selected: chatsMode === "ar" })}
          onClick={() => setChatsMode("ar")}
        >
          AR Sessions
        </button>
      </div>
      <div
        id="chats-panel-orca"
        role="tabpanel"
        aria-labelledby="chats-mode-orca"
        aria-hidden={chatsMode !== "orca"}
        style={{ display: chatsMode === "orca" ? "flex" : "none" }}
        className={chatsModePanel}
      >
        <OrcaChatsPane active={active && chatsMode === "orca"} taskDocuments={taskDocuments} series={series} />
      </div>
      <div
        id="chats-panel-ar"
        role="tabpanel"
        aria-labelledby="chats-mode-ar"
        aria-hidden={chatsMode !== "ar"}
        style={{ display: chatsMode === "ar" ? "flex" : "none" }}
        className={chatsModePanel}
      >
        <SessionsView
          active={active && chatsMode === "ar"}
          selectedLifecycleId={selectedLifecycleId}
          selectedLeafKey={selectedLeafKey}
          taskDocuments={taskDocuments}
          contextMaster={contextMaster}
        />
      </div>
    </>
  );
}
