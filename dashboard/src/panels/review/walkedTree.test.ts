// The walked tree's folding (MIK-R39), pure: what each answer adds, in which order, and when the walk
// starts again. The bodies are served answers (walkReal.* and walkStore.*, with their provenance).
import { expect, it } from 'vitest';
import type { ReviewResult } from '../../data/review';
import {
  MOVED_NOTICE,
  advanceWalk,
  keptFamilies,
  subjectTitle,
  type TreeIntent,
  type Walk,
} from './walkedTree';
import { captured } from './walk.test-utils';

const real = (name: string) => captured<ReviewResult>(`walkReal.${name}.captured.json`).payload!;
const store = (name: string) => captured<ReviewResult>(`walkStore.${name}.captured.json`).payload!;
const SCOPE = 'agents-remember/master/leaf/live';
const keep = (id: number): TreeIntent => ({ id, keep: true });
const fresh = (id: number): TreeIntent => ({ id, keep: false });
const EMPTY: Walk = {
  scope: '',
  intent: 0,
  generation: null,
  source: null,
  families: [],
  current: new Set(),
  replaceNext: false,
  notice: null,
};
const START: Walk = advanceWalk(EMPTY, real('FAM-R6R095RW'), SCOPE, fresh(0));
const ids = (walk: Walk) => walk.families.map((family) => family.entry.family_id);
const members = (walk: Walk, familyId: string) =>
  walk.families
    .find((family) => family.entry.family_id === familyId)!
    .entry.before.members.map((member) => member.member_id);

it('is idempotent: the same answer folded twice changes nothing', () => {
  const again = advanceWalk(START, real('FAM-R6R095RW'), SCOPE, fresh(0));
  expect(again.families).toEqual(START.families);
  const same = real('FAM-R6R095RW');
  const once = advanceWalk(START, same, SCOPE, keep(1));
  expect(advanceWalk(once, same, SCOPE, keep(1))).toBe(once);
});

it('orders families by identifier whatever the arrival order, and tags only the families of no current context', () => {
  const second = advanceWalk(START, real('FAM-2HBJREC2'), SCOPE, keep(1));
  expect(ids(second)).toEqual([...ids(second)].sort());
  expect(second.families).toHaveLength(2);
  expect(keptFamilies(second).map((family) => family.entry.display_label)).toEqual([
    'Coherent intent and source review',
  ]);
  expect(keptFamilies(second)[0].readFor).toEqual({
    kind: 'family',
    id: real('FAM-R6R095RW').knowledge.revision_selection!.record_id,
  });
  // The other arrival order gives the same list: FAM-2HBJREC2 arrives first and is listed second.
  const reversed = advanceWalk(
    advanceWalk(EMPTY, real('FAM-2HBJREC2'), SCOPE, fresh(0)),
    real('INV-2TQGXFAX'),
    SCOPE,
    keep(1),
  );
  expect(ids(reversed)).toEqual(ids(second));
  expect(reversed.families[0].entry.display_label).toBe('Coherent intent and source review');
});

it('keeps the answer a kept family came from, whole', () => {
  const answer = real('INV-2TQGXFAX');
  const both = advanceWalk(START, answer, SCOPE, keep(1));
  const next = real('FAM-2HBJREC2');
  const walked = advanceWalk(both, next, SCOPE, keep(1));
  const [kept] = keptFamilies(walked);
  expect(kept.answer).toBe(answer);
  expect(kept.answer.knowledge.revision_selection?.record_id).toBe(kept.readFor?.id);
  // The selected subject's own family carries the answer it was read from.
  expect(walked.families.find((one) => one.entry.family_id !== kept.entry.family_id)!.answer).toBe(
    next,
  );
});

it('names a subject as its answer does, and one the answer does not carry by its identifier read as words', () => {
  const answer = real('INV-2TQGXFAX');
  const subject = answer.knowledge.revision_selection!;
  expect(subjectTitle(answer, { kind: 'invariant', id: subject.record_id })).toBe('INV-2TQGXFAX');
  const family = answer.family_context!.entries[0];
  expect(subjectTitle(answer, { kind: 'family', id: family.family_id })).toBe(
    'Coherent intent and source review',
  );
  // No real answer lacks its own subject; the last resort is still never a bare identifier.
  expect(subjectTitle(answer, { kind: 'invariant', id: 'no-such-record' })).toBe('no such record');
});

it('starts afresh at an outside selection at once, and the next answer replaces what is left', () => {
  const both = advanceWalk(START, real('INV-2TQGXFAX'), SCOPE, keep(1));
  expect(ids(both)).toHaveLength(2);
  const outside = advanceWalk(both, real('INV-2TQGXFAX'), SCOPE, fresh(2));
  expect(ids(outside)).toHaveLength(2);
  expect(keptFamilies(outside)).toEqual([]);
  const answered = advanceWalk(outside, real('INV-ZS9ZS878'), SCOPE, fresh(2));
  expect(ids(answered)).toEqual([ids(START)[0]]);
  // ... and afterwards the same selection's later answers are folded in.
  const later = advanceWalk(answered, real('FAM-2HBJREC2'), SCOPE, fresh(2));
  expect(ids(later)).toHaveLength(2);
});

it('starts afresh for another comparison and says so only when it dropped kept rows', () => {
  const both = advanceWalk(
    advanceWalk(START, real('INV-2TQGXFAX'), SCOPE, keep(1)),
    real('FAM-2HBJREC2'),
    SCOPE,
    keep(1),
  );
  expect(keptFamilies(both)).toHaveLength(1);
  const other = structuredClone(real('INV-ZS9ZS878'));
  other.source.inventory.after_code_tree_id = '0'.repeat(40);
  const moved = advanceWalk(both, other, SCOPE, keep(1));
  expect(ids(moved)).toHaveLength(1);
  expect(moved.notice).toBe(MOVED_NOTICE);
  expect(advanceWalk(START, other, SCOPE, keep(1)).notice).toBeNull();
});

it('keeps every member and the furthest cursor of a family read twice, in either order', () => {
  const shared = store('sharedPage');
  const continued = store('FAM-F00001Continued');
  const a = advanceWalk(
    advanceWalk({ ...START, scope: '' }, shared, SCOPE, fresh(0)),
    continued,
    SCOPE,
    keep(1),
  );
  const b = advanceWalk(
    advanceWalk({ ...START, scope: '' }, continued, SCOPE, fresh(0)),
    shared,
    SCOPE,
    keep(1),
  );
  const family = (walk: Walk) =>
    walk.families.find((one) => one.entry.display_label === 'FAM-F00001')!.entry;
  for (const walk of [a, b]) {
    const entry = family(walk);
    expect(new Set(members(walk, entry.family_id)).size).toBeGreaterThan(2);
    expect(entry.before.page?.continuation).toBe(
      continued.family_context!.entries[0].before.page?.continuation,
    );
    expect(entry.change_kinds!.members.length).toBe(4);
  }
  expect(new Set(members(a, family(a).family_id))).toEqual(
    new Set(members(b, family(b).family_id)),
  );
});
