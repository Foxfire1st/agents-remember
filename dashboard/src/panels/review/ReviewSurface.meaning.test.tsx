// MIK-R43 mounted: the real ReviewSurface with only fetch replaced, over answers
// captured from this leaf's build (l43.*) plus the L37 bodies (l86.*) for the
// worklist panel and the missing-field failure.
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ReviewSurface } from './ReviewSurface';
import { treeOrderStore } from './triageOrderPreference';
import { treeViewStore } from './triageViewPreference';
import { catalogue, selectedMemberId, serve as serve86, target as target86 } from './l86.test-utils';
import { catalogue as catalogue43, serve, subject, target, taskBody } from './l43.test-utils';

beforeEach(() => {
  localStorage.clear();
  treeViewStore.getState().setView('changed');
  treeOrderStore.getState().setOrder('triage');
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mount(kind: 'family' | 'invariant', selectorId: string) {
  return render(
    <ReviewSurface
      repo={target.repo}
      master={target.master}
      leaf={target.leaf}
      selectorKind={kind}
      selectorId={selectorId}
      onBack={() => undefined}
    />,
  );
}

it('shows effect badges in the changed and the all tree view: one, two, four and unrecorded', async () => {
  serve();
  const view = render(
    <ReviewSurface
      repo={target.repo}
      master={target.master}
      leaf={target.leaf}
      selectorKind="family"
      selectorId={subject.family}
      onBack={() => undefined}
    />,
  );
  const rail = await view.findByTestId('review-catalogue-navigation');
  await view.findByTestId('review-center-family');
  const badges = () =>
    within(rail)
      .getAllByTestId('review-meaning-badge')
      .map((node) => node.textContent);
  // Four steps collapse to first, ellipsis, last; two join with an arrow.
  expect(badges()).toContain('weaken → … → merge');
  expect(badges()).toContain('clarify → strengthen');
  expect(badges()).toContain('clarify');
  expect(badges()).toContain('effect not recorded');
  // The badge is in the row's accessible name.
  const named = within(rail).getByRole('button', { name: /weaken → … → merge/ });
  expect(named.getAttribute('data-tree-node')).toBe('member');
  fireEvent.click(within(rail).getByRole('button', { name: /View: Changed/ }));
  await waitFor(() =>
    expect(within(rail).getByRole('button', { name: /View: All/ })).toBeTruthy(),
  );
  expect(badges()).toContain('weaken → … → merge');
  expect(badges()).toContain('effect not recorded');
});

it('lists every step beside the selected invariant with its fields', async () => {
  const { asked } = serve();
  const view = mount('invariant', subject.invariant4);
  const block = await view.findByTestId('review-recorded-steps');
  expect(block.querySelector('h3')?.textContent).toBe('Recorded steps');
  // More than one step: the successive-judgments line, four steps in attempt order.
  expect(block.textContent).toContain("These are the leaf's successive recorded judgments.");
  const steps = within(block).getAllByTestId('review-recorded-step');
  expect(steps).toHaveLength(4);
  expect(steps[0].textContent).toContain('attempt 1');
  expect(steps[0].textContent).toContain('revision 2');
  expect(steps[0].textContent).toContain('weaken');
  expect(steps[0].textContent).toContain('ROW-F11111');
  expect(steps[3].textContent).toContain('attempt 10');
  expect(steps[3].textContent).toContain('revision 5');
  expect(steps[3].textContent).toContain('merge');
  for (const step of steps) {
    expect(step.textContent).toMatch(/history file (closed|still open)/);
  }
  // Row ids and history paths are readable in full inside the row-record disclosure.
  expect(block.textContent).toContain('knowledge/history/260101-TRV-L1-attempt-10.json');
  // The block is never inside a disclosure.
  expect(block.closest('details')).toBeNull();
  const reads = asked.length;
  expect(asked.filter((url) => url.pathname === '/api/review/intent')).toHaveLength(1);
  expect(asked).toHaveLength(reads);
});

it('shows the family block and the old row without inventing a label', async () => {
  serve();
  const view = mount('family', subject.family);
  const block = await view.findByTestId('review-recorded-steps');
  expect(block.textContent).toContain('attempt 1');
  expect(block.textContent).toContain('revision 2');
  expect(block.textContent).toContain('strengthen');
  expect(block.textContent).not.toContain("These are the leaf's successive recorded judgments.");
  view.unmount();
  const old = mount('family', subject.oldfamily);
  const oldBlock = await old.findByTestId('review-recorded-steps');
  expect(oldBlock.textContent).toContain('effect not recorded');
  expect(oldBlock.textContent).toContain('family revision not recorded');
  expect(oldBlock.textContent).toContain(
    'This row was written before the effect label became required.',
  );
  old.unmount();
});

it('explains all nine labels by keyboard and states none and unavailable', async () => {
  serve();
  const user = userEvent.setup();
  const view = mount('invariant', subject.invariant4);
  const block = await view.findByTestId('review-recorded-steps');
  const toggle = within(block).getByTestId('review-meaning-explanations-toggle');
  expect(toggle.tagName).toBe('BUTTON');
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  // Real keyboard activation: Tab to the toggle, Enter expands, Space collapses.
  await user.tab();
  let tabs = 1;
  while (document.activeElement !== toggle && tabs < 200) {
    await user.tab();
    tabs += 1;
  }
  expect(document.activeElement).toBe(toggle);
  await user.keyboard('{Enter}');
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  const list = within(block).getByTestId('review-meaning-explanations');
  expect(within(list).getAllByRole('listitem')).toHaveLength(9);
  for (const label of [
    'restore',
    'clarify',
    'introduce',
    'strengthen',
    'weaken',
    'replace',
    'split',
    'merge',
    'retire',
  ]) {
    expect(list.textContent).toContain(`${label}:`);
  }
  expect(document.activeElement).toBe(toggle);
  await user.keyboard(' ');
  await waitFor(() => expect(toggle.getAttribute('aria-expanded')).toBe('false'));
  expect(view.queryByTestId('review-meaning-explanations')).toBeNull();
  view.unmount();
  const noneView = mount('invariant', subject.none);
  const noneBlock = await noneView.findByTestId('review-recorded-steps');
  expect(noneBlock.textContent).toContain('No meaning effect recorded by this leaf');
  noneView.unmount();
  const { asked } = serve({ unavailableFor: subject.invariant4 });
  const failedView = mount('invariant', subject.invariant4);
  const failedBlock = await failedView.findByTestId('review-recorded-steps');
  expect(failedBlock.textContent).toContain('incomplete');
  expect(asked.filter((url) => url.pathname === '/api/review/intent')).toHaveLength(1);
});

it('shows a missing field as a failure, not as none', async () => {
  serve86();
  const view = render(
    <ReviewSurface
      repo={target86.repo}
      master={target86.master}
      leaf={target86.leaf}
      selectorKind="invariant"
      selectorId={selectedMemberId}
      onBack={() => undefined}
    />,
  );
  const missing = await view.findByTestId('review-recorded-steps-missing');
  expect(missing.textContent).toContain('invalid answer');
  expect(view.queryByTestId('review-recorded-steps')).toBeNull();
  view.unmount();
});

function mountTask() {
  return render(
    <ReviewSurface
      repo={target.repo}
      master={target.master}
      leaf={target.leaf}
      onBack={() => undefined}
    />,
  );
}

it('fails a no-subject answer without the required field on the source branch', async () => {
  const stripped = {
    ...taskBody,
    payload: { ...taskBody.payload!, meaning_effects: undefined },
  };
  // No selectable subjects: the surface opens the task-context center directly.
  const emptyCatalogue = {
    ...catalogue43,
    entries: [],
    family_total: 0,
    invariant_total: 0,
    total_subjects: 0,
    outline: { ...catalogue43.outline!, families: [], invariants: [] },
  };
  serve({ taskReply: stripped, catalogueReply: emptyCatalogue });
  const view = mountTask();
  // The source-only branch renders (no subject selected) and the required-field
  // failure shows: an answer without the field is invalid, never none.
  await view.findByTestId('review-center-unselected');
  const missing = await view.findByTestId('review-recorded-steps-missing');
  expect(missing.textContent).toContain('invalid answer');
  expect(view.queryByTestId('review-recorded-steps')).toBeNull();
  view.unmount();
});

it('accepts a no-subject answer whose field is genuinely empty', async () => {
  const emptyCatalogue = {
    ...catalogue43,
    entries: [],
    family_total: 0,
    invariant_total: 0,
    total_subjects: 0,
    outline: { ...catalogue43.outline!, families: [], invariants: [] },
  };
  serve({ catalogueReply: emptyCatalogue });
  const view = mountTask();
  // The task capture carries the field with no records: valid, so no failure and
  // the source branch stands on its own.
  await view.findByTestId('review-center-unselected');
  await waitFor(() =>
    expect(view.queryByTestId('review-reading-pending')).toBeNull(),
  );
  expect(view.queryByTestId('review-recorded-steps-missing')).toBeNull();
  expect(view.queryByTestId('review-recorded-steps')).toBeNull();
  view.unmount();
});

it('prints the governing row effect in the worklist panel', async () => {
  serve86();
  const view = render(
    <ReviewSurface
      repo={target86.repo}
      master={target86.master}
      leaf={target86.leaf}
      selectorKind="invariant"
      selectorId={selectedMemberId}
      onBack={() => undefined}
    />,
  );
  const block = await view.findByTestId('review-subject-worklist');
  await waitFor(() =>
    expect(block.textContent).toContain('changed · weaken (ROW-KY2EJG)'),
  );
  view.unmount();
  const familyId = catalogue.outline!.families.find((one) => one.id === 'FAM-7B6N3Y4Q')!
    .selector_id;
  const familyView = render(
    <ReviewSurface
      repo={target86.repo}
      master={target86.master}
      leaf={target86.leaf}
      selectorKind="family"
      selectorId={familyId}
      onBack={() => undefined}
    />,
  );
  const familyBlock = await familyView.findByTestId('review-subject-worklist');
  await waitFor(() =>
    expect(familyBlock.textContent).toContain('changed · effect not recorded (ROW-N46VKD)'),
  );
  familyView.unmount();
});

it('selecting a subject issues no request but its own review', async () => {
  const { asked } = serve();
  const view = mount('invariant', subject.invariant4);
  await view.findByTestId('review-recorded-steps');
  const rail = await view.findByTestId('review-catalogue-navigation');
  const intents = () => asked.filter((url) => url.pathname === '/api/review/intent');
  const entries = () => asked.filter((url) => url.pathname === '/api/review/intent/entries');
  expect(intents()).toHaveLength(1);
  const node = Array.from(rail.querySelectorAll<HTMLElement>('[data-tree-node]')).find((one) =>
    one.textContent?.includes('INV-C11114'),
  )!;
  const seen = asked.length;
  fireEvent.click(node);
  await waitFor(() => expect(intents()).toHaveLength(2));
  // The new subject's review travels alone: no second catalogue read, no new route,
  // and the only other request is the pre-existing entry-cards read.
  expect(entries()).toHaveLength(1);
  const fresh = asked.slice(seen);
  expect(fresh.map((url) => url.pathname).sort()).toEqual(
    expect.arrayContaining(['/api/review/intent']),
  );
  for (const url of fresh) {
    expect(['/api/review/intent', '/api/review/trees']).toContain(url.pathname);
    if (url.pathname === '/api/review/intent') {
      expect(url.searchParams.get('selectorId')).toBe(subject.invariant2);
    } else {
      // The only trees read a selection may add is the pre-existing entry-cards
      // read for the new subject's invariants -- never a second catalogue or lane.
      expect(url.searchParams.has('invariants')).toBe(true);
    }
  }
  const block = await view.findByTestId('review-recorded-steps');
  await waitFor(() => expect(block.textContent).toContain('ROW-F11118'));
  expect(block.textContent).toContain('ROW-F11119');
  expect(block.textContent).toContain('clarify');
  expect(block.textContent).toContain('strengthen');
  view.unmount();
});
