// The subject heading's short title (MIK-R89 v2 rule 1, d9a1ec8f; mockup shots/selected-1920.png):
// the statement's lead clause, which the full title's tooltip and the statement below complete.
import { expect, it } from 'vitest';

import { leadClause } from './leadClause';

it('cuts a statement at its first dash, semicolon or colon', () => {
  expect(
    leadClause('A silent role-agent turn end — a completed turn that hands off to no one — wakes the agent that waits'),
  ).toBe('A silent role-agent turn end');
  expect(leadClause('Every authored knowledge write -- a record or a history row -- goes through one of two command lines')).toBe(
    'Every authored knowledge write',
  );
  expect(leadClause('A text record holds one revision; its history files say who wrote it')).toBe('A text record holds one revision');
  expect(leadClause('Two kinds of record: invariants and families')).toBe('Two kinds of record');
});

it('keeps a title with no such break, or one whose lead would be too short to name it', () => {
  expect(leadClause('A repository publishes knowledge at one declared location per memory layer')).toBe(
    'A repository publishes knowledge at one declared location per memory layer',
  );
  expect(leadClause('Note: values land unchanged')).toBe('Note: values land unchanged');
});
