// Highlighting offers a pick, a click takes it, and copy keeps working (MIK-R188 rules 3 and 4,
// evidence 1). jsdom has no clipboard and no layout: these cases prove the logic (the handler, the
// calls it makes and does not make); the real-browser facts come from the sandbox runs.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SelectionOffer } from './SelectionOffer';
import { TrayBottomRightDock } from './TrayDock';
import { TrayButton } from './TrayButton';
import { EMPTY_TRAY } from './trayPersistence';
import { trayPreferences } from './trayPreferences';
import { trayStore } from './trayStore';

const execCommand = vi.fn(() => true);

function highlight(element: Element) {
  const selection = document.getSelection();
  selection?.removeAllRanges(); // the test's own setup; the tray never calls it
  selection?.selectAllChildren(element);
}

function Page() {
  return (
    <>
      <main data-tray-panel="Operations">
        <p data-testid="passage">The count is per seat.</p>
        <div data-testid="editable" contentEditable suppressContentEditableWarning>
          a draft the developer types
        </div>
      </main>
      <TrayButton />
      <TrayBottomRightDock />
      <SelectionOffer />
    </>
  );
}

beforeEach(() => {
  trayStore.setState({ ...EMPTY_TRAY, open: false, refusal: null, outcome: null });
  trayPreferences.getState().setCopyOnHighlight(true);
  Object.defineProperty(document, 'execCommand', { value: execCommand, configurable: true });
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ state: 'seats', seats: [], nextOffset: null, complete: true }))));
});

afterEach(() => {
  execCommand.mockClear();
  vi.unstubAllGlobals();
  document.getSelection()?.removeAllRanges();
});

describe('a highlight in a dashboard panel', () => {
  it('shows "⊕ add" after the drag ends, and only its click makes a text pick', () => {
    render(<Page />);
    const passage = screen.getByTestId('passage');
    highlight(passage);
    expect(screen.queryByTestId('selection-offer')).toBeNull();
    fireEvent.mouseUp(passage);
    const offer = screen.getByRole('button', { name: 'Add the highlighted text to the AR tray' });
    expect(offer.textContent).toBe('⊕ add');
    expect(trayStore.getState().picks).toEqual([]);
    // Pressing the offer keeps the selection and the focus; its click takes the passage.
    expect(fireEvent.mouseDown(offer)).toBe(false);
    fireEvent.click(offer);
    expect(trayStore.getState().picks.map((pick) => pick.input)).toEqual([
      {
        kind: 'text',
        title: 'The count is per seat.',
        text: 'The count is per seat.',
        provenance: { source: 'panel', panel: 'Operations' },
      },
    ]);
    expect(document.getSelection()?.toString()).toBe('The count is per seat.');
    expect(screen.queryByTestId('selection-offer')).toBeNull();
  });

  it('copies on highlight when the preference is on, and not when it is off; editable fields are left alone', () => {
    render(<Page />);
    const passage = screen.getByTestId('passage');
    highlight(passage);
    fireEvent.mouseUp(passage);
    expect(execCommand.mock.calls).toEqual([['copy']]);

    act(() => trayPreferences.getState().setCopyOnHighlight(false));
    execCommand.mockClear();
    highlight(passage);
    fireEvent.mouseUp(passage);
    expect(execCommand).not.toHaveBeenCalled();
    expect(screen.getByTestId('selection-offer')).toBeTruthy();
    expect(localStorage.getItem('ar.tray.copy-on-highlight.v1')).toBe('off');

    act(() => trayPreferences.getState().setCopyOnHighlight(true));
    const editable = screen.getByTestId('editable');
    fireEvent.mouseDown(editable);
    highlight(editable);
    fireEvent.mouseUp(editable);
    expect(execCommand).not.toHaveBeenCalled();
    expect(screen.queryByTestId('selection-offer')).toBeNull();
  });

  it('survives opening the tray: the selection stays and nothing clears it', () => {
    const clear = vi.spyOn(Selection.prototype, 'removeAllRanges');
    render(<Page />);
    const passage = screen.getByTestId('passage');
    highlight(passage);
    clear.mockClear();
    fireEvent.mouseUp(passage);
    const button = screen.getByRole('button', { name: 'Open the AR tray, 0 picks' });
    expect(fireEvent.mouseDown(button)).toBe(false);
    fireEvent.click(button);
    expect(screen.getByTestId('tray-panel')).toBeTruthy();
    fireEvent.click(screen.getByTestId('selection-offer'));
    expect(screen.getByTestId('tray-pick').textContent).toContain('from Operations');
    expect(document.getSelection()?.toString()).toBe('The count is per seat.');
    expect(document.getSelection()?.isCollapsed).toBe(false);
    expect(clear).not.toHaveBeenCalled();
    clear.mockRestore();
  });

  it('has no code path that calls removeAllRanges()', () => {
    const sources = import.meta.glob(['./**/*.{ts,tsx}', '!./**/*.test.{ts,tsx}'], {
      query: '?raw',
      import: 'default',
      eager: true,
    }) as Record<string, string>;
    expect(Object.keys(sources).length).toBeGreaterThan(10);
    const callers = Object.entries(sources)
      .filter(([, text]) => text.split('\n').some((line) => !line.trim().startsWith('//') && line.includes('removeAllRanges(')))
      .map(([path]) => path);
    expect(callers).toEqual([]);
  });
});
