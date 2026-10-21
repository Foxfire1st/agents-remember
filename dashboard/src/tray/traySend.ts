// Send (MIK-R188 rules 8–10): one message per target, one POST per target. The server composes the
// text in rule 8's order, renders each reference's written form, files the message and delivers or
// holds it; the tray composes the body and applies the answers.
//
// - Delivered and held targets count as sent; a held one is shown as held and never sent again.
// - Refused targets keep the picks and the text and become the tray's targets.
// - A failure to reach the server is a refusal of that target: the tray stays as it was.
// - A message no repository can be named for goes without one: the server files it under its sole
//   repository, or refuses it by name (`unknown-repository`).
import type { StoreApi } from 'zustand/vanilla';

import { seatOf } from './traySeats';
import { shownStore } from './shownChat';
import { refuseOnSend } from './trayModel';
import { base64Of, blobBytes, type TrayImageStore } from './trayImages';
import type { TrayBounds } from './trayPreferences';
import type { TrayState, TrayTargetResult } from './trayStore';
import type { TrayDelivery, TrayMessageBody, TrayPick, TrayReference, TrayWirePick } from './types';

export interface SendDeps {
  fetcher: typeof fetch;
  images: TrayImageStore;
  bounds: TrayBounds;
  newId: () => string;
  now: () => Date;
  // The page's repository: a target without a task files its message under it.
  pageRepository: () => string | null;
  // The repository of a target's task, when the seat list names one.
  targetRepository: (agentId: string) => string | null;
}

export const messagePath = (agentId: string): string => `/api/role-chats/${encodeURIComponent(agentId)}/message`;

function referenceRepository(reference: TrayReference): string {
  if ('repository' in reference) return reference.repository;
  if (reference.kind === 'task') return reference.reference.repository;
  return reference.comparison.repository;
}

// The repository a message is filed under: the page's, else the first reference's, else the
// target's task's. None: the body names none and the server decides.
export function messageRepository(picks: readonly TrayPick[], agentId: string, deps: Pick<SendDeps, 'pageRepository' | 'targetRepository'>): string | null {
  const page = deps.pageRepository();
  if (page) return page;
  for (const pick of picks) if (pick.input.kind === 'reference') return referenceRepository(pick.input.reference);
  return deps.targetRepository(agentId);
}

async function wirePick(pick: TrayPick, images: TrayImageStore): Promise<TrayWirePick | string> {
  const base = { id: pick.id, title: pick.input.title, note: pick.note };
  const input = pick.input;
  if (input.kind === 'reference') return { ...base, kind: 'reference', reference: input.reference };
  if (input.kind === 'text') return { ...base, kind: 'text', text: input.text, provenance: input.provenance };
  const blob = await images.get(input.sha256);
  if (!blob)
    return `“${input.title}” cannot be sent: its bytes were not kept by this browser. Remove it and paste it again.`;
  return { ...base, kind: 'image', image: { mimeType: input.mimeType, data: base64Of(await blobBytes(blob)) } };
}

// The picks in their wire form, or the refusal that names the pick that cannot go.
export async function wirePicks(picks: readonly TrayPick[], images: TrayImageStore): Promise<TrayWirePick[] | string> {
  const wired: TrayWirePick[] = [];
  for (const pick of picks) {
    const wire = await wirePick(pick, images);
    if (typeof wire === 'string') return wire;
    wired.push(wire);
  }
  return wired;
}

const DELIVERY_STATES = new Set(['delivered', 'held', 'refused']);
const textOrNull = (value: unknown): string | null => (typeof value === 'string' ? value : null);

export function parseDelivery(answer: unknown, messageId: string): TrayDelivery | null {
  if (typeof answer !== 'object' || answer === null) return null;
  const value = answer as Record<string, unknown>;
  if (typeof value.state !== 'string' || !DELIVERY_STATES.has(value.state)) return null;
  return {
    state: value.state as TrayDelivery['state'],
    messageId: textOrNull(value.messageId) ?? messageId,
    messageFile: textOrNull(value.messageFile),
    messageFileSha256: textOrNull(value.messageFileSha256),
    ...(typeof value.reason === 'string' ? { reason: value.reason } : {}),
    ...(typeof value.detail === 'string' ? { detail: value.detail } : {}),
  };
}

// The POST body: `repository` is left out when none can be named.
type MessageBody = Omit<TrayMessageBody, 'repository'> & { repository?: string };

const refusedHere = (messageId: string, reason: string, detail: string): TrayDelivery => ({
  state: 'refused',
  messageId,
  messageFile: null,
  messageFileSha256: null,
  reason,
  detail,
});

const errorPoint = (point: unknown): string | null => {
  if (typeof point !== 'object' || point === null) return null;
  const { loc, msg } = point as { loc?: unknown; msg?: unknown };
  if (typeof msg !== 'string') return null;
  const path = Array.isArray(loc) ? loc.filter((part, index) => !(index === 0 && part === 'body')).join('.') : '';
  return path ? `${path}: ${msg}` : msg;
};

// What a server's error body says: a text `detail`, or the points of a validation error.
export function serverDetail(answer: unknown): string | null {
  if (typeof answer !== 'object' || answer === null) return null;
  const detail = (answer as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail.trim() || null;
  if (!Array.isArray(detail)) return null;
  const points = detail.map(errorPoint).filter((point): point is string => point !== null);
  return points.length ? points.join('; ') : null;
}

// An answer without a delivery body is refused by its status, with the server's detail text.
function undelivered(messageId: string, status: number, answer: unknown): TrayDelivery {
  const said = serverDetail(answer);
  const detail = said
    ? `The dashboard server answered ${status}: ${said}${/[.!?]$/.test(said) ? '' : '.'}`
    : `The dashboard server answered ${status} without a delivery answer.`;
  return refusedHere(messageId, `http-${status}`, detail);
}

export async function postMessage(fetcher: typeof fetch, agentId: string, body: MessageBody): Promise<TrayDelivery> {
  try {
    const response = await fetcher(messagePath(agentId), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const answer: unknown = await response.json().catch(() => null);
    return parseDelivery(answer, body.messageId) ?? undelivered(body.messageId, response.status, answer);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return refusedHere(body.messageId, 'unreachable', `The dashboard server could not be reached: ${detail}`);
  }
}

async function sendOne(state: TrayState, picks: TrayWirePick[], agentId: string, deps: SendDeps): Promise<TrayTargetResult> {
  const messageId = deps.newId();
  const repository = messageRepository(state.picks, agentId, deps);
  const body: MessageBody = { messageId, ...(repository ? { repository } : {}), text: state.text, picks };
  return { agentId, delivery: await postMessage(deps.fetcher, agentId, body) };
}

// Sends the tray to every target. Answers false when nothing was sent (no target, a refusal by
// name, or a Send already running).
export async function sendTray(store: StoreApi<TrayState>, deps: SendDeps): Promise<boolean> {
  const state = store.getState();
  if (state.sending || state.targets.length === 0) return false;
  if (state.picks.length === 0 && state.text.trim() === '') return false;
  const refusal = refuseOnSend(state.picks, state.text, deps.bounds);
  if (refusal) {
    store.setState({ refusal });
    return false;
  }
  store.setState({ sending: true, refusal: null });
  const picks = await wirePicks(state.picks, deps.images);
  if (typeof picks === 'string') {
    store.setState({ sending: false, refusal: picks });
    return false;
  }
  const results = await Promise.all(state.targets.map((agentId) => sendOne(state, picks, agentId, deps)));
  store.getState().settle({
    at: deps.now().toISOString(),
    results,
    pickIds: state.picks.map((pick) => pick.id),
    text: state.text,
  });
  return true;
}

export function defaultSendDeps(images: TrayImageStore, bounds: TrayBounds): SendDeps {
  return {
    fetcher: (input, init) => fetch(input, init),
    images,
    bounds,
    newId: () => crypto.randomUUID(),
    now: () => new Date(),
    pageRepository: () => shownStore.getState().repository,
    targetRepository: (agentId) => seatOf(agentId)?.task?.repository ?? null,
  };
}
