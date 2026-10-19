/**
 * The page panel's state and effects, kept out of the markup (MIK-R105 rules 2, 8).
 *
 * Everything mounted here is keyed by the scope it serves (L104-B10): the review target, the frame,
 * the ready revision, its entrypoint and the comparison the channel is bound to. A changed scope
 * hides the old read at once, ignores an answer that arrives for it later, revokes the old channel
 * before a successor is opened, and revokes -- rather than adopts -- a channel the server allocated
 * after the panel stopped wanting it. The four probes in `CanvasPanel.test.tsx` are the control for
 * that behaviour: target replacement, revision replacement, late allocation after disposal, and a
 * late prior-scope answer.
 *
 * Three concerns, one hook each:
 *   * `useCanvasFrame` reads saved state, re-reads after an action and polls live host progress;
 *     these reads never start a model or resume a session;
 *   * `useCanvasChannel` opens one bound channel for the ready revision (optionally re-opening the
 *     saved page after a navigation inside the frame) and revokes it on replacement or disposal;
 *   * `useCanvasMessages` forwards the frame's own messages, accepting them only from its window and
 *     only while the channel that received them is still the channel on screen.
 */

import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

import {
  CanvasRefusal,
  type CanvasComparison,
  type CanvasFrameState,
  type CanvasSession,
  type CanvasTarget,
  type EvidenceDestination,
  attemptOperation,
  generate,
  openSession,
  readFrameState,
  revokeSession,
  sendFrameMessage,
} from './canvasApi';

import { savedComparison, savedEntrypoint } from './canvasModel';

export type { CanvasTarget } from './canvasApi';

export type CanvasStage = 'reading' | 'idle' | 'starting';

function reason(error: unknown): string {
  return error instanceof CanvasRefusal ? `${error.reason}: ${error.message}` : String(error);
}

function comparisonOf(
  reference: string | undefined,
  number: number | undefined,
  history: string | undefined,
): CanvasComparison | undefined {
  return reference === undefined ? undefined : { reference, number, history };
}

/** The exact scope a read or a channel serves, as one comparable value. */
function scopeKey(target: CanvasTarget): string {
  return [
    target.repository,
    target.master,
    target.leaf,
    target.frame,
    target.comparison?.reference ?? '',
    target.comparison?.number ?? '',
    target.comparison?.history ?? '',
  ].join('\u001f');
}

interface FrameRead {
  key: string;
  state: CanvasFrameState | null;
  refusal: string | null;
}

/** The answer for the scope on screen, or nothing: an answer for another scope is not shown. */
function shownRead(answer: FrameRead | null, key: string): FrameRead | null {
  return answer?.key === key ? answer : null;
}

function stageOf(stage: { key: string; value: CanvasStage }, key: string): CanvasStage {
  return stage.key === key ? stage.value : 'reading';
}

function displayedFrame(shown: FrameRead | null) {
  return { state: shown?.state ?? null, refusal: shown?.refusal ?? null };
}

export function useCanvasFrame(target: CanvasTarget) {
  const { repository, master, leaf, frame } = target;
  const reference = target.comparison?.reference;
  const number = target.comparison?.number;
  const history = target.comparison?.history;
  const key = scopeKey(target);
  const [answer, setAnswer] = useState<FrameRead | null>(null);
  const [stage, setStageState] = useState<{ key: string; value: CanvasStage }>({
    key,
    value: 'reading',
  });
  // The scope the panel currently shows, and a counter that makes every superseded read late.
  const latest = useRef(key);
  const sequence = useRef(0);

  const read = useCallback(async () => {
    const comparison = comparisonOf(reference, number, history);
    const asking = scopeKey({ repository, master, leaf, frame, comparison });
    const token = (sequence.current += 1);
    try {
      const state = await readFrameState({ repository, master, leaf, frame, comparison });
      if (token !== sequence.current || asking !== latest.current) return;
      setAnswer({ key: asking, state, refusal: null });
    } catch (error) {
      if (token !== sequence.current || asking !== latest.current) return;
      setAnswer({ key: asking, state: null, refusal: reason(error) });
    }
  }, [repository, master, leaf, frame, reference, number, history]);

  // Opening the reviewer shows what is saved; the first read is the only one on mount, and a new
  // scope's first read replaces the old scope's answer rather than merging with it.
  useEffect(() => {
    latest.current = key;
    setStageState({ key, value: 'reading' });
    void read().finally(() => {
      if (latest.current === key) setStageState({ key, value: 'idle' });
    });
    return () => {
      if (latest.current === key) sequence.current += 1;
    };
  }, [key, read]);

  const setRefusal = useCallback((value: string | null) => {
    const asking = latest.current;
    setAnswer((previous) =>
      previous?.key === asking
        ? { ...previous, refusal: value }
        : { key: asking, state: null, refusal: value },
    );
  }, []);
  const setStage = useCallback(
    (value: CanvasStage) => setStageState({ key: latest.current, value }),
    [],
  );

  // A read for another scope is never shown, even for the render between a prop change and its
  // effect: the answer carries the scope it was asked for.
  const shown = shownRead(answer, key);
  const liveId = shown?.state?.presenter?.live?.attempt_id;
  useEffect(() => {
    if (!liveId) return;
    const timer = window.setTimeout(() => {
      void read();
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [answer, liveId, read]);
  return {
    ...displayedFrame(shown),
    stage: stageOf(stage, key),
    setStage,
    setRefusal,
    read,
  };
}

export interface CanvasChannel {
  session: CanvasSession | null;
  /** Revoke the channel this panel owns, and stop showing it. */
  dispose: (how: string) => void;
  /** Dispose and open the saved page's channel again (the frame navigated inside itself). */
  reopen: () => void;
}

function channelKey(
  target: CanvasTarget,
  revision: number,
  entrypoint: string | null,
  comparison: string | null,
): string {
  return [scopeKey(target), revision, entrypoint ?? '', comparison ?? ''].join('\u001f');
}

/** Whether one allocated channel serves exactly the revision, entrypoint and comparison asked for. */
function exactChannel(
  opened: CanvasSession,
  wanted: { revision: number; entrypoint: string | null; comparison: string | null },
): boolean {
  if (opened.revision !== wanted.revision) return false;
  if (wanted.entrypoint !== null && opened.entrypoint !== wanted.entrypoint) return false;
  if (wanted.comparison !== null && opened.comparison !== wanted.comparison) return false;
  return true;
}

/** The identity one channel serves: its target, the ready revision, its entrypoint and comparison. */
function channelIdentity(
  target: CanvasTarget,
  state: CanvasFrameState | null,
  generation: number,
): {
  identity: string | null;
  revision: number | null;
  entrypoint: string | null;
  comparison: string | null;
} {
  const revision = state?.opening?.state === 'ready' ? state.opening.revision : null;
  // The comparison and entrypoint come from the host's own saved-first answer, in whichever of its
  // spellings that answer uses; both are part of the channel's identity.
  const entrypoint = savedEntrypoint(state);
  const comparison = savedComparison(state);
  const key =
    revision === null
      ? null
      : `${channelKey(target, revision, entrypoint, comparison)}\u001f${generation}`;
  return { identity: key, revision, entrypoint, comparison };
}

export function useCanvasChannel(
  target: CanvasTarget,
  state: CanvasFrameState | null,
): CanvasChannel {
  const { repository, master, leaf, frame } = target;
  const reference = target.comparison?.reference;
  const number = target.comparison?.number;
  const history = target.comparison?.history;
  // A navigation inside the frame disposes the channel and asks for the saved page again under the
  // same identity; the generation is what makes that a re-open rather than no effect.
  const [generation, setGeneration] = useState(0);
  const [channel, setChannel] = useState<{ key: string; session: CanvasSession } | null>(null);
  const owned = useRef<CanvasSession | null>(null);
  const { identity, revision, entrypoint, comparison } = channelIdentity(target, state, generation);

  const dispose = useCallback((how: string) => {
    const current = owned.current;
    owned.current = null;
    setChannel(null);
    if (current) void revokeSession(current.session, how).catch(() => undefined);
  }, []);

  const reopen = useCallback(() => {
    dispose('frame-navigated');
    setGeneration((value) => value + 1);
  }, [dispose]);

  // A channel that no longer serves the mounted identity is revoked before its successor exists.
  useEffect(() => {
    if (channel && channel.key !== identity) dispose('scope-changed');
  }, [channel, identity, dispose]);

  useEffect(() => {
    if (identity === null || revision === null) return undefined;
    let live = true;
    const asked = { revision, entrypoint, comparison };
    void openSession({
      repository,
      master,
      leaf,
      frame,
      revision,
      entrypoint,
      comparison: comparisonOf(reference, number, history),
    })
      .then((opened) => {
        if (live && exactChannel(opened, asked)) {
          owned.current = opened;
          setChannel({ key: identity, session: opened });
          return;
        }
        // Late: allocated after this panel stopped wanting it. It is handed back, never adopted.
        void revokeSession(opened.session, live ? 'stale-scope' : 'panel-disposed').catch(
          () => undefined,
        );
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [
    identity,
    repository,
    master,
    leaf,
    frame,
    revision,
    entrypoint,
    comparison,
    reference,
    number,
    history,
  ]);

  // Disposal revokes whatever this panel still owns, exactly once.
  useEffect(
    () => () => {
      const current = owned.current;
      owned.current = null;
      if (current) void revokeSession(current.session, 'panel-disposed').catch(() => undefined);
    },
    [],
  );

  return {
    session: channel && channel.key === identity ? channel.session : null,
    dispose,
    reopen,
  };
}

/** The channel alone, for a caller that only draws the page. */
export function useCanvasSession(
  target: CanvasTarget,
  state: CanvasFrameState | null,
): CanvasSession | null {
  return useCanvasChannel(target, state).session;
}

/**
 * Forwarding bounds (L104-B08). The server refuses a body past `MAX_REQUEST_BYTES` after it has
 * charged for it; the page's own queue must not be able to make that charge at all. A message past
 * the ceiling, or a burst past the in-flight count or byte budget, is dropped *before* serializing
 * a request, under a named reason, and counted in a fixed-size tally. Dropping never blocks the
 * panel: the frame's own detail stays on screen and the channel stays live.
 */
export const MAX_FORWARDED_MESSAGE_BYTES = 4096;
export const MAX_FORWARD_IN_FLIGHT = 4;
export const MAX_FORWARD_IN_FLIGHT_BYTES = 8 * 1024;

export interface FrameForwardTally {
  oversized: number;
  busy: number;
}

/** Bound traversal and strings before JSON serialization (R4 L104-B08).
 * The registered envelope has one flat payload. Neither a large string, an array, a deep object,
 * nor arbitrarily many properties can make the trusted page walk or serialize unbounded data.
 * Structured-clone work inside the browser precedes delivery and is outside this adapter's bound.
 */
function dataProperty(value: object, key: string): unknown {
  const property = Object.getOwnPropertyDescriptor(value, key);
  if (!property || !('value' in property)) throw new Error('malformed');
  return property.value;
}

function container(value: unknown, depth: number): asserts value is object {
  if (typeof value !== 'object' || Array.isArray(value) || depth > 1) throw new Error('malformed');
}

export function forwardedBody(message: unknown): string | null {
  let fields = 0;
  let characters = 0;
  function copy(value: unknown, depth: number): unknown {
    if (typeof value === 'string') {
      characters += value.length;
      if (characters > MAX_FORWARDED_MESSAGE_BYTES) throw new Error('oversized');
      return value;
    }
    if (value === null || typeof value === 'number' || typeof value === 'boolean') return value;
    container(value, depth);
    const bounded: Record<string, unknown> = Object.create(null);
    for (const key in value) {
      if (++fields > 16 || key.length > 64) throw new Error('oversized');
      bounded[key] = copy(dataProperty(value, key), depth + 1);
    }
    return bounded;
  }
  try {
    const body = JSON.stringify(copy(message, 0));
    return new TextEncoder().encode(body).length <= MAX_FORWARDED_MESSAGE_BYTES ? body : null;
  } catch {
    return null;
  }
}

/**
 * Admit one message for forwarding, or name why it is dropped.
 *
 * `bytes === null` means the message cannot be serialized at all, which is a refusal, not a crash.
 */
export function admitFrameMessage(
  bytes: number | null,
  inFlight: number,
  inFlightBytes: number,
): 'admit' | 'oversized' | 'busy' {
  if (bytes === null || bytes > MAX_FORWARDED_MESSAGE_BYTES) return 'oversized';
  if (inFlight >= MAX_FORWARD_IN_FLIGHT) return 'busy';
  if (inFlightBytes + bytes > MAX_FORWARD_IN_FLIGHT_BYTES) return 'busy';
  return 'admit';
}

export function useCanvasMessages(
  session: CanvasSession | null,
  frameRef: RefObject<HTMLIFrameElement | null>,
  onOpen: (destination: EvidenceDestination) => void,
): RefObject<FrameForwardTally> {
  const tally = useRef<FrameForwardTally>({ oversized: 0, busy: 0 });
  useEffect(() => {
    if (!session) return undefined;
    let live = true;
    let inFlight = 0;
    let inFlightBytes = 0;
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow) return;
      // A saturated channel inspects no untrusted body at all.
      if (inFlight >= MAX_FORWARD_IN_FLIGHT) {
        tally.current.busy += 1;
        return;
      }
      const payload = event.data as { type?: string } | null;
      if (!payload || typeof payload.type !== 'string') return;
      const body = forwardedBody(event.data);
      const bytes = body === null ? null : new TextEncoder().encode(body).length;
      const verdict = admitFrameMessage(bytes, inFlight, inFlightBytes);
      if (verdict !== 'admit') {
        tally.current[verdict] += 1;
        return;
      }
      const cost = bytes as number;
      inFlight += 1;
      inFlightBytes += cost;
      void sendFrameMessage(session.session, body as string)
        .then((answer) => {
          // An answer that travelled while the channel was replaced belongs to the old page.
          if (live) onOpen(answer);
        })
        .catch(() => undefined)
        .finally(() => {
          inFlight -= 1;
          inFlightBytes -= cost;
        });
    };
    window.addEventListener('message', onMessage);
    return () => {
      live = false;
      window.removeEventListener('message', onMessage);
    };
  }, [session, frameRef, onOpen]);
  return tally;
}

function useActivationRequest(identity: string) {
  // One request identity per activation and per comparison: a failed start is retried under the same
  // identity, a start that took, and a comparison that moved, each take their own.
  const request = useRef<{ key: string; value: string }>({ key: '', value: '' });
  const requestId = useCallback(() => {
    if (request.current.key !== identity) {
      request.current = { key: identity, value: crypto.randomUUID() };
    }
    return request.current.value;
  }, [identity]);

  return { request, requestId };
}

type AttemptAction = (
  attempt: string,
  operation: 'cancel' | 'say' | 'resume' | 'publish' | 'replace',
  body: Record<string, unknown>,
) => Promise<void>;

function useAttemptControls(act: AttemptAction, requestId: () => string) {
  const stop = useCallback(
    (attempt: string) => act(attempt, 'cancel', { reason: 'cancelled from the reviewer' }),
    [act],
  );
  const speak = useCallback(
    (attempt: string, text: string) => {
      if (!text.trim()) return Promise.resolve();
      return act(attempt, 'say', { text: text.trim() });
    },
    [act],
  );
  const resume = useCallback((attempt: string) => act(attempt, 'resume', {}), [act]);
  const publish = useCallback((attempt: string) => act(attempt, 'publish', {}), [act]);
  const replace = useCallback(
    (attempt: string) =>
      act(attempt, 'replace', {
        requestId: requestId(),
        reason: 'explicit replacement from the reviewer',
      }),
    [act, requestId],
  );
  return { stop, speak, resume, publish, replace };
}

export function useCanvasActions(
  target: CanvasTarget,
  focus: string,
  read: () => Promise<void>,
  setError: (value: string | null) => void,
) {
  const { repository, master, leaf, frame } = target;
  const reference = target.comparison?.reference;
  const number = target.comparison?.number;
  const history = target.comparison?.history;
  const identity = scopeKey(target);
  const { request, requestId } = useActivationRequest(identity);

  const start = useCallback(
    async (regenerate: boolean, baseRevision: number | null) => {
      try {
        await generate({
          repository,
          master,
          leaf,
          frame,
          comparison: comparisonOf(reference, number, history),
          requestId: requestId(),
          focus: focus.trim() || `explain ${reference ?? 'this comparison'}`,
          regenerate,
          baseRevision,
        });
        request.current = { key: '', value: '' };
        await read();
      } catch (error) {
        setError(reason(error));
      }
    },
    [
      repository,
      master,
      leaf,
      frame,
      reference,
      number,
      history,
      request,
      requestId,
      focus,
      read,
      setError,
    ],
  );
  const act = useCallback(
    async (
      attempt: string,
      operation: 'cancel' | 'say' | 'resume' | 'publish' | 'replace',
      body: Record<string, unknown>,
    ) => {
      try {
        await attemptOperation(
          { repository, master, leaf, frame, comparison: comparisonOf(reference, number, history) },
          attempt,
          operation,
          body,
        );
        if (operation === 'replace') request.current = { key: '', value: '' };
        await read();
      } catch (error) {
        setError(reason(error));
      }
    },
    [repository, master, leaf, frame, reference, number, history, request, read, setError],
  );
  const controls = useAttemptControls(act, requestId);
  return { start, ...controls };
}

/**
 * The comparison on screen, kept across a selection that is still reading (L104-B11).
 *
 * A reading surface answers `undefined` while it reads another subject and after a failed read. The
 * page above it belongs to the comparison, not to the selection: unmounting it on every selection
 * would drop the saved page's channel and lose the reviewer's place in it. A comparison that is
 * really replaced (a new number) replaces the held value too.
 */
export function useRetainedComparison(
  shown: CanvasComparison | undefined,
): CanvasComparison | undefined {
  const [held, setHeld] = useState<CanvasComparison | undefined>(undefined);
  useEffect(() => {
    if (shown && (held?.reference !== shown.reference || held?.number !== shown.number))
      setHeld(shown);
  }, [shown, held]);
  return shown ?? held;
}
