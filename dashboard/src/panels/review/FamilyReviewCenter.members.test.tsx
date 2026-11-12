import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import { resetCardCachesForTests } from '../../data/reviewTrees';
import { FamilyReviewCenter } from './FamilyReviewCenter';

const captured = JSON.parse(
  readFileSync(
    path.join(
      path.dirname(new URL(import.meta.url).pathname),
      'subjectReview.successorFamily.captured.json',
    ),
    'utf8',
  ),
) as ReviewResult;
afterEach(() => {
  cleanup();
  resetCardCachesForTests();
  vi.unstubAllGlobals();
});

it('word-diffs a family member statement once while keeping both revision links', async () => {
  const payload = structuredClone(captured.payload!);
  const entry = payload.family_context!.entries[0];
  const before = { ...entry.before.members[0], statement: 'The member must not change.' };
  const after = {
    ...before,
    invariant_revision_id: 'next-revision',
    statement: 'The member mustnot change.',
  };
  entry.before.members = [before];
  entry.after.members = [after];
  entry.before.members_total = entry.after.members_total = 1;
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ state: 'trees', entries: [] }),
    })),
  );
  const onOpenMember = vi.fn();
  const view = render(
    <FamilyReviewCenter
      payload={payload}
      selection={{ familyId: entry.family_id }}
      layout="split"
      onOpenMember={onOpenMember}
      onRosterNext={vi.fn()}
      onOpenFromCenter={vi.fn()}
    />,
  );
  const members = within(view.getByTestId('review-center-family-members'));
  const diff = members.getByTestId('intent-diff');
  expect(diff.querySelector('del')!.textContent).toContain('must not');
  expect(diff.querySelector('ins')!.textContent).toContain('mustnot');
  expect(members.queryByTestId('intent-diff-whitespace-only')).toBeNull();
  expect(members.getAllByTestId('intent-diff')).toHaveLength(1);
  const links = members.getAllByTestId('review-center-open-member');
  expect(links).toHaveLength(2);
  for (const link of links) fireEvent.click(link);
  expect(onOpenMember.mock.calls).toEqual([
    [entry.family_id, before.invariant_revision_id],
    [entry.family_id, after.invariant_revision_id],
  ]);
  // The family's code locations are the reading column's blocks, read once at the opening (MIK-R89
  // v2 rule 2, W4): the centre asks for no cards of its own.
  expect(view.queryByTestId('review-expression-cards')).toBeNull();
  expect(vi.mocked(fetch)).not.toHaveBeenCalled();
});
