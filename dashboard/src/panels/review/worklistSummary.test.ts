import { expect, it } from 'vitest';
import type { ReviewWorklistSummary, ReviewWorklistView } from '../../data/reviewTrees';
import { fileAnswers, worklistAnswer } from './worklistState';
import { planningMarks } from './worklistGroups';
import fixtures from './compact-worklist.fixture.json';

it.each(fixtures)('preserves existing complete-view gate, planning and file answers for each server summary', (fixture) => {
  const full = fixture.full as ReviewWorklistView;
  const summary = fixture.summary as ReviewWorklistSummary;
  expect(worklistAnswer(summary)).toEqual(worklistAnswer(full));
  expect(fileAnswers(summary)).toEqual(fileAnswers(full));
  expect(planningMarks(summary)).toEqual(planningMarks(full));
});
