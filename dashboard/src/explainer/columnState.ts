// The explainer column's state (MIK-R106 v2 rules 1–5, 7, 8 and 10): what it reads, the form it
// opens, what his Send and Retire call, and the one frame it keeps. The column component renders
// this; every hook here is one block of that state, so none of them grows past a screen.
import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react';

import { usePublishShownChat, type ShownChat } from '../tray/shownChat';
import { pickKindWords } from '../tray/trayModel';
import { trayImageStore, trayStore, useTray } from '../tray/trayStore';
import { useSeatList } from '../tray/traySeats';
import { wirePicks } from '../tray/traySend';
import type { TrayPick, TrayWirePick } from '../tray/types';
import {
  buildExplainer,
  newExplainerId,
  readExplainerState,
  retireExplainer,
  sameExplainerAddress,
  type ExplainerAddress,
  type ExplainerAttemptAnswer,
  type ExplainerBuild,
  type ExplainerBuildBody,
  type ExplainerHeldDelivery,
  type ExplainerRead,
  type ExplainerRetireAnswer,
  type ExplainerSeatKind,
  type ExplainerStalePart,
  type ExplainerState,
} from './client';
import {
  agentMenu,
  buildFailure,
  changeSetPick,
  formReferences,
  phaseOf,
  sendAimedAtArchived,
  type ExplainerMenuRow,
  type ExplainerPhase,
  type FormReference,
} from './model';
import {
  askFirstRequest,
  clearFirstRequest,
  onFirstRequest,
  pendingFirstRequest,
  placeholderExplainerTargetId,
  registerExplainerTarget,
  setActiveExplainerColumn,
  type FirstRequestBody,
} from './request';
import { publishExplainerSeat } from './ExplainerTaskDocument';

// The build is a read at this cadence while it runs; the cadence is the dashboard's, not the
// server's, and a read starts no turn.
export const BUILD_POLL_MS = 1_000;

const NO_PREFILL: ExplainerStalePart[] = [];
const NO_GRADES: string[] = [];

/** The first request's form as this column holds it. */
export interface FormState {
  prefill: ExplainerStalePart[];
  staleGrades: string[];
}

/** A step of a Send or a Retire that could not run, in the words the form shows. */
export interface Failure {
  step: string;
  reason: string;
}

/** What this Send carried. The build panel shows it as waiting to go out, even after the tray empties. */
export interface SentRequest {
  message: string;
  titles: string[];
}

export interface ExplainerColumnOptions {
  planned?: boolean;
  comparisonNumber?: number | null;
  history?: 'live' | 'recorded' | 'planned';
  taskPath?: string;
  masterPath?: string;
}

type SetForm = Dispatch<SetStateAction<FormState | null>>;
type SetFailure = Dispatch<SetStateAction<Failure | null>>;
type SetRead = Dispatch<SetStateAction<ExplainerRead | null>>;

/** A stable address: the same three facts are the same address, however often it is rebuilt. */
export function useExplainerAddress(address: ExplainerAddress): ExplainerAddress {
  const { repository, master, leaf } = address;
  return useMemo(() => ({ repository, master, leaf }), [repository, master, leaf]);
}

/** Opening the column reads its state once; `refresh` asks for another read. Neither posts. */
export function useExplainerRead(address: ExplainerAddress, active: boolean) {
  const target = useExplainerAddress(address);
  const [read, setRead] = useState<ExplainerRead | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!active || !target.leaf) return;
    let cancelled = false;
    void readExplainerState(target).then((answer) => {
      if (!cancelled) setRead(answer);
    });
    return () => {
      cancelled = true;
    };
  }, [target, active, attempt]);
  const refresh = useCallback(() => setAttempt((current) => current + 1), []);
  return { read, setRead, refresh };
}

/** A build that ran is polled until it stops; each read is a read, never a turn. */
export function useBuildPolling(read: ExplainerRead | null, refresh: () => void, active: boolean, sending = false) {
  const building = (read?.kind === 'state' && read.value.build?.state === 'running') || sending;
  useEffect(() => {
    if (!active || !building) return;
    const timer = setInterval(refresh, BUILD_POLL_MS);
    return () => clearInterval(timer);
  }, [active, building, refresh]);
}

function useColumnRead(address: ExplainerAddress, active: boolean, sending: boolean) {
  const { read, setRead, refresh } = useExplainerRead(address, active);
  useBuildPolling(read, refresh, active, sending);
  const state = read?.kind === 'state' ? read.value : null;
  return { read, setRead, refresh, state };
}

function applyFirstRequestAsk(body: FirstRequestBody, setForm: SetForm): void {
  if (body.message !== undefined && trayStore.getState().text === '') {
    trayStore.getState().setText(body.message);
  }
  setForm({ prefill: body.staleParts, staleGrades: body.staleGrades });
}

/**
 * ✦ Explain and a ⊕ ask for the form (MIK-R106 rule 3), from inside the column or from before it
 * was shown: the ask that was pending on mount, and every ask made while the column is mounted.
 */
function useFirstRequestAsks(address: ExplainerAddress, setForm: SetForm): void {
  const target = useExplainerAddress(address);
  useEffect(() => {
    if (!target.leaf) return;
    const pending = pendingFirstRequest(target);
    if (pending) applyFirstRequestAsk(pending, setForm);
  }, [target, setForm]);
  useEffect(() => {
    if (!target.leaf) return;
    return onFirstRequest((body) => {
      if (sameExplainerAddress(body.address, target)) applyFirstRequestAsk(body, setForm);
    });
  }, [target, setForm]);
}

/**
 * A ⊕ on an invariant or a block puts a reference in the tray; with no seat to send it to, that
 * click opens this form, and later ⊕ clicks add references to it (MIK-R106 rule 3).
 */
function usePickOpensForm(
  address: ExplainerAddress,
  count: number,
  seat: ExplainerSeatKind | null,
  active: boolean,
  setForm: SetForm,
): void {
  const target = useExplainerAddress(address);
  const counted = useRef(count);
  useEffect(() => {
    const before = counted.current;
    counted.current = count;
    if (!target.leaf || before !== 0 || count === 0) return;
    if (seat === 'live' || seat === 'closed') return;
    if (active) askFirstRequest(target);
    setForm({ prefill: [], staleGrades: [] });
  }, [target, count, seat, active, setForm]);
}

/** The task document's Retire control and the tray's archived-target lookup follow this column's read. */
function usePublishedSeat(address: ExplainerAddress, read: ExplainerRead | null, active: boolean): void {
  const target = useExplainerAddress(address);
  useEffect(() => {
    if (!active || !target.leaf) return;
    setActiveExplainerColumn(target);
    return () => setActiveExplainerColumn(null);
  }, [target, active]);
  useEffect(() => {
    if (!target.leaf) return;
    if (read?.kind === 'state') {
      publishExplainerSeat(target, read.value);
    } else if (active) {
      registerExplainerTarget(target, { agentId: null, activeSeat: false });
    }
  }, [target, read, active]);
}

function useColumnForm(address: ExplainerAddress, read: ExplainerRead | null, state: ExplainerState | null, active: boolean) {
  const [form, setForm] = useState<FormState | null>(null);
  useFirstRequestAsks(address, setForm);
  const picks = useTray((tray) => tray.picks);
  usePickOpensForm(address, picks.length, state?.seat ?? null, active, setForm);
  usePublishedSeat(address, read, active);
  return { form, setForm, picks };
}

interface SendContext {
  address: ExplainerAddress;
  state: ExplainerState | null;
  prefill: ExplainerStalePart[];
  staleGrades: string[];
  options?: ExplainerColumnOptions;
  refresh: () => void;
  setRead: SetRead;
  setForm: SetForm;
  setSent: Dispatch<SetStateAction<SentRequest | null>>;
  setFailure: SetFailure;
  setHeld: Dispatch<SetStateAction<ExplainerHeldDelivery | null>>;
}

function resolvePlanned(state: ExplainerState | null, options?: ExplainerColumnOptions): boolean {
  return options?.planned !== undefined ? options.planned : state?.binding?.kind === 'planned';
}

function resolveComparisonNumber(state: ExplainerState | null, options?: ExplainerColumnOptions): number | null {
  if (options?.comparisonNumber !== undefined) return options.comparisonNumber;
  return state?.binding ? state.binding.comparison : null;
}

function buildBodyForSend(
  requestId: string,
  wired: TrayWirePick[],
  state: ExplainerState | null,
  prefill: ExplainerStalePart[],
  staleGrades: string[],
  options?: ExplainerColumnOptions,
): ExplainerBuildBody {
  // His words go out as they are: the route composes the references, their notes, the images and
  // the text into the first turn once (MIK-R188 rule 8; serving/explainer.py `_send_facts`). The
  // dashboard must not compose them again or the note and every reference appear twice. A
  // reference-only Send carries no text: the picks name what it explains, and the route composes.
  const message = trayStore.getState().text;
  const planned = resolvePlanned(state, options);
  const comparisonNumber = resolveComparisonNumber(state, options);
  const history = planned ? 'planned' : (options?.history ?? 'live');
  return {
    requestId,
    target: 'new-explainer',
    message,
    picks: wired,
    planned,
    comparisonNumber,
    history,
    stalePartIds: prefill.map((part) => part.id),
    staleGrades,
  };
}

function settleAcceptedBuild(
  accepted: ExplainerAttemptAnswer,
  body: ExplainerBuildBody,
  picksNow: readonly TrayPick[],
  ctx: SendContext,
): boolean {
  const failedBuild = accepted.state.build ? buildFailure(accepted.state.build) : null;
  if (failedBuild) {
    ctx.setRead({ kind: 'state', value: accepted.state });
    ctx.setFailure(failedBuild);
    return false;
  }
  ctx.setHeld(accepted.held ?? null);
  ctx.setSent({ message: body.message, titles: picksNow.map((pick) => pick.input.title) });
  if (accepted.state.seat !== null || accepted.state.build !== null) {
    ctx.setRead({ kind: 'state', value: accepted.state });
  }
  for (const pick of picksNow) trayStore.getState().remove(pick.id);
  trayStore.getState().setText('');
  clearFirstRequest(ctx.address);
  ctx.setForm(null);
  ctx.refresh();
  return true;
}

/** His Send (MIK-R106 rule 4.1): the build is posted once with a stable requestId, and his request goes out as the first turn. */
function useSend(ctx: SendContext, sending: boolean, setSending: Dispatch<SetStateAction<boolean>>) {
  const { address, state, prefill, staleGrades, options, setFailure, setHeld } = ctx;
  const requestIdRef = useRef<string | null>(null);
  const inFlightRef = useRef(false);
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;
  useEffect(() => {
    requestIdRef.current = null;
  }, [address]);
  const resetRequestId = useCallback(() => {
    requestIdRef.current = null;
  }, []);
  const send = useCallback(async () => {
    if (inFlightRef.current) return;
    const picksNow = trayStore.getState().picks;
    const wired = await wirePicks(picksNow, trayImageStore);
    if (typeof wired === 'string') {
      setFailure({ step: 'the request', reason: wired });
      return;
    }
    const requestId = (requestIdRef.current ??= newExplainerId('req'));
    const body = buildBodyForSend(requestId, wired, state, prefill, staleGrades, options);
    inFlightRef.current = true;
    setSending(true);
    setFailure(null);
    setHeld(null);
    const answer = await buildExplainer(address, body);
    inFlightRef.current = false;
    setSending(false);
    if (answer.kind === 'refused') {
      setFailure({ step: 'The build', reason: `${answer.reason}: ${answer.detail}` });
      return;
    }
    if (settleAcceptedBuild(answer.answer, body, picksNow, ctxRef.current)) {
      requestIdRef.current = null;
    }
  }, [address, options, prefill, setFailure, setHeld, setSending, staleGrades, state]);
  return { send, sending, resetRequestId };
}

interface RetireContext {
  address: ExplainerAddress;
  refresh: () => void;
  setForm: SetForm;
  setFailure: SetFailure;
}

/** Retire's confirmation and its one call (MIK-R106 rule 7). */
function useRetire({ address, refresh, setForm, setFailure }: RetireContext) {
  const [retireOpen, setRetireOpen] = useState(false);
  const [receipt, setReceipt] = useState<ExplainerRetireAnswer | null>(null);
  const receiptIdRef = useRef<string | null>(null);
  const toggle = useCallback(() => setRetireOpen((open) => !open), []);
  const cancel = useCallback(() => setRetireOpen(false), []);
  const retire = useCallback(async () => {
    const receiptId = (receiptIdRef.current ??= newExplainerId('retire'));
    const answer = await retireExplainer(address, receiptId);
    if (answer.kind !== 'retired') {
      setRetireOpen(false);
      setFailure({ step: 'Retire', reason: `${answer.reason}: ${answer.detail}` });
      return;
    }
    setReceipt(answer.answer);
    setForm(null);
    const incomplete =
      answer.answer.receipt.state !== 'finished' || answer.answer.receipt.remaining.length > 0;
    if (!incomplete) receiptIdRef.current = null;
    setRetireOpen(incomplete);
    refresh();
  }, [address, refresh, setFailure, setForm]);
  return { retireOpen, receipt, toggle, cancel, retire };
}

function useExplain(
  address: ExplainerAddress,
  state: ExplainerState | null,
  task: string,
  setForm: SetForm,
  setSelectedAgentId: Dispatch<SetStateAction<string | null>>,
): () => void {
  return useCallback(() => {
    setSelectedAgentId(null);
    if (state?.seat === 'live' || state?.seat === 'closed') {
      trayStore.getState().take(changeSetPick(address, `Change set · ${task}`, state.binding?.comparison ?? null));
      return;
    }
    askFirstRequest(address);
    setForm({ prefill: [], staleGrades: [] });
  }, [address, state, task, setForm, setSelectedAgentId]);
}

function useCancel(
  address: ExplainerAddress,
  setForm: SetForm,
  setFailure: SetFailure,
  resetRequestId: () => void,
): () => void {
  return useCallback(() => {
    for (const pick of trayStore.getState().picks) trayStore.getState().remove(pick.id);
    trayStore.getState().setText('');
    clearFirstRequest(address);
    resetRequestId();
    setFailure(null);
    setForm(null);
  }, [address, resetRequestId, setFailure, setForm]);
}

function useTrayWrites() {
  const setMessage = useCallback((text: string) => trayStore.getState().setText(text), []);
  const removePick = useCallback((id: string) => trayStore.getState().remove(id), []);
  const setNote = useCallback((id: string, note: string) => trayStore.getState().setNote(id, note), []);
  return { setMessage, removePick, setNote };
}

function useSelectedAgent(address: ExplainerAddress, active: boolean, state: ExplainerState | null) {
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  useEffect(() => {
    setSelectedAgentId(null);
  }, [address, active]);
  const selectAgent = useCallback(
    (agentId: string) => {
      const next = agentId === state?.agentId ? null : agentId;
      setSelectedAgentId(next);
      if (!trayStore.getState().targetsChosen) {
        trayStore.setState({ targets: [agentId], defaultDecided: true });
      }
    },
    [state?.agentId],
  );
  return { selectedAgentId, setSelectedAgentId, selectAgent };
}

function findSelectedRow(
  rows: ExplainerMenuRow[],
  selectedAgentId: string | null,
  explainerAgentId: string | null,
): ExplainerMenuRow | null {
  if (!selectedAgentId || selectedAgentId === explainerAgentId) return null;
  return rows.find((row) => row.agentId === selectedAgentId) ?? null;
}

function computeFrameTarget(
  selectedRow: ExplainerMenuRow | null,
  state: ExplainerState | null,
): { agentId: string; workspaceId: string } | null {
  if (selectedRow) {
    return {
      agentId: selectedRow.agentId,
      workspaceId: selectedRow.workspaceId ?? state?.workspaceId ?? 'default',
    };
  }
  if (state?.agentId && state.workspaceId) {
    return { agentId: state.agentId, workspaceId: state.workspaceId };
  }
  return null;
}

function computeShownChat(
  address: ExplainerAddress,
  task: string,
  active: boolean,
  selectedRow: ExplainerMenuRow | null,
  state: ExplainerState | null,
): ShownChat | null {
  if (!active || !address.leaf) return null;
  if (selectedRow) return { agentId: selectedRow.agentId, label: `${selectedRow.role} · ${task}` };
  const agentId = state?.agentId ?? placeholderExplainerTargetId(address);
  return { agentId, label: `Explainer · ${task}` };
}

function useShownExplainerChat(shownChat: ShownChat | null): void {
  usePublishShownChat(shownChat);
  useEffect(() => {
    if (!shownChat) return;
    const tray = trayStore.getState();
    if (tray.picks.length === 0 && tray.targets.length === 0) {
      trayStore.setState({ targetsChosen: false, defaultDecided: false });
    }
  }, [shownChat]);
}

function useMenuRows(
  target: ExplainerAddress,
  task: string,
  state: ExplainerState | null,
  selectedAgentId: string | null,
  options?: ExplainerColumnOptions,
): ExplainerMenuRow[] {
  const seats = useSeatList();
  return useMemo(
    () =>
      agentMenu(
        seats.seats,
        task,
        { agentId: state?.agentId ?? null, workspaceId: state?.workspaceId ?? null, live: state?.seat === 'live' },
        {
          repository: target.repository,
          master: target.master,
          leaf: target.leaf,
          taskPath: options?.taskPath,
          masterPath: options?.masterPath,
          selectedAgentId,
        },
      ),
    [seats.seats, task, state?.agentId, state?.workspaceId, state?.seat, target, options?.taskPath, options?.masterPath, selectedAgentId],
  );
}

/** Everything the column draws, as one block: the read, the form, and his Send, Retire and Explain. */
export interface ExplainerColumnState {
  read: ExplainerRead | null;
  state: ExplainerState | null;
  phase: ExplainerPhase;
  refresh: () => void;
  form: FormState | null;
  picks: TrayPick[];
  message: string;
  references: FormReference[];
  rows: ExplainerMenuRow[];
  selectedAgentId: string | null;
  selectAgent: (agentId: string) => void;
  frameTarget: { agentId: string; workspaceId: string } | null;
  showFrame: boolean;
  failure: Failure | null;
  held: ExplainerHeldDelivery | null;
  activeBuild: ExplainerBuild | null;
  sent: SentRequest | null;
  sending: boolean;
  send: () => Promise<void>;
  retireOpen: boolean;
  receipt: ExplainerRetireAnswer | null;
  onRetire: () => Promise<void>;
  onRetireToggle: () => void;
  onRetireCancel: () => void;
  explain: () => void;
  cancel: () => void;
  openForm: () => void;
  setMessage: (text: string) => void;
  removePick: (id: string) => void;
  setNote: (id: string, note: string) => void;
  archivedSend: boolean;
}

export function useExplainerColumn(
  address: ExplainerAddress,
  task: string,
  active: boolean,
  options?: ExplainerColumnOptions,
): ExplainerColumnState {
  const target = useExplainerAddress(address);
  const [sending, setSending] = useState(false);
  const { read, setRead, refresh, state } = useColumnRead(target, active, sending);
  const { form, setForm, picks } = useColumnForm(target, read, state, active);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [held, setHeld] = useState<ExplainerHeldDelivery | null>(null);
  const [sent, setSent] = useState<SentRequest | null>(null);
  const { selectedAgentId, setSelectedAgentId, selectAgent } = useSelectedAgent(target, active, state);
  const message = useTray((tray) => tray.text);
  const prefill = form ? form.prefill : NO_PREFILL;
  const staleGrades = form ? form.staleGrades : NO_GRADES;
  const { send, resetRequestId } = useSend(
    { address: target, state, prefill, staleGrades, options, refresh, setRead, setForm, setSent, setFailure, setHeld },
    sending,
    setSending,
  );
  const retire = useRetire({ address: target, refresh, setForm, setFailure });
  const explain = useExplain(target, state, task, setForm, setSelectedAgentId);
  const cancel = useCancel(target, setForm, setFailure, resetRequestId);
  const openForm = useCallback(() => setForm({ prefill: [], staleGrades: [] }), [setForm]);
  const trayWrites = useTrayWrites();
  const references = useMemo(() => formReferences(picks, (pick) => pickKindWords(pick.input)), [picks]);
  const rows = useMenuRows(target, task, state, selectedAgentId, options);
  const phase = phaseOf(read, form !== null);
  const selectedRow = findSelectedRow(rows, selectedAgentId, state?.agentId ?? null);
  const frameTarget = computeFrameTarget(selectedRow, state);
  const showFrame = Boolean(selectedRow) || phase === 'live' || phase === 'closed';
  useShownExplainerChat(computeShownChat(target, task, active, selectedRow, state));
  return {
    read,
    state,
    refresh,
    form,
    picks,
    message,
    references,
    rows,
    selectedAgentId,
    selectAgent,
    frameTarget,
    showFrame,
    failure,
    held,
    activeBuild: state?.build ?? null,
    sent,
    sending,
    send,
    retireOpen: retire.retireOpen,
    receipt: retire.receipt,
    onRetire: retire.retire,
    onRetireToggle: retire.toggle,
    onRetireCancel: retire.cancel,
    explain,
    cancel,
    openForm,
    setMessage: trayWrites.setMessage,
    removePick: trayWrites.removePick,
    setNote: trayWrites.setNote,
    phase,
    archivedSend: sendAimedAtArchived(state) && picks.length > 0,
  };
}
