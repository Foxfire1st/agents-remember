// Mounted ReviewSurface integration for the Explanation view, PlannedReview, Judgment sorts/chips,
// and review.explainer.moved SSE events (MIK-R106 v2; MIK-R187 v1; MIK-R189 v2; MIK-R103 v2; MIK-R104 v3).
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { resetCardCachesForTests } from '../../../data/reviewTrees';
import { connectState } from '../../../data/stream';
import { enclosure, seedProjection } from '../../detail-panel/test-utils';
import { changeSetModel } from '../changeSetOrder';
import { l181Answer, l181Outline } from '../changeSetOrder.test-utils';
import {
  PLACE,
  renderPage,
  servePage,
  stubIntersection,
} from '../reading/reviewPage.test-utils';
import { publishLeafJudgments } from '../reading/useChangeSet';
import { treeOrderStore } from '../triageOrderPreference';
import { treeViewStore } from '../triageViewPreference';
import { PLANNED_SORTS, PLANNED_TOP_LINE } from './planned/plannedPage';

const EXPLAINER_BASE = `/api/explainer/${PLACE.repo}/${PLACE.master}/${PLACE.leaf}`;
const HASH = `#review?${new URLSearchParams({ repo: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf })}`;

class FakeEventSource {
  static latest: FakeEventSource | null = null;
  listeners = new Map<string, (event: MessageEvent) => void>();
  closed = false;
  constructor(public url: string) {
    FakeEventSource.latest = this;
  }
  addEventListener(name: string, callback: (event: MessageEvent) => void) {
    this.listeners.set(name, callback);
  }
  close() {
    this.closed = true;
  }
  emit(name: string, data: unknown) {
    this.listeners.get(name)?.(
      new MessageEvent(name, { data: JSON.stringify(data) }),
    );
  }
}

let disposeStream: (() => void) | undefined;

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', `/${HASH}`);
  resetCardCachesForTests();
  publishLeafJudgments(PLACE.repo, PLACE.master, PLACE.leaf, [], []);
  treeViewStore.getState().setView('changed');
  treeOrderStore.getState().setOrder('priority');
});

afterEach(() => {
  cleanup();
  disposeStream?.();
  disposeStream = undefined;
  publishLeafJudgments(PLACE.repo, PLACE.master, PLACE.leaf, [], []);
  treeViewStore.getState().setView('changed');
  treeOrderStore.getState().setOrder('priority');
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('mounted ReviewSurface with ExplanationSwitch, ExplanationView, and PlannedReview (MIK-R106 v2; MIK-R189 v2)', () => {
  it('mounts ExplanationSwitch in the top line, renders ExplanationView with tree and chat outside, and selects the exact claim block on return', async () => {
    stubIntersection();
    servePage();
    const baseFetch = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string | URL, init?: RequestInit) => {
        const url = new URL(String(address), 'http://localhost');
        if (url.pathname === `${EXPLAINER_BASE}/state`) {
          return new Response(
            JSON.stringify({
              state: 'ok',
              binding: {
                comparison_owner: 'agents_remember.application.review_tree_comparison',
                comparison_number: 8,
                comparison_reference: `review-comparison:${PLACE.master}/${PLACE.leaf}/8`,
              },
              seat: { state: 'live' },
              retired: false,
              retiredAt: null,
              explanation: {
                revision: 8,
                at: '2026-10-19T12:36:00Z',
                entrypoint: 'index.html',
              },
              stale: {
                total: 2,
                parts: [
                  { id: 'look', title: 'Where to look first', reason: 'changed in #8' },
                  { id: 'risks', title: 'Risks', reason: 'changed in #8' },
                ],
              },
              parts: [{ id: 'look', title: 'Where to look first' }],
              claims: [
                {
                  id: 'c1',
                  part: 'look',
                  basis: 'recorded-fact',
                  text: 'the turn tracker changed here',
                  link: {
                    block: 'RLZ-78JFRR',
                    label: 'turn_ended.ts RLZ-78JFRR',
                  },
                },
              ],
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          );
        }
        if (url.pathname === `${EXPLAINER_BASE}/revisions`) {
          return new Response(
            JSON.stringify({
              state: 'ok',
              pinned: 8,
              revisions: [
                {
                  revision: 8,
                  binding: {
                    comparison_owner: 'agents_remember.application.review_tree_comparison',
                    comparison_number: 8,
                    comparison_reference: `review-comparison:${PLACE.master}/${PLACE.leaf}/8`,
                  },
                  label: '#8',
                  at: '2026-10-19T12:36:00Z',
                  kind: 'global',
                  agreed: true,
                  pinned: true,
                },
              ],
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          );
        }
        if (url.pathname === '/api/canvas/sessions') {
          return new Response(
            JSON.stringify({
              state: 'ok',
              session: 'sess-8',
              revision: 8,
              comparison: `review-comparison:${PLACE.master}/${PLACE.leaf}/8`,
              entrypoint: 'index.html',
              scripts: false,
              sandbox: 'allow-scripts',
              pageUrl: '/canvas/page/sess-8',
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          );
        }
        if (url.pathname.endsWith('/revoke')) {
          return new Response(JSON.stringify({ state: 'ok', revoked: true }), { status: 200 });
        }
        return baseFetch(address, init);
      }),
    );

    const chatToggle = vi.fn();
    renderPage({ chat: { open: false, toggle: chatToggle } });

    await waitFor(() =>
      expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0),
    );
    // Legacy CanvasPanel and second composer are replaced.
    expect(screen.queryByTestId('canvas-panel')).toBeNull();
    expect(screen.queryByTestId('canvas-say')).toBeNull();

    // Top-line switch shows stale count.
    const switchEl = screen.getByTestId('review-explanation-switch');
    expect(screen.getByTestId('review-top-line').contains(switchEl)).toBe(true);
    await waitFor(() =>
      expect(screen.getByTestId('switch-explanation').getAttribute('data-stale')).toBe('2'),
    );

    // Switch to Explanation view: tree and chat toggle remain outside!
    fireEvent.click(screen.getByTestId('switch-explanation'));
    expect(await screen.findByTestId('explanation-view')).toBeTruthy();
    expect(screen.getByTestId('review-tree')).toBeTruthy();
    expect(screen.getByTestId('review-chat-toggle')).toBeTruthy();

    // Click claim link: switches back to Review and selects the exact RLZ-78JFRR block.
    const claimLink = await screen.findByTestId('claim-link');
    fireEvent.click(claimLink);

    await waitFor(() =>
      expect(screen.getByTestId('switch-review').getAttribute('aria-selected')).toBe('true'),
    );
    await waitFor(() => {
      const selectedCards = screen
        .getAllByTestId('review-expression-card')
        .filter((card) => card.dataset.selected === 'true');
      expect(selectedCards).toHaveLength(1);
      expect(selectedCards[0].dataset.entries).toBe('RLZ-78JFRR');
    });
    expect(window.location.hash).toContain('sel=b%3A');
  });

  it('mounts PlannedReview when the leaf is in planning mode (/planned)', async () => {
    stubIntersection();
    servePage();
    const baseFetch = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string | URL, init?: RequestInit) => {
        const url = new URL(String(address), 'http://localhost');
        if (url.pathname === `${EXPLAINER_BASE}/planned`) {
          return new Response(
            JSON.stringify({
              state: 'ok',
              planned: {
                mode: 'planned',
                topLine: PLANNED_TOP_LINE,
                intendedNotice: 'Intended, not a fact about the code',
                authority: 'intended',
                binding: {
                  owner: 'planned',
                  number: 0,
                  reference: `review-comparison:${PLACE.master}/${PLACE.leaf}/planned`,
                  label: 'planned',
                },
                tips: {
                  code: 'a'.repeat(40),
                  codeTree: 'b'.repeat(40),
                  memory: 'c'.repeat(40),
                  memoryTree: 'd'.repeat(40),
                  recordedAt: 'planned-master-tip',
                },
                rows: [
                  {
                    subject: 'invariant:INV-1ZKP4R',
                    kind: 'existing',
                    effect: 'strengthen',
                    title: 'planned · strengthen',
                    statement: 'Current statement at memory tip.',
                    label: null,
                    statementState: 'statement',
                    requirement: 'MIK-R189@v2',
                    part: 'effects',
                  },
                ],
                packets: [
                  {
                    ref: 'MIK-R189@v2',
                    path: 'requirements/MIK-R189-v2.md',
                    title: 'MIK-R189 @ v2 — An intent review before the work starts',
                    body: 'Requirement packet body content.',
                    state: 'rendered',
                    part: 'packets',
                    scope: [
                      {
                        path: 'dashboard/src/panels/review/ReviewSurface.tsx',
                        state: 'resolved',
                        resolvedPath: 'dashboard/src/panels/review/ReviewSurface.tsx',
                      },
                    ],
                  },
                ],
                preparedContext: {
                  heading: 'Prepared context',
                  state: 'present',
                  body: 'Prepared context details.',
                  part: 'prepared-context',
                },
                referenceFiles: [
                  {
                    path: `notes/reference/${PLACE.leaf}/design.md`,
                    name: 'design.md',
                    part: 'reference-files',
                  },
                ],
                sorts: PLANNED_SORTS,
                parts: [],
                actions: [],
                modelTurns: 0,
                agentStarts: 0,
                reads: { taskDocuments: 1, knowledgeStatements: 1, packets: 1, scopePaths: 1 },
              },
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          );
        }
        return baseFetch(address, init);
      }),
    );

    renderPage();
    const planned = await screen.findByTestId('planned-review');
    expect(within(planned).getByTestId('planned-top-line').textContent).toBe('planned · no changes');
    expect(within(planned).getByTestId('planned-packet-body').textContent).toContain(
      'Requirement packet body content.',
    );
    expect(within(planned).getByTestId('planned-scope-address')).toBeTruthy();
  });
});

describe('judgment categories, grades, sorts, ungraded groups, stale marks, and inference chips (MIK-R187)', () => {
  it('orders blocks by judgment scale with Priority tie-break, groups ungraded last under Ungraded (n), preserves stale marks, and renders active-first hover reasons including carried merged grades', async () => {
    const categories = [
      {
        name: 'Risk',
        meaning: 'Failure impact on state integrity',
        scale: [
          { grade: 'High', meaning: 'Breaks state invariants' },
          { grade: 'Medium', meaning: 'Local edge case' },
          { grade: 'Low', meaning: 'Cosmetic' },
        ],
      },
      {
        name: 'Complexity',
        scale: [{ grade: 'Deep' }, { grade: 'Simple' }],
      },
    ];
    const changedEntries = (l181Answer.entries ?? []).filter((e) => e.change === 'changed');
    const targetLowId = changedEntries[0].id;
    const targetHighId = changedEntries[changedEntries.length - 1].id;
    const grades = [
      {
        reference: targetLowId,
        category: 'Risk',
        grade: 'Low',
        reason: 'Isolated helper call',
        stale: false,
      },
      {
        reference: targetHighId,
        category: 'Risk',
        grade: 'High',
        reason: 'Modifies shared lock order',
        stale: true,
        carried: [
          {
            category: 'Risk',
            grade: 'Low',
            reason: 'Merged logging hunk',
            stale: true,
          },
          {
            category: 'Risk',
            grade: 'High',
            reason: 'Modifies shared lock order',
            stale: true,
          },
        ],
      },
      {
        reference: targetHighId,
        category: 'Complexity',
        grade: 'Deep',
        reason: 'Crosses two state machines',
        stale: false,
      },
    ];

    const model = changeSetModel(
      {
        outline: l181Outline,
        catalogue: [],
        cards: {
          state: 'ready',
          entries: l181Answer.entries!,
          comparison: l181Answer.comparison!,
        },
        categories,
        grades,
      },
      'judgment:Risk',
      'changed',
      { selection: null },
    );

    expect(model.sort).toBe('judgment:Risk');
    expect(model.sorts.map((s) => s.sort)).toContain('judgment:Risk');
    expect(model.sorts.map((s) => s.sort)).toContain('judgment:Complexity');

    const subject = model.subjects[0];
    const drawn = subject.changed.filter((n) => n.draws.length > 0);
    // targetHighId (High, stale, carried merged grades) comes first, then targetLowId (Low), then the 11 ungraded locations.
    expect(drawn[0].card.entries.map((e) => e.id)).toContain(targetHighId);
    expect(drawn[0].judgment?.activeGrade).toBe('High');
    expect(drawn[0].judgment?.stale).toBe(true);
    expect(drawn[0].judgment?.chipLabel).toBe('✦ Risk: High · stale');
    // Hover text puts active category (Risk, highest first) before Complexity, with plain-text reasons.
    expect(drawn[0].judgment?.hoverText).toContain('Risk: High (stale) — Modifies shared lock order');
    expect(drawn[0].judgment?.hoverText).toContain('Risk: Low (stale) — Merged logging hunk');
    expect(drawn[0].judgment?.hoverText).toContain('Complexity: Deep — Crosses two state machines');
    expect(
      drawn[0].judgment!.hoverText.indexOf('Risk: High'),
    ).toBeLessThan(drawn[0].judgment!.hoverText.indexOf('Complexity: Deep'));

    expect(drawn[1].card.entries.map((e) => e.id)).toContain(targetLowId);
    expect(drawn[1].judgment?.activeGrade).toBe('Low');
    expect(drawn[1].judgment?.stale).toBe(false);
    expect(drawn[1].judgment?.chipLabel).toBe('✦ Risk: Low');

    // First ungraded location carries ungradedGroupCount = 11 (13 total drawn - 2 graded).
    expect(drawn[2].ungradedGroupCount).toBe(11);
    expect(subject.ungradedLabel).toBe('Ungraded (11)');

    // Under a deterministic sort the same block's one chip reads "✦ graded": the judgment stays
    // labelled and the deterministic order is untouched.
    const priorityModel = changeSetModel(
      {
        outline: l181Outline,
        catalogue: [],
        cards: {
          state: 'ready',
          entries: l181Answer.entries!,
          comparison: l181Answer.comparison!,
        },
        categories,
        grades,
      },
      'priority',
      'changed',
      { selection: null },
    );
    const gradedUnderPriority = priorityModel.subjects[0].changed.find(
      (n) => n.draws.length > 0 && n.card.entries.some((e) => e.id === targetHighId),
    );
    expect(gradedUnderPriority?.judgment?.chipLabel).toBe('✦ graded · stale');
    expect(gradedUnderPriority?.judgment?.activeGrade).toBeNull();
    // The grade never enters the subject's change badge or recorded effect: those stay recorded facts.
    expect(priorityModel.subjects[0].reason).not.toMatch(/High|Low|Deep|Simple/);
    expect(priorityModel.subjects[0].row.badge ?? '').not.toMatch(/High|Low|Deep|Simple/);
    expect(priorityModel.subjects[0].effects.join(' ')).not.toMatch(/High|Low|Deep|Simple/);

    // Mount in ReviewSurface and verify DOM chips and Ungraded (11) group in both tree and reading column.
    stubIntersection();
    servePage();
    const baseFetch = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string | URL, init?: RequestInit) => {
        const url = new URL(String(address), 'http://localhost');
        if (url.pathname === `${EXPLAINER_BASE}/state`) {
          return new Response(
            JSON.stringify({
              state: 'ok',
              seat: { state: 'live' },
              categories,
              grades,
              stale: { parts: [], staleGrades: [targetHighId] },
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          );
        }
        if (url.pathname === `${EXPLAINER_BASE}/revisions`) {
          return new Response(JSON.stringify({ state: 'ok', revisions: [] }), { status: 200 });
        }
        return baseFetch(address, init);
      }),
    );

    treeOrderStore.getState().setOrder('judgment:Risk');
    renderPage();

    await waitFor(() =>
      expect(screen.getAllByTestId('review-judgment-chip').length).toBeGreaterThanOrEqual(4),
    );
    expect(screen.getByTestId('review-tree-ungraded-group').textContent).toContain('Ungraded (11)');
    expect(screen.getByTestId('review-reading-ungraded-group').textContent).toContain(
      'Ungraded (11)',
    );
    const chips = screen.getAllByTestId('review-judgment-chip');
    expect(chips.some((chip) => chip.getAttribute('data-stale') === 'true')).toBe(true);
    expect(chips.some((chip) => chip.textContent?.includes('✦ Risk: High · stale'))).toBe(true);

    // A grade never enters a change badge or a recorded effect: the subject rows' badge and
    // meaning stay the recorded facts, however many chips the tree draws.
    const subjectRows = screen.getAllByTestId('review-family-member-open');
    expect(subjectRows.length).toBeGreaterThan(0);
    for (const row of subjectRows) {
      expect(
        `${row.getAttribute('data-change-primary') ?? ''} ${row.getAttribute('data-meaning') ?? ''}`,
      ).not.toMatch(/High|Low|Deep|Simple/);
    }
  });
});

describe('review.explainer.moved SSE handling (MIK-R186 rule 7; MIK-R187 rule 9)', () => {
  it('immediately shows MovedNotice and Update when explainer work exists, and renews live deterministic reads when no explainer work exists', async () => {
    stubIntersection();
    seedProjection({
      enclosures: [
        enclosure({
          enclosure: '/contracts/l181',
          lifecycleId: 'l181',
          repoName: PLACE.repo,
          taskName: PLACE.master,
          leafId: PLACE.leaf,
          worktreeGroup: '/worktrees/l181-ar',
        }),
      ],
      activeWorktreeGroups: ['l181-ar'],
    });
    vi.stubGlobal('EventSource', FakeEventSource);
    disposeStream = connectState();

    const { asked } = servePage();
    const baseFetch = globalThis.fetch;
    let explainerHasWork = true;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string | URL, init?: RequestInit) => {
        const url = new URL(String(address), 'http://localhost');
        if (url.pathname === `${EXPLAINER_BASE}/state`) {
          return new Response(
            JSON.stringify({
              state: 'ok',
              seat: { state: explainerHasWork ? 'live' : 'none' },
              explanation: explainerHasWork
                ? { revision: 8, at: '2026-10-19T12:36:00Z', entrypoint: 'index.html' }
                : null,
              stale: { parts: [] },
              parts: [],
              claims: [],
              moved: null,
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          );
        }
        if (url.pathname === `${EXPLAINER_BASE}/revisions`) {
          return new Response(JSON.stringify({ state: 'ok', revisions: [] }), { status: 200 });
        }
        return baseFetch(address, init);
      }),
    );

    const view = renderPage();
    await waitFor(() =>
      expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0),
    );
    expect(screen.queryByTestId('moved-notice')).toBeNull();

    // 1. Emit review.explainer.moved while explainer work exists -> shows MovedNotice and Update button immediately, no agent wake.
    act(() => {
      FakeEventSource.latest?.emit('review.explainer.moved', {
        repo: PLACE.repo,
        master: PLACE.master,
        leaf: PLACE.leaf,
        kind: 'comparison-moved',
        from: 8,
        to: 9,
        blocksChanged: 3,
        blocks: 3,
        updateOffered: true,
        text: 'task moved: #8 -> #9, 3 blocks changed',
      });
    });

    const notice = await screen.findByTestId('moved-notice');
    expect(notice.getAttribute('data-moved')).toBe('moved');
    expect(screen.getByTestId('update')).toBeTruthy();

    // Emit cleared event -> clears MovedNotice immediately.
    act(() => {
      FakeEventSource.latest?.emit('review.explainer.moved', {
        repo: PLACE.repo,
        master: PLACE.master,
        leaf: PLACE.leaf,
        kind: 'cleared',
        cleared: true,
      });
    });
    await waitFor(() => expect(screen.queryByTestId('moved-notice')).toBeNull());
    view.unmount();

    // 2. No explainer work exists (live deterministic view): code-only move renews live deterministic reads without navigation or memory edit.
    explainerHasWork = false;
    renderPage();
    await waitFor(() =>
      expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0),
    );
    const intentReadsBefore = asked.filter((u) => u.pathname === '/api/review/intent').length;
    const treeReadsBefore = asked.filter((u) => u.pathname === '/api/review/trees').length;

    act(() => {
      FakeEventSource.latest?.emit('review.explainer.moved', {
        repo: PLACE.repo,
        master: PLACE.master,
        leaf: PLACE.leaf,
        kind: 'comparison-moved',
        from: 8,
        to: 9,
        blocksChanged: 2,
        blocks: 2,
        updateOffered: false,
      });
    });

    await waitFor(() => {
      expect(asked.filter((u) => u.pathname === '/api/review/intent').length).toBeGreaterThan(
        intentReadsBefore,
      );
      expect(asked.filter((u) => u.pathname === '/api/review/trees').length).toBeGreaterThan(
        treeReadsBefore,
      );
    });
  });
});
