// AR's one tray (MIK-R188): the picks the developer collected, his notes and text, and the
// chats he sends them to, until his Send.
//
// - Nothing enters without his click and nothing leaves without his Send (the normative rule).
// - A second pick of the same object adds nothing (rule 1); a pick over a bound is refused by name
//   before it enters (rule 11).
// - The first pick sets the default target once (rule 7); he can choose several targets.
// - The tray is kept per dashboard origin (rule 6): the list and text in localStorage, image bytes in
//   IndexedDB. A storage that refuses keeps the tray in memory and says so (Failure and Recovery).
import { useStore } from 'zustand';
import { createStore, type StoreApi } from 'zustand/vanilla';

import { pickKey } from './pickKey';
import { clockTime, defaultTargetFor, imageSizeRefusal, imageTypeRefusal, refuseOnEntry } from './trayModel';
import { blobBytes, createImageStore, sha256Hex, type TrayImageStore } from './trayImages';
import { EMPTY_TRAY, readKeptTray, writeKeptTray, type KeptTray } from './trayPersistence';
import { trayPreferences, type TrayBounds } from './trayPreferences';
import { shownChatId, shownStore } from './shownChat';
import type { TrayDelivery, TrayPick, TrayPickInput } from './types';

export interface TrayDeps {
  storage: Storage | undefined;
  images: TrayImageStore;
  bounds: () => TrayBounds;
  shownChat: () => string | null;
  now: () => Date;
  // Calls `listener` each time a chat column starts showing a chat; answers the unsubscribe.
  watchShownChat?: (listener: (agentId: string) => void) => () => void;
}

export type TrayPersistence = { state: 'kept' } | { state: 'memory-only'; detail: string };

export interface TrayTargetResult {
  agentId: string;
  delivery: TrayDelivery;
}

// The last Send's answer per target. Delivered and held targets count as sent. `pickIds` and
// `text` are what that Send carried: a pick added while it ran is not part of it and stays.
export interface TraySendOutcome {
  at: string;
  results: TrayTargetResult[];
  pickIds: string[];
  text: string;
}

export interface TrayState extends KeptTray {
  // Shown or closed (closed with picks: the pill). Shown, the tray is minimised to its one line or
  // expanded to the full view (rule 5, 15f92a8f).
  open: boolean;
  minimised: boolean;
  persistence: TrayPersistence;
  refusal: string | null;
  sending: boolean;
  outcome: TraySendOutcome | null;
  // The review page with its chat column closed: a first pick there takes as its default the chat
  // the opening column then shows (`pendingDefault`), unless he chose a target meanwhile.
  deferDefault: boolean;
  pendingDefault: boolean;
  setDeferDefault: (on: boolean) => void;
  resolvePendingDefault: (agentId: string) => void;
  // Leaving the review page ends the wait.
  clearPendingDefault: () => void;
  // Adds one pick; a second pick of the same object adds nothing. Answers whether it was added.
  add: (input: TrayPickInput) => boolean;
  // The developer's click on a ⊕, an "⊕ add" or a gutter line: adds the pick and shows the tray, so
  // he sees the counter, or the refusal that names the pick.
  take: (input: TrayPickInput) => boolean;
  // The tray after a pick: minimised when it was closed, else as it is (15f92a8f).
  reveal: () => void;
  has: (input: TrayPickInput) => boolean;
  remove: (id: string) => void;
  // Puts `input` in the place of pick `id` (a file line range extended by shift-click).
  replace: (id: string | null, input: TrayPickInput) => boolean;
  setNote: (id: string, note: string) => void;
  setText: (text: string) => void;
  setTargets: (agentIds: readonly string[]) => void;
  toggleTarget: (agentId: string) => void;
  // His open (the Tray button, the pill) expands the tray; his close closes it.
  setOpen: (open: boolean) => void;
  setMinimised: (minimised: boolean) => void;
  dismissRefusal: () => void;
  // A pasted image (rule 6). Answers whether it entered the tray.
  addImage: (blob: Blob) => Promise<boolean>;
  setSending: (sending: boolean) => void;
  // Applies a Send's answers: a fully sent tray empties; refused targets keep the picks and the text
  // and become the tray's targets. Either way the tray returns to its line (15f92a8f).
  settle: (outcome: TraySendOutcome) => void;
}

type SetTray = StoreApi<TrayState>['setState'];
type GetTray = StoreApi<TrayState>['getState'];

let counter = 0;
function nextId(now: Date): string {
  counter += 1;
  return `pick-${now.getTime().toString(36)}-${counter}`;
}

const KEPT: TrayPersistence = { state: 'kept' };

function forgetImages(picks: readonly TrayPick[], keep: readonly TrayPick[], images: TrayImageStore): void {
  const kept = new Set(keep.flatMap((pick) => (pick.input.kind === 'image' ? [pick.input.sha256] : [])));
  for (const pick of picks)
    if (pick.input.kind === 'image' && !kept.has(pick.input.sha256)) void images.remove(pick.input.sha256);
}

function pickActions(set: SetTray, get: GetTray, deps: TrayDeps) {
  const add = (input: TrayPickInput): boolean => {
    const state = get();
    const key = pickKey(input);
    if (state.picks.some((pick) => pick.key === key)) return false;
    const refusal = refuseOnEntry(input, state.picks, state.text, deps.bounds());
    if (refusal) {
      set({ refusal });
      return false;
    }
    const now = deps.now();
    const pick: TrayPick = { id: nextId(now), key, addedAt: now.toISOString(), input, note: '' };
    const next: Partial<TrayState> = { picks: [...state.picks, pick], refusal: null, outcome: null };
    if (!state.defaultDecided && !state.targetsChosen) {
      const target = defaultTargetFor(input, deps.shownChat());
      next.targets = target ? [target] : [];
      next.defaultDecided = true;
      next.pendingDefault = target === null && state.deferDefault;
    }
    set(next);
    return true;
  };
  // Removing the last pick undecides a default nobody chose: the next first pick sets it from the
  // chat shown then, or leaves none (rule 7). A target he chose stays.
  const remove = (id: string): void => {
    const state = get();
    const kept = state.picks.filter((pick) => pick.id !== id);
    if (kept.length === state.picks.length) return;
    forgetImages(state.picks, kept, deps.images);
    const undecided = kept.length === 0 && !state.targetsChosen;
    set(undecided ? { picks: kept, refusal: null, targets: [], defaultDecided: false, pendingDefault: false } : { picks: kept, refusal: null });
  };
  return {
    add,
    remove,
    has: (input: TrayPickInput) => {
      const key = pickKey(input);
      return get().picks.some((pick) => pick.key === key);
    },
    replace: (id: string | null, input: TrayPickInput) => {
      const before = get().picks;
      if (id !== null) set({ picks: before.filter((pick) => pick.id !== id) });
      if (add(input)) return true;
      set({ picks: before });
      return false;
    },
    setNote: (id: string, note: string) =>
      set({ picks: get().picks.map((pick) => (pick.id === id ? { ...pick, note: note.trim() } : pick)) }),
  };
}

// A pasted image (rule 6). Every bound is asked before its bytes are stored, so a refused image keeps
// no bytes and no write error (rule 11). A pick that cannot enter after the write, because the tray
// filled meanwhile, takes its bytes back out, unless a live pick holds the same image.
function imageAction(set: SetTray, get: GetTray, deps: TrayDeps, unsaved: Map<string, string>, refresh: () => void) {
  return async (blob: Blob): Promise<boolean> => {
    const time = clockTime(deps.now().toISOString()) ?? 'just now';
    const title = `Screenshot · pasted ${time}`;
    const refusedFirst = imageTypeRefusal(title, blob.type) ?? imageSizeRefusal(title, blob.size, deps.bounds());
    if (refusedFirst) {
      set({ refusal: refusedFirst });
      return false;
    }
    const sha256 = await sha256Hex(await blobBytes(blob));
    const input: TrayPickInput = { kind: 'image', title, mimeType: blob.type, sha256, bytes: blob.size };
    if (get().has(input)) return false;
    const refusal = refuseOnEntry(input, get().picks, get().text, deps.bounds());
    if (refusal) {
      set({ refusal });
      return false;
    }
    try {
      await deps.images.put(sha256, blob);
      unsaved.delete(sha256);
    } catch (error) {
      unsaved.set(sha256, error instanceof Error ? error.message : 'the browser refused the write');
    }
    const added = get().add(input);
    if (!added && !get().has(input)) {
      unsaved.delete(sha256);
      void deps.images.remove(sha256);
    }
    refresh();
    return added;
  };
}

function settle(set: SetTray, get: GetTray, deps: TrayDeps) {
  return (outcome: TraySendOutcome): void => {
    const refused = outcome.results.filter((result) => result.delivery.state === 'refused');
    if (refused.length === 0) {
      const state = get();
      const kept = state.picks.filter((pick) => !outcome.pickIds.includes(pick.id));
      forgetImages(state.picks, kept, deps.images);
      const text = state.text === outcome.text ? '' : state.text;
      // A sent tray returns to its line, which says what the Send did (15f92a8f).
      set({ ...EMPTY_TRAY, picks: kept, text, sending: false, outcome, refusal: null, pendingDefault: false, minimised: true });
      return;
    }
    // The line names the refusing target, and the kept picks wait for it as the tray's targets.
    set({
      targets: refused.map((result) => result.agentId),
      targetsChosen: true,
      pendingDefault: false,
      sending: false,
      outcome,
      minimised: true,
    });
  };
}

// His choice of targets, and the default that waits for the review page's column to open. Any choice
// of his ends the wait.
function targetActions(set: SetTray, get: GetTray) {
  return {
    setTargets: (agentIds: readonly string[]) =>
      set({ targets: [...new Set(agentIds)], targetsChosen: true, pendingDefault: false }),
    toggleTarget: (agentId: string) => {
      const targets = get().targets;
      set({
        targets: targets.includes(agentId) ? targets.filter((id) => id !== agentId) : [...targets, agentId],
        targetsChosen: true,
        pendingDefault: false,
      });
    },
    setDeferDefault: (on: boolean) => {
      if (get().deferDefault !== on) set({ deferDefault: on });
    },
    clearPendingDefault: () => {
      if (get().pendingDefault) set({ pendingDefault: false });
    },
    resolvePendingDefault: (agentId: string) => {
      const state = get();
      if (!state.pendingDefault) return;
      set(state.targetsChosen ? { pendingDefault: false } : { targets: [agentId], pendingDefault: false });
    },
  };
}

function memoryOnly(detail: string): TrayPersistence {
  return {
    state: 'memory-only',
    detail: `The browser's storage refused ${detail}. The tray is kept in memory and will not survive a reload.`,
  };
}

function persistenceOf(writeError: string | null, picks: readonly TrayPick[], unsaved: Map<string, string>): TrayPersistence {
  if (writeError) return memoryOnly(`the tray (${writeError})`);
  const lost = picks.flatMap((pick) =>
    pick.input.kind === 'image' && unsaved.has(pick.input.sha256) ? [unsaved.get(pick.input.sha256)] : [],
  );
  if (lost.length === 0) return KEPT;
  return memoryOnly(`${lost.length === 1 ? 'an image' : `${lost.length} images`} (${lost[0]})`);
}

const keptPart = (state: TrayState): KeptTray => ({
  picks: state.picks,
  text: state.text,
  targets: state.targets,
  targetsChosen: state.targetsChosen,
  defaultDecided: state.defaultDecided,
});

const samePart = (left: KeptTray, right: KeptTray): boolean =>
  (Object.keys(left) as (keyof KeptTray)[]).every((key) => left[key] === right[key]);

// Writes the kept part on every change of it, and says when the tray lives in memory only.
function keepTray(store: StoreApi<TrayState>, deps: TrayDeps, unsaved: Map<string, string>): () => void {
  let writeError: string | null = null;
  const refresh = () => {
    const state = store.getState();
    const next = persistenceOf(writeError, state.picks, unsaved);
    const current = state.persistence;
    const same = next.state === current.state && (next.state === 'kept' || (current.state === 'memory-only' && current.detail === next.detail));
    if (!same) store.setState({ persistence: next });
  };
  store.subscribe((state, previous) => {
    const part = keptPart(state);
    if (samePart(part, keptPart(previous))) return;
    writeError = writeKeptTray(deps.storage, part);
    refresh();
  });
  return refresh;
}

export function createTrayStore(deps: TrayDeps): StoreApi<TrayState> {
  const unsaved = new Map<string, string>();
  let refresh: () => void = () => undefined;
  const store = createStore<TrayState>((set, get) => ({
    ...readKeptTray(deps.storage),
    open: false,
    minimised: false,
    persistence: deps.storage ? KEPT : memoryOnly('the tray (this browser offers no local storage)'),
    refusal: null,
    sending: false,
    outcome: null,
    deferDefault: false,
    pendingDefault: false,
    ...pickActions(set, get, deps),
    take: (input) => {
      const added = get().add(input);
      get().reveal();
      return added;
    },
    reveal: () => {
      if (!get().open) set({ open: true, minimised: true });
    },
    setText: (text) => set({ text }),
    ...targetActions(set, get),
    setOpen: (open) => set({ open, minimised: false }),
    setMinimised: (minimised) => set({ open: true, minimised }),
    dismissRefusal: () => set({ refusal: null }),
    addImage: (blob) => imageAction(set, get, deps, unsaved, () => refresh())(blob),
    setSending: (sending) => set({ sending }),
    settle: settle(set, get, deps),
  }));
  refresh = keepTray(store, deps, unsaved);
  deps.watchShownChat?.((agentId) => store.getState().resolvePendingDefault(agentId));
  return store;
}

function localStorageOrNone(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

// The image bytes of this origin's tray; Send reads them from here too.
export const trayImageStore = createImageStore();

export const trayStore = createTrayStore({
  storage: localStorageOrNone(),
  images: trayImageStore,
  bounds: () => trayPreferences.getState().bounds,
  shownChat: shownChatId,
  now: () => new Date(),
  watchShownChat: (listener) =>
    shownStore.subscribe((state, previous) => {
      const agentId = state.chat?.agentId;
      if (agentId && agentId !== previous.chat?.agentId) listener(agentId);
    }),
});

// Whether the full view shows (rule 5, 15f92a8f).
export const isExpanded = (state: Pick<TrayState, 'open' | 'minimised'>): boolean => state.open && !state.minimised;

export const useTray = <T>(select: (state: TrayState) => T): T => useStore(trayStore, select);
export const useTrayPicks = (): TrayPick[] => useStore(trayStore, (state) => state.picks);
export const useTrayHas = (input: TrayPickInput): boolean => {
  const key = pickKey(input);
  return useStore(trayStore, (state) => state.picks.some((pick) => pick.key === key));
};
