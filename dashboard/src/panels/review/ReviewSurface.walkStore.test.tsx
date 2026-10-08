// MIK-R39 in the mounted reviewer, on the store-authored comparison of MIK-R33
// (walkStore.capture-provenance.json): a member of the second family only, and a kept family whose
// members are not all returned. `ReviewSurface` is the real component and only `fetch` is stubbed.
// Family FAM-F00001 has nine members, FAM-F00002 three; INV-FFFFFF belongs to both and INV-PPPPPP
// to the second only.
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import { ReviewSurface } from './ReviewSurface';
import { treeOrderStore } from './triageOrderPreference';
import {
  J,
  K,
  captured,
  families,
  familyBlock,
  memberOccurrences,
  press,
  reviewCount,
  selectedNode,
  serve,
  settled,
  step,
  subjectOf,
  triageStatus,
  world,
} from './walk.test-utils';

const body = (name: string) => captured<ReviewResult>(`walkStore.${name}.captured.json`);
const SHARED = body('shared');
const SHARED_PAGE = body('sharedPage');
const FAM1 = body('FAM-F00001');
const FAM2 = body('FAM-F00002');
const CONTINUED = body('FAM-F00001Continued');
const MEMBER_P = body('INV-PPPPPP');
const MEMBER_G_ID = FAM1.payload!.family_context!.entries[0].after.members.find(
  (member) => member.display_label === 'INV-GGGGGG',
)!.invariant_id;
const familyId = (result: ReviewResult) => result.payload!.family_context!.entries[0].family_id;
const FAM1_ID = familyId(FAM1);
const FAM2_ID = familyId(FAM2);

let useFirstPage = false;

function answer(url: URL): unknown {
  const selector = url.searchParams.get('selectorId');
  if (selector === null) return undefined;
  if (url.searchParams.get('continuation') !== null) return CONTINUED;
  const asked = `${url.searchParams.get('selectorKind')}:${selector}`;
  if (asked === `invariant:${MEMBER_G_ID}`)
    return new TypeError('INV-GGGGGG review read deliberately failed');
  const served = [FAM1, FAM2, MEMBER_P, useFirstPage ? SHARED_PAGE : SHARED].find(
    (one) => subjectOf(one) === asked,
  );
  return served;
}

beforeEach(() => {
  localStorage.clear();
  treeOrderStore.getState().setOrder('triage');
  useFirstPage = false;
  world.requests = serve({ entries: body('entries'), review: answer });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function open(subject: ReviewResult) {
  const payload = subject.payload!;
  const selection = payload.knowledge.revision_selection!;
  return render(
    <ReviewSurface
      repo={payload.candidate.repository_id}
      master={payload.candidate.master}
      leaf={payload.candidate.leaf_id}
      selectorKind={selection.record_kind}
      selectorId={selection.record_id}
      onBack={() => undefined}
    />,
  );
}

type View = ReturnType<typeof open>;
const labelOf = (node: HTMLElement) =>
  node.dataset.family ? `family ${node.dataset.family}` : (node.textContent ?? '');
const clickFamilyRow = async (view: View, id: string) => {
  fireEvent.click(within(familyBlock(view, id)).getByTestId('review-family-open'));
  await waitFor(
    () => expect(view.getByTestId('review-center-family').dataset.family).toBe(id),
  );
  await settled(view);
};

it("keeps both families as j selects the second family's member and k returns through a failed read", async () => {
  const view = open(SHARED);
  await view.findAllByTestId('review-change-badge', undefined);
  await waitFor(() => expect(families(view)).toHaveLength(2));
  await clickFamilyRow(view, FAM2_ID);
  const first = families(view);
  expect(new Set(first)).toEqual(new Set([FAM1_ID, FAM2_ID]));
  // j from the second family's own row selects INV-PPPPPP, a member of that family only.
  await step(view, J, 1);
  expect(selectedNode(view)!.textContent).toContain('INV-PPPPPP');
  expect(
    selectedNode(view)!.closest<HTMLElement>('[data-testid=review-family]')!.dataset.family,
  ).toBe(FAM2_ID);
  expect(families(view)).toEqual(first);
  expect(view.getByTestId('review-family-kept').closest('[data-testid=review-family]')).toBe(
    familyBlock(view, FAM1_ID),
  );
  expect(memberOccurrences(familyBlock(view, FAM1_ID)).size).toBe(9);
  // k returns to the family row it came from, and again to the first family's last change.
  await step(view, K, 0);
  expect(labelOf(selectedNode(view)!)).toBe(`family ${FAM2_ID}`);
  const failedRow = within(familyBlock(view, FAM1_ID))
    .getAllByTestId('review-family-member-open')
    .find((node) => node.textContent?.includes('INV-GGGGGG'))!;
  // This read deliberately fails: the requested row stays selected and neither family is lost.
  await step(view, K, 1);
  expect(selectedNode(view)).toBe(failedRow);
  expect(view.getByTestId('review-reading-problem').textContent).toContain(
    'INV-GGGGGG review read deliberately failed',
  );
  expect(families(view)).toEqual(first);
});

it('stops j at the continuation control of a kept family, loads nothing, and continues it by selecting it', async () => {
  useFirstPage = true;
  treeOrderStore.getState().setOrder('authored');
  const view = open(SHARED);
  await view.findAllByTestId('review-change-badge', undefined);
  await waitFor(() => expect(families(view)).toEqual([FAM1_ID, FAM2_ID]));
  // Both families return two members per side: the first one has unreturned members.
  expect(familyBlock(view, FAM1_ID).dataset.membersUnreturned).toBe('true');
  await clickFamilyRow(view, FAM2_ID);
  expect(familyBlock(view, FAM1_ID).dataset.familyKept).toBe('true');
  const returned = memberOccurrences(familyBlock(view, FAM1_ID)).size;
  expect(returned).toBeLessThan(9);

  // The first family's last returned change; j goes on to its continuation control and stops there.
  const changes = within(familyBlock(view, FAM1_ID))
    .getAllByTestId('review-family-member-open')
    .filter((node) => node.dataset.changePrimary !== 'unchanged');
  const last = changes[changes.length - 1];
  last.focus();
  const before = reviewCount();
  press(J);
  const control = within(familyBlock(view, FAM1_ID)).getAllByTestId('review-family-roster-next')[0];
  expect(document.activeElement).toBe(control);
  expect(triageStatus(view)).toContain('Further members of FAM-F00001 are not yet returned');
  expect(reviewCount() - before).toBe(0);
  // The next j moves on, to the second family's own row.
  await step(view, J, 0);
  expect(selectedNode(view)!.dataset.family).toBe(FAM2_ID);

  // The control selects the first family, keeps the tree, and continues the roster at its cursor.
  const cursor = control.dataset.continuation!;
  const asked = reviewCount();
  fireEvent.click(control);
  await waitFor(
    () => expect(view.getByTestId('review-center-family').dataset.family).toBe(FAM1_ID),
  );
  await settled(view);
  const request = world.requests.reviews()[asked];
  expect([
    request.searchParams.get('selectorId'),
    request.searchParams.get('continuation'),
  ]).toEqual([FAM1.payload!.knowledge.revision_selection!.record_id, cursor]);
  expect(reviewCount() - asked).toBe(1);
  expect(familyBlock(view, FAM2_ID).dataset.familyKept).toBe('true');
  const continued = memberOccurrences(familyBlock(view, FAM1_ID)).size;
  expect(continued).toBeGreaterThan(returned);
  expect(familyBlock(view, FAM1_ID).dataset.membersUnreturned).toBe('true');

  // The continued members survive a selection of the other family.
  await clickFamilyRow(view, FAM2_ID);
  expect(familyBlock(view, FAM1_ID).dataset.familyKept).toBe('true');
  expect(memberOccurrences(familyBlock(view, FAM1_ID)).size).toBe(continued);
});

it('keeps the first family when a member of the second is opened from the reading area, and k steps back', async () => {
  const view = open(SHARED);
  await view.findAllByTestId('review-change-badge', undefined);
  await waitFor(() => expect(families(view)).toHaveLength(2));
  await clickFamilyRow(view, FAM2_ID);
  const first = families(view);
  const before = reviewCount();
  const opener = within(view.getByTestId('review-center-column'))
    .getAllByTestId('review-center-open-member')
    .find((button) => button.textContent?.includes('INV-PPPPPP'))!;
  fireEvent.click(opener);
  await waitFor(() => expect(view.getByTestId('review-center-member')).toBeTruthy());
  await settled(view);
  expect(reviewCount() - before).toBe(1);
  expect(selectedNode(view)!.textContent).toContain('INV-PPPPPP');
  expect(families(view)).toEqual(first);
  expect(familyBlock(view, FAM1_ID).dataset.familyKept).toBe('true');
  await step(view, K, 0);
  expect(labelOf(selectedNode(view)!)).toBe(`family ${FAM2_ID}`);
});
