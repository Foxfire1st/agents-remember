/**
 * The Explanation view's own first request (MIK-R106 v2 rules 3 and 4; MIK-R211 rules 3 and 4).
 *
 * Pressing ✦ Explain in this view used to only ask the review page's chat column, which lives in the
 * cockpit's right rail: the ask is what opens that rail, and the press then toggled it shut again,
 * so nothing appeared where the developer pressed. This panel is the request where it was pressed:
 *
 *   * the form: the tray's references, his words, the fixed target and Send — nothing is sent until
 *     he presses it (rule 3, 10);
 *   * his Send: the build posted once with a stable request id, its progress drawn here while it
 *     runs (the server's own steps, counts and percentage), and then the page, or the failure in
 *     the server's own words with his request kept for another Send (rule 4; MIK-R211 rule 3).
 *   * his Cancel while it runs (MIK-R211 v2 rules 4 and 5): the box and this form each draw one,
 *     and each posts the running build's own request id to `…/cancel`. The attempt is then over:
 *     the view re-reads `/state`, so whatever the server reports — the ended build, its step and
 *     its own reason, or no record at all — is what the box says, and Send is free again.
 *
 * It reads `GET …/state` while a build runs, at the dashboard's own cadence; a read starts no turn.
 * The build record and its parsing are the chat column's (`src/explainer/client.ts`), so both places
 * say the same thing about the same failure.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { css } from '../../../../styled-system/css';
import { BuildProgress, isRunningBuild } from '../../../explainer/ExplainerColumnPanels';
import {
  buildExplainer,
  newExplainerId,
  type ExplainerAddress,
  type ExplainerBuild,
  type ExplainerBuildBody,
} from '../../../explainer/client';
import {
  bindingWords,
  buildFailure,
  canSend,
  firstRequestLine,
  fixedTarget,
  formReferences,
  type FormReference,
} from '../../../explainer/model';
import { clearFirstRequest } from '../../../explainer/request';
import { pickKindWords } from '../../../tray/trayModel';
import { trayImageStore, trayStore, useTray } from '../../../tray/trayStore';
import { wirePicks } from '../../../tray/traySend';
import type { TrayPick, TrayWirePick } from '../../../tray/types';
import {
  cancelExplainerBuild,
  readExplainerState,
  refusalInfo,
  type BindingOwner,
  type ExplainerBinding,
  type ExplainerCancel,
} from './explainerApi';

/** The cadence of the read while a build runs: the dashboard's, not the server's. */
export const EXPLANATION_BUILD_POLL_MS = 1_000;

const panel = css({
  display: 'grid',
  gap: '0.5rem',
  padding: '0.75rem 0.85rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'purple',
  borderRadius: '3px',
  background: 'bgPanel',
  minWidth: 0,
});
const heading = css({ fontFamily: 'mono', fontSize: '0.82rem', margin: 0 });
const line = css({ fontSize: '0.78rem', color: 'muted', margin: 0 });
const failure = css({ fontFamily: 'mono', fontSize: '0.78rem', color: 'dormant', margin: 0 });
const refs = css({ margin: 0, paddingLeft: '1rem', display: 'grid', gap: '0.2rem', fontSize: '0.76rem' });
const refNote = css({ color: 'muted', fontFamily: 'mono', fontSize: '0.7rem', marginLeft: '0.4rem' });
const wish = css({
  width: '100%',
  minHeight: '4.5rem',
  fontFamily: 'mono',
  fontSize: '0.78rem',
  color: 'inherit',
  background: 'transparent',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  padding: '0.4rem',
});
const actions = css({ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' });
const grow = css({ flex: 1 });
const quiet = css({
  fontFamily: 'mono',
  fontSize: '0.72rem',
  padding: '0.2rem 0.5rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'transparent',
  color: 'inherit',
  cursor: 'pointer',
});
const primary = css({
  fontFamily: 'mono',
  fontSize: '0.8rem',
  padding: '0.35rem 0.7rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'purple',
  borderRadius: '3px',
  color: 'purple',
  background: 'transparent',
  cursor: 'pointer',
  _disabled: { opacity: 0.45, cursor: 'default' },
});

export interface ExplanationRequestProps {
  target: ExplainerAddress;
  /** The leaf as the view names it, for the fixed target. */
  task: string;
  /** The binding the request will be built against, as `/state` answered it. */
  binding?: ExplainerBinding | null;
  /** The comparison on screen (MIK-R185 v2), so the request names it. */
  comparison?: { reference: string; number?: number; owner?: BindingOwner | string };
  history?: 'live' | 'recorded';
  /** A build the view's own read already holds: running, done, or stopped at a failed step. */
  build?: ExplainerBuild | null;
  /** A build finished: the view re-reads its page. */
  onBuilt: () => void;
  /** His Cancel: the region closes and nothing is sent. */
  onCancel: () => void;
}

// ---- the form's parts ----

/** One reference in the request, as the tray holds it. */
function RequestReference({
  reference,
  onRemove,
}: {
  reference: FormReference;
  onRemove: (id: string) => void;
}) {
  return (
    <li data-testid="explanation-request-reference">
      <span>{reference.title}</span>
      <span className={refNote}>{reference.detail}</span>
      {reference.note.trim() ? <span className={refNote}>“{reference.note}”</span> : null}{' '}
      <button
        type="button"
        className={quiet}
        aria-label={`Remove ${reference.title} from this request`}
        data-testid="explanation-request-reference-remove"
        onClick={() => onRemove(reference.id)}
      >
        ×
      </button>
    </li>
  );
}

function RequestReferences({
  references,
  onRemove,
}: {
  references: readonly FormReference[];
  onRemove: (id: string) => void;
}) {
  if (references.length === 0) {
    return (
      <p className={line} data-testid="explanation-request-empty-references">
        No references yet. Press ⊕ on a block or an invariant, or write the request below.
      </p>
    );
  }
  return (
    <ul className={refs} aria-label="References in this request" data-testid="explanation-request-references">
      {references.map((reference) => (
        <RequestReference key={reference.id} reference={reference} onRemove={onRemove} />
      ))}
    </ul>
  );
}

/** The fixed target, his Cancel and his Send. Send is refused until there is something to send. */
function RequestActions({
  targetWords,
  sendable,
  sending,
  onCancel,
  onSend,
}: {
  targetWords: string;
  sendable: boolean;
  sending: boolean;
  onCancel: () => void;
  onSend: () => void;
}) {
  return (
    <div className={actions}>
      <span className={line} data-testid="explanation-request-target">
        {targetWords}
      </span>
      <span className={grow} />
      <button type="button" className={quiet} data-testid="explanation-request-cancel" onClick={onCancel}>
        Cancel
      </button>
      <button
        type="button"
        className={primary}
        disabled={!sendable}
        data-testid="explanation-request-send"
        onClick={onSend}
      >
        {sending ? 'Sending…' : 'Send'}
      </button>
    </div>
  );
}

function RequestFields({
  task,
  binding,
  references,
  message,
  sendable,
  sending,
  onCancel,
  onSend,
}: {
  task: string;
  binding: ExplainerBinding | null;
  references: readonly FormReference[];
  message: string;
  sendable: boolean;
  sending: boolean;
  onCancel: () => void;
  onSend: () => void;
}) {
  return (
    <>
      <RequestReferences references={references} onRemove={(id) => trayStore.getState().remove(id)} />
      <textarea
        className={wish}
        rows={4}
        aria-label="What do you want explained?"
        placeholder="What do you want explained? Leave it empty for the end-to-end review."
        value={message}
        onChange={(event) => trayStore.getState().setText(event.target.value)}
      />
      <RequestActions
        targetWords={`to: ${fixedTarget(task)} · bound to ${bindingWords(bindingViewOf(binding))}`}
        sendable={sendable}
        sending={sending}
        onCancel={onCancel}
        onSend={onSend}
      />
      <p className={line} data-testid="explanation-request-line">
        {firstRequestLine(references, message)}
      </p>
    </>
  );
}

/** The build's box, and the two sentences a press can answer with. */
function RequestProblems({
  stopped,
  refusal,
}: {
  stopped: { step: string; reason: string } | null;
  refusal: string | null;
}) {
  return (
    <>
      {stopped ? (
        <p className={failure} role="alert" data-testid="explanation-request-failure" data-step={stopped.step}>
          {`${stopped.step} failed: ${stopped.reason}. Your request stays here for a new Send.`}
        </p>
      ) : null}
      {refusal ? (
        <p className={failure} role="alert" data-testid="explanation-request-refusal">
          {`The request could not go out: ${refusal}`}
        </p>
      ) : null}
    </>
  );
}

// ---- the state it holds ----

interface RequestSend {
  sent: ExplainerBuild | null;
  setSent: (build: ExplainerBuild | null) => void;
  refusal: string | null;
  setRefusal: (detail: string | null) => void;
  sending: boolean;
  send: () => void;
  /** A cancelled attempt is over: the next Send is a new request, not a repeat of that one. */
  resetRequestId: () => void;
}

type BuildAnswer =
  | { kind: 'refused'; reason: string; detail: string }
  | { kind: 'accepted'; build: ExplainerBuild | null };

/** What goes out: his words as they are, the tray's references, once. */
function requestBody(
  requestId: string,
  wired: TrayWirePick[],
  props: Pick<ExplanationRequestProps, 'binding' | 'comparison' | 'history'>,
): ExplainerBuildBody {
  const { binding, comparison, history } = props;
  const planned = binding?.owner === 'planned' || comparison?.owner === 'planned';
  return {
    requestId,
    target: 'new-explainer',
    message: trayStore.getState().text,
    picks: wired,
    planned,
    comparisonNumber: comparison?.number ?? binding?.number ?? null,
    history: planned ? 'planned' : (history ?? 'live'),
    stalePartIds: [],
    staleGrades: [],
  };
}

async function requestBuild(
  address: ExplainerAddress,
  requestId: string,
  wired: TrayWirePick[],
  props: Pick<ExplanationRequestProps, 'binding' | 'comparison' | 'history'>,
): Promise<BuildAnswer> {
  const answer = await buildExplainer(address, requestBody(requestId, wired, props));
  if (answer.kind === 'refused') return answer;
  return { kind: 'accepted', build: answer.answer.state.build ?? null };
}

/** His request went out: the tray empties and the ask is done with. */
function settleSent(
  picks: readonly TrayPick[],
  address: ExplainerAddress,
  accepted: ExplainerBuild | null,
  onBuilt: () => void,
): void {
  for (const pick of picks) trayStore.getState().remove(pick.id);
  trayStore.getState().setText('');
  clearFirstRequest(address);
  if (accepted?.state !== 'running') onBuilt();
}

/** His Send: one build, one request id, and his request kept when it answers with a failure. */
function useRequestSend(
  address: ExplainerAddress,
  props: Pick<ExplanationRequestProps, 'binding' | 'comparison' | 'history' | 'onBuilt'>,
): RequestSend {
  const [sent, setSent] = useState<ExplainerBuild | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const inFlight = useRef(false);
  const requestIdRef = useRef<string | null>(null);
  const { onBuilt } = props;

  const send = useCallback(() => {
    if (inFlight.current) return;
    inFlight.current = true;
    setSending(true);
    setRefusal(null);
    const picksNow = trayStore.getState().picks;
    void (async () => {
      const wired = await wirePicks(picksNow, trayImageStore);
      const requestId = (requestIdRef.current ??= newExplainerId('req'));
      const outcome =
        typeof wired === 'string'
          ? { kind: 'refused' as const, reason: 'the request', detail: wired }
          : await requestBuild(address, requestId, wired, props);
      inFlight.current = false;
      setSending(false);
      if (outcome.kind === 'refused') {
        setRefusal(`${outcome.reason}: ${outcome.detail}`);
        return;
      }
      setSent(outcome.build);
      if (outcome.build?.state === 'failed') return; // The failure names itself; his request stays.
      settleSent(picksNow, address, outcome.build, onBuilt);
      requestIdRef.current = null;
    })();
  }, [address, props, onBuilt]);

  const resetRequestId = useCallback(() => {
    requestIdRef.current = null;
  }, []);

  return { sent, setSent, refusal, setRefusal, sending, send: () => send(), resetRequestId };
}

/** While the build runs, the server's own record is read at the dashboard's cadence. */
function useBuildRead(
  address: ExplainerAddress,
  build: ExplainerBuild | null,
  setSent: (build: ExplainerBuild | null) => void,
  setRefusal: (detail: string) => void,
  onBuilt: () => void,
): void {
  const running = build?.state === 'running';
  const live = useRef(true);
  const onBuiltRef = useRef(onBuilt);
  onBuiltRef.current = onBuilt;
  const sentRef = useRef(setSent);
  sentRef.current = setSent;
  const refusalRef = useRef(setRefusal);
  refusalRef.current = setRefusal;
  const addressRef = useRef(address);
  addressRef.current = address;

  useEffect(() => {
    if (!running) return undefined;
    live.current = true;
    const timer = setInterval(() => {
      void readExplainerState(addressRef.current)
        .then((state) => {
          if (!live.current) return;
          const next = state.build ?? null;
          if (!next) return;
          sentRef.current(next);
          if (next.state !== 'running') onBuiltRef.current();
        })
        .catch((error: unknown) => {
          if (live.current) refusalRef.current(error instanceof Error ? error.message : String(error));
        });
    }, EXPLANATION_BUILD_POLL_MS);
    return () => {
      live.current = false;
      clearInterval(timer);
    };
  }, [running]);
}

/** The tray's picks as the form's references. */
function useRequestReferences(): FormReference[] {
  const picks = useTray((tray) => tray.picks);
  return useMemo(() => formReferences(picks, (pick) => pickKindWords(pick.input)), [picks]);
}

interface RequestStop {
  cancelling: boolean;
  /** The cancel's refusal, named; the build is then still the developer's to end. */
  problem: string | null;
  /** The cancel's own receipt in words, for a record the server has already cleared. */
  cancelled: string | null;
  stop: () => void;
  /** A new Send is a new attempt: the last cancel's sentence goes with it. */
  clear: () => void;
}

/** What the cancel's receipt says, in one sentence. It never claims more than the host reported. */
function cancelWords(cancel: ExplainerCancel): string {
  if (cancel.requestUncertain) {
    return 'The cancel reached the host, but it named no live turn of this request: that attempt stays uncertain.';
  }
  if (cancel.turnStopped) return 'The build was cancelled: the host stopped its turn.';
  return 'The build was cancelled: the host reported no live turn to stop.';
}

/**
 * His Cancel while the build runs (MIK-R211 v2 rules 4 and 5): the running build's own request id
 * — which `parseExplainerBuild` keeps in `attempt` — goes to `…/cancel` exactly once. The attempt is
 * then over: this form's own record is dropped, the next Send is a new request, and the view re-reads
 * `/state` through the `onBuilt` it already has. Nothing here starts a poll of its own, and nothing
 * invents an ended build: whatever the server reports is what the box and this form show.
 */
function useRequestStop(
  address: ExplainerAddress,
  build: ExplainerBuild | null,
  setSent: (build: ExplainerBuild | null) => void,
  onBuilt: () => void,
  resetRequestId: () => void,
): RequestStop {
  const [cancelling, setCancelling] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState<string | null>(null);
  const inFlight = useRef(false);
  const running = build !== null && isRunningBuild(build);
  const requestId = build?.attempt ?? '';

  const stop = useCallback(() => {
    if (inFlight.current || !running || !requestId) return;
    inFlight.current = true;
    setCancelling(true);
    setProblem(null);
    setCancelled(null);
    void (async () => {
      try {
        const receipt = await cancelExplainerBuild(address, requestId);
        resetRequestId();
        setSent(null);
        setCancelled(cancelWords(receipt));
        onBuilt();
      } catch (error) {
        const named = refusalInfo(error);
        setProblem(named ? `${named.reason}: ${named.detail}` : String(error));
      } finally {
        inFlight.current = false;
        setCancelling(false);
      }
    })();
  }, [address, running, requestId, setSent, onBuilt, resetRequestId]);

  return { cancelling, problem, cancelled, stop, clear: () => setCancelled(null) };
}

/**
 * The request is with the explainer: the one press that ends a stuck attempt, drawn where the form
 * waits (MIK-R211 v2 rules 4 and 5). Send is not here — a running build is not a second request.
 */
function RequestWaiting({
  cancelling,
  problem,
  onCancel,
}: {
  cancelling: boolean;
  problem: string | null;
  onCancel: () => void;
}) {
  return (
    <>
      <div className={actions}>
        <span className={grow} />
        <button
          type="button"
          className={quiet}
          disabled={cancelling}
          data-testid="explanation-request-cancel"
          onClick={onCancel}
        >
          {cancelling ? 'Cancelling…' : 'Cancel the build'}
        </button>
      </div>
      {problem ? (
        <p className={failure} role="alert" data-testid="explanation-request-cancel-failure">
          {`The build could not be cancelled: ${problem}`}
        </p>
      ) : null}
    </>
  );
}

/** The binding as the form's words take it: `#8`, or `planned · no changes`. */
function bindingViewOf(binding: ExplainerBinding | null) {
  if (!binding) return null;
  const planned = binding.owner === 'planned';
  return {
    kind: binding.owner,
    comparison: binding.number,
    label: binding.label ?? (planned ? 'planned · no changes' : `#${binding.number}`),
  };
}

/** What the panel draws, from his Send's answer or from the build the view's own read holds. */
interface RequestView {
  build: ExplainerBuild | null;
  stopped: { step: string; reason: string } | null;
  title: string;
  phase: string;
}

function requestViewOf(sent: ExplainerBuild | null, fromRead: ExplainerBuild | null): RequestView {
  const build = sent ?? fromRead;
  if (!build) {
    return { build: null, stopped: null, title: 'Ask the explainer to read this change set', phase: 'form' };
  }
  return { build, stopped: buildFailure(build), title: 'Building the explainer', phase: build.state };
}

export function ExplanationRequest({
  target,
  task,
  binding = null,
  comparison,
  history,
  build: fromRead = null,
  onBuilt,
  onCancel,
}: ExplanationRequestProps) {
  const { repository, master, leaf } = target;
  const address = useMemo<ExplainerAddress>(
    () => ({ repository, master, leaf }),
    [repository, master, leaf],
  );
  const message = useTray((tray) => tray.text);
  const references = useRequestReferences();
  const { sent, setSent, refusal, setRefusal, sending, send, resetRequestId } = useRequestSend(address, {
    binding,
    comparison,
    history,
    onBuilt,
  });
  const view = requestViewOf(sent, fromRead);
  useBuildRead(address, view.build, setSent, setRefusal, onBuilt);
  const stop = useRequestStop(address, view.build, setSent, onBuilt, resetRequestId);
  const sendable = !sending && canSend(references, message);

  return (
    <section className={panel} data-testid="explanation-request-form" data-phase={view.phase}>
      <h3 className={heading}>{view.title}</h3>
      {view.build ? (
        <BuildProgress
          build={view.build}
          request={message}
          titles={references.map((ref) => ref.title)}
          onCancel={stop.stop}
          cancelling={stop.cancelling}
        />
      ) : null}
      <RequestProblems stopped={view.stopped} refusal={refusal} />
      {stop.cancelled ? (
        <p className={failure} role="status" data-testid="explanation-request-cancelled">
          {stop.cancelled}
        </p>
      ) : null}
      {view.build !== null && isRunningBuild(view.build) ? (
        <RequestWaiting
          cancelling={stop.cancelling}
          problem={stop.problem}
          onCancel={stop.stop}
        />
      ) : (
        <RequestFields
          task={task}
          binding={binding}
          references={references}
          message={message}
          sendable={sendable}
          sending={sending}
          onCancel={onCancel}
          onSend={() => {
            stop.clear();
            send();
          }}
        />
      )}
    </section>
  );
}
