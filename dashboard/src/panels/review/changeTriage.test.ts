// MIK-R33 triage over the store-authored comparison's served bodies (triage.capture-provenance.json):
// the same comparison mcp/tests/test_review_change_kinds.py asserts the badge facts of. These tests
// check that the tree orders, counts and merges the delivered facts and never decides one.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import type { ReviewFamilyContextEntry, ReviewResult } from '../../data/review';
import {
  breakdownText,
  familyTriage,
  mergeChangeKinds,
  orderFamilies,
  orderMemberRows,
} from './changeTriage';
import { mergeFamilyContinuation } from './familyWalkMerge';
import { memberRows } from './FamilyTree';

const captured = (name: string): ReviewResult =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as ReviewResult;

const family = captured('triage.family.captured.json');
const page = captured('triage.familyPage.captured.json');
const continued = captured('triage.familyContinued.captured.json');
const shared = captured('triage.shared.captured.json');

const labelled = (entries: ReviewFamilyContextEntry[]) =>
  entries.map((entry) => entry.display_label);
const only = (result: ReviewResult): ReviewFamilyContextEntry =>
  result.payload!.family_context!.entries.find((entry) => entry.display_label === 'FAM-F00001')!;
const invariants = (entry: ReviewFamilyContextEntry, order: 'triage' | 'authored') =>
  orderMemberRows(entry, memberRows(entry), order).map((row) => row.member.display_label);

describe('family triage', () => {
  it('counts the returned member occurrences by their delivered primary kind', () => {
    const triage = familyTriage(only(family))!;
    expect(triage.counts).toEqual({
      intent: 2,
      implementation: 3,
      membership: 0,
      unknown: 1,
      unchanged: 3,
    });
    expect(triage.guarantee).toBe('unchanged');
    expect(triage).toMatchObject({ returned: 9, total: 9, partial: false });
    // The highest member weight is intent, although the guarantee itself is unchanged.
    expect(triage.weight).toBe(4);
    expect(breakdownText(triage)).toBe('2 intent · 3 impl · 0 membership · 1 unknown of 9');
  });

  it('scopes a partial page to the members it returned and names the total', () => {
    const entry = only(page);
    const triage = familyTriage(entry)!;
    const returned = new Set(
      [...entry.before.members, ...entry.after.members].map((member) => member.member_id),
    );
    expect(triage.partial).toBe(true);
    expect(triage.returned).toBe(returned.size);
    expect(triage.returned).toBeLessThan(9);
    expect(breakdownText(triage)).toMatch(
      new RegExp(`of ${returned.size} returned \\(9 total\\)$`),
    );
    // A family with unreturned members sorts no lower than one holding an unknown member.
    expect(triage.weight).toBeGreaterThanOrEqual(1);
  });

  it('scopes a complete-looking family whose total is unknown (SYNTHETIC: no total)', () => {
    const entry = only(family);
    expect([entry.before, entry.after].every((side) => side.page?.complete)).toBe(true);
    const unknownTotal = {
      ...entry,
      change_kinds: { ...entry.change_kinds!, members_total: undefined },
    };
    const triage = familyTriage(unknownTotal)!;
    expect(breakdownText(triage)).toBe(
      '2 intent · 3 impl · 0 membership · 1 unknown of 9 returned (total unknown)',
    );
    // SYNTHETIC: with every member unchanged, the unknown total alone weighs the family unknown.
    const quiet = {
      ...unknownTotal,
      change_kinds: {
        ...unknownTotal.change_kinds,
        guarantee: 'unchanged' as const,
        members: unknownTotal.change_kinds.members.map((one) => ({
          ...one,
          primary: 'unchanged' as const,
          marks: [],
        })),
      },
    };
    expect(familyTriage(quiet)!.weight).toBe(1);
  });

  it('weighs a family by its own guarantee (SYNTHETIC: every member unchanged)', () => {
    const entry = only(family);
    const members = entry.change_kinds!.members.map((one) => ({
      ...one,
      primary: 'unchanged' as const,
      marks: [],
    }));
    const revised = {
      ...entry,
      change_kinds: { ...entry.change_kinds!, members, guarantee: 'intent' as const },
    };
    const quiet = { ...entry, change_kinds: { ...entry.change_kinds!, members } };
    expect(familyTriage(revised)!.weight).toBe(4);
    expect(familyTriage(quiet)!.weight).toBe(0);
  });

  it('says the total is unknown rather than inventing one', () => {
    const entry = only(page);
    const kinds = { ...entry.change_kinds!, members_total: undefined };
    expect(breakdownText(familyTriage({ ...entry, change_kinds: kinds })!)).toMatch(
      /returned \(total unknown\)$/,
    );
  });
});

describe('tree order', () => {
  it('lists changes first by weight then authored position, keeping every sibling', () => {
    // INV-AAAAAA is revised, so it has two rows (one per revision); they stay together.
    expect(invariants(only(family), 'triage')).toEqual([
      'INV-AAAAAA',
      'INV-AAAAAA',
      'INV-HHHHHH',
      'INV-BBBBBB',
      'INV-CCCCCC',
      'INV-DDDDDD',
      'INV-GGGGGG',
      'INV-EEEEEE',
      'INV-FFFFFF',
      'INV-KKKKKK',
    ]);
  });

  it("switches to pure authored order: the family record's members list", () => {
    expect(invariants(only(family), 'authored')).toEqual([
      'INV-AAAAAA',
      'INV-AAAAAA',
      'INV-BBBBBB',
      'INV-CCCCCC',
      'INV-DDDDDD',
      'INV-EEEEEE',
      'INV-FFFFFF',
      'INV-GGGGGG',
      'INV-KKKKKK',
      'INV-HHHHHH',
    ]);
  });

  it('orders families by their highest member or guarantee weight, then context order', () => {
    const entries = shared.payload!.family_context!.entries;
    expect(labelled(entries)).toEqual(['FAM-F00001', 'FAM-F00002']);
    // Both weigh intent (a revised member; a revised guarantee): the context order stands.
    expect(labelled(orderFamilies(entries, 'triage'))).toEqual(['FAM-F00001', 'FAM-F00002']);
    // SYNTHETIC: the first family's facts made all-unchanged, so the revised guarantee leads.
    const [first, second] = entries;
    const quiet: ReviewFamilyContextEntry = {
      ...first,
      change_kinds: {
        ...first.change_kinds!,
        members: first.change_kinds!.members.map((member) => ({
          ...member,
          primary: 'unchanged',
          marks: [],
        })),
      },
    };
    expect(labelled(orderFamilies([quiet, second], 'triage'))).toEqual([
      'FAM-F00002',
      'FAM-F00001',
    ]);
    expect(labelled(orderFamilies([quiet, second], 'authored'))).toEqual([
      'FAM-F00001',
      'FAM-F00002',
    ]);
  });

  it('sorts a partial family above a complete unchanged one (SYNTHETIC facts)', () => {
    const unchanged = (entry: ReviewFamilyContextEntry): ReviewFamilyContextEntry => ({
      ...entry,
      change_kinds: {
        ...entry.change_kinds!,
        members: entry.change_kinds!.members.map((member) => ({
          ...member,
          primary: 'unchanged',
          marks: [],
        })),
      },
    });
    const complete = unchanged(only(family));
    const partial = { ...unchanged(only(page)), family_id: 'partial' };
    expect(familyTriage(complete)!.weight).toBe(0);
    expect(familyTriage(partial)!.weight).toBe(1);
    expect(orderFamilies([complete, partial], 'triage').map((entry) => entry.family_id)).toEqual([
      'partial',
      complete.family_id,
    ]);
  });

  it('leaves a dataset review (no change facts) in its landed order', () => {
    const dataset = only(family);
    const bare = { ...dataset, change_kinds: undefined };
    const rows = memberRows(bare);
    expect(orderMemberRows(bare, rows, 'triage')).toEqual(rows);
    expect(orderFamilies([bare], 'triage')).toEqual([bare]);
  });
});

describe('roster walk', () => {
  it('keeps the facts of every member the walk has returned', () => {
    const first = only(page);
    const cursor = first.before.page!.continuation!;
    const merged = mergeFamilyContinuation(page.payload!, continued.payload!, cursor);
    expect(merged).not.toBeNull();
    const entry = merged!.family_context!.entries.find((one) => one.family_id === first.family_id)!;
    const described = new Set(entry.change_kinds!.members.map((member) => member.member_id));
    const returned = new Set(
      [...entry.before.members, ...entry.after.members].map((member) => member.member_id),
    );
    expect(described).toEqual(returned);
    expect(familyTriage(entry)!.returned).toBeGreaterThan(familyTriage(first)!.returned);
  });

  it('unions two deliveries and applies the newer family facts', () => {
    const a = only(page).change_kinds!;
    // SYNTHETIC: the later delivery lacks one member the earlier one described, and says the
    // total is unknown -- the member's facts are kept, the newer family facts apply.
    const kept = a.members[0];
    const b = {
      ...only(continued).change_kinds!,
      members_total: undefined,
      members: only(continued).change_kinds!.members.filter(
        (member) => member.member_id !== kept.member_id,
      ),
    };
    const merged = mergeChangeKinds(a, b)!;
    const ids = new Set([...a.members, ...b.members].map((member) => member.member_id));
    expect(new Set(merged.members.map((member) => member.member_id))).toEqual(ids);
    expect(merged.members).toContainEqual(kept);
    expect(merged.members_total).toBeUndefined();
    expect(mergeChangeKinds(undefined, undefined)).toBeUndefined();
    expect(mergeChangeKinds(a, undefined)).toBe(a);
  });
});
