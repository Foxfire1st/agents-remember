// The traversal status of `useChangeTraversal` (MIK-R39 rule 11), on the hook alone. Every render
// is recorded, so the test sees the very render in which the tree shows other rows; a mounted test
// sees only what is left when that turn is over.
import { cleanup, fireEvent, render } from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, expect, it } from 'vitest';
import { useChangeTraversal } from './changeTraversal';

afterEach(cleanup);

const NO_LATER = 'No later change in this tree; the selection stays.';

// A tree without a change: every move forward ends at once. `rendered` takes each render's status.
function Probe({ rows, rendered }: { rows: string; rendered: string[] }) {
  const tree = useRef<HTMLElement>(null);
  const { move, status } = useChangeTraversal(tree, false, rows);
  rendered.push(`${rows}: ${status}`);
  return (
    <section ref={tree}>
      <ul data-testid="review-family-list" />
      <button type="button" onClick={() => move(1)}>
        next
      </button>
    </section>
  );
}

it('renders a status only beside the rows it was said for, and never again once other rows were shown', () => {
  const rendered: string[] = [];
  const view = render(<Probe rows="first" rendered={rendered} />);
  fireEvent.click(view.getByText('next'));
  expect(rendered.at(-1)).toBe(`first: ${NO_LATER}`);
  // Other rows: no render shows the status beside them, the first one included.
  view.rerender(<Probe rows="second" rendered={rendered} />);
  const beside = rendered.filter((one) => one.startsWith('second'));
  expect(beside.length).toBeGreaterThan(0);
  expect(new Set(beside)).toEqual(new Set(['second: ']));
  // The same rows again: the status does not come back.
  const before = rendered.length;
  view.rerender(<Probe rows="first" rendered={rendered} />);
  expect(new Set(rendered.slice(before))).toEqual(new Set(['first: ']));
  // A new move is answered as before.
  fireEvent.click(view.getByText('next'));
  expect(rendered.at(-1)).toBe(`first: ${NO_LATER}`);
});
