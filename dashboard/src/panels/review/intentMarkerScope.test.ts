// MIK-R34 (review R1 F4, F5): the scope asks the server only for a path the change inventory lists,
// once per surface, and says which paths it lists. `fetch` is the only stand-in.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewSourceInventory } from '../../data/review';
import type { ReviewFileClassification } from '../../data/reviewLane';
import { markerInventory, useIntentMarkerScope } from './intentMarkerScope';

const body = JSON.parse(
  readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), 'markerReturn.file.captured.json'),
    'utf8',
  ),
) as { file_classification: ReviewFileClassification };
const NOTES = body.file_classification.path;

afterEach(() => vi.unstubAllGlobals());

it('classifies a listed path once per surface, and never an unlisted one', async () => {
  const fetchFn = vi.fn<(address: string) => Promise<Response>>(
    async () =>
      ({
        ok: true,
        status: 200,
        json: async () => ({ state: 'trees', file_classification: body.file_classification }),
      }) as Response,
  );
  vi.stubGlobal('fetch', fetchFn);
  const { result } = renderHook(() =>
    useIntentMarkerScope({
      repo: 'agents-remember',
      master: 'm',
      leaf: 'l',
      comparison: 2,
      changed: [NOTES],
      partial: true,
      moves: { capture: () => () => undefined, open: () => undefined },
    }),
  );
  const scope = result.current!;
  expect([scope.listed(NOTES), scope.listed('docs/unchanged.md'), scope.partial]).toEqual([
    true,
    false,
    true,
  ]);
  // An unlisted path is never asked about.
  expect(scope.classify('docs/unchanged.md')).toBeNull();
  expect(fetchFn).not.toHaveBeenCalled();
  // A listed one is asked once, and the answer is shared.
  const first = scope.classify(NOTES);
  expect(scope.classify(NOTES)).toBe(first);
  expect(await first).toEqual({ phase: 'ready', value: body.file_classification });
  expect(fetchFn).toHaveBeenCalledTimes(1);
  const asked = new URL(fetchFn.mock.calls[0][0], 'http://localhost').searchParams;
  expect([asked.get('file'), asked.get('comparison')]).toEqual([NOTES, '2']);
  expect(result.current!.peek(NOTES)).toEqual({ phase: 'ready', value: body.file_classification });
});

it('counts an inventory that is partial, or not measured at all, as partial (review R2)', () => {
  const inventory: ReviewSourceInventory = {
    state: 'measured',
    entries: [{ path: NOTES, status: 'modified', content: 'text', mode_change: false }],
    listed_total: 1,
    detail: 'measured',
    partial: false,
    command: 'git diff',
    unrepresentable_paths: [],
  };
  expect(markerInventory(inventory)).toEqual({ changed: [NOTES], partial: false });
  expect(markerInventory({ ...inventory, partial: true }).partial).toBe(true);
  // Not measured: nothing establishes that the listed paths are all the changed ones.
  expect(markerInventory({ ...inventory, state: 'unavailable' }).partial).toBe(true);
});
