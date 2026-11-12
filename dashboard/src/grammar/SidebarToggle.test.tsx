// MIK-R89 v2 rule 8: the one shared sidebar toggle and its remembered state. The six pages that
// mount it are walked in cockpit/sidebarToggle.pages.test.tsx.
import { act, cleanup, render, renderHook } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SidebarToggle, sidebarKey, useSidebar } from './SidebarToggle';

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe('the shared sidebar toggle', () => {
  it('names its sidebar, says whether it is shown, and toggles by click, Enter and Space', async () => {
    const onToggle = vi.fn();
    const view = render(
      <SidebarToggle side="left" open name="Tree sidebar" controls="tree" keyHint="[" onToggle={onToggle} />,
    );
    const button = view.getByRole('button', { name: 'Tree sidebar' });
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(button.getAttribute('aria-controls')).toBe('tree');
    expect(button.getAttribute('title')).toBe('Hide tree sidebar ([)');
    // The open band of the panel icon is drawn on the sidebar's own side.
    expect(button.querySelector('rect[fill-opacity]')?.getAttribute('x')).toBe('1.5');
    const user = userEvent.setup();
    await user.click(button);
    button.focus();
    await user.keyboard('{Enter}');
    await user.keyboard(' ');
    expect(onToggle).toHaveBeenCalledTimes(3);

    view.rerender(<SidebarToggle side="right" open={false} name="Chat sidebar" onToggle={onToggle} />);
    const closed = view.getByRole('button', { name: 'Chat sidebar' });
    expect(closed.getAttribute('aria-expanded')).toBe('false');
    expect(closed.getAttribute('title')).toBe('Show chat sidebar');
    expect(closed.querySelector('rect[fill-opacity]')).toBeNull();
    // INV-WEZ24E: no dashboard control speaks of navigation.
    expect(closed.outerHTML.toLowerCase()).not.toContain('navigation');
  });

  it('remembers each page and side under its own key, and the review chat side not at all', () => {
    const tree = renderHook(() => useSidebar('knowledge', 'left'));
    expect(tree.result.current.open).toBe(true);
    act(() => tree.result.current.toggle());
    expect(tree.result.current.open).toBe(false);
    expect(window.localStorage.getItem(sidebarKey('knowledge', 'left'))).toBe('0');
    expect(window.localStorage.getItem(sidebarKey('knowledge', 'right'))).toBeNull();
    // A new mount (a reload) restores the stored side.
    tree.unmount();
    expect(renderHook(() => useSidebar('knowledge', 'left')).result.current.open).toBe(false);

    const chat = renderHook(() => useSidebar('review', 'right', { remember: false, initial: false }));
    expect(chat.result.current.open).toBe(false);
    act(() => chat.result.current.toggle());
    expect(chat.result.current.open).toBe(true);
    expect(window.localStorage.getItem(sidebarKey('review', 'right'))).toBeNull();
  });
});
