// L127-C-F03: long objective/design bodies render as bounded previews with
// own views, and Back restores the exact scroll position — including a
// nonzero one. Short bodies render whole with no link.
import { fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { seedTaskDocuments, taskDoc } from './test-utils';

const DOC = '/tasks/repo-a/m/01_long.json';

function longText(prefix: string, lines: number): string {
  return Array.from({ length: lines }, (_, i) => `${prefix} line ${i + 1}`).join('\n');
}

function stubScroll(y: number) {
  const scrollTo = vi.fn();
  const prevY = Object.getOwnPropertyDescriptor(window, 'scrollY');
  const prevTo = Object.getOwnPropertyDescriptor(window, 'scrollTo');
  Object.defineProperty(window, 'scrollY', { value: y, configurable: true });
  Object.defineProperty(window, 'scrollTo', { value: scrollTo, configurable: true });
  return {
    scrollTo,
    restore() {
      if (prevY) Object.defineProperty(window, 'scrollY', prevY);
      else delete (window as unknown as Record<string, unknown>).scrollY;
      if (prevTo) Object.defineProperty(window, 'scrollTo', prevTo);
      else delete (window as unknown as Record<string, unknown>).scrollTo;
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('long objective and design blocks (L127-C-F03)', () => {
  it('bounds a ten-line objective with a counted link to the whole text', () => {
    seedTaskDocuments([
      taskDoc({ kind: 'subTask', docPath: DOC, objective: longText('Objective', 10) }),
    ]);
    const { container, getByTestId, queryByText } = render(
      <DetailPanel selectedId={`taskdoc:${DOC}`} />,
    );

    expect(getByTestId('objective-all-link').textContent).toContain('all 10 lines');
    expect(queryByText(/Objective line 8/)).toBeTruthy();
    expect(queryByText(/Objective line 9/)).toBeNull();
    expect(container.querySelector('[data-block="objective"]')).toBeTruthy();
  });

  it('opens the whole objective in its own view and restores nonzero scroll on Back', async () => {
    seedTaskDocuments([
      taskDoc({ kind: 'subTask', docPath: DOC, objective: longText('Objective', 10) }),
    ]);
    const scroll = stubScroll(312);
    try {
      const { getByTestId, getByText } = render(<DetailPanel selectedId={`taskdoc:${DOC}`} />);

      fireEvent.click(getByTestId('objective-all-link'));
      expect(getByTestId('doc-view-title').textContent).toBe('Objective');
      expect(getByText(/Objective line 10/)).toBeTruthy();
      expect(scroll.scrollTo).toHaveBeenCalledWith(0, 0);
      fireEvent.click(getByTestId('doc-view-back'));
      await waitFor(() => expect(scroll.scrollTo).toHaveBeenCalledWith(0, 312));
    } finally {
      scroll.restore();
    }
  });

  it('bounds a long design body with its own counted view', () => {
    seedTaskDocuments([taskDoc({ kind: 'subTask', docPath: DOC, design: longText('Design', 12) })]);
    const { getByTestId, queryByText } = render(<DetailPanel selectedId={`taskdoc:${DOC}`} />);

    expect(getByTestId('design-all-link').textContent).toContain('all 12 lines');
    expect(queryByText(/Design line 9/)).toBeNull();
    fireEvent.click(getByTestId('design-all-link'));
    expect(getByTestId('doc-view-title').textContent).toBe('Design');
    expect(queryByText(/Design line 12/)).toBeTruthy();
  });

  it('renders a short objective whole with no link', () => {
    seedTaskDocuments([
      taskDoc({ kind: 'subTask', docPath: DOC, objective: 'Line one.\nLine two.' }),
    ]);
    const { queryByTestId, getByText } = render(<DetailPanel selectedId={`taskdoc:${DOC}`} />);

    expect(queryByTestId('objective-all-link')).toBeNull();
    expect(getByText(/Line two/)).toBeTruthy();
  });

  it('bounds long objectives on master pages too', () => {
    const masterPath = '/tasks/repo-a/m/task.json';
    seedTaskDocuments([
      taskDoc({ kind: 'master', docPath: masterPath, objective: longText('Master objective', 9) }),
    ]);
    const { getByTestId, queryByText } = render(
      <DetailPanel selectedId={`taskdoc:${masterPath}`} />,
    );

    expect(getByTestId('objective-all-link').textContent).toContain('all 9 lines');
    expect(queryByText(/Master objective line 9/)).toBeNull();
  });

  it('bounds a 21,021-character single paragraph by characters, not lines', () => {
    // L127-C-F03 residual: one source line can exceed any viewport.
    const paragraph = `Head marker ${'x'.repeat(20997)} tail marker`;
    expect(paragraph.length).toBe(21021);
    seedTaskDocuments([taskDoc({ kind: 'subTask', docPath: DOC, objective: paragraph })]);
    const { getByTestId, getByText, queryByText } = render(
      <DetailPanel selectedId={`taskdoc:${DOC}`} />,
    );

    expect(getByTestId('objective-all-link').textContent).toContain(
      `${paragraph.length} characters`,
    );
    expect(getByText(/Head marker/)).toBeTruthy();
    expect(queryByText(/tail marker/)).toBeNull();
    fireEvent.click(getByTestId('objective-all-link'));
    expect(getByTestId('doc-view-title').textContent).toBe('Objective');
    expect(getByText(/tail marker/)).toBeTruthy();
  });

  it('bounds a single-paragraph design the same way', () => {
    const paragraph = `Design head ${'y'.repeat(5000)} design tail`;
    seedTaskDocuments([taskDoc({ kind: 'subTask', docPath: DOC, design: paragraph })]);
    const { getByTestId, queryByText } = render(<DetailPanel selectedId={`taskdoc:${DOC}`} />);

    expect(getByTestId('design-all-link').textContent).toContain(`${paragraph.length} characters`);
    expect(queryByText(/design tail/)).toBeNull();
  });
});
