// MIK-R39 in the mounted reviewer, on real data: the family tree keeps the rows the reader walked.
//
// The bodies are the REAL served answers of lane D2's scenario (the scratch leaf 260928-MIK-L33 after
// its scratch curation; walkReal.capture-provenance.json). `ReviewSurface` is the real component and
// only `fetch` is stubbed. Family FAM-R6R095RW has seven changes, the last of them the shared member
// INV-2TQGXFAX; FAM-2HBJREC2 is the other family of that member. Each test fails when the keeping is
// removed (see the mutation record in the leaf's evidence), except the last three: they pin rule 11's
// end message, which speaks of the tree as shown and is dropped for good when the tree then shows
// other rows (an answer adds a later change; the filter is cleared or changed).
import { fireEvent, waitFor, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import {
  CHANGES,
  HBJ_ID,
  HBJ_TITLE,
  J,
  K,
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
  open,
  reviewCount,
  selectedName,
  selectedNode,
  settled,
  step,
  triageStatus,
} from './walk.test-utils';

installWorld();

it('walks seven changes and the second family by key, and k returns through every change passed', async () => {
  const view = open(R6R);
  await view.findAllByTestId('review-change-badge', undefined);
  const workspace = view.getByTestId('review-workspace');
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  within(familyBlock(view, R6R_ID)).getByTestId('review-family-open').focus();

  // Presses 1 to 7: the seven changes, one review read each.
  for (const change of CHANGES) {
    await step(view, J, 1);
    expect(selectedName(view)).toBe(change);
  }
  // The seventh is the shared member, and the tree then shows both of its families.
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(keptTags(view)).toEqual([]);

  // Press 8 selects the second family's own row and opens its family review; the first family stays,
  // whole, tagged as kept and last read for the shared member.
  await step(view, J, 1);
  expect(selectedNode(view)!.dataset.family).toBe(HBJ_ID);
  expect(view.getByTestId('review-center-family').dataset.family).toBe(HBJ_ID);
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(keptTags(view)).toHaveLength(1);
  expect(keptTags(view)[0]).toBe('kept');
  expect(familyBlock(view, R6R_ID).dataset.familyKept).toBe('true');
  expect(memberOccurrences(familyBlock(view, R6R_ID)).size).toBe(24);

  // Press 9: nothing later in the walked tree. No request is made.
  await step(view, J, 0);
  expect(triageStatus(view)).toBe('No later change in this tree; the selection stays.');
  expect(selectedNode(view)!.dataset.family).toBe(HBJ_ID);

  // k returns to the shared member under the first family, and six more presses reach the first change.
  await step(view, K, 0);
  expect(selectedName(view)).toBe('INV-2TQGXFAX');
  expect(
    selectedNode(view)!.closest<HTMLElement>('[data-testid=review-family]')!.dataset.family,
  ).toBe(R6R_ID);
  for (const change of [...CHANGES].reverse().slice(1)) {
    await step(view, K, 0);
    expect(selectedName(view)).toBe(change);
  }
  await step(view, K, 0);
  expect(triageStatus(view)).toBe('No earlier change in this tree; the selection stays.');
  expect(selectedName(view)).toBe('INV-2E8MG43K');
  // The workspace is the same node throughout.
  expect(view.getByTestId('review-workspace')).toBe(workspace);
});

// A node's accessible description, as the role query computes it (whitespace collapsed).
function descriptionOf(node: HTMLElement): string {
  let description = '';
  within(node.parentElement!).getAllByRole('button', {
    description: (computed, element) => {
      if (element === node) description = computed;
      return true;
    },
  });
  return description.replace(/\s+/g, ' ').trim();
}

it('keeps the first family when its second family is clicked, and the previous-change control reaches the shared member', async () => {
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

  // A click on the second family's row opens its review and no longer narrows the tree.
  const before = reviewCount();
  await clickedSecondFamily(view);
  expect(reviewCount() - before).toBe(1);
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(familyBlock(view, R6R_ID).dataset.familyKept).toBe('true');
  expect(memberOccurrences(familyBlock(view, R6R_ID)).size).toBe(24);
  // The visible previous-change control selects the shared member under the first family: the same
  // selection a key step makes.
  fireEvent.click(view.getByTestId('review-previous-change'));
  await settled(view);
  expect(selectedName(view)).toBe('INV-2TQGXFAX');
  expect(
    selectedNode(view)!.closest<HTMLElement>('[data-testid=review-family]')!.dataset.family,
  ).toBe(R6R_ID);
});

it('tags a kept family, counts it apart, describes the subject context only and reads one answer', async () => {
  const view = open(SHARED);
  await clickedSecondFamily(view);
  const rail = view.getByTestId('review-catalogue-navigation');
  // The tag is visible and is the family row's accessible description; it names the subject the
  // family was last read for.
  const tag = view.getByTestId('review-family-kept');
  expect(tag.textContent).toBe('kept');
  const keptRow = within(familyBlock(view, R6R_ID)).getByTestId('review-family-open');
  expect(descriptionOf(keptRow)).toContain('Kept; last read for invariant INV-2TQGXFAX');
  const ownRow = within(familyBlock(view, HBJ_ID)).getByTestId('review-family-open');
  expect(ownRow.getAttribute('aria-describedby')).toBeNull();
  expect(view.queryByTestId('review-family-filter-scope')).toBeNull();
  expect(view.queryByText('Family context details')).toBeNull();
  // The reading area shows the selected family's review and nothing of the kept family.
  const center = view.getByTestId('review-center-column');
  expect(within(center).getByTestId('review-center-family').dataset.family).toBe(HBJ_ID);
  expect(center.textContent).toContain(HBJ_TITLE);
  expect(center.textContent).not.toContain(R6R_TITLE);
  expect(center.querySelectorAll(`[data-family="${R6R_ID}"]`)).toHaveLength(0);
  expect(center.querySelector('[data-testid="review-family-kept"]')).toBeNull();
  expect(rail.contains(tag)).toBe(true);
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
  // The hidden kept family is not visited: nothing earlier in the tree as shown.
  await step(view, K, 0);
  expect(triageStatus(view)).toBe('No earlier change in this tree; the selection stays.');
  fireEvent.change(filter, { target: { value: '' } });
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(memberOccurrences(familyBlock(view, R6R_ID)).size).toBe(24);
  expect(familyBlock(view, R6R_ID).dataset.familyKept).toBe('true');
  await step(view, K, 0);
  expect(selectedName(view)).toBe('INV-2TQGXFAX');
});

it('drops "No later change" when the filter that hid the later family is cleared', async () => {
  const view = open(SHARED);
  await waitFor(() => expect(families(view)).toEqual([R6R_ID, HBJ_ID]));
  // The shared member is the last change of the first family. Under a filter that shows this family
  // alone, the tree as shown holds nothing later.
  expect(selectedName(view)).toBe('INV-2TQGXFAX');
  selectedNode(view)!.focus();
  const filter = view.getByTestId('review-family-filter');
  fireEvent.change(filter, { target: { value: R6R_TITLE } });
  expect(families(view)).toEqual([R6R_ID]);
  await step(view, J, 0);
  expect(triageStatus(view)).toBe('No later change in this tree; the selection stays.');
  // Cleared, the filter shows the second family again: a later change, so the message is gone, and
  // the next j moves on to that family.
  fireEvent.change(filter, { target: { value: '' } });
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(triageStatus(view)).toBe('');
  await step(view, J, 1);
  expect(selectedNode(view)!.dataset.family).toBe(HBJ_ID);
});

it('does not show "No later change" again when the filter is changed and changed back to the same rows', async () => {
  const view = open(SHARED);
  await clickedSecondFamily(view);
  // The second family's own row is the last change of the tree.
  await step(view, J, 0);
  expect(triageStatus(view)).toBe('No later change in this tree; the selection stays.');
  const filter = view.getByTestId('review-family-filter');
  fireEvent.change(filter, { target: { value: 'Comparison-bound' } });
  expect(families(view)).toEqual([HBJ_ID]);
  expect(triageStatus(view)).toBe('');
  // The tree shows the rows the message was said for again. The message answered one key press and
  // is gone: the selection may have changed in between.
  fireEvent.change(filter, { target: { value: '' } });
  expect(families(view)).toEqual([R6R_ID, HBJ_ID]);
  expect(triageStatus(view)).toBe('');
  // A new press is answered as before.
  await step(view, J, 0);
  expect(triageStatus(view)).toBe('No later change in this tree; the selection stays.');
});
