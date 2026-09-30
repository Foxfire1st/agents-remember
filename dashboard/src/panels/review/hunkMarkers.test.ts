// MIK-R34: what each hunk's intent marker names, and where it sits. Every body is a REAL served
// per-file classification (hunkMarkers.capture-provenance.json): MIK-L32's lane fixture world -- real
// code and memory Git trees, reopened as the reviewer reopens a comparison -- in six scenarios.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import type { ReviewFileClassification } from '../../data/reviewLane';
import { fileMarks, type HunkMark, markAnchor, occurrenceLabel, wholeSide } from './hunkMarkers';

type Scenario =
  'precuration' | 'curated' | 'before_unread' | 'after_unread' | 'both_unread' | 'partial';
const bodies = JSON.parse(
  readFileSync(
    path.join(
      path.dirname(new URL(import.meta.url).pathname),
      'hunkMarkers.classifier.captured.json',
    ),
    'utf8',
  ),
) as Record<Scenario, Record<string, { file_classification: ReviewFileClassification }>>;
const classified = (scenario: Scenario, file: string) => bodies[scenario][file].file_classification;

function hunks(file: ReviewFileClassification): HunkMark[] {
  const marks = fileMarks(file);
  if (marks.kind !== 'hunks') throw new Error(`expected per-hunk marks, got ${marks.kind}`);
  return marks.hunks;
}

const occurrences = (mark: HunkMark, invariant: string) =>
  [...mark.realizations, ...mark.proofs]
    .find((entry) => entry.invariant === invariant)!
    .occurrences.map((one) => [occurrenceLabel(one), one.sides.join('+')]);

describe('what each hunk names', () => {
  it('names every intersecting invariant of a replace hunk, with each family occurrence', () => {
    // pkg/lines.txt after curation: line 2 replaced inside two recorded ranges, line 4 replaced where
    // the after side supplies no range, line 6 deleted where no range lies.
    const [two, four, six] = hunks(classified('curated', 'pkg/lines.txt'));
    expect([two.label, four.label, six.label]).toEqual([
      '2 intents',
      'attribution unknown',
      'unexplained',
    ]);
    expect(two.realizations.map((entry) => entry.invariant)).toEqual(['INV-AAAAAA', 'INV-BBBBBB']);
    // A shared invariant is listed once, under each family that records it on the before side.
    expect(occurrences(two, 'INV-AAAAAA')).toEqual([
      ['FAM-F00001 r1 · member', 'before'],
      ['FAM-F00002 r1 · removed or reassigned in after', 'before'],
      ['FAM-F00003 r1 · before-only', 'before'],
    ]);
    expect(occurrences(two, 'INV-BBBBBB')).toEqual([['No recorded family', 'before']]);
    expect(four.unknown.map((one) => one.side)).toEqual(['after']);
    expect([six.realizations, six.unknown]).toEqual([[], []]);
  });

  it('selects the member row of the named family, or the invariant alone without one', () => {
    const [two] = hunks(classified('curated', 'pkg/lines.txt'));
    const link = classified('curated', 'pkg/lines.txt').hunks[0].links[0];
    const [member] = two.realizations[0].occurrences;
    expect(member.target).toEqual({
      invariant: 'INV-AAAAAA',
      invariantKey: link.invariant_key,
      familyKey: link.families[0].family_key,
      memberRevisionKey: link.invariant_revision_key,
      family: 'FAM-F00001',
      state: 'member',
    });
    const noFamily = two.realizations[1].occurrences[0];
    expect(noFamily.target).toEqual({
      invariant: 'INV-BBBBBB',
      invariantKey: classified('curated', 'pkg/lines.txt').hunks[0].links[1].invariant_key,
      state: 'confirmed_no_family',
    });
  });

  it('merges one revision recorded on both sides and names an insertion-only hunk', () => {
    const [edit, insertion] = hunks(classified('curated', 'pkg/a.py'));
    // RLZ-A00001 links the edit on both sides; FAM-F00001 records the same revision on both.
    expect(edit.label).toBe('INV-AAAAAA');
    expect(occurrences(edit, 'INV-AAAAAA')[0]).toEqual(['FAM-F00001 r1 · member', 'before+after']);
    expect([insertion.before.count, insertion.after]).toEqual([0, { start: 7, count: 4 }]);
    expect([insertion.label, occurrences(insertion, 'INV-CCCCCC')]).toEqual([
      'INV-CCCCCC',
      [['No recorded family', 'after']],
    ]);
  });

  it('lists a proof entry as a test with its facet, never counted with realizations', () => {
    const [mark] = hunks(classified('precuration', 'tests/test_a.py'));
    expect([mark.label, mark.compact]).toEqual(['INV-AAAAAA · test', '1t']);
    expect(mark.realizations).toEqual([]);
    expect(mark.proofs.map((entry) => [entry.kind, entry.entryIds, entry.facets])).toEqual([
      ['proof', ['PRF-A00004'], ['land returns its value.']],
    ]);
  });

  it('names unknown membership, with and without a family', () => {
    // The after memory tree cannot be read: before-side memberships are unknown, never guessed.
    const [edit] = hunks(classified('after_unread', 'pkg/a.py'));
    expect(occurrences(edit, 'INV-AAAAAA').map(([label]) => label)).toEqual([
      'FAM-F00001 r1 · membership unknown',
      'FAM-F00002 r1 · membership unknown',
      'FAM-F00003 r1 · membership unknown',
    ]);
    // A before-side family record did not parse: no family can be confirmed or named.
    const [two] = hunks(classified('partial', 'pkg/lines.txt'));
    expect(occurrences(two, 'INV-BBBBBB')).toEqual([['Attribution unknown', 'before']]);
    expect(two.realizations[0].occurrences[0].target.familyKey).toBeUndefined();
  });

  it('carries the reason an unknown membership is not established to its target', () => {
    // A named family: the before-side record is compared with an after side that was not read.
    const [edit] = hunks(classified('after_unread', 'pkg/a.py'));
    const [named] = edit.realizations[0].occurrences;
    expect([named.target.state, named.target.family]).toEqual(['membership_unknown', 'FAM-F00001']);
    expect(named.target.reason).toMatch(
      /^the after knowledge could not be read \(.+\), so whether FAM-F00001 still lists INV-AAAAAA there is not established$/,
    );
    // No family named: a family record of the link's own side did not parse.
    const [two] = hunks(classified('partial', 'pkg/lines.txt'));
    expect(two.realizations[0].occurrences[0].target.reason).toBe(
      'not every family record of the before knowledge could be read, so whether a family lists ' +
        'INV-BBBBBB on that side is not established',
    );
    // A confirmed membership, or a confirmed absence, carries no reason.
    const [shared] = hunks(classified('curated', 'pkg/lines.txt'));
    expect(
      [...shared.realizations[0].occurrences, ...shared.realizations[1].occurrences].map(
        (one) => one.target.reason,
      ),
    ).toEqual([undefined, undefined, undefined, undefined]);
  });

  it("keeps the readable side's links and names the unread side's changed lines unknown", () => {
    const file = classified('before_unread', 'pkg/a.py');
    const [edit, insertion] = hunks(file);
    expect([edit.label, edit.compact]).toEqual(['1 intent · unknown', '1?']);
    expect(edit.unknown).toEqual([
      { side: 'before', detail: expect.stringContaining('the before knowledge is unavailable') },
    ]);
    // The insertion changes no before line, so the unread before side leaves it untouched.
    expect([insertion.label, insertion.unknown]).toEqual(['INV-CCCCCC', []]);
    // Its hunks of unknown attribution keep their own marks.
    expect(hunks(classified('before_unread', 'pkg/lines.txt')).map((one) => one.label)).toEqual([
      'attribution unknown',
      'attribution unknown',
      'attribution unknown',
    ]);
  });

  it('gives a confirmed-unregistered file, or one no side of which is readable, one file mark', () => {
    expect(fileMarks(classified('precuration', 'pkg/new.py'))).toMatchObject({
      kind: 'file',
      tone: 'unexplained',
    });
    expect(fileMarks(classified('both_unread', 'pkg/a.py'))).toMatchObject({
      kind: 'file',
      tone: 'attribution_unknown',
    });
    // A file of unknown attribution with readable sides keeps one mark per hunk.
    expect(hunks(classified('precuration', 'pkg/stale.py')).map((one) => one.label)).toEqual([
      'attribution unknown',
    ]);
  });
});

describe('where each mark sits', () => {
  const lines = hunks(classified('curated', 'pkg/lines.txt'));
  const whole = { before: wholeSide(6), after: wholeSide(5) };

  it("places each mark on the owner's side lines, a deletion on the before side", () => {
    expect(lines.map((mark) => markAnchor(mark, 'split', whole))).toEqual([
      { side: 'after', line: 2 },
      { side: 'after', line: 4 },
      { side: 'before', line: 6 },
    ]);
  });

  it('places an inline deletion on the after line below its removed lines, or the last one', () => {
    // Mid-file (the real notes.py deletion of line 210, after none (after L209)): below, on L210.
    const notes = JSON.parse(
      readFileSync(
        path.join(
          path.dirname(new URL(import.meta.url).pathname),
          'markerReturn.file.captured.json',
        ),
        'utf8',
      ),
    ) as { file_classification: ReviewFileClassification };
    const deletion = hunks(notes.file_classification)[1];
    expect([deletion.before, deletion.after]).toEqual([
      { start: 210, count: 1 },
      { start: 209, count: 0 },
    ]);
    const long = { before: wholeSide(400), after: wholeSide(400) };
    expect(markAnchor(deletion, 'inline', long)).toEqual({ side: 'after', line: 210 });
    expect(markAnchor(deletion, 'split', long)).toEqual({ side: 'before', line: 210 });
    // At the end of the file there is no line below: the last one.
    expect(markAnchor(lines[2], 'inline', whole)).toEqual({ side: 'after', line: 5 });
    const [, insertion] = hunks(classified('curated', 'pkg/a.py'));
    expect(markAnchor(insertion, 'inline', { before: wholeSide(6), after: wholeSide(10) })).toEqual(
      { side: 'after', line: 7 },
    );
  });

  it('marks every hunk a window draws, a neighbour shown as context too', () => {
    // The lane's window for the replace at line 4 (three context lines): before L1-6, after L1-5. It
    // draws the line 2 replace and the line 6 delete as context -- each keeps its own mark.
    const window = { before: { first: 1, last: 6 }, after: { first: 1, last: 5 } };
    expect(lines.map((mark) => markAnchor(mark, 'split', window)?.line)).toEqual([2, 4, 6]);
    // A window that draws none of a hunk's changed lines places no mark for it.
    const narrow = { before: { first: 4, last: 4 }, after: { first: 4, last: 4 } };
    expect(lines.map((mark) => markAnchor(mark, 'split', narrow))).toEqual([
      null,
      { side: 'after', line: 4 },
      null,
    ]);
  });

  it('marks a one-sided pane on the side it draws', () => {
    const [added] = hunks({ ...classified('precuration', 'pkg/new.py'), bucket: 'attributed' });
    expect(markAnchor(added, 'split', { before: null, after: wholeSide(2) })).toEqual({
      side: 'after',
      line: 1,
    });
  });
});
