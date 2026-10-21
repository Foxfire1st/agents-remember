// The review page's local display choices (workspaceState.ts): one open Full file, the reading
// column's opened groups, and the worklist view a family choice leaves.
import { act, renderHook } from '@testing-library/react';
import { expect, it } from 'vitest';
import { useWorkspaceState } from './ReviewWorkspace';

it('opens and closes a group by id, and opening an open group changes nothing', () => {
  const { result } = renderHook(() => useWorkspaceState());
  act(() => result.current.toggleGroup('g1'));
  expect(result.current.openGroups.has('g1')).toBe(true);
  const opened = result.current.openGroups;
  act(() => result.current.openGroup('g1'));
  expect(result.current.openGroups).toBe(opened);
  act(() => result.current.toggleGroup('g1'));
  expect(result.current.openGroups.has('g1')).toBe(false);
});

it('leaves the worklist view when a family position is chosen', () => {
  const { result } = renderHook(() => useWorkspaceState());
  act(() => result.current.setWorklist(true));
  act(() => result.current.setChosen({ familyId: 'family' }));
  expect(result.current.worklist).toBe(false);
  expect(result.current.chosen).toEqual({ familyId: 'family' });
});
