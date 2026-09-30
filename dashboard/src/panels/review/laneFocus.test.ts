// The unexplained-changes lane's presentation rules (MIK-R32): which hunks a destination focuses on,
// the window each focused diff shows on the server's own side line numbers, and the totals line.
// The file body is the REAL served per-file classification of the MIK-L32 scratch leaf
// (laneReview.capture-provenance.json).
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, it } from 'vitest';
import type { ReviewFileClassification, ReviewUnexplainedLane } from '../../data/reviewLane';
import {
  destinationGroups,
  destinationOf,
  destinationTotals,
  focusedHunks,
  hunkWindow,
  sideLines,
  spanLabel,
} from './laneFocus';

const captured = <T>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const file = captured<{ file_classification: ReviewFileClassification }>(
  'laneReview.file.captured.json',
).file_classification;
const lane = captured<{ lane: ReviewUnexplainedLane }>('laneReview.lane.captured.json').lane;

it('opens an attributed file on its hunks of the destination class, a bucket file on all of them', () => {
  // review_comparison_retention.py: a linked edit inside _side_binding and an appended helper.
  expect(file.bucket).toBe('attributed');
  expect(file.hunks.map((hunk) => hunk.classification)).toEqual(['linked', 'unexplained']);
  expect(focusedHunks(file, 'unexplained').map((hunk) => spanLabel(hunk.after))).toEqual([
    'L572–577',
  ]);
  expect(focusedHunks(file, 'unknown')).toEqual([]);
  // A file of the destination's own bucket opens on every hunk it has.
  const unexplainedFile = { ...file, bucket: 'unexplained' as const };
  expect(focusedHunks(unexplainedFile, 'unexplained')).toHaveLength(2);
});

it('cuts each window on the server side line numbers, with context, clamped to the text', () => {
  const lines = sideLines('1\n2\n3\n4\n5\n6\n7\n8\n');
  expect(lines).toHaveLength(8);
  // A replace of line 4: lines 1..7 (three lines of context each way).
  expect(hunkWindow({ start: 4, count: 1 }, lines)).toEqual({
    first: 1,
    text: '1\n2\n3\n4\n5\n6\n7\n',
    beyond: false,
  });
  // A side where the hunk changes nothing (an insertion after line 6): the gap between 6 and 7.
  expect(hunkWindow({ start: 6, count: 0 }, lines)).toMatchObject({ first: 4, beyond: false });
  expect(hunkWindow({ start: 6, count: 0 }, lines).text).toBe('4\n5\n6\n7\n8\n');
  // An insertion before the first line.
  expect(hunkWindow({ start: 0, count: 0 }, lines).first).toBe(1);
  // A hunk past the text held (a bounded prefix) is never drawn from the wrong lines.
  expect(hunkWindow({ start: 12, count: 2 }, lines).beyond).toBe(true);
});

it('reports a destination file total and hunk total separately, in the server order', () => {
  const unexplained = destinationOf(lane, 'unexplained')!;
  expect(destinationTotals(unexplained)).toBe('5 files · 3 hunks · 2 non-text');
  const [own, attributed] = destinationGroups(unexplained);
  expect(own.map((one) => one.bucket)).toEqual(['unexplained', 'unexplained', 'unexplained']);
  expect(attributed.map((one) => one.bucket)).toEqual(['attributed', 'attributed']);
  // Hunk counts never change the file totals: the buckets still sum to the changed-file total.
  expect(lane.attributed! + lane.unexplained! + lane.attribution_unknown!).toBe(lane.changed_total);
  expect(destinationTotals(destinationOf(lane, 'unknown')!)).toBe('1 file · 1 hunk');
});
