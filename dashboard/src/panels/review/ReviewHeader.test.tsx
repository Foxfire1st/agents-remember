import { cleanup, render, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { ReviewHeader } from './ReviewHeader';

afterEach(cleanup);

it('mounts visible Keys beside Refresh and restores trigger focus after closing its reference', async () => {
  const user = userEvent.setup();
  const onBack = vi.fn();
  const view = render(
    <div data-kbzone="review">
      <ReviewHeader
        repo="synthetic-repo"
        master="synthetic-master"
        leaf="synthetic-leaf"
        selectorKind="invariant"
        selectorId="INV-SYNTHETIC"
        instead={null}
        history="recorded"
        refresh={<button type="button">Refresh</button>}
        onBack={onBack}
      />
    </div>,
  );
  const keys = view.getByRole('button', { name: 'Keys' });
  expect(view.getByRole('button', { name: 'Refresh' }).nextElementSibling).toBe(keys);
  await user.click(keys);
  const dialog = await view.findByRole('dialog', { name: 'Reviewer keys' });
  expect(within(dialog).getByText('Next change').parentElement?.textContent).toContain('j');
  expect(within(dialog).getByText('Previous change').parentElement?.textContent).toContain('k');
  expect(dialog.contains(document.activeElement)).toBe(true);
  await user.keyboard('{Escape}');
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(keys));
  await user.click(keys);
  const reopened = await view.findByRole('dialog', { name: 'Reviewer keys' });
  await user.click(within(reopened).getByRole('button', { name: 'Close' }));
  await waitFor(() => expect(view.queryByRole('dialog')).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(keys));
  const identifiers = view.getByTestId('review-subject');
  expect(identifiers.tagName).toBe('DIV');
  expect(identifiers.textContent).toContain('invariant INV-SYNTHETIC');
  expect(view.getByTestId('review-history').textContent).toBe('Historical task comparison');
  await user.click(view.getByTestId('review-back'));
  expect(onBack).toHaveBeenCalledOnce();
});
