// The reviewer's page: one admitted comparison explained by a dedicated presenter (MIK-R105 rules
// 2, 8, 13; MIK-R104 rules 5, 7).
//
// WHAT THE PANEL OWNS. The explicit Generate control, the builder's progress and activity, the side
// conversation, the frame the presenter published, and the exact evidence the page asks to open. It
// owns no review state: opening the panel reads the saved page, showing it opens a bound session, and
// neither starts a model. Only the control starts authoring, and it always says so.
//
// WHAT IT DOES NOT DO. It does not generate on mount, it does not "resume" a closed
// session by reading it, and it does not turn the presenter's prose into a verdict. The deterministic
// detail surface stays where it is, below and untouched: this is a page above it, not a replacement.
//
// THE COMPARISON IS THE IDENTITY (L104-B11). Every read, channel and start names the comparison on
// screen -- its reference, its lineage number and the history it came from -- so a page built for one
// comparison is never presented, regenerated or navigated as if it explained another. The host route
// accepts only repository/master/leaf/frame today; the panel sends the comparison anyway and reports
// it, and it binds its own display so the truth does not depend on the host honouring it yet.
//
// THE FRAME IS UNTRUSTED. It is embedded with the sandbox the host's policy names (never
// `allow-same-origin`), its messages are only accepted from its own window, and every message it sends
// is forwarded to the host, which validates the binding before doing anything. Document departure,
// loss of the document-owned liveness port, or a second load revokes the old channel and opens the
// saved page again. The binding is delivered inside the admitted document, never to its persistent
// window handle after a load. A frame
// that navigated itself is an accepted channel under MIK-R104 v2 -- the panel never claims to have
// blocked the navigation.
//
// SCRIPTS are whatever the host reports (MIK-R104 v2 enables them). The panel states the host's own
// value and never derives a permission of its own: when the host reports scripts enabled the frame
// runs the page's scripts in that sandbox, and when it reports them disabled the same bytes are shown
// without them.

import { useCallback, useMemo, useRef, useState } from 'react';

import { css } from '../../../../styled-system/css';
import type {
  CanvasAttempt,
  CanvasComparison,
  CanvasConversation,
  CanvasFrameState,
  CanvasSession,
  CanvasTarget,
  EvidenceDestination,
} from './canvasApi';
import {
  useCanvasActions,
  useCanvasChannel,
  useCanvasFrame,
  useCanvasMessages,
  type CanvasStage,
} from './canvasHooks';
import { useDocumentPresence } from './canvasDocument';
import {
  AUTHORSHIP_NOTE,
  canGenerate,
  destinationLine,
  frameForComparison,
  generateLabel,
  panelPhase,
  phaseLine,
  progressLines,
  regeneration,
} from './canvasModel';

const shell = css({
  border: '1px solid token(colors.grid)',
  borderRadius: '3px',
  background: 'bgPanel',
  color: 'ink',
  marginBottom: '1rem',
  overflow: 'hidden',
});

const head = css({
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: '0.75rem',
  padding: '0.5rem 0.75rem',
  borderBottom: '1px solid token(colors.grid)',
});

const body = css({ padding: '0.75rem', display: 'grid', gap: '0.5rem' });

const action = css({
  fontFamily: 'mono',
  fontSize: '0.75rem',
  padding: '0.25rem 0.6rem',
  border: '1px solid token(colors.grid)',
  borderRadius: '3px',
  background: 'transparent',
  color: 'inherit',
  cursor: 'pointer',
  _disabled: { opacity: 0.5, cursor: 'default' },
});

const note = css({ fontFamily: 'mono', fontSize: '0.72rem', opacity: 0.8 });
const list = css({ margin: 0, paddingLeft: '1rem', fontFamily: 'mono', fontSize: '0.72rem' });
const frameStyle = css({ width: '100%', height: '32rem', border: '0', borderRadius: '3px' });
const entry = { marginLeft: '0.5rem', minWidth: '20rem' };

/** One evidence navigation this surface may make, with the identity it was verified against. */
export interface CanvasEvidenceNavigation {
  selector: { kind: 'invariant' | 'family'; id: string };
  /** The review target the opened reference belongs to. */
  target: { repository: string; master: string; leaf: string };
  /** The comparison the page is showing, in both identities this panel holds. */
  comparison: { reference: string; number?: number };
  /** The typed destination (tree, record revision, row, proof) as the host named it. */
  location: Record<string, unknown>;
}

export interface CanvasPanelProps {
  repository: string;
  master: string;
  leaf: string;
  /** The comparison on screen: its reference, its lineage number and the history it came from. */
  comparison?: CanvasComparison;
  /** Where the review can show an exact destination itself; absent means the panel names it. */
  onOpenEvidence?: (destination: CanvasEvidenceNavigation) => void;
}

export function CanvasPanel(props: CanvasPanelProps) {
  // No comparison on screen means no page and no read: a panel that is not about a comparison does
  // not talk to the host at all.
  if (!props.comparison) return null;
  return <CanvasPage {...props} comparison={props.comparison} />;
}

/** The page's two explicit gestures: one start, and one navigation inside the frame. */
function usePanelGestures(parts: {
  state: CanvasFrameState | null;
  phase: ReturnType<typeof panelPhase>;
  actions: ReturnType<typeof useCanvasActions>;
  session: CanvasSession | null;
  frameRef: React.RefObject<HTMLIFrameElement | null>;
  reopen: () => void;
  setStage: (value: CanvasStage) => void;
  setRefusal: (value: string | null) => void;
}) {
  const { state, phase, actions, session, frameRef, reopen, setStage, setRefusal } = parts;
  const [navigated, setNavigated] = useState(false);
  const loads = useRef<Record<string, number>>({});
  const onDeparture = useCallback(() => {
    setNavigated(true);
    reopen();
  }, [reopen]);
  const inspectDocument = useDocumentPresence(session, frameRef, onDeparture);
  const start = useCallback(() => {
    const retained = regeneration(state);
    const regenerate = phase === 'stale' || phase === 'ready';
    if (regenerate && retained.state === 'unavailable') {
      setRefusal(`retained-assets-unavailable: ${retained.detail}`);
      return;
    }
    setNavigated(false);
    setStage('starting');
    void actions
      .start(regenerate, retained.state === 'retained' ? retained.revision : null)
      .finally(() => setStage('idle'));
  }, [actions, phase, state, setRefusal, setStage]);
  // A second load of one channel is the frame navigating away from the admitted document: the channel
  // is revoked and the saved page is opened again, never the document it moved to.
  const onFrameLoad = useCallback(() => {
    const id = session?.session;
    if (!id) return false;
    const count = (loads.current[id] ?? 0) + 1;
    loads.current[id] = count;
    if (count < 2) {
      inspectDocument();
      return true;
    }
    setNavigated(true);
    reopen();
    return false;
  }, [session, reopen, inspectDocument]);
  return { start, onFrameLoad, navigated };
}

function conversationOf(state: CanvasFrameState | null): CanvasConversation[] {
  return state?.presenter?.attempts[0]?.conversation ?? [];
}

function CanvasPage({
  repository,
  master,
  leaf,
  comparison,
  onOpenEvidence,
}: CanvasPanelProps & { comparison: CanvasComparison }) {
  const frameId = frameForComparison();
  const target = useMemo<CanvasTarget>(
    () => ({ repository, master, leaf, frame: frameId, comparison }),
    [repository, master, leaf, frameId, comparison],
  );
  const [focus, setFocus] = useState('');
  const [message, setMessage] = useState('');
  const [destination, setDestination] = useState<string | null>(null);
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const { state, refusal, stage, setStage, setRefusal, read } = useCanvasFrame(target);
  const { session, reopen } = useCanvasChannel(target, state);
  const live = state?.presenter?.live ?? null;
  const latest = live ?? state?.presenter?.attempts[0] ?? null;
  const phase = panelPhase(state, live, comparison);
  const onOpen = useCallback(
    (answer: EvidenceDestination) => {
      const opened = destinationLine(answer, { repository, master, leaf, comparison });
      setDestination(opened.line);
      if (!opened.selector || !onOpenEvidence) return;
      onOpenEvidence({
        selector: opened.selector,
        target: { repository, master, leaf },
        comparison: { reference: comparison.reference, number: comparison.number },
        location: opened.location ?? {},
      });
    },
    [repository, master, leaf, comparison, onOpenEvidence],
  );
  useCanvasMessages(session, frameRef, onOpen);
  const actions = useCanvasActions(target, focus, read, setRefusal);
  const { start, onFrameLoad, navigated } = usePanelGestures({
    state,
    phase,
    actions,
    session,
    frameRef,
    reopen,
    setStage,
    setRefusal,
  });

  return (
    <section className={shell} data-testid="canvas-panel" data-phase={phase} data-stage={stage}>
      <PanelHead
        phase={phase}
        live={live}
        disabled={!canGenerate(state, phase) || stage === 'starting'}
        onStart={start}
        onStop={() => (live ? void actions.stop(live.attempt_id) : undefined)}
      />
      <div className={body}>
        <PanelNote
          state={state}
          phase={phase}
          comparison={comparison}
          refusal={refusal}
          destination={destination}
          navigated={navigated}
        />
        <PanelFocus state={state} focus={focus} onFocus={setFocus} live={live} />
        <PanelBuilder
          live={latest}
          message={message}
          onMessage={setMessage}
          onSend={() => (latest ? void actions.speak(latest.attempt_id, message) : undefined)}
        />
        <PanelAttemptControls latest={latest} live={live} actions={actions} />
        <PanelConversation turns={conversationOf(state)} />
        <PanelFrame session={session} state={state} frameRef={frameRef} onLoad={onFrameLoad} />
        <PanelAuthorship state={state} />
      </div>
    </section>
  );
}

function PanelAttemptControls({
  latest,
  live,
  actions,
}: {
  latest: CanvasAttempt | null;
  live: CanvasAttempt | null;
  actions: ReturnType<typeof useCanvasActions>;
}) {
  if (!latest) return null;
  return (
    <div className={note}>
      {latest.session_id && latest.state !== 'cancelled' ? (
        <button
          type="button"
          className={action}
          onClick={() => void actions.resume(latest.attempt_id)}
        >
          Resume recorded session
        </button>
      ) : null}
      {live ? (
        <button
          type="button"
          className={action}
          onClick={() => void actions.publish(latest.attempt_id)}
        >
          Publish checked draft
        </button>
      ) : null}
      <button
        type="button"
        className={action}
        onClick={() => void actions.replace(latest.attempt_id)}
      >
        Replace presenter
      </button>
    </div>
  );
}

function PanelHead(props: {
  phase: ReturnType<typeof panelPhase>;
  live: CanvasAttempt | null;
  disabled: boolean;
  onStart: () => void;
  onStop: () => void;
}) {
  return (
    <div className={head}>
      <strong className={note}>The page</strong>
      <div className={note} data-testid="canvas-phase">
        {props.phase}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        {props.live ? (
          <button type="button" className={action} onClick={props.onStop}>
            Cancel
          </button>
        ) : null}
        <button
          type="button"
          className={action}
          data-testid="canvas-generate"
          disabled={props.disabled}
          onClick={props.onStart}
        >
          {generateLabel(props.phase)}
        </button>
      </div>
    </div>
  );
}

function PanelNote(props: {
  state: CanvasFrameState | null;
  phase: ReturnType<typeof panelPhase>;
  comparison: CanvasComparison;
  refusal: string | null;
  destination: string | null;
  navigated: boolean;
}) {
  return (
    <>
      <p className={note} data-testid="canvas-line">
        {phaseLine(props.state, props.phase, props.comparison)}
      </p>
      {props.state?.launch?.state === 'refused' ? (
        <p className={note} data-testid="canvas-launch">
          No launch: {props.state.launch.detail}
        </p>
      ) : null}
      {props.navigated ? (
        <p className={note} data-testid="canvas-navigated">
          The frame loaded a new document, so its previous channel was revoked and the saved page is
          shown again.
        </p>
      ) : null}
      {props.refusal ? (
        <p className={note} data-testid="canvas-refusal">
          {props.refusal}
        </p>
      ) : null}
      {props.destination ? (
        <p className={note} data-testid="canvas-destination">
          {props.destination}
        </p>
      ) : null}
    </>
  );
}

function PanelFocus(props: {
  state: CanvasFrameState | null;
  focus: string;
  onFocus: (value: string) => void;
  live: CanvasAttempt | null;
}) {
  if (props.live) return null;
  return (
    <label className={note}>
      What should it explain?
      <input
        className={action}
        style={entry}
        data-testid="canvas-focus"
        value={props.focus}
        onChange={(event) => props.onFocus(event.target.value)}
        placeholder="the change to this comparison"
      />
    </label>
  );
}

function PanelBuilder(props: {
  live: CanvasAttempt | null;
  message: string;
  onMessage: (value: string) => void;
  onSend: () => void;
}) {
  if (!props.live) return null;
  return (
    <div data-testid="canvas-progress">
      <p className={note}>{`attempt ${props.live.attempt_id} · ${props.live.state}`}</p>
      {props.live.host_status ? (
        <p className={note} data-testid="canvas-host-status">
          {`Host: ${props.live.host_status.state}. ${props.live.host_status.detail ?? ''}`}
        </p>
      ) : null}
      <ul className={list}>
        {progressLines(props.live).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <label className={note}>
        Ask about this page
        <input
          className={action}
          style={entry}
          data-testid="canvas-say"
          value={props.message}
          onChange={(event) => props.onMessage(event.target.value)}
        />
      </label>
      <button type="button" className={action} onClick={props.onSend}>
        Send
      </button>
    </div>
  );
}

function PanelConversation({ turns }: { turns: CanvasConversation[] }) {
  if (!turns.length) return null;
  return (
    <ul className={list} data-testid="canvas-conversation">
      {turns.slice(-6).map((turn) => (
        <li key={`${turn.at}-${turn.role}`}>{`${turn.role}: ${turn.text}`}</li>
      ))}
    </ul>
  );
}

function PanelFrame(props: {
  session: CanvasSession | null;
  state: CanvasFrameState | null;
  frameRef: React.RefObject<HTMLIFrameElement | null>;
  onLoad: () => boolean;
}) {
  if (!props.session || props.state?.opening?.state !== 'ready') return null;
  return (
    <iframe
      key={props.session.session}
      ref={props.frameRef}
      className={frameStyle}
      data-testid="canvas-frame"
      title="the explained comparison"
      sandbox={props.session.sandbox}
      src={props.session.pageUrl}
      onLoad={props.onLoad}
    />
  );
}

function PanelAuthorship({ state }: { state: CanvasFrameState | null }) {
  if (state?.opening?.state !== 'ready') return null;
  // The host's own value, stated as the host's: the panel neither claims a script ran nor withholds
  // one the host enabled (MIK-R104 v2 enables them; the sandbox is still the host's).
  const scripts = state.policy?.scripts
    ? ' The host reports scripts enabled for this page; it runs in the sandboxed frame.'
    : ' The host reports scripts disabled for this page.';
  return (
    <p className={note} data-testid="canvas-authorship">
      {AUTHORSHIP_NOTE}
      {scripts}
    </p>
  );
}
