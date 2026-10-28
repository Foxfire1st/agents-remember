// R39 partial-read preservation under R86: the complete synthetic index outline keeps all rows,
// while the immutable partial review body supplies only its loaded facts and main-page continuation.
// The main-page cursor is sent unchanged. (The end message of `j`/`k` speaks of the reading column's
// blocks since MIK-R89 v2 rule 11, not of the tree's order: changeTraversal.test.tsx pins it.)
import { fireEvent, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it } from 'vitest';
import type { ReviewResult } from '../../data/review';
import {
  HBJ_ID,
  R6R_ID,
  SHARED,
  bodyOf,
  clickedSecondFamily,
  families,
  familyBlock,
  installWorld,
  memberOccurrences,
  memberRow,
  open,
  reviewCount,
  settled,
  subjectOf,
  world,
} from './walk.test-utils';

installWorld();

const PAGE = bodyOf('INV-2TQGXFAX-page2');
const CONTINUED = bodyOf('FAM-R6R095RW-continued');

beforeEach(() => {
  const previous = world.answer;
  world.answer = (url, asked): unknown => {
    if (url.searchParams.get('continuation') !== null) return CONTINUED;
    return asked === subjectOf(SHARED) ? PAGE : previous(url, asked);
  };
});

it('shows complete outline rows for a partial read and keeps continuation in the main page', async () => {
  const view = open(PAGE as ReviewResult);
  await clickedSecondFamily(view, [R6R_ID, HBJ_ID]);
  const block = familyBlock(view, R6R_ID);
  expect(block.dataset.familyKept).toBe('true');
  expect(memberOccurrences(block).size).toBe(24);
  expect(within(block).queryByTestId('review-family-roster-next')).toBeNull();
  // A family opens only its overview (MIK-R211 v4 rule 10); the partial roster's continuation is in
  // the detail of a member read under it. INV-2E8MG43K is R6R's alone, so HBJ stays the kept family.
  fireEvent.click(within(block).getByTestId('review-family-open'));
  await waitFor(
    () => expect(view.getByTestId('review-family-overview').dataset.family).toBe(R6R_ID),
  );
  await settled(view);
  expect(familyBlock(view, HBJ_ID).dataset.familyKept).toBe('true');
  fireEvent.click(memberRow(view, R6R_ID, 'INV-2E8MG43K'));
  const control = (await view.findAllByTestId('review-center-roster-next'))[0];
  expect(familyBlock(view, HBJ_ID).dataset.familyKept).toBe('true');
  const cursor = control.dataset.continuation!;
  const asked = reviewCount();
  fireEvent.click(control);
  await waitFor(() => expect(reviewCount() - asked).toBe(1));
  await settled(view);
  // The continuation pages the member's read it was offered under, with the cursor unchanged.
  const request = world.requests.reviews()[asked];
  expect([
    request.searchParams.get('selectorKind'),
    request.searchParams.get('selectorId'),
    request.searchParams.get('continuation'),
  ]).toEqual(['invariant', bodyOf('INV-2E8MG43K').payload!.knowledge.revision_selection!.record_id, cursor]);
  expect(memberOccurrences(familyBlock(view, R6R_ID)).size).toBe(24);
  // The kept family stays kept across the continuation.
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(familyBlock(view, HBJ_ID).dataset.familyKept).toBe('true');
});
