import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { ReviewResult } from '../../../data/review';
import { FamilyMemberStatement, memberStatementPairs } from './FamilyMemberStatement';

const captured = JSON.parse(
  readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), '../gitTrees.family.captured.json'),
    'utf8',
  ),
) as ReviewResult;
afterEach(cleanup);

it('compares different side texts at the same member revision identity', () => {
  const entry = structuredClone(captured.payload!.family_context!.entries[0]);
  const member = { ...entry.before.members[0], statement: 'A member must not change.' };
  const after = { ...member, statement: 'A member mustnot change.' };
  entry.before.members = [member];
  entry.after.members = [after];
  const pair = memberStatementPairs(entry).get(member.invariant_id!);
  const view = render(<FamilyMemberStatement member={after} pair={pair} />);
  const diff = view.getByTestId('intent-diff');
  expect(diff.querySelector('del')!.textContent).toContain('must not');
  expect(diff.querySelector('ins')!.textContent).toContain('mustnot');
  expect(view.queryByTestId('intent-diff-whitespace-only')).toBeNull();
});
