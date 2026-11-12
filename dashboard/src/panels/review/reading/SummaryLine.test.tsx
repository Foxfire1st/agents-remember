import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';

import { changeSetModel } from '../changeSetOrder';
import { l181Inputs, l181Outline } from '../changeSetOrder.test-utils';
import { SummaryLine } from './SummaryLine';

afterEach(cleanup);

function model() {
  return changeSetModel(l181Inputs(), 'priority', 'changed', { selection: null });
}

function draw(value: ReturnType<typeof model>) {
  return render(<SummaryLine model={value} catalogue={{ loading: false, outline: l181Outline }} summary={{ phase: 'loading' }} lane={null} />);
}

it('shows inventory totals while selected-invariant code has not been requested', () => {
  const value = model();
  value.cards = 'reading';
  value.locations = 'ready';
  const view = draw(value);
  expect(view.getByTestId('review-summary-locations').textContent).toBe('15 changed locations in 5 files');
  expect(view.getByTestId('review-summary-folded').textContent).toBe('59 unchanged or not comparable, folded');
});

it('uses ellipses only while the complete inventory is still being read', () => {
  const value = model();
  value.locations = 'reading';
  value.counts.changedLocations = undefined;
  value.counts.changedFiles = undefined;
  value.counts.folded = undefined;
  const view = draw(value);
  expect(view.getByTestId('review-summary-locations').textContent).toBe('… changed locations in … files');
  expect(view.getByTestId('review-summary-folded').textContent).toBe('… unchanged or not comparable, folded');
});

it.each(['unavailable', 'ready'] as const)('names missing totals as unavailable once the inventory is %s', (state) => {
  const value = model();
  value.cards = 'reading';
  value.locations = state;
  value.counts.changedLocations = undefined;
  value.counts.changedFiles = undefined;
  value.counts.folded = undefined;
  const view = draw(value);
  expect(view.getByTestId('review-summary-locations').textContent).toBe('changed locations: unavailable');
  expect(view.getByTestId('review-summary-folded').textContent).toBe('unchanged or not comparable: unavailable');
});
