// The Explanation view's decisions, without a browser (MIK-R106 v2 rule 6; MIK-R103 v2; MIK-R104 v3).
//
// One case per promise the packets make and the view must not break: a planned binding is never
// called #0; the agreed revision is pinned and the removal list never holds it; only the task total
// offers removal; a moved leaf without work offers no Update; and a page with no part map is still
// one part's worth of list.
import { describe, expect, it } from 'vitest';

import type { ExplainerRevision, ExplainerState } from './explainerApi';
import {
  BASIS_LEGEND,
  TASK_TOTAL_REFUSAL,
  agreedRevision,
  basisLabel,
  bindingLabel,
  clockOf,
  defaultComparePair,
  defaultShownRevision,
  hasWork,
  isPinned,
  movedLine,
  orderRevisions,
  phaseOf,
  removalOffered,
  removableRevisions,
  staleGradesOf,
  staleParts,
  staleRegenerationOf,
  standfirstText,
  updateOffered,
} from './explainerModel';
import {
  EXPLAINER_FRAME_ID,
  parseRevisions,
  parseState,
  readBinding,
  stateUrl,
} from './explainerApi';

const binding = (owner: 'recorded' | 'planned', number: number, label?: string) => ({
  owner,
  number,
  reference: `review-comparison:task/leaf/${owner === 'planned' ? 'planned' : number}`,
  label: label ?? null,
});

const revision = (over: Partial<ExplainerRevision> & { revision: number }): ExplainerRevision => ({
  binding: binding('recorded', 8, '#8'),
  at: '2026-10-19T12:36:00Z',
  kind: 'global',
  agreed: false,
  pinned: false,
  ...over,
});

const state = (over: Partial<ExplainerState> = {}): ExplainerState => ({
  state: 'ok',
  binding: binding('recorded', 9, '#9'),
  seat: { state: 'live' },
  retired: false,
  retiredAt: null,
  explanation: { revision: 8, at: '2026-10-19T12:36:00Z', entrypoint: 'index.html', comparison: null },
  building: null,
  stale: { parts: [] },
  parts: [],
  claims: [],
  moved: null,
  limit: null,
  ...over,
});

describe('bindingLabel', () => {
  it('names a recorded comparison by its number and a planned one "planned", never #0', () => {
    expect(bindingLabel(binding('recorded', 8, '#8'))).toBe('#8');
    expect(bindingLabel(binding('planned', 0))).toBe('planned');
    expect(bindingLabel(null)).toBe('no binding');
  });
});

describe('the agreed revision', () => {
  const rows = [
    revision({ revision: 7, binding: binding('recorded', 7, '#7') }),
    revision({ revision: 8, agreed: true, pinned: true }),
    revision({ revision: 9, binding: binding('recorded', 9, '#9'), kind: 'focused', focus: 'Risks' }),
  ];

  it('is the one marked agreed, and the pin also marks it', () => {
    expect(agreedRevision(rows, null)?.revision).toBe(8);
    expect(isPinned(rows[1], null)).toBe(true);
    // The task document's `intentReview.agreedRevision` alone is enough, even without the flag.
    expect(isPinned(rows[0], 7)).toBe(true);
  });

  it('is never offered for removal, and a removed revision is not offered again', () => {
    expect(removableRevisions(rows, 8).map((row) => row.revision)).toEqual([7, 9]);
    const removed = [...rows, revision({ revision: 6, removed: true })];
    expect(removableRevisions(removed, 8).map((row) => row.revision)).toEqual([7, 9]);
  });
});

describe('the removal control', () => {
  it('appears only for the task total', () => {
    expect(removalOffered({ reason: TASK_TOTAL_REFUSAL, detail: '' })).toBe(true);
    // The 16 MiB per-revision ceiling refuses one revision; removing others does not help.
    expect(removalOffered({ reason: 'revision-too-large', detail: '' })).toBe(false);
    expect(removalOffered(null)).toBe(false);
  });
});

describe('phaseOf', () => {
  it('says what exists: nothing, a build, or a saved page', () => {
    expect(phaseOf(null)).toBe('reading');
    expect(phaseOf(state({ explanation: null }))).toBe('none');
    expect(phaseOf(state({ explanation: null, building: { state: 'building' } }))).toBe('building');
    expect(phaseOf(state())).toBe('saved');
    expect(phaseOf(state({ state: 'refused', reason: 'x' }))).toBe('unavailable');
  });
});

describe('Update', () => {
  it('is offered for a moved leaf that has work, and never for one that has none', () => {
    const moved = { from: 8, to: 9, blocks: 4 };
    expect(hasWork(state())).toBe(true);
    expect(updateOffered(state({ moved }))).toBe(true);
    // `moved-no-explainer`: no page exists, the view renews itself, and no control is offered.
    expect(updateOffered(state({ moved, explanation: null }))).toBe(false);
    expect(updateOffered(state({ moved: null }))).toBe(false);
  });

  it('names the moved comparison and its block count', () => {
    expect(movedLine({ from: 8, to: 9, blocks: 4 })).toBe('Task moved: #8 → #9 · 4 blocks changed');
    expect(movedLine({ from: null, to: 9, blocks: 1 })).toBe('Task moved: no number → #9 · 1 block changed');
  });
});

describe('the trusted stale list', () => {
  it('carries the parts and the true total, and an unreadable staleness claims nothing', () => {
    const parts = [{ id: 'look', title: 'Where to look first', reason: 'noteTurnEnded changed' }];
    expect(staleParts(state({ stale: { parts, total: 5 } }))).toEqual({ parts, total: 5, unavailable: null });
    expect(staleParts(state({ stale: { parts } })).total).toBe(1);
    expect(staleParts(state({ stale: { parts: [], unavailable: 'the snapshot is gone' } })).unavailable).toBe(
      'the snapshot is gone',
    );
    // No stale section at all is an empty list, not a guess.
    expect(staleParts(state({ stale: null }))).toEqual({ parts: [], total: 0, unavailable: null });
  });
});

describe('the registry of bases', () => {
  it('names the four MIK-R102 bases, inference marked as inference', () => {
    expect(BASIS_LEGEND.map((entry) => entry.basis)).toEqual([
      'recorded-fact',
      'mechanical-signal',
      'presenter-inference',
      'intended',
    ]);
    expect(basisLabel('presenter-inference')).toContain('✦');
    expect(basisLabel('intended')).toBe('intended');
  });
});

describe('Compare', () => {
  it('pairs the shown revision with the newest other one, and refuses a single revision', () => {
    const rows = [
      revision({ revision: 7, binding: binding('recorded', 7, '#7') }),
      revision({ revision: 8 }),
      revision({ revision: 9, binding: binding('recorded', 9, '#9') }),
    ];
    expect(defaultComparePair(rows, 8)).toEqual({ left: 9, right: 8 });
    expect(defaultComparePair(rows, null)).toEqual({ left: 8, right: 9 });
    expect(defaultComparePair([rows[1]], 8)).toBeNull();
  });
});

describe('clockOf', () => {
  it('shows the revision time in the reader’s own clock, and nothing for a missing time', () => {
    expect(clockOf('2026-10-19T12:36:00Z')).toMatch(/^\d{2}:\d{2}$/);
    expect(clockOf(null)).toBe('');
    expect(clockOf('not a time')).toBe('');
  });
});

describe('native serializer compatibility and revision ordering (MIK-R106 v2; MIK-R103 v2; MIK-R187)', () => {
  it('parses FrameBinding.model_dump() comparison_* fields and nested explainer identity', () => {
    const dumpedBinding = {
      comparison_owner: 'agents_remember.application.review_tree_comparison',
      comparison_number: 8,
      comparison_reference: 'review-comparison:task/leaf/8',
      task_id: 'task',
      leaf_id: 'leaf',
    };
    expect(readBinding(dumpedBinding, '#8')).toMatchObject({
      owner: 'recorded',
      number: 8,
      reference: 'review-comparison:task/leaf/8',
      label: '#8',
    });

    const plannedBinding = {
      comparison_owner: 'planned',
      comparison_number: 0,
      comparison_reference: 'review-comparison:task/leaf/planned',
    };
    expect(readBinding(plannedBinding, 'planned')).toMatchObject({
      owner: 'planned',
      number: 0,
      reference: 'review-comparison:task/leaf/planned',
      label: 'planned',
    });

    const parsedState = parseState({
      state: 'ok',
      binding: dumpedBinding,
      seat: { state: 'live' },
      retired: false,
      retiredAt: null,
      explainer: {
        agentId: 'agent-nested-1',
        workspace: '/tmp/wks-nested',
        generation: 3,
        present: true,
        binding: { binding: dumpedBinding },
        label: '#8',
      },
      stale: {
        parts: [{ id: 'look', title: 'Look', reason: 'changed' }],
        staleGrades: ['RLZ-1'],
        regeneration: {
          label: 'Regenerate stale (1 part · 1 grade)',
          message: 'Regenerate revision 8.',
          partCount: 1,
          gradeCount: 1,
        },
      },
    });
    expect(parsedState.binding?.owner).toBe('recorded');
    expect(parsedState.binding?.number).toBe(8);
    expect(parsedState.explainer).toMatchObject({
      agentId: 'agent-nested-1',
      workspace: '/tmp/wks-nested',
      workspaceId: '/tmp/wks-nested',
      generation: 3,
    });
    expect(parsedState.seat.agentId).toBe('agent-nested-1');
    expect(staleGradesOf(parsedState)).toEqual(['RLZ-1']);
    expect(staleRegenerationOf(parsedState, 8)).toEqual({
      label: 'Regenerate stale (1 part · 1 grade)',
      message: 'Regenerate revision 8.',
      partCount: 1,
      gradeCount: 1,
    });

    const parsedRevisions = parseRevisions({
      state: 'ok',
      pinned: 8,
      revisions: [
        {
          revision: 9,
          binding: dumpedBinding,
          label: '#8',
          at: '2026-10-19T13:10:00Z',
          kind: 'global',
          focus: 'Risks I see',
          agreed: false,
          pinned: false,
        },
        {
          revision: 8,
          binding: dumpedBinding,
          label: '#8',
          at: '2026-10-19T12:36:00Z',
          kind: 'global',
          focus: 'the change set of leaf',
          agreed: true,
          pinned: true,
        },
      ],
    });
    expect(parsedRevisions.revisions).toHaveLength(2);
    expect(parsedRevisions.revisions[0].kind).toBe('focused');
    expect(parsedRevisions.revisions[1].kind).toBe('global');
  });

  it('orders global revisions before focused revisions, selects global as default latest, and uses the selected row binding in standfirst', () => {
    const rows = [
      revision({ revision: 9, binding: binding('recorded', 9, '#9'), kind: 'focused', focus: 'Locking' }),
      revision({ revision: 7, binding: binding('recorded', 7, '#7'), kind: 'global' }),
      revision({ revision: 8, binding: binding('recorded', 8, '#8'), kind: 'global' }),
    ];
    const ordered = orderRevisions(rows);
    expect(ordered.map((r) => [r.revision, r.kind])).toEqual([
      [7, 'global'],
      [8, 'global'],
      [9, 'focused'],
    ]);
    expect(defaultShownRevision(ordered, state({ explanation: { revision: 9, at: null, entrypoint: null, comparison: null } }))).toBe(8);
    expect(standfirstText(state({ binding: binding('recorded', 9, '#9') }), 7, ordered)).toContain(
      'revision 7 · bound to #7 · global',
    );
    expect(EXPLAINER_FRAME_ID).toBe('explainer');
    expect(
      stateUrl(
        { repository: 'r', master: 'm', leaf: 'l' },
        { revision: 7, comparison: 'ref-7', comparisonNumber: 7, comparisonOwner: 'recorded' },
      ),
    ).toBe(
      '/api/explainer/r/m/l/state?revision=7&comparison=ref-7&comparisonNumber=7&comparisonOwner=recorded',
    );
  });
});
