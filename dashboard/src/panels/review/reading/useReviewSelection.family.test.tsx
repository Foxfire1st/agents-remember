import { act, cleanup, renderHook } from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, expect, it, vi } from 'vitest';

import { changeSetModel } from '../changeSetOrder';
import { l181Inputs } from '../changeSetOrder.test-utils';
import { useWorkspaceState } from '../workspaceState';
import { useReviewSelection, type SelectionDeps } from './useReviewSelection';

afterEach(cleanup);

it('reads the selected family itself and preserves its walk context without selecting a member', () => {
  const navigate = vi.fn();
  const source = l181Inputs();
  const { result } = renderHook(() => {
    const workspace = useWorkspaceState();
    const deps = useRef<SelectionDeps | null>(null);
    deps.current = {
      navigationSelect: navigate,
      workspace,
      setInstead: vi.fn(),
      setPage: vi.fn(),
      arm: vi.fn(),
      cards: source.cards,
      model: changeSetModel(source, 'authored', 'all', { selection: null }),
      older: false,
    };
    return useReviewSelection(null, deps);
  });
  const family = { kind: 'family' as const, id: 'family-own-context' };
  act(() => result.current.select({ kind: 'subject', subject: family }));
  expect(navigate).toHaveBeenCalledExactlyOnceWith(
    family,
    { familyId: family.id },
    { keepTree: true, page: undefined },
  );
  expect(result.current.selection).toEqual({ kind: 'subject', subject: family });
});
