// The tray's bounds are dashboard preferences the developer sets on the dashboard (MIK-R188 rule 11,
// e6ac7a3e): kept in this origin's storage under `ar.tray.bounds.v1`, with a way back to the defaults.
import { describe, expect, it } from 'vitest';

import { BOUNDS_KEY, DEFAULT_BOUNDS, MIB, createTrayPreferences } from './trayPreferences';

function memoryStorage(refuse = false): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      if (refuse) throw new Error('The quota has been exceeded.');
      values.delete(key);
    },
    setItem: (key, value) => {
      if (refuse) throw new Error('The quota has been exceeded.');
      values.set(key, value);
    },
  };
}

describe('the tray bounds preference', () => {
  it('takes a positive whole bound, refuses any other, keeps it across a reload and goes back to the defaults', () => {
    const storage = memoryStorage();
    const first = createTrayPreferences(storage);
    expect(first.getState().bounds).toEqual(DEFAULT_BOUNDS);
    expect(first.getState().setBound('maxPicks', 3)).toBe(true);
    expect(first.getState().setBound('maxImageBytes', 2 * MIB)).toBe(true);
    expect([0, -1, 2.5, Number.NaN].map((wrong) => first.getState().setBound('maxMessageBytes', wrong))).toEqual([false, false, false, false]);
    expect(first.getState().bounds).toEqual({ ...DEFAULT_BOUNDS, maxPicks: 3, maxImageBytes: 2 * MIB });
    // Only the bounds he changed are stored, in the one JSON object under the key.
    expect(JSON.parse(storage.getItem(BOUNDS_KEY) ?? 'null')).toEqual({ maxPicks: 3, maxImageBytes: 2 * MIB });
    const reloaded = createTrayPreferences(storage);
    expect(reloaded.getState().bounds).toEqual({ ...DEFAULT_BOUNDS, maxPicks: 3, maxImageBytes: 2 * MIB });
    reloaded.getState().resetBounds();
    expect(reloaded.getState().bounds).toEqual(DEFAULT_BOUNDS);
    expect(storage.getItem(BOUNDS_KEY)).toBeNull();
    expect(createTrayPreferences(storage).getState().bounds).toEqual(DEFAULT_BOUNDS);
  });

  it('holds a bound for the page’s lifetime when the storage refuses the write', () => {
    const preferences = createTrayPreferences(memoryStorage(true));
    expect(preferences.getState().setBound('maxPicks', 4)).toBe(true);
    expect(preferences.getState().bounds.maxPicks).toBe(4);
    preferences.getState().resetBounds();
    expect(preferences.getState().bounds).toEqual(DEFAULT_BOUNDS);
  });
});
