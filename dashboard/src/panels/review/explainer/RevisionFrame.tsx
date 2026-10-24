/**
 * One revision of the explanation page, in MIK-R104 v3's isolated host (MIK-R106 v2 rule 6).
 *
 * The frame is the host's: its sandbox comes from the session the host opened, its document from that
 * session's own `pageUrl`, and its messages are accepted only from its window. Document departure,
 * loss of the document-owned liveness port, or a second load revokes only this frame's channel and
 * reopens the saved revision without rebinding a replacement document or disturbing a sibling frame.
 *
 * Opening a frame is a channel, not a model turn: this component has no start, no generate and no
 * resume (MIK-R106 v2 rules 8 and 10).
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { css } from '../../../../styled-system/css';
import { trayStore } from '../../../tray/trayStore';
import type { TrayPickInput } from '../../../tray/types';
import {
  CanvasRefusal,
  sendFrameMessage,
  type CanvasSession,
  type EvidenceDestination,
} from '../canvas/canvasApi';
import { useDocumentPresence } from '../canvas/canvasDocument';
import {
  admitFrameMessage,
  forwardedBody,
  MAX_FORWARD_IN_FLIGHT,
} from '../canvas/canvasHooks';
import {
  closeRevisionPage,
  openRevisionPage,
  refusalInfo,
  type ExplainerBinding,
  type ExplainerTarget,
} from './explainerApi';

const shell = css({
  display: 'grid',
  gap: '0.35rem',
  minWidth: 0,
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'bgPanel',
  overflow: 'hidden',
});

const label = css({
  display: 'flex',
  gap: '0.5rem',
  alignItems: 'baseline',
  padding: '0.3rem 0.5rem',
  borderBottomWidth: '1px',
  borderBottomStyle: 'solid',
  borderBottomColor: 'grid',
  fontFamily: 'mono',
  fontSize: '0.72rem',
  color: 'muted',
});

const page = css({ width: '100%', height: '34rem', border: '0', display: 'block' });
const note = css({ padding: '0.5rem', fontFamily: 'mono', fontSize: '0.72rem', color: 'muted' });

const MAX_STALE_PARTS = 32;
const MAX_PART_ID_LENGTH = 64;
const PART_ID_PATTERN = /^[a-z0-9][a-z0-9-]*$/;
const MAX_SELECTIONS = 12;
const MAX_SELECTION_QUESTION = 120;
const MAX_SELECTION_CHOICE = 80;
const MAX_QUESTION_LENGTH = 1024;
const OFFER_COOLDOWN_MS = 1000;

const PROTOCOL_REFUSALS = new Set([
  'malformed',
  'oversized',
  'wrong-version',
  'unknown-op',
  'foreign-session',
  'stale-revision',
  'foreign-comparison',
  'replayed',
  'rate-limited',
  'exhausted',
  'revoked',
  'wrong-window',
]);

export interface StalePartsEnvelope {
  envelope: {
    schema: 'ar-canvas-message/v1';
    protocolVersion: 1;
    type: 'stale-parts';
    requestId: string;
    session: string;
    revision: number;
    comparison: string;
    payload: { parts: string[]; total: number };
  };
  dropped: string[];
  truncated: number;
}

function isValidPartId(rawId: unknown, known: ReadonlySet<string> | null): rawId is string {
  if (typeof rawId !== 'string' || rawId.length === 0 || rawId.length > MAX_PART_ID_LENGTH) {
    return false;
  }
  if (rawId === 'page' || !PART_ID_PATTERN.test(rawId)) return false;
  return known === null || known.has(rawId);
}

export function buildStalePartsEnvelope(
  session: CanvasSession,
  stalePartIds: readonly string[],
  knownPartIds?: readonly string[],
  staleTotal?: number,
): StalePartsEnvelope {
  const known = knownPartIds && knownPartIds.length > 0 ? new Set(knownPartIds) : null;
  const valid: string[] = [];
  const dropped: string[] = [];
  for (const rawId of stalePartIds) {
    if (!isValidPartId(rawId, known)) {
      dropped.push(String(rawId));
      continue;
    }
    if (!valid.includes(rawId)) valid.push(rawId);
  }
  const sent = valid.slice(0, MAX_STALE_PARTS);
  const truncated = Math.max(0, valid.length - sent.length);
  for (const extra of valid.slice(MAX_STALE_PARTS)) dropped.push(extra);
  const total = Math.max(staleTotal ?? valid.length, sent.length);
  return {
    envelope: {
      schema: 'ar-canvas-message/v1',
      protocolVersion: 1,
      type: 'stale-parts',
      requestId: `host-${crypto.randomUUID().replace(/-/g, '').slice(0, 16)}`,
      session: session.session,
      revision: session.revision,
      comparison: session.comparison,
      payload: { parts: sent, total },
    },
    dropped,
    truncated,
  };
}

interface ValidatedSelection {
  question: string;
  choice: string;
}

function unwrapPayloadObject(data: Record<string, unknown>): Record<string, unknown> {
  if (typeof data.payload === 'object' && data.payload !== null && !Array.isArray(data.payload)) {
    return data.payload as Record<string, unknown>;
  }
  return data;
}

function nonBlankString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}

function isSelectionObject(row: unknown): row is Record<string, unknown> {
  if (typeof row !== 'object' || row === null || Array.isArray(row)) return false;
  const keys = Object.keys(row);
  return keys.length === 2 && keys.includes('question') && keys.includes('choice');
}

function validateSelectionRow(row: unknown): ValidatedSelection | string {
  if (!isSelectionObject(row)) return 'malformed';
  const q = nonBlankString(row.question);
  const c = nonBlankString(row.choice);
  if (!q || !c) return 'malformed';
  if (q.length > MAX_SELECTION_QUESTION || c.length > MAX_SELECTION_CHOICE) return 'oversized';
  return { question: q, choice: c };
}

function validateSelectionsPayload(data: Record<string, unknown>): ValidatedSelection[] | string {
  const payload = unwrapPayloadObject(data);
  if (data.payload !== undefined && Object.keys(payload).some((k) => k !== 'selections')) {
    return 'malformed';
  }
  const rows = payload.selections;
  if (!Array.isArray(rows) || rows.length === 0) return 'malformed';
  if (rows.length > MAX_SELECTIONS) return 'oversized';
  const result: ValidatedSelection[] = [];
  for (const row of rows) {
    const checked = validateSelectionRow(row);
    if (typeof checked === 'string') return checked;
    result.push(checked);
  }
  return result;
}

function validateExplanationPayload(data: Record<string, unknown>): string | null {
  const payload = unwrapPayloadObject(data);
  if (data.payload !== undefined && Object.keys(payload).some((k) => k !== 'question')) {
    return null;
  }
  const q = payload.question;
  if (typeof q !== 'string' || !q.trim() || q.length > MAX_QUESTION_LENGTH) return null;
  return q;
}

function checkIdentityMismatch(
  data: Record<string, unknown>,
  session: CanvasSession,
): string | null {
  if (data.protocolVersion !== undefined && data.protocolVersion !== 1) return 'wrong-version';
  if (data.session !== undefined && data.session !== session.session) return 'foreign-session';
  if (data.revision !== undefined && data.revision !== session.revision) return 'stale-revision';
  if (data.comparison !== undefined && session.comparison && data.comparison !== session.comparison) {
    return 'foreign-comparison';
  }
  return null;
}

function validateEnvelopeBinding(
  data: Record<string, unknown>,
  session: CanvasSession,
  seenRequests: Set<string>,
): string | null {
  const mismatch = checkIdentityMismatch(data, session);
  if (mismatch) return mismatch;
  if (typeof data.requestId === 'string') {
    if (seenRequests.has(data.requestId)) return 'replayed';
    seenRequests.add(data.requestId);
  }
  return null;
}

function placeRevisionPickInTray(
  panelKey: string,
  title: string,
  text: string,
  explainerAgentId?: string | null,
): string | null {
  const state = trayStore.getState();
  const existing = state.picks.find(
    (pick) =>
      pick.input.kind === 'text' &&
      pick.input.provenance.source === 'panel' &&
      pick.input.provenance.panel === panelKey,
  );
  const input: TrayPickInput = {
    kind: 'text',
    title,
    text,
    provenance: { source: 'panel', panel: panelKey },
  };
  const ok = state.replace(existing?.id ?? null, input);
  if (!ok) return null;
  state.reveal();
  if (explainerAgentId && trayStore.getState().targets.length === 0) {
    trayStore.setState({ targets: [explainerAgentId], defaultDecided: true });
  }
  const added = trayStore
    .getState()
    .picks.find(
      (pick) =>
        pick.input.kind === 'text' &&
        pick.input.provenance.source === 'panel' &&
        pick.input.provenance.panel === panelKey,
    );
  return added?.id ?? null;
}

interface Channel {
  key: string;
  session: CanvasSession;
}

const keyOf = (
  target: ExplainerTarget,
  revision: number,
  slot: string,
  generation: number,
) => [target.repository, target.master, target.leaf, revision, slot, generation].join('\u001f');

function useRevisionChannel(
  target: ExplainerTarget,
  revision: number,
  binding?: ExplainerBinding | null,
  compareSlot?: 'primary' | 'left' | 'right' | 'compare' | null,
): {
  session: CanvasSession | null;
  problem: string | null;
  reopen: () => void;
} {
  const slot = compareSlot ?? 'primary';
  const [generation, setGeneration] = useState(0);
  const key = keyOf(target, revision, slot, generation);
  const [channel, setChannel] = useState<Channel | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const owned = useRef<CanvasSession | null>(null);
  const { repository, master, leaf } = target;

  const reopen = useCallback(() => {
    const current = owned.current;
    owned.current = null;
    setChannel(null);
    if (current) closeRevisionPage(current.session, 'frame-navigated');
    setGeneration((value) => value + 1);
  }, []);

  useEffect(() => {
    let live = true;
    setProblem(null);
    setChannel(null);
    void openRevisionPage({ repository, master, leaf }, revision, { binding, compareSlot })
      .then((opened) => {
        const openedRevision = opened.revision ?? revision;
        if (!live || openedRevision !== revision) {
          closeRevisionPage(opened.session, live ? 'stale-scope' : 'panel-disposed');
          return;
        }
        const normalized: CanvasSession = { ...opened, revision: openedRevision };
        owned.current = normalized;
        setChannel({ key, session: normalized });
      })
      .catch((error) => {
        if (!live) return;
        const named = refusalInfo(error);
        if (named) {
          setProblem(`${named.reason}: ${named.detail}`);
          return;
        }
        if (error instanceof CanvasRefusal) {
          setProblem(`${error.reason}: ${error.message}`);
          return;
        }
        setProblem(String(error));
      });
    return () => {
      live = false;
    };
  }, [key, revision, repository, master, leaf, binding, compareSlot]);

  useEffect(() => {
    if (!channel || channel.key === key) return;
    owned.current = null;
    closeRevisionPage(channel.session.session, 'scope-changed');
    setChannel(null);
  }, [channel, key]);

  useEffect(
    () => () => {
      const current = owned.current;
      owned.current = null;
      if (current) closeRevisionPage(current.session, 'panel-disposed');
    },
    [],
  );

  return { session: channel?.key === key ? channel.session : null, problem, reopen };
}

export interface RevisionFrameProps {
  target: ExplainerTarget;
  /** The retained revision this frame shows, exactly (MIK-R103 v2). */
  revision: number;
  /** The line above the frame: its binding, time and kind, as the selector lists them. */
  caption: string;
  /** The revision's own binding, passed when opening the session. */
  binding?: ExplainerBinding | null;
  /** Slot identifier so two compare frames maintain independent channels. */
  compareSlot?: 'primary' | 'left' | 'right' | 'compare' | null;
  /** Advisory stale part IDs to send to the frame via the `stale-parts` host message. */
  stalePartIds?: readonly string[];
  /** Admitted part IDs from the revision's part map. */
  knownPartIds?: readonly string[];
  /** Total count of stale parts when more are stale than listed. */
  staleTotal?: number;
  /** The leaf's explainer agent ID, used as default tray target for confirmed selections. */
  explainerAgentId?: string | null;
  /** Where the page's own evidence operation goes; the review page's selection owner decides. */
  onEvidence?: (destination: EvidenceDestination) => void;
  testId?: string;
}

function applyFrameOperation(
  op: string,
  data: Record<string, unknown>,
  target: ExplainerTarget,
  revision: number,
  explainerAgentId: string | null | undefined,
  lastOfferAt: { current: number },
  onReady: () => void,
): { ok: true; pickId: string | null } | { ok: false; refusal: string } {
  if (op === 'ready') {
    onReady();
    return { ok: true, pickId: null };
  }
  if (op === 'offer-selections') {
    const now = performance.now();
    if (now - lastOfferAt.current < OFFER_COOLDOWN_MS) {
      return { ok: false, refusal: 'rate-limited' };
    }
    const validated = validateSelectionsPayload(data);
    if (typeof validated === 'string') return { ok: false, refusal: validated };
    lastOfferAt.current = now;
    const summary = validated.map((row) => `${row.question}: ${row.choice}`).join(' · ');
    const fullText = validated.map((row) => `${row.question}: ${row.choice}`).join('\n');
    const panelKey = `explanation-selections:${target.repository}/${target.master}/${target.leaf}#r${revision}`;
    const pickId = placeRevisionPickInTray(
      panelKey,
      `Selections (revision ${revision}) · ${summary}`,
      fullText,
      explainerAgentId,
    );
    return { ok: true, pickId };
  }
  if (op === 'request-explanation') {
    const question = validateExplanationPayload(data);
    if (!question) return { ok: false, refusal: 'malformed' };
    const panelKey = `explanation-request:${target.repository}/${target.master}/${target.leaf}#r${revision}`;
    const pickId = placeRevisionPickInTray(
      panelKey,
      `Explanation request (revision ${revision}) · ${question}`,
      question,
      explainerAgentId,
    );
    return { ok: true, pickId };
  }
  if (op !== 'open-evidence' && op !== 'status') {
    return { ok: false, refusal: 'unknown-op' };
  }
  return { ok: true, pickId: null };
}

// The host's refusal, named only when its own answer says so: the reply is checked field by field
// before its reason is trusted, because only a protocol name may reach the page's refusal line.
function protocolRefusalOf(answer: unknown): string | null {
  if (typeof answer !== 'object' || answer === null) return null;
  if (!('state' in answer) || answer.state !== 'refused') return null;
  if (!('reason' in answer) || typeof answer.reason !== 'string') return null;
  return PROTOCOL_REFUSALS.has(answer.reason) ? answer.reason : null;
}

function forwardValidatedFrameMessage(
  sessionId: string,
  op: string,
  body: string,
  pickIdToRollback: string | null,
  live: { current: boolean },
  setLastRefusal: (refusal: string | null) => void,
  onEvidence: ((dest: EvidenceDestination) => void) | undefined,
  onDone: () => void,
): void {
  void sendFrameMessage(sessionId, body)
    .then((answer) => {
      if (!live.current) return;
      const refusal = protocolRefusalOf(answer);
      if (refusal) {
        setLastRefusal(refusal);
        if (pickIdToRollback) trayStore.getState().remove(pickIdToRollback);
        return;
      }
      if (op === 'open-evidence') onEvidence?.(answer);
    })
    .catch((error) => {
      if (!live.current) return;
      if (error instanceof CanvasRefusal && PROTOCOL_REFUSALS.has(error.reason)) {
        setLastRefusal(error.reason);
        if (pickIdToRollback) trayStore.getState().remove(pickIdToRollback);
      }
    })
    .finally(onDone);
}

function extractFrameEnvelope(
  event: MessageEvent,
  frameWindow: Window | null | undefined,
): Record<string, unknown> | null {
  if (event.source !== frameWindow || event.source === window) return null;
  const rawData = event.data;
  if (typeof rawData !== 'object' || rawData === null || Array.isArray(rawData)) return null;
  const data = rawData as Record<string, unknown>;
  if (data.schema === 'ar-canvas-document/v1' || typeof data.type !== 'string') return null;
  return data;
}

function useRevisionFrameProtocol(params: {
  session: CanvasSession | null;
  frameRef: React.RefObject<HTMLIFrameElement | null>;
  target: ExplainerTarget;
  revision: number;
  explainerAgentId: string | null | undefined;
  onEvidence: ((destination: EvidenceDestination) => void) | undefined;
  onReady: () => void;
  setLastRefusal: (refusal: string | null) => void;
}): void {
  const { session, frameRef, target, revision, explainerAgentId, onEvidence, onReady, setLastRefusal } =
    params;
  useLayoutEffect(() => {
    if (!session) return undefined;
    const live = { current: true };
    let inFlight = 0;
    let inFlightBytes = 0;
    const lastOfferAt = { current: -Infinity };
    const seenRequests = new Set<string>();

    const onMessage = (event: MessageEvent) => {
      const data = extractFrameEnvelope(event, frameRef.current?.contentWindow);
      if (!data || inFlight >= MAX_FORWARD_IN_FLIGHT) return;

      const body = forwardedBody(data);
      const bytes = body === null ? null : new TextEncoder().encode(body).length;
      const verdict = admitFrameMessage(bytes, inFlight, inFlightBytes);
      if (verdict !== 'admit') {
        setLastRefusal(verdict);
        return;
      }

      const bindingError = validateEnvelopeBinding(data, session, seenRequests);
      if (bindingError) {
        setLastRefusal(bindingError);
        return;
      }

      const applied = applyFrameOperation(
        data.type as string,
        data,
        target,
        revision,
        explainerAgentId,
        lastOfferAt,
        onReady,
      );
      if (!applied.ok) {
        setLastRefusal(applied.refusal);
        return;
      }

      const cost = bytes as number;
      inFlight += 1;
      inFlightBytes += cost;
      forwardValidatedFrameMessage(
        session.session,
        data.type as string,
        body as string,
        applied.pickId,
        live,
        setLastRefusal,
        onEvidence,
        () => {
          inFlight -= 1;
          inFlightBytes -= cost;
        },
      );
    };

    window.addEventListener('message', onMessage);
    return () => {
      live.current = false;
      window.removeEventListener('message', onMessage);
    };
  }, [session, frameRef, target, revision, explainerAgentId, onEvidence, onReady, setLastRefusal]);
}

function RevisionFrameBody({
  problem,
  session,
  frameRef,
  revision,
  compareSlot,
  onFrameLoad,
}: {
  problem: string | null;
  session: CanvasSession | null;
  frameRef: React.RefObject<HTMLIFrameElement | null>;
  revision: number;
  compareSlot?: RevisionFrameProps['compareSlot'];
  onFrameLoad: () => void;
}) {
  if (problem) {
    return (
      <p className={note} role="alert" data-testid="explanation-frame-problem">
        {`This revision cannot be shown: ${problem}`}
      </p>
    );
  }
  if (!session) {
    return (
      <p className={note} data-testid="explanation-frame-opening">
        Opening the saved page…
      </p>
    );
  }
  return (
    <iframe
      key={session.session}
      ref={frameRef}
      className={page}
      data-testid="explanation-frame"
      data-revision={revision}
      data-slot={compareSlot ?? 'primary'}
      title={`The explanation, revision ${revision}`}
      sandbox={session.sandbox}
      src={session.pageUrl}
      onLoad={onFrameLoad}
    />
  );
}

function useStalePartsMessenger(
  session: CanvasSession | null,
  frameRef: React.RefObject<HTMLIFrameElement | null>,
  stalePartIds: readonly string[] | undefined,
  knownPartIds: readonly string[] | undefined,
  staleTotal: number | undefined,
) {
  const [staleStats, setStaleStats] = useState({ dropped: 0, truncated: 0 });
  const frameReady = useRef(false);
  const sentNonEmptyStale = useRef(false);

  const sendStaleParts = useCallback(() => {
    if (!session || !frameRef.current?.contentWindow) return;
    const ids = stalePartIds ?? [];
    const built = buildStalePartsEnvelope(session, ids, knownPartIds, staleTotal);
    setStaleStats({ dropped: built.dropped.length, truncated: built.truncated });
    frameRef.current.contentWindow.postMessage(built.envelope, '*');
    sentNonEmptyStale.current = ids.length > 0;
  }, [session, frameRef, stalePartIds, knownPartIds, staleTotal]);

  const onReady = useCallback(() => {
    frameReady.current = true;
    sendStaleParts();
  }, [sendStaleParts]);

  const markNotReady = useCallback(() => {
    frameReady.current = false;
  }, []);

  useEffect(() => {
    if (!session || stalePartIds === undefined) return;
    if (stalePartIds.length > 0 || sentNonEmptyStale.current || frameReady.current) sendStaleParts();
  }, [session, stalePartIds, sendStaleParts]);

  return { staleStats, onReady, markNotReady };
}

export function RevisionFrame({
  target,
  revision,
  caption,
  binding,
  compareSlot,
  stalePartIds,
  knownPartIds,
  staleTotal,
  explainerAgentId,
  onEvidence,
  testId,
}: RevisionFrameProps) {
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const { session, problem, reopen } = useRevisionChannel(target, revision, binding, compareSlot);
  const [navigated, setNavigated] = useState(false);
  const [lastRefusal, setLastRefusal] = useState<string | null>(null);
  const loads = useRef<Record<string, number>>({});
  const { staleStats, onReady, markNotReady } = useStalePartsMessenger(
    session,
    frameRef,
    stalePartIds,
    knownPartIds,
    staleTotal,
  );

  const onDeparture = useCallback(() => {
    markNotReady();
    setNavigated(true);
    reopen();
  }, [markNotReady, reopen]);
  const inspectDocument = useDocumentPresence(session, frameRef, onDeparture);

  const onFrameLoad = useCallback(() => {
    const id = session?.session;
    if (!id) return;
    const count = (loads.current[id] ?? 0) + 1;
    loads.current[id] = count;
    if (count < 2) inspectDocument();
    else onDeparture();
  }, [session, inspectDocument, onDeparture]);

  useRevisionFrameProtocol({
    session,
    frameRef,
    target,
    revision,
    explainerAgentId,
    onEvidence,
    onReady,
    setLastRefusal,
  });

  return (
    <section
      className={shell}
      data-testid={testId ?? 'explanation-frame-slot'}
      data-revision={revision}
      data-slot={compareSlot ?? 'primary'}
      data-stale-dropped={staleStats.dropped}
      data-stale-truncated={staleStats.truncated}
      data-last-refusal={lastRefusal ?? undefined}
    >
      <p className={label} data-testid="explanation-frame-caption">{caption}</p>
      {navigated ? (
        <p className={note} data-testid="explanation-frame-navigated">
          The frame loaded a new document, so its previous channel was revoked and the saved page is shown again.
        </p>
      ) : null}
      <RevisionFrameBody
        problem={problem}
        session={session}
        frameRef={frameRef}
        revision={revision}
        compareSlot={compareSlot}
        onFrameLoad={onFrameLoad}
      />
    </section>
  );
}
