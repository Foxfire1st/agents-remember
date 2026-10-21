// File lines from the File Viewer's gutter (MIK-R188 rule 1, W29): one line per click, shift-click
// extends the range from the last clicked line, a line already in the tray adds nothing, the ⊕ is a
// named keyboard action (MIK-R89 v2 rule 11), and only the File Viewer's pane carries the gutter.
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { DualPane } from '../panels/file-viewer/DualPane';
import { FilePane } from '../panels/file-viewer/FilePane';
import { takeFileLine } from './fileLineGutter';
import { EMPTY_TRAY } from './trayPersistence';
import { trayStore } from './trayStore';

const target = { repository: 'agents-remember', scope: 'mainline', path: 'dashboard/src/a.ts' };

const ranges = () =>
  trayStore.getState().picks.map((pick) =>
    pick.input.kind === 'reference' && pick.input.reference.kind === 'file-line'
      ? [pick.input.reference.start, pick.input.reference.end]
      : null,
  );

beforeEach(() => trayStore.setState({ ...EMPTY_TRAY, open: false, refusal: null, outcome: null }));

describe('the file line ⊕', () => {
  it('takes one line per click; shift-click extends the range from the last clicked line', () => {
    let anchor = takeFileLine(target, 12, false, null);
    expect(ranges()).toEqual([[12, 12]]);
    anchor = takeFileLine(target, 15, true, anchor);
    expect(ranges()).toEqual([[12, 15]]);
    anchor = takeFileLine(target, 10, true, anchor);
    expect(ranges()).toEqual([[10, 12]]);
    anchor = takeFileLine(target, 20, false, anchor);
    expect(ranges()).toEqual([
      [10, 12],
      [20, 20],
    ]);
    // A second click on a line already taken adds nothing.
    anchor = takeFileLine(target, 20, false, anchor);
    expect(ranges()).toHaveLength(2);
    expect(trayStore.getState().picks[0].input.title).toBe('a.ts L10–12 · agents-remember');
    // Nor does a click on a line inside a range in the tray; the range stays as it is.
    const ids = trayStore.getState().picks.map((pick) => pick.id);
    anchor = takeFileLine(target, 11, false, anchor);
    expect([ranges(), trayStore.getState().picks.map((pick) => pick.id)]).toEqual([
      [
        [10, 12],
        [20, 20],
      ],
      ids,
    ]);
    // A shift-click still extends the last clicked line's range to it.
    takeFileLine(target, 11, true, anchor);
    expect(ranges()).toEqual([
      [10, 12],
      [11, 20],
    ]);
  });

  it('reaches the line ⊕ by keyboard: one named tab stop that takes a line or a range, and keeps its focus', async () => {
    const code = {
      scope: 'mainline',
      path: 'dashboard/src/a.ts',
      language: 'text',
      size: 24,
      truncated: false,
      content: 'one\ntwo\nthree\nfour\nfive\n',
      onboarding: { status: 'missing' as const },
    };
    const viewer = render(<DualPane code={code} sidecar={{ state: 'empty' }} split={false} repository="agents-remember" />);
    // jsdom has no Tab key: focus stands for reaching the one tab stop of the gutter.
    const add = await screen.findByRole('button', { name: 'Add line 1 to the AR tray' });
    expect(Array.from(viewer.container.querySelectorAll('.cm-trayGutter [tabindex="0"]'))).toEqual([add]);
    act(() => add.focus());
    fireEvent.keyDown(add, { key: 'Enter' });
    expect(ranges()).toEqual([[1, 1]]);
    const focused = () => document.activeElement?.getAttribute('aria-label');
    expect(focused()).toBe('Line 1 is in the AR tray');
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowDown' });
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowDown' });
    expect(focused()).toBe('Add line 3 to the AR tray');
    fireEvent.keyDown(document.activeElement as Element, { key: 'Enter', shiftKey: true });
    expect(ranges()).toEqual([[1, 3]]);
    // Enter on a line inside the range adds nothing; Space adds a line as Enter does.
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowUp' });
    expect(focused()).toBe('Line 2 is in the AR tray');
    fireEvent.keyDown(document.activeElement as Element, { key: 'Enter' });
    expect(ranges()).toEqual([[1, 3]]);
    fireEvent.keyDown(document.activeElement as Element, { key: 'End' });
    expect(focused()).toBe('Add line 6 to the AR tray');
    fireEvent.keyDown(document.activeElement as Element, { key: ' ' });
    expect(ranges()).toEqual([
      [1, 3],
      [6, 6],
    ]);
    // The other gutters stay hidden from assistive technology, and a mouse press takes no focus.
    expect(viewer.container.querySelector('.cm-lineNumbers')?.getAttribute('aria-hidden')).toBe('true');
    const element = viewer.container.querySelector('.cm-trayGutter .cm-gutterElement:not([style*="visibility: hidden"])') as Element;
    expect(fireEvent.mouseDown(element)).toBe(false);
    viewer.unmount();
  });

  it('draws the gutter in the File Viewer’s pane only, and a click on it takes the line', async () => {
    const code = {
      scope: 'mainline',
      path: 'dashboard/src/a.ts',
      language: 'text',
      size: 14,
      truncated: false,
      content: 'one\ntwo\nthree\n',
      onboarding: { status: 'missing' as const },
    };
    // The File Viewer hands its repository to the dual pane, which names the file for the gutter.
    const viewer = render(<DualPane code={code} sidecar={{ state: 'empty' }} split={false} repository="agents-remember" />);
    const gutter = await waitFor(() => {
      const found = viewer.container.querySelector('.cm-trayGutter');
      if (!found) throw new Error('no tray gutter yet');
      return found;
    });
    fireEvent.click(gutter.querySelector('.cm-gutterElement:not([style*="visibility: hidden"])') ?? gutter);
    expect(ranges()).toEqual([[1, 1]]);
    viewer.unmount();

    const excerpt = render(<FilePane content={'one\ntwo\n'} language="text" />);
    await waitFor(() => expect(excerpt.container.querySelector('.cm-editor')).not.toBeNull());
    expect(excerpt.container.querySelector('.cm-trayGutter')).toBeNull();
  });
});
