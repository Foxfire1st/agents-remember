// MIK-R39 in the mounted R86 reviewer, using a labelled synthetic outline over the store-authored comparison of MIK-R33
// (walkStore.capture-provenance.json): a member of the second family only, and a kept family whose
// members are not all returned. `ReviewSurface` is the real component and only `fetch` is stubbed.
// Since MIK-R89 v2 rule 11 `j`/`k` walk the reading column's blocks, not the tree's rows (these answers
// carry no code locations), so the walk here is made by the rows themselves.
// Family FAM-F00001 has nine members, FAM-F00002 three; INV-FFFFFF belongs to both and INV-PPPPPP
// to the second only.
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewResult } from '../../data/review';
import { ReviewSurface } from './ReviewSurface';
import { syntheticCatalogue } from './outline.test-utils';
import { treeViewStore } from './triageViewPreference';
import { treeOrderStore } from './triageOrderPreference';
import {
  captured,
  families,
  familyBlock,
  memberOccurrences,
  reviewCount,
  selectedNode,
  serve,
  settled,
  subjectOf,
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
  if (useFirstPage && asked === `family:${FAM1_ID}`) return CONTINUED;
  if (asked === `invariant:${MEMBER_G_ID}`)
    return new TypeError('INV-GGGGGG review read deliberately failed');
  const served = [FAM1, FAM2, MEMBER_P, useFirstPage ? SHARED_PAGE : SHARED].find(
    (one) => subjectOf(one) === asked,
  );
  return served;
}

beforeEach(() => {
  localStorage.clear();
  treeOrderStore.getState().setOrder('priority');
  treeViewStore.getState().setView('all');
  useFirstPage = false;
  world.requests = serve({
    entries: { ...body('entries'), ...syntheticCatalogue([FAM1, FAM2]) },
    review: answer,
  });
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
  node.dataset.treeNode === 'family' ? `family ${node.dataset.family}` : (node.textContent ?? '');
// A family row opens the family's overview (MIK-R211 v4 rule 10).
const clickFamilyRow = async (view: View, id: string) => {
  fireEvent.click(within(familyBlock(view, id)).getByTestId('review-family-open'));
  await waitFor(
    () => expect(view.getByTestId('review-family-overview').dataset.family).toBe(id),
  );
  await settled(view);
};
const memberRowOf = (view: View, familyId: string, label: string) =>
  within(familyBlock(view, familyId))
    .getAllByTestId('review-family-member-open')
    .find((node) => node.textContent?.includes(label))!;

it("keeps both families as the second family's own member is selected, and an explicit unknown read fails", async () => {
  const view = open(SHARED);
  await view.findAllByTestId('review-family-open', undefined);
  await waitFor(() => expect(families(view)).toHaveLength(2));
  await clickFamilyRow(view, FAM2_ID);
  const first = families(view);
  expect(new Set(first)).toEqual(new Set([FAM1_ID, FAM2_ID]));
  // INV-PPPPPP, a member of the second family only, by its row: one read.
  const before = reviewCount();
  fireEvent.click(
    within(familyBlock(view, FAM2_ID))
      .getAllByTestId('review-family-member-open')
      .find((node) => node.textContent?.includes('INV-PPPPPP'))!,
  );
  await settled(view);
  expect(reviewCount() - before).toBe(1);
  expect(selectedNode(view)!.textContent).toContain('INV-PPPPPP');
  expect(
    selectedNode(view)!.closest<HTMLElement>('[data-testid=review-family]')!.dataset.family,
  ).toBe(FAM2_ID);
  expect(families(view)).toEqual(first);
  expect(view.getByTestId('review-family-kept').closest('[data-testid=review-family]')).toBe(
    familyBlock(view, FAM1_ID),
  );
  expect(memberOccurrences(familyBlock(view, FAM1_ID)).size).toBe(9);
  await clickFamilyRow(view, FAM2_ID);
  expect(labelOf(selectedNode(view)!)).toBe(`family ${FAM2_ID}`);
  const failedRow = within(familyBlock(view, FAM1_ID))
    .getAllByTestId('review-family-member-open')
    .find((node) => node.textContent?.includes('INV-GGGGGG'))!;
  // Unknown is not a j/k stop in R86; an explicit selection may still read and fail.
  fireEvent.click(failedRow);
  await view.findByTestId('review-reading-problem', undefined);
  expect(selectedNode(view)).toBe(failedRow);
  expect(view.getByTestId('review-reading-problem').textContent).toContain(
    'INV-GGGGGG review read deliberately failed',
  );
  expect(families(view)).toEqual(first);
});

it('keeps complete outline rows while a member read continues its partial family roster in the main page', async () => {
  useFirstPage = true;
  treeOrderStore.getState().setOrder('authored');
  const view = open(SHARED);
  await view.findAllByTestId('review-family-open', undefined);
  await clickFamilyRow(view, FAM2_ID);
  expect(familyBlock(view, FAM1_ID).dataset.familyKept).toBe('true');
  const rows = memberOccurrences(familyBlock(view, FAM1_ID)).size;
  expect(rows).toBe(9);
  expect(view.queryByTestId('review-family-roster-next')).toBeNull();
  await clickFamilyRow(view, FAM1_ID);
  // The partial roster's continuation is in the detail of a member read under the family.
  fireEvent.click(memberRowOf(view, FAM1_ID, 'INV-FFFFFF'));
  const control = (await view.findAllByTestId('review-center-roster-next'))[0];
  const cursor = control.dataset.continuation!;
  const asked = reviewCount();
  fireEvent.click(control);
  await waitFor(() => expect(reviewCount() - asked).toBe(1));
  await settled(view);
  // The continuation pages the member's read it was offered under, with the cursor unchanged.
  const request = world.requests.reviews()[asked];
  expect([
    request.searchParams.get('selectorId'),
    request.searchParams.get('continuation'),
  ]).toEqual([SHARED.payload!.knowledge.revision_selection!.record_id, cursor]);
  expect(memberOccurrences(familyBlock(view, FAM1_ID)).size).toBe(rows);
  await clickFamilyRow(view, FAM2_ID);
  expect(familyBlock(view, FAM1_ID).dataset.familyKept).toBe('true');
  expect(memberOccurrences(familyBlock(view, FAM1_ID)).size).toBe(rows);
});

it('keeps the first family when a member of the second is opened from its slot, and marks it under the second family', async () => {
  const view = open(SHARED);
  await view.findAllByTestId('review-family-open', undefined);
  await waitFor(() => expect(families(view)).toHaveLength(2));
  await clickFamilyRow(view, FAM2_ID);
  const first = families(view);
  const before = reviewCount();
  // The family opens its members' overview now (MIK-R211 v4 rule 10): a member opens from its summary row.
  const opener = within(view.getByTestId('review-family-overview'))
    .getAllByTestId('review-member-summary')
    .find((row) => row.dataset.subject === MEMBER_P.payload!.knowledge.revision_selection!.record_id)!
    .querySelector('button')!;
  fireEvent.click(opener);
  await waitFor(() => expect(view.getByTestId('review-center-member')).toBeTruthy());
  await settled(view);
  expect(reviewCount() - before).toBe(1);
  expect(selectedNode(view)!.textContent).toContain('INV-PPPPPP');
  expect(families(view)).toEqual(first);
  expect(familyBlock(view, FAM1_ID).dataset.familyKept).toBe('true');
  expect(selectedNode(view)!.closest<HTMLElement>('[data-testid=review-family]')).toBe(familyBlock(view, FAM2_ID));
  expect(labelOf(selectedNode(view)!)).not.toBe(`family ${FAM2_ID}`);
});
