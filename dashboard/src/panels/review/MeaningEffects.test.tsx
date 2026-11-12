// The Recorded steps block in the reading column (MIK-R43; placed in the subject by 9dde2b09): its
// heading takes the column's section-label look and its step lines and toggle read as the column's
// quiet parts (D401, the mockup's `.label` and `.smeta`), with MIK-R43's content unchanged.
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';

import type { RecordMeaningEffects } from '../../data/review';
import { MissingMeaningEffects, RecordedStepsBlock } from './MeaningEffects';
import { muted, sublabel } from './reading/readingStyles';

afterEach(cleanup);

const answer: RecordMeaningEffects = {
  record: 'f0f0f0f0-0000-5000-8000-000000000001',
  kind: 'invariant',
  state: 'steps',
  detail: '',
  steps: [
    {
      attempt: 1,
      row_id: 'ROW-TBCYD5',
      history_path: 'knowledge/history/260928-MIK-L181.json',
      closed: true,
      reason: 'A leaf seat now wakes the seat itself for the first two ends of a run.',
      effect: 'weaken',
      revision: 3,
    },
  ],
};

it('draws its heading as a section label and its step line and toggle as quiet parts', () => {
  const view = render(<RecordedStepsBlock answer={answer} />);
  const block = view.getByTestId('review-recorded-steps');
  const heading = within(block).getByRole('heading', { level: 3, name: 'Recorded steps' });
  expect(heading.className).toContain(sublabel);
  // The steps are a list whose order the lines say ("attempt n"): no list marker is drawn for them.
  expect(block.querySelector('ol')).toBeNull();
  const step = within(block).getByTestId('review-recorded-step');
  expect(step.getAttribute('role')).toBe('listitem');
  const line = within(step).getByTestId('review-recorded-step-line');
  expect(line.textContent).toBe('attempt 1 · revision 3 · weaken · ROW-TBCYD5');
  expect(line.className).toContain(muted);
  const toggle = within(block).getByTestId('review-meaning-explanations-toggle');
  expect(toggle.tagName).toBe('BUTTON');
  // Out of the column's shared control look (ReviewLayout.tsx), as its other quiet controls are.
  expect(toggle.hasAttribute('data-own-look')).toBe(true);
  // MIK-R43's content is unchanged and nothing of it moves behind a closed disclosure.
  expect(step.textContent).toContain(answer.steps[0].reason);
  expect(step.textContent).toContain('history file closed');
  expect([...block.querySelectorAll('details')].map((node) => node.dataset.disclosure)).toEqual(['identifiers']);
  fireEvent.click(toggle);
  expect(within(block).getAllByRole('listitem')).toHaveLength(10);
});

it('draws the missing-field failure under the same section label', () => {
  const view = render(<MissingMeaningEffects />);
  const heading = within(view.getByTestId('review-recorded-steps-missing')).getByRole('heading', { level: 3 });
  expect(heading.className).toContain(sublabel);
});
