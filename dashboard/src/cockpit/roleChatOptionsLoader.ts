import { useCallback, useEffect, useRef, useState } from "react";

import {
  boundTasklessRequest,
  isTasklessRole,
  isUncertainRoleExecution,
  readSelectedRoleRequest,
  roleDocumentScope,
  roleOptionsScope,
  roleRequestKey,
  sameRoleDocumentScope,
  sameRoleOptionsScope,
  usesRequestReceipts,
  type RoleDocumentScope,
  type RoleExecutionReceipt,
  type RoleLaunchSelection,
  type RoleLauncherOptions,
  type RoleOptionsScope,
  type RoleTasklessActiveRequest,
  type SavedRoleCatalog,
} from "./roleLaunchModel";
import { rememberRoleDefaults } from "./launcherDefaultsMemory";
import type { ActiveRequestsRef, ExecutionErrorState, ResultFlowState } from "./roleChatResult";

// How soon the options are read again after the backend answered that a launch is in progress.
const LAUNCH_REREAD_MS = 1500;

export interface OptionsFlowState {
  savedCatalogState: { scope: RoleOptionsScope; value: SavedRoleCatalog } | null;
  optionsState: { scope: RoleOptionsScope; value: RoleLauncherOptions } | null;
  optionsLoadingScope: RoleOptionsScope | null;
  optionsErrorState: { scope: RoleOptionsScope; message: string } | null;
}

export type SetExecutionState = (
  state:
    | { scope: RoleDocumentScope; value: RoleExecutionReceipt }
    | null
    | ((current: { scope: RoleDocumentScope; value: RoleExecutionReceipt } | null) =>
      | { scope: RoleDocumentScope; value: RoleExecutionReceipt }
      | null),
) => void;

// A launch holds the backend's launch lock for as long as it runs, and the options and
// result routes answer that with a mark of their own. That is no error of the selection
// shown: the launcher reads again shortly, until the launch has answered.
export function useLaunchReread(requestOptionsRefresh: () => void): {
  waitForLaunch: () => void;
} {
  const rereadTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(rereadTimer.current), []);
  const waitForLaunch = useCallback(() => {
    window.clearTimeout(rereadTimer.current);
    rereadTimer.current = window.setTimeout(() => requestOptionsRefresh(), LAUNCH_REREAD_MS);
  }, [requestOptionsRefresh]);
  return { waitForLaunch };
}

function buildOptionsBody(
  documentScope: RoleDocumentScope,
  requestScope: RoleOptionsScope,
  refreshAgentCatalog: boolean,
) {
  return {
    ...documentScope,
    ...(requestScope.agentId ? { agentId: requestScope.agentId } : {}),
    ...(refreshAgentCatalog ? { refreshCatalog: true } : {}),
  };
}

function adoptBoundRequest(
  bound: boolean,
  scope: RoleDocumentScope,
  executions: RoleExecutionReceipt[],
  active: RoleTasklessActiveRequest | undefined,
  update: (scope: RoleDocumentScope, request: RoleTasklessActiveRequest | undefined) => void,
) {
  if (!bound && isTasklessRole(scope)) return active;
  const request = boundTasklessRequest(executions, active, !isTasklessRole(scope));
  if (request) update(scope, request);
  return request;
}

interface ExecutionWriters {
  setExecutionState: SetExecutionState;
  setExecutionErrorState: ResultFlowState["setExecutionErrorState"];
  updateActiveRequest: ResultFlowState["updateActiveRequest"];
  rememberExecution: ResultFlowState["rememberExecution"];
  rememberFailureExecution: ResultFlowState["rememberExecution"];
  fetchExecutionResult: (scope: RoleDocumentScope, reportNotFound: boolean, requestId?: string) => Promise<RoleExecutionReceipt | undefined>;
}

// A saved execution behind the active request becomes the run's receipt: pending while
// uncertain (keeping the retry selection), settled otherwise.
function adoptSavedExecution(
  writers: ExecutionWriters,
  requestScope: RoleOptionsScope,
  activeRequest: RoleTasklessActiveRequest | undefined,
  savedExecution: RoleExecutionReceipt | undefined,
): void {
  if (!savedExecution?.requestId) return;
  const pending = isUncertainRoleExecution(savedExecution);
  writers.updateActiveRequest(
    requestScope,
    pending
      ? {
          requestId: savedExecution.requestId,
          pending: true,
          ...(activeRequest?.retryPayload ?? savedExecution.retryPayload
            ? { retryPayload: activeRequest?.retryPayload ?? savedExecution.retryPayload }
            : {}),
        }
      : { requestId: savedExecution.requestId },
  );
  writers.rememberExecution(requestScope, savedExecution);
}

// The saved execution behind the active request is adopted, then a resumed request id
// polls its result.
function adoptRequestExecutionState(
  writers: ExecutionWriters,
  activeRequestsRef: ActiveRequestsRef,
  boundSelection: RoleLaunchSelection | undefined,
  requestScope: RoleOptionsScope,
  executions: RoleExecutionReceipt[],
): void {
  const activeRequest = adoptBoundRequest(
    Boolean(boundSelection),
    requestScope,
    executions,
    activeRequestsRef.current[roleRequestKey(requestScope)] ?? readSelectedRoleRequest(requestScope),
    writers.updateActiveRequest,
  );
  const savedExecution = activeRequest
    ? executions.find((execution) => execution.requestId === activeRequest?.requestId)
    : undefined;
  adoptSavedExecution(writers, requestScope, activeRequest, savedExecution);
  const activeRequestId = activeRequestsRef.current[roleRequestKey(requestScope)]?.requestId;
  if (activeRequestId) void writers.fetchExecutionResult(roleDocumentScope(requestScope), false, activeRequestId);
}

interface OptionsStores {
  setSavedCatalogState: (state: OptionsFlowState["savedCatalogState"]) => void;
  setOptionsErrorState: (state: OptionsFlowState["optionsErrorState"]) => void;
  setOptionsState: (state: OptionsFlowState["optionsState"]) => void;
}

// A 200 answers the launch options: the role defaults, the agent catalog, and the
// saved execution behind the active request.
function applyOptionsSuccess(
  stores: OptionsStores,
  writers: ExecutionWriters,
  activeRequestsRef: ActiveRequestsRef,
  boundSelection: RoleLaunchSelection | undefined,
  value: RoleLauncherOptions,
  requestScope: RoleOptionsScope,
  refreshAgentCatalog: boolean,
  retryOptions: boolean,
): void {
  stores.setOptionsErrorState(null);
  // The options answer's execution becomes the failure reference only — never the
  // active execution authority. A delayed options reply settling after a newer
  // Result receipt leaves that newer receipt in charge.
  if (value.execution) writers.rememberFailureExecution(requestScope, value.execution);
  rememberRoleDefaults(requestScope, value.roleDefaults);
  stores.setOptionsState({ scope: requestScope, value });
  writers.setExecutionErrorState((current) =>
    refreshAgentCatalog || retryOptions || !current?.sticky ? null : current,
  );
  if (usesRequestReceipts(requestScope)) {
    adoptRequestExecutionState(writers, activeRequestsRef, boundSelection, requestScope, value.executions ?? []);
  }
}

// A refusal is a reread mark while a launch is in progress, else an options error.
function applyOptionsRefusal(
  setOptionsErrorState: OptionsStores["setOptionsErrorState"],
  waitForLaunch: () => void,
  isCurrentOptionsScope: (scope: RoleOptionsScope) => boolean,
  response: Response,
  value: { detail?: unknown; launchInProgress?: unknown },
  requestScope: RoleOptionsScope,
): void {
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
}

function failOptionsLoad(
  setOptionsErrorState: OptionsStores["setOptionsErrorState"],
  isCurrentOptionsScope: (scope: RoleOptionsScope) => boolean,
  requestScope: RoleOptionsScope,
  reason: unknown,
): void {
  if (reason instanceof DOMException && reason.name === "AbortError") return;
  if (isCurrentOptionsScope(requestScope)) {
    setOptionsErrorState({
      scope: requestScope,
      message: "Could not load role launch options. Check the dashboard connection and retry.",
    });
  }
}

interface OptionsLoadContext {
  active: boolean;
  boundSelection: RoleLaunchSelection | undefined;
  selectionComplete: boolean;
  documentScope: RoleDocumentScope;
  optionsScope: RoleOptionsScope;
  // The refresh generation this load answers; read by the effect's dependency array.
  optionsRefresh: number;
  catalogRefresh: number;
  activeRequestsRef: ActiveRequestsRef;
  waitForLaunch: () => void;
  isCurrentOptionsScope: (scope: RoleOptionsScope) => boolean;
  setOptionsLoadingScope: (scope: RoleOptionsScope | null) => void;
  setOptionsErrorState: OptionsStores["setOptionsErrorState"];
  setOptionsState: OptionsStores["setOptionsState"];
  setSavedCatalogState: OptionsStores["setSavedCatalogState"];
  setExecutionState: SetExecutionState;
  setExecutionErrorState: ResultFlowState["setExecutionErrorState"];
  updateActiveRequest: ResultFlowState["updateActiveRequest"];
  rememberExecution: ResultFlowState["rememberExecution"];
  rememberFailureExecution: ResultFlowState["rememberExecution"];
  fetchExecutionResult: (scope: RoleDocumentScope, reportNotFound: boolean, requestId?: string) => Promise<RoleExecutionReceipt | undefined>;
  sentCatalogRefresh: { current: number };
  explicitOptionsRefresh: { current: boolean };
}

// While the fresh options request is pending, this snapshot is what the launcher offers: its
// choices are selectable and a start reads them, silently (MIK-R215); the fresh answer replaces it.
function readSavedCatalog(ctx: OptionsLoadContext, scope: RoleOptionsScope, signal: AbortSignal, pending: () => boolean): void {
  void fetch("/api/role-launch/saved-catalog", { signal, cache: "no-store" }).then(async (response) => {
    if (!response.ok) return;
    const value = await response.json() as SavedRoleCatalog;
    if (pending() && !signal.aborted && ctx.isCurrentOptionsScope(scope) && Array.isArray(value.agents)) {
      ctx.setSavedCatalogState({ scope, value });
    }
  }).catch(() => { /* No saved answer is an absent preview; the fresh request still decides. */ });
}

// One options load for the selected scope: the request with its catalog-refresh mark,
// the answer folded into the stores, and the abort on scope change.
function runOptionsLoad(ctx: OptionsLoadContext): () => void {
  if (!ctx.active || !ctx.selectionComplete) return () => {};
  const requestScope = ctx.optionsScope;
  const retryOptions = ctx.explicitOptionsRefresh.current;
  ctx.explicitOptionsRefresh.current = false;
  const refreshAgentCatalog = ctx.catalogRefresh > ctx.sentCatalogRefresh.current;
  if (refreshAgentCatalog) ctx.sentCatalogRefresh.current = ctx.catalogRefresh;
  const controller = new AbortController();
  let freshPending = true;
  ctx.setSavedCatalogState(null);
  readSavedCatalog(ctx, requestScope, controller.signal, () => freshPending);
  const body = buildOptionsBody(ctx.documentScope, requestScope, refreshAgentCatalog);
  const stores: OptionsStores = {
    setOptionsErrorState: ctx.setOptionsErrorState,
    setOptionsState: ctx.setOptionsState,
    setSavedCatalogState: ctx.setSavedCatalogState,
  };
  const writers: ExecutionWriters = {
    setExecutionState: ctx.setExecutionState,
    setExecutionErrorState: ctx.setExecutionErrorState,
    updateActiveRequest: ctx.updateActiveRequest,
    rememberExecution: ctx.rememberExecution,
    rememberFailureExecution: ctx.rememberFailureExecution,
    fetchExecutionResult: ctx.fetchExecutionResult,
  };
  ctx.setOptionsLoadingScope(requestScope);
  ctx.setExecutionState((current) =>
    current && sameRoleDocumentScope(current.scope, ctx.documentScope) ? null : current,
  );
  void fetch("/api/role-launch/options", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: controller.signal,
  }).then(async (response) => {
    if (!response.ok) {
      if (ctx.isCurrentOptionsScope(requestScope)) ctx.setOptionsState(null);
      const value = (await response.json().catch(() => ({}))) as { detail?: unknown; launchInProgress?: unknown };
      applyOptionsRefusal(ctx.setOptionsErrorState, ctx.waitForLaunch, ctx.isCurrentOptionsScope, response, value, requestScope);
      return;
    }
    const value = (await response.json()) as RoleLauncherOptions;
    if (!ctx.isCurrentOptionsScope(requestScope)) return;
    applyOptionsSuccess(stores, writers, ctx.activeRequestsRef, ctx.boundSelection, value, requestScope, refreshAgentCatalog, retryOptions);
  }).catch((reason: unknown) => {
    if (!(reason instanceof DOMException && reason.name === "AbortError") && ctx.isCurrentOptionsScope(requestScope)) ctx.setOptionsState(null);
    failOptionsLoad(ctx.setOptionsErrorState, ctx.isCurrentOptionsScope, requestScope, reason);
  }).finally(() => {
    freshPending = false;
    if (!controller.signal.aborted && ctx.isCurrentOptionsScope(requestScope)) {
      ctx.setSavedCatalogState(null);
      ctx.setOptionsLoadingScope(null);
    }
  });
  return () => { freshPending = false; controller.abort(); };
}

export type { ExecutionErrorState };

// The current response and its loading/error scopes; saved catalog parts are owned by the server.
function useOptionsStores(): OptionsFlowState & {
  setOptionsState: OptionsStores["setOptionsState"];
  setSavedCatalogState: OptionsStores["setSavedCatalogState"];
  setOptionsLoadingScope: (scope: RoleOptionsScope | null) => void;
  setOptionsErrorState: OptionsStores["setOptionsErrorState"];
  sentCatalogRefresh: { current: number };
  explicitOptionsRefresh: { current: boolean };
  requestCatalogRefresh: (bump: () => void) => void;
  requestExplicitOptionsRefresh: (bump: () => void) => void;
} {
  const [savedCatalogState, setSavedCatalogState] = useState<OptionsFlowState["savedCatalogState"]>(null);
  const [optionsState, setOptionsState] = useState<OptionsFlowState["optionsState"]>(null);
  const [optionsLoadingScope, setOptionsLoadingScope] = useState<RoleOptionsScope | null>(null);
  const [optionsErrorState, setOptionsErrorState] = useState<OptionsFlowState["optionsErrorState"]>(null);
  const sentCatalogRefresh = useRef(0);
  const explicitOptionsRefresh = useRef(false);
  return {
    optionsState,
    savedCatalogState,
    optionsLoadingScope,
    optionsErrorState,
    setOptionsState,
    setSavedCatalogState,
    setOptionsLoadingScope,
    setOptionsErrorState,
    sentCatalogRefresh,
    explicitOptionsRefresh,
    requestCatalogRefresh: (bump: () => void) => {
      bump();
    },
    requestExplicitOptionsRefresh: (bump: () => void) => {
      explicitOptionsRefresh.current = true;
      bump();
    },
  };
}

export interface RoleChatOptionsLoaderArgs {
  active: boolean;
  boundSelection: RoleLaunchSelection | undefined;
  selectionComplete: boolean;
  documentScope: RoleDocumentScope;
  optionsScope: RoleOptionsScope;
  optionsRefresh: number;
  catalogRefresh: number;
  bumpOptionsRefresh: () => void;
  bumpCatalogRefresh: () => void;
  currentSelectionRef: { current: RoleLaunchSelection };
  activeRequestsRef: ActiveRequestsRef;
  waitForLaunch: () => void;
  setExecutionState: SetExecutionState;
  setExecutionErrorState: ResultFlowState["setExecutionErrorState"];
  updateActiveRequest: ResultFlowState["updateActiveRequest"];
  rememberExecution: ResultFlowState["rememberExecution"];
  rememberFailureExecution: ResultFlowState["rememberExecution"];
  fetchExecutionResult: (scope: RoleDocumentScope, reportNotFound: boolean, requestId?: string) => Promise<RoleExecutionReceipt | undefined>;
}

// The launch options behind the row: loaded for the selected scope, reread while a
// launch holds the backend lock, and refreshed on demand. Owns the options stores;
// the execution writers arrive as stable callbacks, so the load runs once per
// selection, never per render.
export function useRoleChatOptionsLoader(args: RoleChatOptionsLoaderArgs): OptionsFlowState & {
  isCurrentOptionsScope: (scope: RoleOptionsScope) => boolean;
  requestCatalogRefresh: () => void;
  requestExplicitOptionsRefresh: () => void;
} {
  const stores = useOptionsStores();
  const { optionsState, savedCatalogState, optionsLoadingScope, optionsErrorState } = stores;
  const isCurrentOptionsScope = useCallback(
    (scope: RoleOptionsScope) =>
      sameRoleOptionsScope(roleOptionsScope(args.currentSelectionRef.current), scope),
    [args.currentSelectionRef],
  );
  const requestCatalogRefresh = useCallback(() => {
    stores.requestCatalogRefresh(args.bumpCatalogRefresh);
  }, [stores, args.bumpCatalogRefresh]);
  const requestExplicitOptionsRefresh = useCallback(() => {
    stores.requestExplicitOptionsRefresh(args.bumpOptionsRefresh);
  }, [stores, args.bumpOptionsRefresh]);
  useEffect(() => runOptionsLoad({
    active: args.active,
    boundSelection: args.boundSelection,
    selectionComplete: args.selectionComplete,
    documentScope: args.documentScope,
    optionsScope: args.optionsScope,
    optionsRefresh: args.optionsRefresh,
    catalogRefresh: args.catalogRefresh,
    activeRequestsRef: args.activeRequestsRef,
    waitForLaunch: args.waitForLaunch,
    isCurrentOptionsScope,
    setOptionsLoadingScope: stores.setOptionsLoadingScope,
    setOptionsErrorState: stores.setOptionsErrorState,
    setOptionsState: stores.setOptionsState,
    setSavedCatalogState: stores.setSavedCatalogState,
    setExecutionState: args.setExecutionState,
    setExecutionErrorState: args.setExecutionErrorState,
    updateActiveRequest: args.updateActiveRequest,
    rememberExecution: args.rememberExecution,
    rememberFailureExecution: args.rememberFailureExecution,
    fetchExecutionResult: args.fetchExecutionResult,
    sentCatalogRefresh: stores.sentCatalogRefresh,
    explicitOptionsRefresh: stores.explicitOptionsRefresh,
  }), [args.active, args.boundSelection, args.selectionComplete, args.documentScope, args.optionsScope, args.optionsRefresh, args.catalogRefresh, args.activeRequestsRef, args.waitForLaunch, isCurrentOptionsScope, stores.setOptionsLoadingScope, stores.setOptionsErrorState, stores.setOptionsState, stores.setSavedCatalogState, args.setExecutionState, args.setExecutionErrorState, args.updateActiveRequest, args.rememberExecution, args.rememberFailureExecution, args.fetchExecutionResult, stores.sentCatalogRefresh, stores.explicitOptionsRefresh]);
  return {
    optionsState,
    savedCatalogState,
    optionsLoadingScope,
    optionsErrorState,
    isCurrentOptionsScope,
    requestCatalogRefresh,
    requestExplicitOptionsRefresh,
  };
}
