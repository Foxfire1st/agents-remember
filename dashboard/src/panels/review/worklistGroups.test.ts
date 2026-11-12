// The worklist as the reviewer groups it (MIK-R25 rule 3, MIK-R11 rule 7, MIK-L10's ruling on many
// unexplained items). The worklist is the REAL served tree view of the converted scratch leaf
// (../../data/reviewTrees.captured.json); the unexplained items are shaped exactly as MIK-R10's
// `unexplained_hunk` items (facts path, coverage, row), because that leaf is not yet on this base.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

import type { ReviewTreesResult, ReviewWorklistItem } from '../../data/reviewTrees';
import { planningMarks, rowSubjects, worklistGroups } from './worklistGroups';

const trees = JSON.parse(
  readFileSync(
    path.join(
      path.dirname(new URL(import.meta.url).pathname),
      '../../data/reviewTrees.captured.json',
    ),
    'utf8',
  ),
) as ReviewTreesResult;
const worklist = trees.worklist!;

function hunk(file: string, state: string, n: number): ReviewWorklistItem {
  return {
    id: `sha256:${file}:${n}`,
    kind: 'unexplained_hunk',
    subject: `hunk:${file}@sha256:${n}..absent`,
    facts: { path: file, coverage: { state }, row: `hunk:sha256:${file}:${n}` },
  };
}

describe('the worklist groups', () => {
  it('marks knowledge items planned or unplanned and lists the declared effects no row delivered', () => {
    const groups = worklistGroups(worklist);
    expect(groups.knowledge.map((entry) => [entry.item.subject, entry.item.planning])).toEqual([
      ['FAM-2HBJREC2', 'unplanned'],
      ['INV-2TQGXFAX', 'planned'],
    ]);
    expect(groups.plannedUntouched.map((entry) => entry.item.subject)).toEqual([
      'planned:family:FAM-BWQ4XYF9#strengthen',
      'planned:invariant:INV-2TQGXFAX#clarify',
    ]);
    const family = groups.knowledge.find((entry) => entry.item.subject === 'FAM-2HBJREC2')!;
    expect(family.rows.map((row) => [row.owner, row.disposition])).toEqual([
      ['260928-MIK-L31', 'no_impact'],
    ]);
    expect(planningMarks(worklist).get('INV-2TQGXFAX')).toBe('touched invariant · planned');
  });

  it('groups unexplained items by file and coverage state and finds their rows by facts.row', () => {
    const items = [
      hunk('b.py', 'uncovered', 1),
      hunk('a.py', 'covered', 2),
      hunk('a.py', 'uncovered', 3),
      hunk('a.py', 'covered', 4),
    ];
    const row = {
      id: 'ROW-AAAAAA',
      owner: 'L',
      subject: 'hunk:sha256:a.py:2',
      disposition: 'no_invariant',
      row: {},
    };
    const groups = worklistGroups({ ...worklist, items, history_rows: [row] });
    expect(
      groups.unexplained.map((group) => [
        group.path,
        group.count,
        group.coverage.map((one) => [one.state, one.entries.length]),
      ]),
    ).toEqual([
      [
        'a.py',
        3,
        [
          ['covered', 2],
          ['uncovered', 1],
        ],
      ],
      ['b.py', 1, [['uncovered', 1]]],
    ]);
    expect(groups.unexplained[0].coverage[0].entries[0].rows).toEqual([row]);
    expect(rowSubjects(items[1])).toEqual([items[1].subject, 'hunk:sha256:a.py:2']);
    expect(groups.knowledge).toEqual([]);
  });
});
