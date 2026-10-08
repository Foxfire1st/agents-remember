// MIK-R39 rule 8 on a REAL partial answer (walkReal.capture-provenance.json, "partial_round"): the
// real route asked for the shared member with its own `pageSize=2`, so both of its families return two
// members per side. The first family is kept once the second is selected; `j` stops at its
// continuation control, loads nothing, and the control continues it by selecting it. An end message
// (rule 11) is dropped when the continuation returns members, and when the order control lists the
// rows in another order.
import { fireEvent, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it } from 'vitest';
import type { ReviewResult } from '../../data/review';
import {
  HBJ_ID,
  J,
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
  press,
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

it('stops j at the control of a kept real partial family, then continues it by selecting it', async () => {
  const view = open(PAGE as ReviewResult);
  // With two members returned per side the second family weighs more, so it is displayed first.
  await clickedSecondFamily(view, [HBJ_ID, R6R_ID]);
  const block = familyBlock(view, R6R_ID);
  expect(block.dataset.familyKept).toBe('true');
  expect(block.dataset.membersUnreturned).toBe('true');
  const returned = memberOccurrences(block).size;
  expect(returned).toBe(3);

  const changes = within(block)
    .getAllByTestId('review-family-member-open')
    .filter((node) => node.dataset.changePrimary !== 'unchanged');
  changes[changes.length - 1].focus();
  const before = reviewCount();
  press(J);
  const control = within(block).getAllByTestId('review-family-roster-next')[0];
  await waitFor(() => expect(document.activeElement).toBe(control));
  expect(triageStatus(view)).toContain('are not yet returned');
  expect(reviewCount() - before).toBe(0);
  // The next j has nothing later to move to: the kept family is the last in the displayed order.
  press(J);
  await waitFor(
    () => expect(triageStatus(view)).toBe('No later change in this tree; the selection stays.'),
  );
  expect(reviewCount() - before).toBe(0);
  expect(selectedNode(view)!.dataset.family).toBe(HBJ_ID);

  const cursor = control.dataset.continuation!;
  const asked = reviewCount();
  fireEvent.click(control);
  await waitFor(
    () => expect(view.getByTestId('review-center-family').dataset.family).toBe(R6R_ID),
  );
  await settled(view);
  const request = world.requests.reviews()[asked];
  expect([
    request.searchParams.get('selectorKind'),
    request.searchParams.get('continuation'),
  ]).toEqual(['family', cursor]);
  expect(reviewCount() - asked).toBe(1);
  expect(memberOccurrences(familyBlock(view, R6R_ID)).size).toBeGreaterThan(returned);
  expect(familyBlock(view, HBJ_ID).dataset.familyKept).toBe('true');
  // "No later change" was said of the tree before these members were returned: it is gone.
  expect(triageStatus(view)).toBe('');
});

it('drops "No earlier change" when the order control lists the other family before the selection', async () => {
  const view = open(PAGE as ReviewResult);
  // Changes first, the second family is displayed first, and its own row is the first change.
  await clickedSecondFamily(view, [HBJ_ID, R6R_ID]);
  await step(view, K, 0);
  expect(triageStatus(view)).toBe('No earlier change in this tree; the selection stays.');
  // In authored order the first family is listed before it, with a changed member: an earlier
  // change, so the message is gone, and the next k moves to that member.
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
