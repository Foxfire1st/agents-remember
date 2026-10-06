// MIK-R39 in the mounted reviewer, on real data: the family tree keeps the rows the reader walked.
//
// The bodies are the REAL served answers of lane D2's scenario (the scratch leaf 260928-MIK-L33 after
// its scratch curation; walkReal.capture-provenance.json). `ReviewSurface` is the real component and
// only `fetch` is stubbed. Family FAM-R6R095RW has seven changes, the last of them the shared member
// INV-2TQGXFAX; FAM-2HBJREC2 is the other family of that member. Each test fails when the keeping is
// removed (see the mutation record in the leaf's evidence), except the last three: they pin rule 11's
// end message, which speaks of the tree as shown and is dropped for good when the tree then shows
// other rows (an answer adds a later change; the filter is cleared or changed).
import { act, fireEvent, waitFor, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
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
  WAIT,
  type View,
  clickedSecondFamily,
  families,
  familyBlock,
  installWorld,
  keptTags,
  memberOccurrences,
  open,
  press,
  reviewCount,
  revisionOf,
  selectedName,
  selectedNode,
  settled,
  step,
  triageStatus,
} from './walk.test-utils';

installWorld();

it('walks seven changes and the second family by key, and k returns through every change passed', async () => {
  const view = open(R6R);
  await view.findAllByTestId('review-change-badge', undefined, WAIT);
  const workspace = view.getByTestId('review-workspace');
  expect(families(view)).toEqual([R6R_ID]);
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
  expect(keptTags(view)[0]).toContain('INV-2TQGXFAX');
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

const catalogueRows = (view: View) =>
  view.queryAllByTestId('review-catalogue-subject').map((row) => row.dataset.subjectId);

it('keeps the first family when its second family is clicked, and the previous-change control reaches the shared member', async () => {
  const view = open(R6R);
  await view.findAllByTestId('review-change-badge', undefined, WAIT);
  // The shared member is chosen from the list of all invariants: both of its families are shown.
  const all = view.getByTestId('review-all-invariants') as HTMLDetailsElement;
  all.open = true;
  const sharedRow = within(all)
    .getAllByTestId('review-catalogue-subject')
    .find(
      (row) => row.dataset.subjectId === SHARED.payload!.knowledge.revision_selection!.record_id,
    )!;
  fireEvent.click(sharedRow);
  await waitFor(() => expect(families(view)).toEqual([R6R_ID, HBJ_ID]), WAIT);
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
  expect(tag.textContent).toBe('kept · last read for invariant INV-2TQGXFAX');
  const keptRow = within(familyBlock(view, R6R_ID)).getByTestId('review-family-open');
  expect(descriptionOf(keptRow)).toBe('kept · last read for invariant INV-2TQGXFAX');
  const ownRow = within(familyBlock(view, HBJ_ID)).getByTestId('review-family-open');
  expect(ownRow.getAttribute('aria-describedby')).toBeNull();
  // The scope line counts the families of the selected subject and the kept ones apart.
  expect(view.getByTestId('review-family-filter-scope').textContent).toContain(
    '1 families · 1 kept',
  );
  // "Family context details" describes the selected subject's context only: one family.
  const counts = view.getByTestId('review-family-counts').textContent ?? '';
  expect(counts).toContain('1 of 1 recorded family context(s) composed');
  // The reading area shows the selected family's review and nothing of the kept family.
  const center = view.getByTestId('review-center-column');
  expect(within(center).getByTestId('review-center-family').dataset.family).toBe(HBJ_ID);
  expect(center.textContent).toContain(HBJ_TITLE);
  expect(center.textContent).not.toContain(R6R_TITLE);
  expect(center.querySelectorAll(`[data-family="${R6R_ID}"]`)).toHaveLength(0);
  expect(center.textContent).not.toContain('kept');
  expect(rail.contains(tag)).toBe(true);
});

it('lists a catalogue row for every family the tree shows while it shows a kept one', async () => {
  const view = open(SHARED);
  await waitFor(() => expect(families(view)).toEqual([R6R_ID, HBJ_ID]), WAIT);
  // Without a kept family the tree stands in for the families it shows, as before.
  expect(catalogueRows(view)).not.toContain(R6R_ID);
  expect(catalogueRows(view)).not.toContain(HBJ_ID);
  await clickedSecondFamily(view);
  const rows = view.getAllByTestId('review-catalogue-subject');
  expect(catalogueRows(view)).toEqual(expect.arrayContaining([R6R_ID, HBJ_ID]));
  // The tree stands among those rows as a child of the navigation itself, as before this leaf: no
  // element of its own is put around it.
  const navigation = view.getByTestId('review-catalogue-navigation');
  expect(view.getByTestId('review-family-tree').parentElement).toBe(navigation);
  expect(rows.find((row) => row.dataset.subjectId === R6R_ID)!.parentElement).toBe(navigation);
  // Without badges: a catalogue row is its label alone.
  for (const row of rows.filter((one) => [R6R_ID, HBJ_ID].includes(one.dataset.subjectId!)))
    expect(row.querySelector('[data-testid=review-change-badge]')).toBeNull();
  // Choosing the second family there starts afresh with that family alone.
  const before = reviewCount();
  fireEvent.click(rows.find((row) => row.dataset.subjectId === HBJ_ID)!);
  await waitFor(() => expect(families(view)).toEqual([HBJ_ID]), WAIT);
  expect(keptTags(view)).toEqual([]);
  expect(reviewCount() - before).toBe(0);
  expect(catalogueRows(view)).not.toContain(HBJ_ID);
});

it('lets a filter hide kept families with the others and restores them when it is cleared', async () => {
  const view = open(SHARED);
  await clickedSecondFamily(view);
  const filter = view.getByTestId('review-family-filter');
  fireEvent.change(filter, { target: { value: 'Comparison-bound' } });
  expect(families(view)).toEqual([HBJ_ID]);
  expect(view.getByTestId('review-family-filter-scope').textContent).toContain(
    '1/2 families (1 of this subject · 1 kept)',
  );
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

// A held key repeats. From the third further press on, React queues the same status without
// rendering it and applies it after the answer's render: the status must not depend on an update
// made in that render.
it.each([1, 3, 5])(
  'drops "No later change" when the answer that %i further press(es) of j outran brings a later change',
  async (further) => {
    const view = open(R6R);
    await view.findAllByTestId('review-change-badge', undefined, WAIT);
    // The shared member's answer, which brings the second family, is held until the test releases
    // it.
    const shared = SHARED.payload!.knowledge.revision_selection!.record_id;
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => (release = resolve));
    const served = globalThis.fetch;
    vi.stubGlobal('fetch', async (address: string, init?: RequestInit) => {
      if (String(address).includes(shared)) await held;
      return served(address, init);
    });
    // From the fifth change, j selects the sixth, and the next j the shared member.
    const fifth = within(familyBlock(view, R6R_ID))
      .getAllByTestId('review-family-member-open')
      .find((node) => node.dataset.revision === revisionOf(CHANGES[4]))!;
    fifth.focus();
    fireEvent.click(fifth);
    await settled(view);
    await step(view, J, 1);
    expect(selectedName(view)).toBe(CHANGES[5]);
    press(J);
    expect(selectedName(view)).toBe('INV-2TQGXFAX');
    expect(view.getByTestId('review-surface').dataset.reviewPending).toBeDefined();
    expect(families(view)).toEqual([R6R_ID]);
    // Every further j outruns that answer: the tree as it is shown holds nothing later.
    for (let pressed = 0; pressed < further; pressed += 1) press(J);
    expect(triageStatus(view)).toBe('No later change in this tree; the selection stays.');
    release();
    await waitFor(() => expect(families(view)).toEqual([R6R_ID, HBJ_ID]), WAIT);
    // Every render React still owed has been made.
    await act(async () => {});
    // The second family is a later change, so the message is no longer true of the tree: it is
    // gone, and the next j moves on to that family.
    expect(triageStatus(view)).toBe('');
    await step(view, J, 1);
    expect(selectedNode(view)!.dataset.family).toBe(HBJ_ID);
  },
);

it('drops "No later change" when the filter that hid the later family is cleared', async () => {
  const view = open(SHARED);
  await waitFor(() => expect(families(view)).toEqual([R6R_ID, HBJ_ID]), WAIT);
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
