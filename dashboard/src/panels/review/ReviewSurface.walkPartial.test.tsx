// R39 partial-read preservation under R86: the complete synthetic index outline keeps all rows,
// while the immutable partial review body supplies only its loaded facts and main-page continuation.
// The main-page cursor is sent unchanged, and status messages disappear when the shown order changes.
import { fireEvent, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it } from 'vitest';
import type { ReviewResult } from '../../data/review';
import {
  HBJ_ID,
  K,
  R6R_ID,
  SHARED,
  bodyOf,
  clickedSecondFamily,
  families,
  familyBlock,
  installWorld,
  memberOccurrences,
  open,
  reviewCount,
  selectedNode,
  settled,
  step,
  subjectOf,
  triageStatus,
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

it('drops "No earlier change" when the order control lists the other family before the selection', async () => {
  const view = open(PAGE as ReviewResult);
  // Changes first, the second family is displayed first, and its own row is the first change.
  await clickedSecondFamily(view, [R6R_ID, HBJ_ID]);
  const filter = view.getByTestId('review-family-filter');
  fireEvent.change(filter, { target: { value: HBJ_ID } });
  await step(view, K, 0);
  expect(triageStatus(view)).toBe('No earlier change in this tree; the selection stays.');
  // In authored order the first family is listed before it, with a changed member: an earlier
  // change, so the message is gone, and the next k moves to that member.
  fireEvent.change(filter, { target: { value: '' } });
  fireEvent.click(view.getByTestId('review-tree-order'));
  await waitFor(
    () => expect(view.getByTestId('review-tree-order').dataset.order).toBe('authored'),
  );
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(triageStatus(view)).toBe('');
  await step(view, K, 0);
  expect(selectedNode(view)!.closest<HTMLElement>('[data-testid=review-family]')).toBe(
    familyBlock(view, R6R_ID),
  );
});
