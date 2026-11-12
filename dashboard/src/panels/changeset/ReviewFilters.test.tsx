import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { ReviewFilters } from './ReviewFilters';

afterEach(cleanup);

it('names the changed-file attribution filter and why its count is unavailable', () => {
  const view = render(<ReviewFilters data={null} filter="unexplained" onFilter={() => undefined}
    context={{ page: { comparison: 1, filter: 'unexplained' }, lane: null,
      inventory: { state: 'unavailable', entries: [], listed_total: 0, detail: 'read failed',
        partial: false, command: 'git diff', unrepresentable_paths: [] } }} />);
  expect(view.getByRole('button', { name: 'Changes without an invariant (the change set could not be measured)' })).toBeTruthy();
});
