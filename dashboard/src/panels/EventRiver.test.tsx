import { cleanup, render } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { dashboardStore } from '../data/store';
import { observerEvent } from '../test/fixtures/wire';
import type { ObserverEvent } from '../types/event';
import { EventRiver } from './EventRiver';
import { formatEventTime } from './eventSummary';

// The Event River list is virtualized (TanStack), which mounts only the rows whose positions fall
// inside the scroll viewport. TanStack measures the viewport and rows from offsetWidth/offsetHeight,
// which jsdom reports as 0 (no layout), so nothing would mount. Give every box a non-zero size so a
// real viewport + measured rows let the newest window of rows mount and be asserted; off-screen rows
// stay virtualized out (the no-cap behaviour is asserted via the retained count in the header).
const sizedBox: Record<string, PropertyDescriptor | undefined> = {};
beforeAll(() => {
  for (const [prop, value] of [
    ['offsetHeight', 600],
    ['offsetWidth', 320],
  ] as const) {
    sizedBox[prop] = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop);
    Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, get: () => value });
  }
});
afterAll(() => {
  for (const prop of ['offsetHeight', 'offsetWidth']) {
    const original = sizedBox[prop];
    if (original) Object.defineProperty(HTMLElement.prototype, prop, original);
  }
});

afterEach(cleanup);
beforeEach(() =>
  dashboardStore.setState({
    analytics: null,
    enclosures: {},
    events: [],
    eventsHydrated: false,
    eventsLastTs: null,
  }),
);

function ev(partial: Partial<ObserverEvent> & Pick<ObserverEvent, 'kind'>): ObserverEvent {
  return observerEvent({
    id: `e-${partial.kind}-${Math.random().toString(36).slice(2)}`,
    ts: '2026-06-23T10:11:12+00:00',
    actor: 'model',
    ...partial,
  });
}

describe('EventRiver readable activity feed', () => {
  it('waits for the raw event stream to hydrate before claiming an empty river', () => {
    const view = render(<EventRiver />);
    expect(view.getByText('Syncing event history.')).not.toBeNull();
    expect(view.queryByText('No events yet.')).toBeNull();

    dashboardStore.setState({ eventsHydrated: true });
    view.rerender(<EventRiver />);
    expect(view.getByText('No events yet.')).not.toBeNull();
  });

  it("renders 'Read: <basename>' with the repo, and the full path on hover", () => {
    const full = 'mcp/src/agents_remember/kernel/coordination_context/contracts.py';
    dashboardStore.setState({
      events: [
        ev({
          kind: 'read.packet',
          enclosure: 'scope-1',
          data: {
            repoId: 'agents-remember',
            files: [{ path: full, lines: 'full', status: 'found', bytes: 2270 }],
          },
        }),
      ],
    });
    const { getByText } = render(<EventRiver />);
    const label = getByText('Read: contracts.py');
    expect(label.getAttribute('title')).toBe(full);
    const item = label.closest("[data-testid='river-item']");
    expect(item?.textContent).toContain('agents-remember');
  });

  it("summarizes a multi-file batch as '+N more' with every path on hover", () => {
    dashboardStore.setState({
      events: [
        ev({
          kind: 'read.packet',
          data: {
            repoId: 'agents-remember',
            files: [{ path: 'a/one.py' }, { path: 'b/two.py' }, { path: 'c/three.py' }],
          },
        }),
      ],
    });
    const { getByText } = render(<EventRiver />);
    const label = getByText('Read: one.py +2 more');
    const title = label.getAttribute('title') ?? '';
    expect(title).toContain('a/one.py');
    expect(title).toContain('b/two.py');
    expect(title).toContain('c/three.py');
  });

  it('retains and virtualizes the full window beyond the old newest-60 cap', () => {
    dashboardStore.setState({
      events: [
        ev({
          id: 'first',
          kind: 'read.packet',
          data: { repoId: 'agents-remember', files: [{ path: 'oldest/first.py' }] },
        }),
        ...Array.from({ length: 65 }, (_, i) =>
          ev({
            id: `extra-${i}`,
            kind: 'read.packet',
            data: { repoId: 'agents-remember', files: [{ path: `batch/file-${i}.py` }] },
          }),
        ),
      ],
    });
    const { getByText } = render(<EventRiver />);
    // The whole window is retained and counted (no newest-N display slice); the newest row mounts.
    // Off-screen rows are virtualized out of the DOM, not dropped from the feed.
    expect(getByText(/Event river · 66/)).not.toBeNull();
    expect(getByText('Read: file-64.py')).not.toBeNull();
  });

  it('translates tool.completed rows with friendly tool copy, result state, tokens, and agent actor copy', () => {
    dashboardStore.setState({
      events: [
        ev({
          kind: 'tool.completed',
          enclosure: 'scope-1',
          data: { tool: 'context_packet', ok: true, tokens: 1234 },
        }),
      ],
    });
    const { getByText } = render(<EventRiver />);
    expect(getByText('Checked workspace context')).not.toBeNull();
    const item = getByText('Checked workspace context').closest("[data-testid='river-item']");
    expect(item?.textContent).toContain('agent');
    expect(item?.textContent).toContain('observed');
    // Earlier-kind rows keep the base outcome label (ok), not the host-path
    // vocabulary (done); see the R1 legacy-outcome cases below.
    expect(item?.textContent).toContain(' · ok · ');
    expect(item?.textContent).toContain('1,234 tokens');
  });

  it('shows who called on host-path rows: role, task, tool, and done', () => {
    dashboardStore.setState({
      events: [
        ev({
          kind: 'tool.completed',
          data: {
            tool: 'worktree_status',
            ok: true,
            tokens: 5,
            role: 'worker',
            agentId: 'agent-1',
            taskTitle: 'River Leaf',
          },
        }),
      ],
    });
    const { getByText, getByTestId } = render(<EventRiver />);
    expect(getByText('Checked worktree status')).not.toBeNull();
    const item = getByText('Checked worktree status').closest("[data-testid='river-item']");
    expect(item?.textContent).toContain('Worker · River Leaf');
    expect(item?.textContent).toContain('done');
    expect(getByTestId('event-river-last-fed').textContent).toMatch(/Last event \d\d:\d\d:\d\d/);
  });

  it('shows refused with its code on refused host-path rows', () => {
    dashboardStore.setState({
      events: [
        ev({
          kind: 'tool.completed',
          data: {
            tool: 'role_start',
            ok: false,
            state: 'refused',
            code: 'recipient-busy',
            role: 'manager',
            taskTitle: 'River Master',
          },
        }),
      ],
    });
    const { getByText } = render(<EventRiver />);
    expect(getByText('Started agent failed')).not.toBeNull();
    const item = getByText('Started agent failed').closest("[data-testid='river-item']");
    expect(item?.textContent).toContain('Manager · River Master');
    expect(item?.textContent).toContain('refused (recipient-busy)');
  });

  it('shows the state a tool answer reached instead of done', () => {
    dashboardStore.setState({
      events: [
        ev({
          kind: 'tool.completed',
          data: {
            tool: 'role_message',
            ok: true,
            state: 'accepted',
            role: 'manager',
            taskTitle: 'River Master',
            targetRole: 'worker',
          },
        }),
      ],
    });
    const { getByText } = render(<EventRiver />);
    expect(getByText('Sent message')).not.toBeNull();
    const item = getByText('Sent message').closest("[data-testid='river-item']");
    expect(item?.textContent).toContain('accepted');
    expect(item?.textContent).toContain('Worker');
  });

  it('names a chat without a role with its harness', () => {
    dashboardStore.setState({
      events: [
        ev({
          kind: 'tool.completed',
          data: { tool: 'task_doc', ok: true, role: 'chat', harness: 'codex' },
        }),
      ],
    });
    const { getByText } = render(<EventRiver />);
    expect(getByText('Updated task document')).not.toBeNull();
    const item = getByText('Updated task document').closest("[data-testid='river-item']");
    expect(item?.textContent).toContain('chat without a role · codex');
  });

  it('says when the river was last fed on a silent river', () => {
    dashboardStore.setState({
      events: [],
      eventsHydrated: true,
      eventsLastTs: '2026-06-23T09:00:00+00:00',
    });
    const { getByText, queryByText } = render(<EventRiver />);
    expect(queryByText('No events yet.')).toBeNull();
    expect(getByText(/No events in the last hour\./)).not.toBeNull();
    expect(getByText(/Last event \d\d:\d\d:\d\d/).textContent).toContain('Last event');
  });

  it('keeps unknown raw event kinds with enclosure context before the repo', () => {
    dashboardStore.setState({
      events: [ev({ kind: 'custom.unhandled', enclosure: 'leaf-enclosure', repoId: 'repo-root' })],
    });
    const { getByText } = render(<EventRiver />);
    const item = getByText('custom.unhandled').closest("[data-testid='river-item']");
    expect(item?.textContent).toContain('leaf-enclosure');
    expect(item?.textContent).not.toContain('repo-root');
  });
});

function toolRow(
  id: string,
  ts: string,
  data: Record<string, unknown>,
  extra?: Partial<ObserverEvent>,
): ObserverEvent {
  return ev({ id, ts, kind: 'tool.completed', actor: 'model', data, ...extra });
}

function lastFedText(container: HTMLElement): string | null {
  return container.querySelector("[data-testid='event-river-last-fed']")?.textContent ?? null;
}

describe('R1 repair: newest head, verbatim unknowns, legacy outcomes', () => {
  const newer = '2026-10-08T21:00:00+00:00';
  const older = '2026-10-08T20:59:00+00:00';

  it('heads the newest timestamp rather than the last arrival', () => {
    dashboardStore.setState({
      events: [
        toolRow('newer', newer, { tool: 'worktree_status', ok: true, role: 'worker' }),
        toolRow('older', older, { tool: 'worktree_status', ok: true, role: 'worker' }),
      ],
      eventsHydrated: true,
      eventsLastTs: older,
    });
    const { container } = render(<EventRiver />);
    expect(lastFedText(container)).toContain(formatEventTime(newer));
  });

  it('does not move the head backwards on an older late arrival', () => {
    dashboardStore.setState({
      events: [toolRow('older', older, { tool: 'worktree_status', ok: true, role: 'worker' })],
      eventsHydrated: true,
      eventsLastTs: newer,
    });
    const { container } = render(<EventRiver />);
    expect(lastFedText(container)).toContain(formatEventTime(newer));
  });

  it('falls back to the store time on an empty window', () => {
    dashboardStore.setState({ events: [], eventsHydrated: true, eventsLastTs: older });
    const { getByText } = render(<EventRiver />);
    expect(getByText(/No events in the last hour\./)).not.toBeNull();
    expect(getByText(/Last event \d\d:\d\d:\d\d/).textContent).toContain(formatEventTime(older));
  });

  it('ignores malformed and absent timestamps', () => {
    dashboardStore.setState({
      events: [
        toolRow('bad', 'not-a-time', { tool: 'worktree_status', ok: true, role: 'worker' }),
        toolRow('good', newer, { tool: 'worktree_status', ok: true, role: 'worker' }),
      ],
      eventsHydrated: true,
      eventsLastTs: 'also-bad',
    });
    const { container } = render(<EventRiver />);
    expect(lastFedText(container)).toContain(formatEventTime(newer));
  });

  it('shows no last-fed line when nothing names a valid time', () => {
    dashboardStore.setState({
      events: [toolRow('bad', 'not-a-time', { tool: 'worktree_status', ok: true, role: 'worker' })],
      eventsHydrated: true,
      eventsLastTs: null,
    });
    const { container } = render(<EventRiver />);
    expect(container.querySelector("[data-testid='event-river-last-fed']")).toBeNull();
  });

  it('renders unknown caller and target roles verbatim', () => {
    dashboardStore.setState({
      events: [
        toolRow('unknown', newer, {
          tool: 'role_message',
          ok: true,
          role: 'pilot_x',
          taskTitle: 'River Leaf',
          targetRole: 'Night-Shift.Lead',
        }),
      ],
      eventsHydrated: true,
    });
    const { getByText } = render(<EventRiver />);
    const item = getByText('Sent message').closest("[data-testid='river-item']");
    expect(item?.textContent).toContain('pilot_x · River Leaf');
    expect(item?.textContent).not.toContain('Pilot x');
    expect(item?.textContent).toContain('Night-Shift.Lead');
  });

  it('keeps friendly labels for known roles and verbatim task fallbacks', () => {
    dashboardStore.setState({
      events: [
        toolRow('known', newer, {
          tool: 'worktree_status',
          ok: true,
          role: 'worker',
          taskId: 'weird_TASK.id',
        }),
      ],
      eventsHydrated: true,
    });
    const { getByText } = render(<EventRiver />);
    const item = getByText('Checked worktree status').closest("[data-testid='river-item']");
    expect(item?.textContent).toContain('Worker · weird_TASK.id');
  });

  it('keeps the base ok label on earlier-kind success rows', () => {
    dashboardStore.setState({
      events: [
        toolRow('legacy', newer, { tool: 'worktree_status', ok: true }, { enclosure: 'scope-1' }),
      ],
      eventsHydrated: true,
    });
    const { getByText } = render(<EventRiver />);
    const item = getByText('Checked worktree status').closest("[data-testid='river-item']");
    expect(item?.textContent).toContain(' · ok · ');
    expect(item?.textContent).not.toContain('done');
  });

  it('keeps the base failed label on earlier-kind refusal rows', () => {
    dashboardStore.setState({
      events: [
        toolRow(
          'legacy-bad',
          newer,
          { tool: 'worktree_status', ok: false },
          { enclosure: 'scope-1' },
        ),
      ],
      eventsHydrated: true,
    });
    const { getByText } = render(<EventRiver />);
    const item = getByText('Checked worktree status failed').closest("[data-testid='river-item']");
    expect(item?.textContent).toContain('failed');
    expect(item?.textContent).not.toContain('refused');
  });

  it('keeps done, refused with code, and state on rows with caller facts', () => {
    dashboardStore.setState({
      events: [
        toolRow('done', newer, { tool: 'worktree_status', ok: true, role: 'worker' }),
        toolRow('refused', newer, {
          tool: 'role_message',
          ok: false,
          role: 'manager',
          code: 'recipient-busy',
        }),
        toolRow('state', newer, { tool: 'task_doc', ok: true, role: 'worker', state: 'planning' }),
      ],
      eventsHydrated: true,
    });
    const { getByText } = render(<EventRiver />);
    expect(
      getByText('Checked worktree status').closest("[data-testid='river-item']")?.textContent,
    ).toContain('done');
    expect(
      getByText('Sent message failed').closest("[data-testid='river-item']")?.textContent,
    ).toContain('refused (recipient-busy)');
    expect(
      getByText('Updated task document').closest("[data-testid='river-item']")?.textContent,
    ).toContain('planning');
  });
});
