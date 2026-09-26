// Ordinary entry uses the real catalogue/read cycle; captured records supply the semantic content.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewEntry, ReviewPayload, ReviewResult } from '../../data/review';
import { ReviewSurface } from './ReviewSurface';

const captured = JSON.parse(
  readFileSync(
    path.join(
      path.dirname(new URL(import.meta.url).pathname),
      'familyReview.complete.captured.json',
    ),
    'utf8',
  ),
) as ReviewResult;
const recorded = captured.payload as ReviewPayload;
const families = recorded.family_context!.entries;
const catalogue: ReviewEntry[] = families.map((family) => ({
  selector_kind: 'family',
  selector_id: family.family_id,
  label: family.display_label ?? family.family_id,
  presence: 'both',
}));
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
const target = {
  repo: 'agents-remember',
  master: 'review-navigation',
  leaf: 'recorded-task',
  onBack: () => undefined,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('opens a real family from ordinary entry, navigates another family in place and preserves the full source population', async () => {
  const urls: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      urls.push(url);
      if (url.pathname.endsWith('/entries'))
        return response({
          state: 'entries',
          entries: catalogue,
          total_subjects: catalogue.length,
          family_total: catalogue.length,
          invariant_total: 0,
        });
      if (url.pathname.endsWith('/source-content'))
        return response({
          state: 'refused',
          refusal: {
            code: 'not-found',
            detail: 'source bytes are outside this captured interaction',
            next_action: 'inspect the mounted source route',
          },
        });
      if (url.searchParams.get('selectorKind') === 'invariant') return response(captured);
      const family = families.find((item) => item.family_id === url.searchParams.get('selectorId'));
      return response({
        ...captured,
        payload: {
          ...recorded,
          family_context: {
            ...recorded.family_context,
            entries: family ? [family] : [],
            families_returned: family ? 1 : 0,
            state: family ? 'recorded' : 'no_subject_selected',
          },
        },
      });
    }),
  );
  const view = render(<ReviewSurface {...target} />);
  await waitFor(() =>
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[0].family_id),
  );
  expect(view.getByTestId('review-workspace').dataset.fullFile).toBe('false');
  const allFiles = view.getAllByTestId('review-inventory-entry').map((row) => row.textContent);
  const second = view
    .getAllByTestId('review-catalogue-subject')
    .find((button) => button.dataset.subjectId === families[1].family_id)!;
  fireEvent.click(second);
  await waitFor(() =>
    expect(view.getByTestId('review-center-family').dataset.family).toBe(families[1].family_id),
  );
  expect(urls.some((url) => url.searchParams.get('selectorId') === families[1].family_id)).toBe(
    true,
  );
  expect(document.activeElement).toBe(view.getAllByTestId('review-family-open')[0]);
  const member = families[1].after.members.find(
    (row) => row.invariant_id === recorded.knowledge.revision_selection?.record_id,
  )!;
  fireEvent.click(
    view
      .getAllByTestId('review-family-member-open')
      .find((node) => node.dataset.revision === member.invariant_revision_id)!,
  );
  const center = await view.findByTestId('review-center-member');
  const statement = center.querySelector('[data-testid^="review-center-member-"]')!;
  const expressions = within(center).getByTestId('review-expression-diffs');
  expect(
    statement.compareDocumentPosition(expressions) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(view.getAllByTestId('review-inventory-entry').map((row) => row.textContent)).toEqual(
    allFiles,
  );
  const file = view.getAllByTestId('review-inventory-open')[0];
  file.focus();
  fireEvent.click(file);
  await view.findByTestId('review-source-refusal');
  fireEvent.click(view.getByTestId('review-center-full-file'));
  fireEvent.change(view.getByTestId('review-center-diff-layout'), { target: { value: 'inline' } });
  expect(view.getByTestId('review-workspace').dataset.fullFile).toBe('true');
  const region = view.getByTestId('review-center-column');
  const focus = vi.spyOn(region, 'focus');
  const scroll = vi.spyOn(region, 'scrollIntoView');
  fireEvent.click(view.getByTestId('review-jump-to-selection'));
  expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  expect(scroll).toHaveBeenCalledWith({ block: 'start' });
  expect(view.getByTestId('review-workspace').dataset.diffLayout).toBe('inline');
  expect(file.getAttribute('aria-expanded')).toBe('true');
  fireEvent.click(file);
  expect(document.activeElement).toBe(file);
  fireEvent.click(view.getByTestId('review-task-source'));
  await waitFor(() => expect(view.getByTestId('review-center-unselected')).toBeTruthy());
  expect(view.getAllByTestId('review-inventory-entry')).toHaveLength(allFiles.length);
});

it('keeps unreadable knowledge source-only without inventing a family or telling the reader to select one', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) =>
      address.includes('/entries')
        ? response({
            state: 'refused',
            refusal: {
              code: 'knowledge_unavailable',
              detail: 'The historical knowledge snapshot cannot be read.',
              next_action: 'Inspect source or restore the recorded snapshot.',
            },
          })
        : response({
            ...captured,
            payload: {
              ...recorded,
              knowledge: { ...recorded.knowledge, selection_state: 'task_context' },
              family_context: {
                ...recorded.family_context,
                state: 'unavailable',
                entries: [],
                detail: 'Historical snapshot unreadable.',
              },
            },
          }),
    ),
  );
  const view = render(<ReviewSurface {...target} />);
  const tree = await view.findByTestId('review-family-tree');
  expect(tree.dataset.familyState).toBe('unavailable');
  expect(tree.textContent).toContain('could not be read');
  expect(tree.textContent).not.toContain('Choose');
  expect(view.queryByTestId('review-family-open')).toBeNull();
  expect(view.getAllByTestId('review-inventory-entry')).toHaveLength(
    recorded.source.inventory.entries.length,
  );
  expect(view.getByTestId('review-center-unselected').textContent).toContain('Source');
});
