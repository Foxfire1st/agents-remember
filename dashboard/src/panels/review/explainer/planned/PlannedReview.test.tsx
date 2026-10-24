// The planned-mode page, end to end (MIK-R189 rule 5).
//
// One case per promise a reader can check:
//   * the top line reads `planned · no changes`, above the intended notice;
//   * the effects are `planned · <effect>` rows: an existing subject shows its current statement, a
//     `new:` subject its label, and a subject the memory tip did not hold says so;
//   * packets show their scope paths as addresses at the code tip or as plain text;
//   * the Prepared context shows its section, or the word `absent`, and the reference files are
//     listed;
//   * only the deterministic sorts are offered, they reorder the rows, and nothing on the page
//     starts anything: no fetch, no frame, no host channel.
//
// Assertions are plain DOM assertions: this suite loads no jest-dom matchers.
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PlannedReview } from './PlannedReview';
import {
  PLANNED_SORTS,
  PLANNED_TOP_LINE,
  readPlannedPage,
  sortRows,
  type PlannedPage,
} from './plannedPage';

const text = (element: Element | null | undefined): string => element?.textContent ?? '';

function payload(over: Partial<PlannedPage> = {}): PlannedPage {
  const base = {
    mode: 'planned' as const,
    topLine: PLANNED_TOP_LINE,
    intendedNotice: 'Intended, not a fact about the code',
    authority: 'intended',
    binding: {
      owner: 'planned' as const,
      number: 0,
      reference: 'review-comparison:T/L/planned',
      label: 'planned',
    },
    tips: { code: 'a'.repeat(40), codeTree: 'b'.repeat(40), memory: 'c'.repeat(40), memoryTree: 'd'.repeat(40), recordedAt: 'planned-master-tip' },
    rows: [
      {
        subject: 'invariant:INV-AAAAAA',
        kind: 'existing' as const,
        effect: 'clarify',
        title: 'planned · clarify',
        statement: "a claim's authority rests on a resolving basis",
        label: null,
        statementState: 'statement' as const,
        requirement: 'MIK-R189@v1',
        part: 'effects',
      },
      {
        subject: 'family:FAM-CCCCCC',
        kind: 'existing' as const,
        effect: 'strengthen',
        title: 'planned · strengthen',
        statement: null,
        label: null,
        statementState: 'unread' as const,
        requirement: 'MIK-R189@v1',
        part: 'effects',
      },
      {
        subject: 'new:Toast queue',
        kind: 'new' as const,
        effect: 'introduce',
        title: 'planned · introduce',
        statement: null,
        label: 'Toast queue',
        statementState: 'new' as const,
        requirement: 'MIK-R102@v2',
        part: 'effects',
      },
    ],
    packets: [
      {
        ref: 'MIK-R189@v1',
        path: 'requirements/MIK-R189-v1-x.md',
        title: 'MIK-R189 @ v1 — An intent review',
        state: 'rendered' as const,
        part: 'packets',
        scope: [
          {
            path: 'mcp/src/agents_remember/tasks/document.py',
            state: 'resolved' as const,
            resolvedPath: 'mcp/src/agents_remember/tasks/document.py',
          },
          {
            path: 'mcp/src/agents_remember/not-here.py',
            state: 'text' as const,
            resolvedPath: null,
          },
        ],
      },
    ],
    preparedContext: {
      heading: 'Prepared context',
      state: 'present' as const,
      body: 'the tray mockup, picked',
      part: 'prepared-context',
    },
    referenceFiles: [
      { path: 'notes/reference/L/picks.md', name: 'picks.md', part: 'reference-files' },
    ],
    sorts: PLANNED_SORTS.map((entry) => ({ ...entry })),
    parts: [
      { id: 'effects', title: 'Planned effects' },
      { id: 'packets', title: 'Packets' },
      { id: 'prepared-context', title: 'Prepared context' },
      { id: 'reference-files', title: 'Reference files' },
    ],
    actions: [],
    modelTurns: 0 as const,
    agentStarts: 0 as const,
    reads: { taskDocuments: 1, knowledgeStatements: 2, packets: 1, scopePaths: 2 },
  };
  return { ...base, ...over };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the planned page', () => {
  it("keeps the product's top line even when the payload names another", () => {
    render(<PlannedReview payload={payload({ topLine: '#0 · changes' })} />);

    expect(text(screen.getByTestId('planned-top-line'))).toBe('planned · no changes');
  });

  it('opens under the top line planned · no changes, with the intended notice', () => {
    render(<PlannedReview payload={payload()} />);

    expect(text(screen.getByTestId('planned-top-line'))).toBe('planned · no changes');
    expect(text(screen.getByTestId('planned-intended-notice'))).toBe(
      'Intended, not a fact about the code',
    );
    expect(screen.getByTestId('planned-review').getAttribute('data-mode')).toBe('planned');
  });

  it('draws every effect as a planned · <effect> row', () => {
    render(<PlannedReview payload={payload()} />);

    expect(screen.getAllByTestId('planned-row-title').map((title) => text(title))).toEqual([
      'planned · clarify',
      'planned · strengthen',
      'planned · introduce',
    ]);
  });

  it("shows an existing subject's current statement and a new subject's label", () => {
    render(<PlannedReview payload={payload()} />);

    const rows = screen.getAllByTestId('planned-row');
    expect(text(rows[0])).toContain("a claim's authority rests on a resolving basis");
    expect(text(rows[2])).toContain('Toast queue');
    // The label, not a statement: a new subject has no statement to show.
    expect(text(rows[2])).toContain('planned · introduce');
  });

  it('says when no statement was read, rather than inventing one', () => {
    render(<PlannedReview payload={payload()} />);

    const rows = screen.getAllByTestId('planned-row');
    expect(text(rows[1])).toContain('no statement was read at the memory tip');
  });

  it('shows a packet with its scope: an address at the code tip, or plain text', () => {
    render(<PlannedReview payload={payload()} />);

    expect(text(screen.getByTestId('planned-packet'))).toContain('MIK-R189 @ v1 — An intent review');
    const paths = screen.getAllByTestId('planned-scope-path');
    expect(paths.map((entry) => entry.getAttribute('data-scope'))).toEqual(['resolved', 'text']);
    expect(text(paths[0])).toBe('code · mcp/src/agents_remember/tasks/document.py');
    expect(text(paths[1])).toBe('not at the code tip · mcp/src/agents_remember/not-here.py');
  });

  it('captions a package-relative path by the code tip path that answered it', () => {
    const page = payload();
    page.packets[0].scope = [
      { path: 'tasks/document.py', state: 'resolved', resolvedPath: 'mcp/src/agents_remember/tasks/document.py' },
    ];

    render(<PlannedReview payload={page} />);

    expect(text(screen.getByTestId('planned-scope-path'))).toBe(
      'code · mcp/src/agents_remember/tasks/document.py',
    );
  });

  it('shows the Prepared context section, or the word absent', () => {
    const { unmount } = render(<PlannedReview payload={payload()} />);
    expect(text(screen.getByTestId('planned-prepared-context'))).toBe('the tray mockup, picked');
    unmount();

    render(
      <PlannedReview
        payload={payload({
          preparedContext: {
            heading: 'Prepared context',
            state: 'absent',
            body: '',
            part: 'prepared-context',
          },
        })}
      />,
    );
    expect(text(screen.getByTestId('planned-prepared-context'))).toBe('absent');
  });

  it('lists the reference files by their task-relative path', () => {
    render(<PlannedReview payload={payload()} />);

    expect(text(screen.getByTestId('planned-reference-file'))).toBe(
      'notes/reference/L/picks.md',
    );
  });

  it('offers only the deterministic sorts, and reorders the rows with them', () => {
    render(<PlannedReview payload={payload()} />);

    const control = screen.getByRole('group', { name: 'sort the planned effects' });
    const buttons = within(control).getAllByRole('button');
    expect(buttons.map((button) => button.getAttribute('data-testid'))).toEqual([
      'planned-sort-declared',
      'planned-sort-subject',
      'planned-sort-effect',
    ]);
    for (const button of buttons) {
      expect(text(button)).not.toMatch(/judgment|grade|confidence/i);
    }
    // The only controls on the page are those three: nothing else can start anything.
    expect(screen.getAllByRole('button')).toHaveLength(3);

    fireEvent.click(screen.getByTestId('planned-sort-subject'));
    expect(screen.getAllByTestId('planned-row').map((row) => row.getAttribute('data-subject'))).toEqual(
      ['family:FAM-CCCCCC', 'invariant:INV-AAAAAA', 'new:Toast queue'],
    );

    fireEvent.click(screen.getByTestId('planned-sort-effect'));
    expect(screen.getAllByTestId('planned-row').map((row) => row.getAttribute('data-subject'))).toEqual(
      ['invariant:INV-AAAAAA', 'new:Toast queue', 'family:FAM-CCCCCC'],
    );

    fireEvent.click(screen.getByTestId('planned-sort-declared'));
    expect(screen.getAllByTestId('planned-row').map((row) => row.getAttribute('data-subject'))).toEqual(
      ['invariant:INV-AAAAAA', 'family:FAM-CCCCCC', 'new:Toast queue'],
    );
  });

  it('starts nothing: no fetch is made and no frame or host channel is mounted', () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);

    const { container } = render(<PlannedReview payload={payload()} />);
    for (const button of screen.getAllByRole('button')) fireEvent.click(button);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(container.querySelectorAll('iframe')).toHaveLength(0);
    expect(container.querySelectorAll('[data-testid="revision-frame"]')).toHaveLength(0);
  });

  it('reads one host answer as a planned page, and refuses anything else', () => {
    const answer = readPlannedPage(JSON.parse(JSON.stringify(payload())));

    expect(answer?.topLine).toBe('planned · no changes');
    expect(answer?.rows).toHaveLength(3);
    expect(readPlannedPage({ state: 'ok', planned: payload() })?.rows).toHaveLength(3);
    expect(readPlannedPage({ state: 'refused', reason: 'planned-unavailable' })).toBeNull();
    expect(readPlannedPage({ mode: 'recorded' })).toBeNull();
    expect(readPlannedPage(null)).toBeNull();
  });

  it("ignores a payload's own sort list: the control is the product's", () => {
    const answer = readPlannedPage({
      ...JSON.parse(JSON.stringify(payload())),
      sorts: [{ id: 'judgment', label: 'by judgment', order: 'the grade' }],
    });

    expect(answer?.sorts.map((entry) => entry.id)).toEqual(['declared', 'subject', 'effect']);
  });

  it('sorts stably, so equal keys keep the declaration order', () => {
    const rows = payload().rows;
    expect(sortRows(rows, 'subject').map((row) => row.subject)).toEqual([
      'family:FAM-CCCCCC',
      'invariant:INV-AAAAAA',
      'new:Toast queue',
    ]);
    expect(sortRows(rows, 'declared')).toEqual(rows);
  });

  it('renders packet bodies safely, navigates resolved Scope tip addresses while keeping unresolved paths as text, and synchronizes planned tree and reading rows', () => {
    const onOpenAddress = vi.fn();
    const richPayload = readPlannedPage({
      ...JSON.parse(JSON.stringify(payload())),
      packets: [
        {
          ref: 'MIK-R189@v2',
          path: 'requirements/MIK-R189-v2.md',
          title: 'MIK-R189 @ v2 — An intent review before the work starts',
          body: '## Normative Requirement\nRender safe packet bodies and <script>alert(1)</script> as inert text.',
          state: 'rendered',
          part: 'packets',
          scope: [
            {
              path: 'mcp/src/agents_remember/tasks/document.py',
              state: 'resolved',
              resolvedPath: 'mcp/src/agents_remember/tasks/document.py',
              exists: true,
              tipAddress: 'code-tip:mcp/src/agents_remember/tasks/document.py',
            },
            {
              path: 'mcp/src/agents_remember/not-here.py',
              state: 'text',
              resolvedPath: null,
              exists: false,
            },
          ],
        },
      ],
      referenceFiles: [
        { path: 'notes/reference/L/picks.md', name: 'picks.md', part: 'reference-files' },
        { path: 'notes/reference/L/nested/diagram.svg', name: 'diagram.svg', part: 'reference-files' },
      ],
    })!;

    render(<PlannedReview payload={richPayload} onOpenAddress={onOpenAddress} />);

    const packetBody = screen.getByTestId('planned-packet-body');
    expect(text(packetBody)).toContain('Render safe packet bodies');
    expect(packetBody.querySelector('script')).toBeNull();

    const scopePaths = screen.getAllByTestId('planned-scope-path');
    const resolvedLink = within(scopePaths[0]).getByTestId('planned-scope-address');
    expect(within(scopePaths[1]).queryByTestId('planned-scope-address')).toBeNull();

    fireEvent.click(resolvedLink);
    expect(onOpenAddress).toHaveBeenCalledWith({
      path: 'mcp/src/agents_remember/tasks/document.py',
      codeTip: 'a'.repeat(40),
      codeTree: 'b'.repeat(40),
      tipAddress: 'code-tip:mcp/src/agents_remember/tasks/document.py',
    });
    expect(text(screen.getByTestId('planned-opened-address'))).toContain(
      'mcp/src/agents_remember/tasks/document.py',
    );

    const treeRows = screen.getAllByTestId('planned-tree-row');
    expect(treeRows.map((row) => row.getAttribute('data-subject'))).toEqual([
      'invariant:INV-AAAAAA',
      'family:FAM-CCCCCC',
      'new:Toast queue',
    ]);
    fireEvent.click(treeRows[1]);
    expect(treeRows[1].getAttribute('data-selected')).toBe('true');
    expect(screen.getAllByTestId('planned-row')[1].getAttribute('data-selected')).toBe('true');

    fireEvent.click(screen.getByTestId('planned-sort-subject'));
    expect(
      screen.getAllByTestId('planned-tree-row').map((row) => row.getAttribute('data-subject')),
    ).toEqual(['family:FAM-CCCCCC', 'invariant:INV-AAAAAA', 'new:Toast queue']);

    expect(screen.getAllByTestId('planned-reference-file').map((el) => text(el))).toEqual([
      'notes/reference/L/picks.md',
      'notes/reference/L/nested/diagram.svg',
    ]);
  });
});
