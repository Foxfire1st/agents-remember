// The column's pure rules (MIK-R106 v2 rules 1–5, 7 and 8): which chat it selects, the
// placeholder's words, the form's fixed target and references, the build's steps and percentages,
// the later request's change-set pick, Retire's two lists, and the header's menu.
import { describe, expect, it } from 'vitest';

import type { ExplainerBuild, ExplainerState } from './client';
import type { TrayPick, TraySeat } from '../tray/types';
import {
  agentMenu,
  bindingWords,
  buildFailure,
  buildPercent,
  canRetire,
  canSend,
  changeSetPick,
  columnSelection,
  firstRequestLine,
  fixedTarget,
  formReferences,
  phaseOf,
  phaseWords,
  placeholderWords,
  retireConfirmation,
  sendAimedAtArchived,
  stalePrefill,
  stepDetail,
  stepPercent,
} from './model';

const state = (patch: Partial<ExplainerState> = {}): ExplainerState => ({
  seat: null,
  agentId: null,
  workspaceId: null,
  generation: 0,
  retiredAt: null,
  binding: null,
  build: null,
  staleParts: [],
  ...patch,
});

const pick = (id: string, title: string, note = ''): TrayPick => ({
  id,
  key: id,
  addedAt: '2026-10-19T12:00:00Z',
  input: {
    kind: 'reference',
    title,
    reference: { kind: 'invariant', repository: 'repo', id: title, memoryTree: 'mem' },
  },
  note,
});

const build = (steps: ExplainerBuild['steps'], patch: Partial<ExplainerBuild> = {}): ExplainerBuild => ({
  attempt: 'a1',
  state: 'running',
  startedAt: '2026-10-19T14:31:00Z',
  steps,
  failedStep: null,
  reason: null,
  ...patch,
});

describe('the column selects the explainer or its placeholder (rule 1)', () => {
  it('selects the placeholder while no seat exists, never the Worker', () => {
    const phase = phaseOf({ kind: 'state', value: state() }, false);
    expect(phase).toBe('placeholder');
    expect(columnSelection(phase)).toBe('placeholder');
  });

  it('selects the explainer for a live, a closed, a missing and a building seat', () => {
    expect(phaseOf({ kind: 'state', value: state({ seat: 'live' }) }, false)).toBe('live');
    expect(columnSelection('live')).toBe('explainer');
    expect(phaseOf({ kind: 'state', value: state({ seat: 'closed' }) }, false)).toBe('closed');
    expect(columnSelection('closed')).toBe('explainer');
    expect(
      phaseOf({ kind: 'state', value: state({ seat: 'missing' }) }, false),
    ).toBe('placeholder');
    expect(phaseOf({ kind: 'state', value: state({ build: build([]) }) }, false)).toBe('building');
    expect(columnSelection('building')).toBe('explainer');
  });

  it('gives the form the column while he has it open, and the placeholder after Retire', () => {
    const live = { kind: 'state' as const, value: state({ seat: 'live' }) };
    expect(phaseOf(live, true)).toBe('first-request');
    expect(columnSelection('first-request')).toBe('placeholder');
    const retired = { kind: 'state' as const, value: state({ retiredAt: '2026-10-19T15:12:00Z' }) };
    expect(phaseOf(retired, false)).toBe('retired');
    expect(columnSelection('retired')).toBe('placeholder');
  });

  it('shows the placeholder, not the Worker, when the state cannot be read', () => {
    expect(phaseOf({ kind: 'unavailable', detail: 'the host is unreachable' }, false)).toBe('placeholder');
    expect(phaseOf(null, false)).toBe('placeholder');
  });

  it('names the status in the mockup’s words', () => {
    expect(phaseWords('placeholder', state())).toBe('not started');
    expect(phaseWords('first-request', state())).toBe('new request · not sent');
    expect(phaseWords('building', state())).toBe('starting');
    expect(phaseWords('live', state({ seat: 'live' }))).toBe('live · idle');
    expect(phaseWords('closed', state({ seat: 'closed' }))).toBe('closed · resumes when shown');
  });
});

describe('the placeholder (rule 2)', () => {
  it('fixes the role, names the settings role and says nothing runs until he asks', () => {
    const words = placeholderWords(state());
    expect(words.role).toBe('Explainer');
    expect(words.settingsRole).toBe('review-presenter');
    expect(words.line).toBe('Nothing runs and no agent exists until you ask.');
    expect(words.headline).toBe('No explainer for this task yet.');
    expect(words.retiredLine).toBeNull();
  });

  it('says when a retired explainer ended, and that nothing restarts by itself', () => {
    const words = placeholderWords(state({ retiredAt: '2026-10-19T15:12:00Z' }));
    expect(words.headline).toMatch(/^The explainer was retired at \d\d:\d\d\.$/);
    expect(words.retiredLine).toContain('Press ✦ Explain');
    const untimed = placeholderWords(state({ retiredAt: '' }));
    expect(untimed.headline).toBe('The explainer was retired.');
  });
});

describe('the first request’s form (rule 3)', () => {
  it('names the fixed target', () => {
    expect(fixedTarget('L181')).toBe('a new explainer for L181');
  });

  it('names a planning leaf’s binding as planned · no changes', () => {
    expect(bindingWords({ kind: 'planned', comparison: 0, label: 'planned' })).toBe('planned · no changes');
    expect(bindingWords({ kind: 'recorded', comparison: 8, label: '#8' })).toBe('#8');
    expect(bindingWords(null)).toBe('the comparison on screen');
  });

  it('lists the tray’s references with their notes, and counts what will go', () => {
    const references = formReferences([pick('p1', 'INV-1ZKP4R', 'start here'), pick('p2', 'RLZ-78JFRR')], () => 'invariant');
    expect(references.map((reference) => reference.id)).toEqual(['p1', 'p2']);
    expect(references[0]).toMatchObject({ title: 'INV-1ZKP4R', detail: 'invariant', note: 'start here' });
    expect(firstRequestLine(references, 'Explain it')).toBe(
      "One message: 2 references the agent opens with AR's tools, 1 note, your text.",
    );
  });

  it('refuses Send with nothing to send', () => {
    const references = formReferences([pick('p1', 'INV-1')], () => 'invariant');
    expect(canSend(references, '')).toBe(true);
    expect(canSend([], '  ')).toBe(false);
    expect(canSend([], 'what changed?')).toBe(true);
  });

  it('pre-fills the stale parts of a Regenerate with no seat', () => {
    const stale = state({ staleParts: [{ id: 'part-3', title: 'Part 3 · the seat’s return', reason: 'moved' }] });
    expect(stalePrefill(stale).map((part) => part.id)).toEqual(['part-3']);
    expect(stalePrefill(null)).toEqual([]);
  });

  it('opens the form for a Send aimed at an archived explainer', () => {
    expect(sendAimedAtArchived(state({ seat: null, retiredAt: '2026-10-19T15:12:00Z' }))).toBe(true);
    expect(sendAimedAtArchived(state({ seat: 'missing' }))).toBe(true);
    expect(sendAimedAtArchived(state({ seat: 'live' }))).toBe(false);
    expect(sendAimedAtArchived(state({ seat: 'closed' }))).toBe(false);
  });
});

describe('the build’s panel (rule 4.1)', () => {
  const steps: ExplainerBuild['steps'] = [
    { name: 'Create the explainer’s workspace', state: 'done' },
    {
      name: 'Copy the code snapshot, read-only',
      state: 'running',
      counts: { kind: 'files', done: 2050, total: 4100 },
      detail: '≈ 4,100 files · 95 MiB',
    },
    { name: 'Start the explainer in Paseo', state: 'waiting' },
  ];

  it('gives a step that counts files or bytes its own percentage', () => {
    expect(stepPercent(steps[0])).toBeNull();
    expect(stepPercent(steps[1])).toBe(50);
    expect(stepDetail(steps[1])).toBe('2,050 of 4,100 files · ≈ 4,100 files · 95 MiB · 50%');
  });

  it('averages the steps into the build’s percentage', () => {
    expect(buildPercent(build(steps))).toBe(50);
  });

  it('names the failed step and its reason, and no other build has one', () => {
    expect(buildFailure(build(steps))).toBeNull();
    const failed = build(
      [
        { name: 'Create the explainer’s workspace', state: 'done' },
        { name: 'Copy the memory snapshot, read-only', state: 'failed', reason: 'no space left on device' },
        { name: 'Start the explainer in Paseo', state: 'waiting' },
      ],
      { state: 'failed', failedStep: 'Copy the memory snapshot, read-only', reason: 'no space left on device' },
    );
    expect(buildFailure(failed)).toEqual({
      step: 'Copy the memory snapshot, read-only',
      reason: 'no space left on device',
    });
  });
});

describe('later requests (rule 5)', () => {
  it('adds the change-set reference, which only his Send sends', () => {
    const added = changeSetPick({ repository: 'repo', master: 'master', leaf: 'L181' }, 'Change set · L181', 8);
    expect(added.kind).toBe('reference');
    if (added.kind !== 'reference' || added.reference.kind !== 'change-set') throw new Error('not a change set');
    expect(added.reference.comparison.number).toBe(8);
    expect(added.reference.comparison.pinRef).toBe('refs/ar/review/master/L181/8');
  });
});

describe('Retire (rule 7)', () => {
  it('names what ends and what stays', () => {
    const words = retireConfirmation('L181', state({ seat: 'live', workspaceId: '/coord/explainer/repo/L181' }));
    expect(words.headline).toBe('Retire the explainer of L181?');
    expect(words.ends).toHaveLength(3);
    expect(words.ends[0]).toContain('The seat');
    expect(words.ends[1]).toContain('AR_EXPLAINER_WORKSPACE');
    expect(words.ends[2]).toContain('/coord/explainer/repo/L181');
    expect(words.stays).toHaveLength(2);
    expect(words.stays[0]).toContain('from a retired explainer');
    expect(words.stays[1]).toContain('grades');
  });

  it('offers Retire only while a seat exists', () => {
    expect(canRetire(state({ seat: 'live' }))).toBe(true);
    expect(canRetire(state({ seat: 'closed' }))).toBe(true);
    expect(canRetire(state({ retiredAt: '2026-10-19T15:12:00Z' }))).toBe(false);
    expect(canRetire(null)).toBe(false);
  });
});

describe('the header’s agent menu (rule 1)', () => {
  const seats: TraySeat[] = [
    { agentId: 'a-exp', role: 'explainer', task: { repository: 'repo', path: 'm/L.json', id: 'L181', title: 'L181' }, createdAt: '2026-10-19' },
    { agentId: 'a-wrk', role: 'worker', task: { repository: 'repo', path: 'm/L.json', id: 'L181', title: 'L181' }, createdAt: '2026-10-19' },
    { agentId: 'a-other', role: 'worker', task: { repository: 'repo', path: 'm/other.json', id: 'L118', title: 'L118' }, createdAt: '2026-10-19' },
  ];

  it('lists the task’s chats only, with the explainer first and current', () => {
    const rows = agentMenu(seats, 'L181', { agentId: 'a-exp', live: true });
    expect(rows.map((row) => row.agentId)).toEqual(['a-exp', 'a-wrk']);
    expect(rows[0]).toMatchObject({ role: 'Explainer', current: true, state: 'live' });
    expect(rows[1]).toMatchObject({ role: 'Worker', current: false });
  });

  it('lists the task’s other chats while no explainer exists, and never another task’s', () => {
    const rows = agentMenu(seats, 'L181', { agentId: null, live: false });
    expect(rows.map((row) => row.agentId)).toEqual(['a-wrk']);
  });

  it('filters by exact repository, master and task path, excluding foreign seats with the same short id', () => {
    const scopedSeats: TraySeat[] = [
      { agentId: 'a-wrk-own', workspaceId: 'wks-own', role: 'worker', task: { repository: 'repo', path: 'master/L181.json', id: 'L181', title: 'L181' }, createdAt: '2026-10-19' },
      { agentId: 'a-rev-own', workspaceId: 'wks-rev', role: 'reviewer', task: { repository: 'repo', path: 'master/L181.json', id: 'L181', title: 'L181' }, createdAt: '2026-10-19' },
      { agentId: 'a-foreign-repo', workspaceId: 'wks-fr', role: 'worker', task: { repository: 'other-repo', path: 'master/L181.json', id: 'L181', title: 'L181' }, createdAt: '2026-10-19' },
      { agentId: 'a-foreign-master', workspaceId: 'wks-fm', role: 'worker', task: { repository: 'repo', path: 'other-master/L181.json', id: 'L181', title: 'L181' }, createdAt: '2026-10-19' },
    ];
    const rows = agentMenu(
      scopedSeats,
      'L181',
      { agentId: 'a-exp', live: true },
      { repository: 'repo', master: 'master', leaf: 'L181', selectedAgentId: 'a-wrk-own' },
    );
    expect(rows.map((row) => row.agentId)).toEqual(['a-exp', 'a-wrk-own', 'a-rev-own']);
    expect(rows.find((row) => row.agentId === 'a-wrk-own')?.current).toBe(true);
    expect(rows.find((row) => row.agentId === 'a-exp')?.current).toBe(false);
  });
});
