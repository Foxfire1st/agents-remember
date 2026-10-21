import { cleanup, render, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { ReviewHeader } from './ReviewHeader';

afterEach(cleanup);

// The top line (MIK-R89 v2 rule 6): back, the task's title (the leaf id when the task list has none),
// the chip and comparison slots, Refresh and the keys reference with the page's two sidebar keys.
it('mounts visible Keys beside Refresh and restores trigger focus after closing its reference', async () => {
  const user = userEvent.setup();
  const onBack = vi.fn();
  const view = render(
    <div data-kbzone="review">
      <ReviewHeader
        leaf="synthetic-leaf"
        title="A synthetic task"
        chip={<span data-testid="probe-chip">chip</span>}
        refresh={<button type="button">Refresh</button>}
        onBack={onBack}
      />
    </div>,
  );
  expect(view.getByTestId('review-title').textContent).toBe('A synthetic task');
  expect(view.getByTestId('probe-chip')).toBeTruthy();
  const keys = view.getByRole('button', { name: 'Keys' });
  expect(view.getByRole('button', { name: 'Refresh' }).nextElementSibling).toBe(keys);
  await user.click(keys);
  const dialog = await view.findByRole('dialog', { name: 'Reviewer keys' });
  expect(within(dialog).getByText('Next block').parentElement?.textContent).toContain('j');
  expect(within(dialog).getByText('Previous block').parentElement?.textContent).toContain('k');
  expect(within(dialog).getByText('Tree sidebar').parentElement?.textContent).toContain('[');
  expect(within(dialog).getByText('Chat sidebar').parentElement?.textContent).toContain(']');
  expect(dialog.contains(document.activeElement)).toBe(true);
  await user.keyboard('{Escape}');
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(keys));
  await user.click(keys);
  const reopened = await view.findByRole('dialog', { name: 'Reviewer keys' });
  await user.click(within(reopened).getByRole('button', { name: 'Close' }));
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(keys));
  await user.click(view.getByTestId('review-back'));
  expect(onBack).toHaveBeenCalledOnce();
});

it('names the leaf when the task list holds no title', () => {
  const view = render(<ReviewHeader leaf="synthetic-leaf" onBack={() => undefined} />);
  expect(view.getByTestId('review-title').textContent).toBe('synthetic-leaf');
});

// MIK-R89 v2 rules 1 and 11 at 390 px. jsdom has no layout, so this pins the declarations the line
// depends on: at 40rem and narrower the title box keeps the first line beside back and sends the
// chip and the comparison to the next ones, and none of its texts can run past the line or under the
// chip: the label and the record wrap, and the title takes its own line and ends in an ellipsis.
it('keeps every part of the top line inside the line at 40rem', () => {
  const narrow = (name: string) => `[@media_(max-width:_40rem)]:${name}`;
  const view = render(
    <ReviewHeader
      leaf="260928-MIK-L181"
      title="A seat that stops without handing over is sent back to its work"
      record="Reconstructed from recorded endpoints"
      chip={<span data-testid="probe-chip">chip</span>}
      onBack={() => undefined}
    />,
  );
  const classes = (node: Element) => [...node.classList];
  const line = view.getByTestId('review-top-line');
  const title = view.getByTestId('review-title');
  const box = title.parentElement!;
  const label = box.querySelector('b')!;
  expect(classes(line)).toContain('flex-wrap_wrap');
  expect(classes(box)).toEqual(
    expect.arrayContaining(['min-w_0', narrow('flex_1_1_14rem'), narrow('flex-wrap_wrap')]),
  );
  expect(classes(label)).toContain(narrow('white-space_normal'));
  expect(classes(title)).toEqual(
    expect.arrayContaining(['min-w_0', 'ov_hidden', 'tov_ellipsis', narrow('flex-b_100%')]),
  );
  expect(classes(view.getByTestId('review-history'))).toContain(narrow('white-space_normal'));
});

// D401 (the page compared with the mockup by eye; mockup app.js:179, `Intent review · L181`): the
// label names the leaf by its short id, the full id in its tooltip, so the task's title fits the
// 1440 px line; a record label gives way before the title does and keeps its first word.
it("names the leaf by its short id, so the title fits, and lets a record label give way first", () => {
  const view = render(
    <ReviewHeader
      leaf="260928-MIK-L181"
      title="A seat that stops without handing over is sent back to its work"
      record="Historical task comparison"
      onBack={() => undefined}
    />,
  );
  const classes = (node: Element) => [...node.classList];
  const label = view.getByTestId('review-title').parentElement!.querySelector('b')!;
  expect(label.textContent).toBe('Intent review · L181');
  expect(label.getAttribute('title')).toBe('260928-MIK-L181');
  expect(classes(view.getByTestId('review-history'))).toEqual(expect.arrayContaining(['flex-sh_100', 'min-w_9ch']));
  expect(classes(view.getByTestId('review-title')).some((name) => name.startsWith('flex-sh_'))).toBe(false);
  view.unmount();
  const other = render(<ReviewHeader leaf="synthetic-leaf" onBack={() => undefined} />);
  const plain = other.getByTestId('review-title').parentElement!.querySelector('b')!;
  expect(plain.textContent).toBe('Intent review · synthetic-leaf');
  expect(plain.hasAttribute('title')).toBe(false);
});
