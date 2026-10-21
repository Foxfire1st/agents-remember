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
  familyBlock,
  installWorld,
  memberOccurrences,
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
  fireEvent.click(within(block).getByTestId('review-family-open'));
  await waitFor(
    () => expect(view.getByTestId('review-center-family').dataset.family).toBe(R6R_ID),
  );
  const control = view.getAllByTestId('review-center-roster-next')[0];
  const cursor = control.dataset.continuation!;
  const asked = reviewCount();
  fireEvent.click(control);
  await waitFor(() => expect(reviewCount() - asked).toBe(1));
  await settled(view);
  const request = world.requests.reviews()[asked];
  expect([
    request.searchParams.get('selectorKind'),
    request.searchParams.get('continuation'),
  ]).toEqual(['family', cursor]);
  expect(memberOccurrences(familyBlock(view, R6R_ID)).size).toBe(24);
  expect(familyBlock(view, HBJ_ID).dataset.familyKept).toBe('true');
});
