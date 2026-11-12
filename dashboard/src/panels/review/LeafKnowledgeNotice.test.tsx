// Worklist facts stay visible; read failures never mean no items.
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import type { ReviewTreesResult } from '../../data/reviewTrees';
import { LeafWorklistView, SubjectWorklistItems } from './LeafKnowledgeChanges';
const trees = JSON.parse(
  readFileSync(
    path.join(path.dirname(new URL(import.meta.url).pathname), 'laneReview.trees.captured.json'),
    'utf8',
  ),
) as ReviewTreesResult;
afterEach(cleanup);
it('states pending, failed and unconverted reads visibly', () => {
  const open = vi.fn();
  const view = render(
    <LeafWorklistView read={{ phase: 'loading' }} onOpenChanges={open} onOpenPath={open} />,
  );
  expect(screen.getByRole('status').textContent).toContain("Reading the leaf's worklist");
  view.rerender(
    <LeafWorklistView
      read={{
        phase: 'unavailable',
        problem: {
          token: 'unavailable-history',
          code: 'candidate_unresolved',
          detail: 'The worklist could not be read.',
          nextAction: 'Retry after the inputs settle.',
        },
      }}
      onOpenChanges={open}
      onOpenPath={open}
    />,
  );
  expect(screen.getByRole('status').textContent).toContain('Retry after the inputs settle.');
  expect(view.container.querySelector('details')).toBeNull();
  view.rerender(
    <LeafWorklistView read={{ phase: 'not-converted' }} onOpenChanges={open} onOpenPath={open} />,
  );
  expect(screen.getByRole('status').textContent).toContain('memory is not converted');
  view.rerender(<SubjectWorklistItems recordId="INV-ABSENT" read={{ phase: 'loading' }} />);
  expect(screen.getByRole('status').textContent).toContain("Reading the leaf's worklist");
});
it('draws all items and file links with only one identifiers disclosure per group', () => {
  const changes = vi.fn();
  const paths = vi.fn();
  const view = render(
    <LeafWorklistView
      read={{ phase: 'trees', trees }}
      reviewComparison={trees.comparison!.number}
      onOpenChanges={changes}
      onOpenPath={paths}
    />,
  );
  expect(screen.getAllByTestId('review-worklist-item')).toHaveLength(trees.worklist!.items.length);
  const details = [...view.container.querySelectorAll('details')];
  expect(details.length).toBeLessThanOrEqual(8);
  expect(details.every((one) => one.dataset.disclosure === 'identifiers')).toBe(true);
  expect(view.container.querySelector('details [data-review-content]')).toBeNull();
  expect(view.container.querySelector('pre')).toBeNull();
  expect(view.container.textContent).not.toContain('Item facts');
  fireEvent.click(screen.getByRole('button', { name: 'Open changed knowledge files' }));
  expect(changes).toHaveBeenCalledOnce();
  const first = screen.getAllByTestId('review-unexplained-file')[0];
  fireEvent.click(first.querySelector('button')!);
  expect(paths).toHaveBeenCalledWith(first.dataset.path);
  expect(screen.getByTestId('review-worklist-linkage').textContent).toMatch(
    /changed hunks in \d+ files are linked/,
  );
});
it('shows only record items, an honest empty state and the comparison binding', () => {
  const record = trees.worklist!.items.find((one) => one.kind === 'touched_invariant')!.subject;
  const view = render(
    <SubjectWorklistItems
      read={{ phase: 'trees', trees }}
      recordId={record}
      reviewComparison={trees.comparison!.number}
    />,
  );
  expect(screen.getAllByTestId('review-worklist-item')).toHaveLength(
    trees.worklist!.items.filter((one) => one.subject === record).length,
  );
  expect(view.container.querySelector('details')).toBeNull();
  view.rerender(
    <SubjectWorklistItems
      read={{ phase: 'trees', trees }}
      recordId="INV-ABSENT"
      reviewComparison={trees.comparison!.number}
    />,
  );
  expect(view.container.textContent).toContain('No worklist item names this record.');
  view.rerender(
    <SubjectWorklistItems
      read={{ phase: 'trees', trees }}
      recordId={record}
      reviewComparison={trees.comparison!.number + 1}
    />,
  );
  expect(screen.getByRole('status').textContent).toContain('belongs to comparison');
  expect(view.container.textContent).not.toContain('No worklist item');
});
