// The tray's dashboard preferences (MIK-R188 rules 4 and 11). They live in the browser's storage
// of this dashboard origin, never in the AR settings file: that file is read-only, and a new key
// there breaks older installed commands.
//
// - Copy on highlight is on by default, because the developer asked that highlighting also copies.
//   It can be turned off, since it overwrites the clipboard on every highlight.
// - The bounds are counts: picks per message, bytes per image and bytes per message. The developer
//   sets them under "Bounds" in the tray's foot, beside copy on highlight, and can go back to the
//   defaults (rule 11, e6ac7a3e). Only a bound that differs from its default is stored.
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

export const COPY_ON_HIGHLIGHT_KEY = 'ar.tray.copy-on-highlight.v1';
export const BOUNDS_KEY = 'ar.tray.bounds.v1';

export const MIB = 1024 * 1024;

export interface TrayBounds {
  maxPicks: number;
  maxImageBytes: number;
  maxMessageBytes: number;
}

export const DEFAULT_BOUNDS: TrayBounds = { maxPicks: 20, maxImageBytes: 5 * MIB, maxMessageBytes: 20 * MIB };

type ReadableStorage = Pick<Storage, 'getItem'>;

export function readCopyOnHighlight(storage: ReadableStorage | undefined): boolean {
  try {
    return storage?.getItem(COPY_ON_HIGHLIGHT_KEY) !== 'off';
  } catch {
    return true;
  }
}

const positive = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value > 0;

// A stored bound that is not a positive whole number keeps its default; the others are taken.
export function readBounds(storage: ReadableStorage | undefined): TrayBounds {
  let stored: unknown = null;
  try {
    const raw = storage?.getItem(BOUNDS_KEY);
    stored = raw ? JSON.parse(raw) : null;
  } catch {
    stored = null;
  }
  if (!stored || typeof stored !== 'object') return DEFAULT_BOUNDS;
  const value = stored as Partial<Record<keyof TrayBounds, unknown>>;
  return {
    maxPicks: positive(value.maxPicks) ? value.maxPicks : DEFAULT_BOUNDS.maxPicks,
    maxImageBytes: positive(value.maxImageBytes) ? value.maxImageBytes : DEFAULT_BOUNDS.maxImageBytes,
    maxMessageBytes: positive(value.maxMessageBytes) ? value.maxMessageBytes : DEFAULT_BOUNDS.maxMessageBytes,
  };
}

interface TrayPreferencesState {
  copyOnHighlight: boolean;
  bounds: TrayBounds;
  setCopyOnHighlight: (on: boolean) => void;
  // Takes a positive whole number; answers false and keeps the bound for any other value.
  setBound: (name: keyof TrayBounds, value: number) => boolean;
  resetBounds: () => void;
}

const BOUND_NAMES = Object.keys(DEFAULT_BOUNDS) as (keyof TrayBounds)[];

// A storage failure must not break a preference; the value holds for this page's lifetime.
function keepBounds(store: Storage | undefined, bounds: TrayBounds): void {
  const changed = BOUND_NAMES.filter((name) => bounds[name] !== DEFAULT_BOUNDS[name]);
  try {
    if (changed.length === 0) store?.removeItem(BOUNDS_KEY);
    else store?.setItem(BOUNDS_KEY, JSON.stringify(Object.fromEntries(changed.map((name) => [name, bounds[name]]))));
  } catch {
    // Kept in memory only.
  }
}

function storage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

export function createTrayPreferences(store: Storage | undefined = storage()) {
  return createStore<TrayPreferencesState>((set, get) => ({
    copyOnHighlight: readCopyOnHighlight(store),
    bounds: readBounds(store),
    setCopyOnHighlight: (on) => {
      try {
        store?.setItem(COPY_ON_HIGHLIGHT_KEY, on ? 'on' : 'off');
      } catch {
        // A storage failure must not break the switch; it holds for this page's lifetime.
      }
      set({ copyOnHighlight: on });
    },
    setBound: (name, value) => {
      if (!positive(value)) return false;
      const bounds = { ...get().bounds, [name]: value };
      keepBounds(store, bounds);
      set({ bounds });
      return true;
    },
    resetBounds: () => {
      keepBounds(store, DEFAULT_BOUNDS);
      set({ bounds: DEFAULT_BOUNDS });
    },
  }));
}

export const trayPreferences = createTrayPreferences();

export const useCopyOnHighlight = (): boolean => useStore(trayPreferences, (state) => state.copyOnHighlight);
export const useTrayBounds = (): TrayBounds => useStore(trayPreferences, (state) => state.bounds);
