// MIK-R39 in the mounted review page, on real data: the tree keeps the families the reader walked.
//
// The bodies are the REAL served answers of lane D2's scenario (the scratch leaf 260928-MIK-L33 after
// its scratch curation; walkReal.capture-provenance.json). `ReviewSurface` is the real component and
// only `fetch` is stubbed. Family FAM-R6R095RW has seven changes, the last of them the shared member
// INV-2TQGXFAX; FAM-2HBJREC2 is the other family of that member. The tree is the one change-set
// model's (tree/ReviewTree.tsx): a row selects, and a selection reads its subject only.
//
// MIK-R89 v2 rule 11 moved `j`/`k` from the tree's rows to the reading column's blocks
// (changeTraversal.ts): the walk by key of the seven changes is now a walk by their rows, and the
// end message speaks of blocks. The cases that pinned the end message to the filtered tree (W12: the
// filter is the tree's only) are covered by changeTraversal.test.tsx's status case.
import { fireEvent, waitFor, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import {
  CHANGES,
  HBJ_ID,
  HBJ_TITLE,
  J,
  R6R,
  R6R_ID,
  R6R_TITLE,
  SHARED,
  clickedSecondFamily,
  families,
  familyBlock,
  installWorld,
  keptTags,
  memberOccurrences,
  memberRow,
  open,
  reviewCount,
  selectedName,
  selectedNode,
  settled,
  slotOf,
  step,
  triageStatus,
} from './walk.test-utils';

installWorld();

it('lists the seven changes first, selects each and the second family by its row with one read each, and keeps the first family', async () => {
  const view = open(R6R);
  await view.findAllByTestId('review-change-badge', undefined);
  const workspace = view.getByTestId('review-workspace');
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  const rows = within(familyBlock(view, R6R_ID)).getAllByTestId('review-family-member-open');
  expect(rows.slice(0, CHANGES.length).map((row) => row.dataset.subjectId)).toEqual(
    CHANGES.map((change) => memberRow(view, R6R_ID, change).dataset.subjectId),
  );

  // The seven changes, one review read each.
  for (const change of CHANGES) {
    const before = reviewCount();
    fireEvent.click(memberRow(view, R6R_ID, change));
    await settled(view);
    expect(selectedName(view)).toBe(change);
    expect(reviewCount() - before).toBe(1);
  }
  // The seventh is the shared member, and the tree shows both of its families.
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(keptTags(view)).toEqual([]);

  // The second family's own row opens its family review; the first family stays, whole, tagged as
  // kept and last read for the shared member.
  const before = reviewCount();
  await clickedSecondFamily(view);
  expect(reviewCount() - before).toBe(1);
  expect(selectedNode(view)!.dataset.family).toBe(HBJ_ID);
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(keptTags(view)).toEqual(['kept']);
  expect(familyBlock(view, R6R_ID).dataset.familyKept).toBe('true');
  expect(memberOccurrences(familyBlock(view, R6R_ID)).size).toBe(24);

  // These answers carry no code locations, so the column has no block: j says so and the selection
  // stays, with no request.
  selectedNode(view)!.focus();
  await step(view, J, 0);
  expect(triageStatus(view)).toBe('No later block; the selection stays.');
  expect(selectedNode(view)!.dataset.family).toBe(HBJ_ID);

  // The shared member's row under the first family selects it there again.
  fireEvent.click(memberRow(view, R6R_ID, 'INV-2TQGXFAX'));
  await settled(view);
  expect(selectedName(view)).toBe('INV-2TQGXFAX');
  expect(selectedNode(view)!.closest<HTMLElement>('[data-testid=review-family]')!.dataset.family).toBe(R6R_ID);
  // The page is the same node throughout.
  expect(view.getByTestId('review-workspace')).toBe(workspace);
});

it('keeps the first family when its second family is clicked, and its shared member row selects it under the first family', async () => {
  const view = open(R6R);
  await view.findAllByTestId('review-change-badge', undefined);
  const sharedRow = within(familyBlock(view, R6R_ID))
    .getAllByTestId('review-family-member-open')
    .find(
      (row) => row.dataset.subjectId === SHARED.payload!.knowledge.revision_selection!.record_id,
    )!;
  fireEvent.click(sharedRow);
  await waitFor(() => expect(families(view)).toEqual([R6R_ID, HBJ_ID]));
  await settled(view);
  expect(keptTags(view)).toEqual([]);

  // A click on the second family's row opens its review and does not narrow the tree.
  const before = reviewCount();
  await clickedSecondFamily(view);
  expect(reviewCount() - before).toBe(1);
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(familyBlock(view, R6R_ID).dataset.familyKept).toBe('true');
  expect(memberOccurrences(familyBlock(view, R6R_ID)).size).toBe(24);
  // The shared member under the first family is a row of the kept family: selecting it marks that
  // occurrence, not the one under the second family.
  fireEvent.click(memberRow(view, R6R_ID, 'INV-2TQGXFAX'));
  await settled(view);
  expect(selectedName(view)).toBe('INV-2TQGXFAX');
  expect(
    selectedNode(view)!.closest<HTMLElement>('[data-testid=review-family]')!.dataset.family,
  ).toBe(R6R_ID);
});

it('tags a kept family, describes the subject it was read for, and reads one answer for the selected family', async () => {
  const view = open(SHARED);
  await clickedSecondFamily(view);
  const tree = view.getByTestId('review-tree');
  // The tag is visible on the kept family's row and names the subject the family was last read for.
  const tag = view.getByTestId('review-family-kept');
  expect(tag.textContent).toBe('kept');
  expect(tag.getAttribute('title')).toContain('Kept; last read for invariant INV-2TQGXFAX');
  const keptRow = within(familyBlock(view, R6R_ID)).getByTestId('review-family-open');
  expect(keptRow.contains(tag)).toBe(true);
  expect(keptRow.getAttribute('aria-label')).toContain('kept');
  // The row the reader focuses carries the description, not the tag inside it.
  const described = document.getElementById(keptRow.getAttribute('aria-describedby') ?? '');
  expect(described?.textContent).toContain('Kept; last read for invariant INV-2TQGXFAX');
  expect(tag.hasAttribute('aria-describedby')).toBe(false);
  const ownRow = within(familyBlock(view, HBJ_ID)).getByTestId('review-family-open');
  expect(within(ownRow).queryByTestId('review-family-kept')).toBeNull();
  expect(view.queryByTestId('review-family-filter-scope')).toBeNull();
  expect(view.queryByText('Family context details')).toBeNull();
  // The selected family's slot shows its review and nothing of the kept family.
  const slot = slotOf(view);
  expect(within(slot).getByTestId('review-center-family').dataset.family).toBe(HBJ_ID);
  // The family's own name is its heading's; the read's label is in Technical details (MIK-R89 v2
  // rule 6, amended 9dde2b09).
  expect(view.getByTestId('review-subject-provenance').textContent).toContain(HBJ_TITLE);
  expect(slot.textContent).not.toContain(R6R_TITLE);
  expect(slot.querySelectorAll(`[data-family="${R6R_ID}"]`)).toHaveLength(0);
  expect(view.getByTestId('review-center-column').querySelector('[data-testid="review-family-kept"]')).toBeNull();
  expect(tree.contains(tag)).toBe(true);
});

it('keeps one outline row for every family and selecting one does not reset the walk', async () => {
  const view = open(SHARED);
  await clickedSecondFamily(view);
  expect(view.queryByTestId('review-catalogue-subject')).toBeNull();
  expect(view.queryByTestId('review-all-invariants')).toBeNull();
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  const row = within(familyBlock(view, R6R_ID)).getByTestId('review-family-open');
  fireEvent.click(row);
  await waitFor(
    () => expect(view.getByTestId('review-center-family').dataset.family).toBe(R6R_ID),
  );
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(keptTags(view)).toHaveLength(1);
});

it('lets a filter hide kept families with the others and restores them when it is cleared', async () => {
  const view = open(SHARED);
  await clickedSecondFamily(view);
  const filter = view.getByTestId('review-family-filter');
  fireEvent.change(filter, { target: { value: 'Comparison-bound' } });
  expect(families(view)).toEqual([HBJ_ID]);
  fireEvent.change(filter, { target: { value: '' } });
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(memberOccurrences(familyBlock(view, R6R_ID)).size).toBe(24);
  expect(familyBlock(view, R6R_ID).dataset.familyKept).toBe('true');
  // The filter is the tree's alone (W12): the column still holds both families' headings.
  const column = view.getByTestId('review-center-column');
  fireEvent.change(filter, { target: { value: 'Comparison-bound' } });
  expect(within(column).getAllByTestId('review-family-heading').map((node) => node.dataset.family)).toEqual([
    R6R_ID,
    HBJ_ID,
  ]);
});
