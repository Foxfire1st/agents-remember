// The tray's targets come from the host-wide list of live AR seats (MIK-R188 rule 7), read from the
// dashboard server by pages (`GET /api/role-chats?offset=<n>`). The tray also names a chat by it:
// "Manager · 260928-MIK" on a target, "from Manager · 14:05" on a passage. A listing that did not
// end (`complete: false`) is said so: a seat missing from it is unknown, not gone.
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

import { shownStore, type ShownChat } from './shownChat';
import { shortAgent } from './trayModel';
import type { TraySeat, TraySeatPage, TraySeatTask } from './types';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const isText = (value: unknown): value is string => typeof value === 'string';

function readTask(value: unknown): TraySeatTask | null | undefined {
  if (value === null) return null;
  if (!isRecord(value)) return undefined;
  const { repository, path, id, title } = value;
  if (!isText(repository) || !isText(path) || !isText(id) || !isText(title)) return undefined;
  return { repository, path, id, title };
}

function readSeat(value: unknown): TraySeat | null {
  if (!isRecord(value) || !isText(value.agentId) || !isText(value.role)) return null;
  const task = readTask(value.task);
  if (task === undefined) return null;
  return {
    agentId: value.agentId,
    ...(isText(value.workspaceId) ? { workspaceId: value.workspaceId } : {}),
    role: value.role,
    task,
    createdAt: isText(value.createdAt) ? value.createdAt : '',
  };
}

export function parseSeatPage(answer: unknown): TraySeatPage {
  if (isRecord(answer) && answer.state === 'unavailable')
    return { state: 'unavailable', detail: isText(answer.detail) ? answer.detail : 'the seat list is unavailable' };
  if (!isRecord(answer) || answer.state !== 'seats' || !Array.isArray(answer.seats))
    return { state: 'unavailable', detail: 'the seat list answered in a form the dashboard does not read' };
  const seats = answer.seats.map(readSeat).filter((seat): seat is TraySeat => seat !== null);
  const next = answer.nextOffset;
  return {
    state: 'seats',
    seats,
    nextOffset: typeof next === 'number' && Number.isInteger(next) && next >= 0 ? next : null,
    complete: answer.complete === true,
  };
}

export async function fetchSeatPage(offset: number, fetcher: typeof fetch = fetch): Promise<TraySeatPage> {
  try {
    const response = await fetcher(`/api/role-chats?offset=${offset}`);
    if (!response.ok) return { state: 'unavailable', detail: `the dashboard server answered ${response.status}` };
    return parseSeatPage(await response.json());
  } catch (error) {
    return { state: 'unavailable', detail: error instanceof Error ? error.message : 'the dashboard server could not be reached' };
  }
}

// ---- the list the tray holds ----

interface SeatListState {
  seats: TraySeat[];
  nextOffset: number | null;
  complete: boolean;
  status: 'idle' | 'loading' | 'ready' | 'unavailable';
  detail: string | null;
}

const IDLE: SeatListState = { seats: [], nextOffset: null, complete: true, status: 'idle', detail: null };

export const seatStore = createStore<SeatListState>(() => IDLE);

// Reads the first page again (`more: false`) or the next one. Rows of a later page join the list.
export async function loadSeats(more = false, fetcher: typeof fetch = fetch): Promise<void> {
  const current = seatStore.getState();
  if (current.status === 'loading') return;
  if (more && current.nextOffset === null) return;
  const offset = more ? (current.nextOffset as number) : 0;
  seatStore.setState({ status: 'loading' });
  const page = await fetchSeatPage(offset, fetcher);
  if (page.state === 'unavailable') {
    seatStore.setState({ status: 'unavailable', detail: page.detail });
    return;
  }
  const kept = more ? current.seats.filter((seat) => !page.seats.some((row) => row.agentId === seat.agentId)) : [];
  seatStore.setState({
    seats: [...kept, ...page.seats],
    nextOffset: page.nextOffset,
    complete: page.complete,
    status: 'ready',
    detail: null,
  });
}

export const useSeatList = (): SeatListState => useStore(seatStore);

// ---- names ----

const capitalized = (role: string): string => (role ? role[0].toUpperCase() + role.slice(1) : role);

export const seatOf = (agentId: string): TraySeat | null =>
  seatStore.getState().seats.find((seat) => seat.agentId === agentId) ?? null;

// A seat's agent, by the shortest prefix of its id, at least eight characters, that no other listed
// seat of the same role and task shares (rule 7): two Investigators of Projects read apart.
export function agentTag(seat: TraySeat, seats: readonly TraySeat[]): string {
  const twins = seats.filter(
    (other) => other.agentId !== seat.agentId && other.role === seat.role && other.task?.id === seat.task?.id,
  );
  let length = 8;
  while (length < seat.agentId.length && twins.some((twin) => twin.agentId.startsWith(seat.agentId.slice(0, length))))
    length += 1;
  return seat.agentId.slice(0, length);
}

// "Manager · 260928-MIK": the role and the task, which Send's button may name alone (d9a1ec8f).
export const seatRoleTask = (seat: TraySeat): string => `${capitalized(seat.role)} · ${seat.task?.id ?? 'Projects'}`;

// "Manager · 260928-MIK · b1d2c3e4": the role, the task and the agent.
export const seatLabel = (seat: TraySeat, seats: readonly TraySeat[] = []): string =>
  `${seatRoleTask(seat)} · ${agentTag(seat, seats)}`;

export function roleOf(agentId: string): string | null {
  const seat = seatOf(agentId);
  return seat ? capitalized(seat.role) : null;
}

// A chat's name in the tray: its seat row once the seat list holds it, else the shell's label of
// the shown chat, else its id. Only a chat named by its id carries `unnamed`, which says why. A seat's
// name has two parts: `short`, its role and task, and `tag`, its agent; `label` is the whole.
export interface ChatName {
  label: string;
  short: string;
  tag: string | null;
  unnamed: string | null;
}

function unnamedWhy(list: SeatListState): string {
  if (list.status === 'idle' || list.status === 'loading') return 'the seat list is still being read';
  if (list.status === 'unavailable') return `the seat list is unavailable (${list.detail ?? 'no detail'})`;
  if (!list.complete) return "the host's list did not end, and the part read does not hold this chat";
  return "the host's list of live seats does not hold this chat; it may have ended";
}

function nameIn(agentId: string, list: SeatListState, shown: ShownChat | null): ChatName {
  const seat = list.seats.find((row) => row.agentId === agentId);
  if (seat) {
    const tag = agentTag(seat, list.seats);
    return { label: seatLabel(seat, list.seats), short: seatRoleTask(seat), tag, unnamed: null };
  }
  if (shown?.agentId === agentId && shown.label) return { label: shown.label, short: shown.label, tag: null, unnamed: null };
  const label = shortAgent(agentId);
  return { label, short: label, tag: null, unnamed: `${label} is named by its id: ${unnamedWhy(list)}.` };
}

// Chat names for a component: it renders again when the seat list or the shown chat changes, so a
// name follows the list as it arrives.
export function useChatNames(): (agentId: string) => ChatName {
  const list = useSeatList();
  const shown = useStore(shownStore, (state) => state.chat);
  return (agentId) => nameIn(agentId, list, shown);
}
