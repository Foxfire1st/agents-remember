import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';

import { KEYMAP_STORAGE_KEY } from '../../data/keymap/preferences';

import { ReviewKeys } from './ReviewKeys';

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

it('opens a named key reference from a visible Keys control and shows effective bindings', async () => {
  window.localStorage.setItem(KEYMAP_STORAGE_KEY, JSON.stringify({
    version: 1, bindings: { 'review.nextChange': 'N' },
  }));
  const view = render(<div data-kbzone="review"><ReviewKeys /></div>);
  fireEvent.click(view.getByRole('button', { name: 'Keys' }));
  const popup = await view.findByRole('dialog', { name: 'Reviewer keys' });
  expect(within(popup).getByText('Next change').parentElement?.textContent).toContain('N');
  expect(within(popup).getByText('Previous change').parentElement?.textContent).toContain('k');
  fireEvent.click(within(popup).getByRole('button', { name: 'Close' }));
  expect(view.queryByRole('dialog')).toBeNull();
});

it('opens with ? outside text fields, ignores editable and other zones, and removes its listener', async () => {
  const view = render(
    <div>
      <div data-kbzone="review">
        <ReviewKeys />
        <input aria-label="Search" />
        <input aria-label="Read-only text" readOnly />
        <textarea aria-label="Notes" />
        <div contentEditable suppressContentEditableWarning data-testid="editable"><span>typing</span></div>
      </div>
      <div data-kbzone="changeset"><button>Change set</button></div>
    </div>,
  );
  const keys = view.getByRole('button', { name: 'Keys' });
  fireEvent.keyDown(keys, { key: '?', shiftKey: true });
  const popup = await view.findByRole('dialog', { name: 'Reviewer keys' });
  fireEvent.click(within(popup).getByRole('button', { name: 'Close' }));
  for (const target of [view.getByLabelText('Search'), view.getByLabelText('Read-only text'), view.getByLabelText('Notes'), view.getByText('typing'), view.getByRole('button', { name: 'Change set' })]) {
    expect(fireEvent.keyDown(target, { key: '?', shiftKey: true })).toBe(true);
    expect(view.queryByRole('dialog')).toBeNull();
  }
  expect(fireEvent.keyDown(keys, { key: '?', ctrlKey: true })).toBe(true);
  expect(view.queryByRole('dialog')).toBeNull();
  view.unmount();
  expect(fireEvent.keyDown(document.body, { key: '?', shiftKey: true })).toBe(true);
});
