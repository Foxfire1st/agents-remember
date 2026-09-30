// MIK-R34: how the review workspace moves for an intent marker -- following selects the target's tree
// position through the rail's own selection, and the return puts the reading position back. The
// workspace and navigation here are recording stand-ins for the real ones' setters.
import { describe, expect, it, vi } from 'vitest';
import type { FamilySelection } from './FamilyTree';
import { workspaceMarkerMoves } from './markerNavigation';
import type { ReviewNavigationState } from './ReviewNavigation';
import type { WorkspaceState } from './ReviewWorkspace';

function workspace(over: Partial<WorkspaceState> = {}) {
  const calls: string[] = [];
  const record =
    (name: string) =>
    (...args: unknown[]) =>
      calls.push(`${name} ${JSON.stringify(args[0] ?? null)}`);
  const state = {
    chosen: { familyId: 'fam-a', memberRevisionId: 'rev-a' } as FamilySelection | null,
    lane: { destination: 'unknown', path: 'src/a.py' },
    openPath: null,
    layout: 'inline',
    fullFile: true,
    focusSelection: { current: { from: null } },
    setChosen: record('setChosen'),
    setLane: record('setLane'),
    setOpenPath: record('setOpenPath'),
    setLayout: record('setLayout'),
    setFullFile: record('setFullFile'),
    ...over,
  } as unknown as WorkspaceState;
  return { state, calls };
}

function navigation(calls: string[]): ReviewNavigationState {
  return {
    catalogue: {} as ReviewNavigationState['catalogue'],
    subject: { kind: 'family', id: 'fam-a' },
    onSelect: vi.fn((subject, context) =>
      calls.push(`onSelect ${JSON.stringify(subject ?? null)} ${JSON.stringify(context ?? null)}`),
    ),
  };
}

describe('following a marker', () => {
  it("selects the invariant's own review at its member row of the named family", () => {
    const { state, calls } = workspace();
    workspaceMarkerMoves(state, navigation(calls)).open({
      invariant: 'INV-X',
      invariantKey: 'inv-x',
      familyKey: 'fam-b',
      memberRevisionKey: 'rev-x1',
      state: 'member',
    });
    expect(calls).toEqual([
      'setOpenPath null',
      'onSelect {"kind":"invariant","id":"inv-x"} {"familyId":"fam-b","memberRevisionId":"rev-x1"}',
    ]);
  });

  it('selects the invariant alone when no family records it', () => {
    const { state, calls } = workspace();
    workspaceMarkerMoves(state, navigation(calls)).open({
      invariant: 'INV-Y',
      invariantKey: 'inv-y',
      state: 'confirmed_no_family',
    });
    expect(calls[1]).toBe('onSelect {"kind":"invariant","id":"inv-y"} null');
  });
});

describe('the return', () => {
  it('restores the subject, then the lane, the opened file, the layout and the full-file choice', () => {
    const { state, calls } = workspace();
    const moves = workspaceMarkerMoves(state, navigation(calls));
    const restore = moves.capture();
    calls.length = 0;
    restore();
    expect(calls).toEqual([
      'onSelect {"kind":"family","id":"fam-a"} {"familyId":"fam-a","memberRevisionId":"rev-a"}',
      'setLane {"destination":"unknown","path":"src/a.py"}',
      'setOpenPath null',
      'setLayout "inline"',
      'setFullFile true',
    ]);
    // Focus goes back to the marker, not to the tree's selection.
    expect(state.focusSelection.current).toBeNull();
  });

  it('restores the family selection directly where the workspace has no navigation', () => {
    const { state, calls } = workspace();
    const restore = workspaceMarkerMoves(state).capture();
    restore();
    expect(calls[0]).toBe('setChosen {"familyId":"fam-a","memberRevisionId":"rev-a"}');
  });
});
