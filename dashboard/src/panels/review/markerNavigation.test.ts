// MIK-R34: how the review page moves for an intent marker -- following selects the target the way
// the page selects it, and the return puts the reading position back. The workspace and the page's
// selection here are recording stand-ins for the real ones.
import { describe, expect, it } from 'vitest';
import type { FamilySelection } from './FamilyTree';
import { workspaceMarkerMoves, type MarkerSelection } from './markerNavigation';
import type { WorkspaceState } from './ReviewWorkspace';

function workspace(over: Partial<WorkspaceState> = {}) {
  const calls: string[] = [];
  const record =
    (name: string) =>
    (...args: unknown[]) =>
      calls.push(`${name} ${JSON.stringify(args[0] ?? null)}`);
  const state = {
    chosen: { familyId: 'fam-a', memberRevisionId: 'rev-a' } as FamilySelection | null,
    worklist: true,
    focusSelection: { current: { from: null } },
    setChosen: record('setChosen'),
    setWorklist: record('setWorklist'),
    ...over,
  } as unknown as WorkspaceState;
  return { state, calls };
}

function selection(calls: string[], state: WorkspaceState): MarkerSelection {
  return {
    current: () => ({ subject: { kind: 'family', id: 'fam-a' }, chosen: state.chosen }),
    select: (subject, context) => calls.push(`select ${JSON.stringify(subject ?? null)} ${JSON.stringify(context)}`),
  };
}

describe('following a marker', () => {
  it("selects the invariant's own review at its member row of the named family", () => {
    const { state, calls } = workspace();
    workspaceMarkerMoves(state, selection(calls, state)).open({
      invariant: 'INV-X',
      invariantKey: 'inv-x',
      familyKey: 'fam-b',
      memberRevisionKey: 'rev-x1',
      state: 'member',
    });
    expect(calls).toEqual([
      'select {"kind":"invariant","id":"inv-x"} {"familyId":"fam-b","memberRevisionId":"rev-x1"}',
    ]);
  });

  it('selects the invariant alone when no family records it', () => {
    const { state, calls } = workspace();
    workspaceMarkerMoves(state, selection(calls, state)).open({
      invariant: 'INV-Y',
      invariantKey: 'inv-y',
      state: 'confirmed_no_family',
    });
    expect(calls[0]).toBe('select {"kind":"invariant","id":"inv-y"} null');
  });
});

describe('the return', () => {
  // A card has no Full file of its own (MIK-R89 v2 rule 4): the return has none to reopen.
  it('restores the subject, then the worklist view', () => {
    const { state, calls } = workspace();
    const restore = workspaceMarkerMoves(state, selection(calls, state)).capture();
    calls.length = 0;
    restore();
    expect(calls).toEqual([
      'select {"kind":"family","id":"fam-a"} {"familyId":"fam-a","memberRevisionId":"rev-a"}',
      'setWorklist true',
    ]);
    // Focus goes back to the marker, not to the tree's selection.
    expect(state.focusSelection.current).toBeNull();
  });
});
