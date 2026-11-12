// The landed leaf's delivered pair on the page (MIK-R195 rules 2 and 4): the one line a comparison
// that is not the delivered pair states, its link to the delivered view, the delivered identities in
// Technical details, and the comparison menu's delivered row. The line speaks about which pair is
// shown and nothing about whether an earlier record's own count or statement was right.
import { cleanup, render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { ComparisonSelector } from './top-line/ComparisonSelector';
import { DeliveredFacts, DeliveredLine } from './ReviewScopeHeader';

afterEach(cleanup);

const DELIVERED = {
  state: 'differs' as const,
  statement:
    'Comparison #10 is not that pair: the leaf delivered code 25756231 and memory 456f89a2. Open the delivered comparison to read it.',
  comparison: 11,
  shown: 10,
  code_base_tree: '84402c25d3cb9b090fce33d8a3019534f486115e',
  code_candidate_tree: '25756231aa86f63ba59f91bec01146d27461e470',
  memory_base_tree: '95ed81d5b37118ba39fa37a5e4c3f6761ef913f2',
  memory_candidate_tree: '456f89a2e9c1c2cac541a990f4ea69e180f1514d',
};

it('states the mismatch once and opens the delivered view without a second review surface', async () => {
  const user = userEvent.setup();
  const onOpen = vi.fn();
  const view = render(<DeliveredLine delivered={DELIVERED} onOpen={onOpen} />);
  const line = view.getByTestId('review-delivered-line');
  expect(line.dataset.delivered).toBe('differs');
  expect(line.textContent).toContain('Comparison #10 is not that pair');
  await user.click(view.getByTestId('review-delivered-open'));
  expect(onOpen).toHaveBeenCalledOnce();
});

it('states no line for the delivered pair itself and offers no link while unavailable', () => {
  const delivered = render(
    <DeliveredLine delivered={{ ...DELIVERED, state: 'delivered' }} onOpen={() => undefined} />,
  );
  expect(delivered.queryByTestId('review-delivered-line')).toBeNull();
  const unavailable = render(
    <DeliveredLine
      delivered={{
        state: 'unavailable',
        statement:
          'The delivery cannot be established, so this comparison cannot be compared with it.',
      }}
      onOpen={() => undefined}
    />,
  );
  // An unavailable delivery is said, and it is not turned into a delivered view the reader can open.
  expect(unavailable.getByTestId('review-delivered-line').dataset.delivered).toBe('unavailable');
  expect(unavailable.queryByTestId('review-delivered-open')).toBeNull();
});

it('lists the delivered identities in the page’s technical details', () => {
  const view = render(<DeliveredFacts delivered={DELIVERED} />);
  const facts = view.getByTestId('review-scope-delivered');
  expect(facts.dataset.delivered).toBe('differs');
  expect(facts.textContent).toContain('Delivered pair · #11');
  expect(facts.textContent).toContain('25756231');
  expect(facts.textContent).toContain('456f89a2');
  expect(facts.textContent).toContain('84402c25');
});

it('offers the delivered pair as its own comparison-menu row and marks the number that holds it', async () => {
  const user = userEvent.setup();
  const onChoose = vi.fn();
  const onChooseDelivered = vi.fn();
  const view = render(
    <ComparisonSelector
      leaf="260928-MIK-L187"
      shown={10}
      resolved={11}
      delivered={DELIVERED}
      recordedAt={null}
      now={new Date('2026-10-21T00:00:00Z')}
      latest={12}
      onChoose={onChoose}
      onChooseDelivered={onChooseDelivered}
      pick={null}
    />,
  );
  await user.click(view.getByTestId('review-comparison-button'));
  const delivered = await view.findByTestId('review-comparison-delivered');
  expect(delivered.textContent).toContain('#11');
  await user.click(delivered);
  // The delivered row asks for the delivered view itself -- the page's delivered action, which also
  // leaves a recorded frame -- rather than choosing a number (MIK-R195 F06).
  expect(onChooseDelivered).toHaveBeenCalledTimes(1);
  expect(onChoose).not.toHaveBeenCalled();
  await user.click(view.getByTestId('review-comparison-button'));
  const options = await view.findAllByTestId('review-comparison-option');
  const states = new Map(options.map((one) => [Number(one.dataset.number), one.textContent ?? '']));
  expect(states.get(11)).toContain('delivered');
  expect(states.get(12)).toContain('latest');
  expect(states.get(10)).toContain('read-only');
});
