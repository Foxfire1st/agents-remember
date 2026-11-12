// Mounted ReviewSurface integration for the Explanation view, PlannedReview, Judgment sorts/chips,
// and review.explainer.moved SSE events (MIK-R106 v2; MIK-R187 v1; MIK-R189 v2; MIK-R103 v2; MIK-R104 v3).
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { resetCardCachesForTests } from '../../../data/reviewTrees';
import { connectState } from '../../../data/stream';
import {
  enclosure,
  seedProjection,
  seedTaskEntries,
  taskEntry,
} from '../../detail-panel/test-utils';
import { changeSetModel } from '../changeSetOrder';
import { l181Answer, l181Outline } from '../changeSetOrder.test-utils';
import {
  PLACE,
  renderPage,
  selectInvariant,
  servePage,
  stubIntersection,
  waitForOpening,
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
    this.listeners.get(name)?.(new MessageEvent(name, { data: JSON.stringify(data) }));
  }
}

let disposeStream: (() => void) | undefined;

// The `/stale` answer the host derives from the same facts `/state` carries (explainer_routes.py
// `handle_stale`): the staleness nested and spread, the grades and categories, the shown revision and
// the watcher's notice -- and no seat. The Review tab's badge reads it alone.
function staleAnswerOf(state: Record<string, unknown>): Response {
  const stale = (state.stale ?? { parts: [] }) as Record<string, unknown>;
  return new Response(
    JSON.stringify({
      state: 'ok',
      stale,
      ...stale,
      grades: state.grades ?? [],
      categories: state.categories ?? [],
      explanation: state.explanation ?? null,
      movedNotice: state.moved ?? null,
    }),
    { status: 200, headers: { 'content-type': 'application/json' } },
  );
}

const explainerReads = (urls: readonly URL[], route: string) =>
  urls.filter((url) => url.pathname === `${EXPLAINER_BASE}/${route}`).length;

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
    const evidenceRequests: Record<string, unknown>[] = [];
    const explainerAsked: URL[] = [];
    const stateBody = {
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
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string | URL, init?: RequestInit) => {
        const url = new URL(String(address), 'http://localhost');
        if (url.pathname.startsWith(`${EXPLAINER_BASE}/`)) explainerAsked.push(url);
        if (url.pathname === `${EXPLAINER_BASE}/stale`) return staleAnswerOf(stateBody);
        if (url.pathname === `${EXPLAINER_BASE}/state`) {
          return new Response(JSON.stringify(stateBody), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          });
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
        if (url.pathname === '/api/canvas/sessions/sess-8/messages') {
          evidenceRequests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
          return new Response(
            JSON.stringify({
              state: 'opened',
              operation: 'open-evidence',
              reference: 'RLZ-78JFRR',
              comparison: `review-comparison:${PLACE.master}/${PLACE.leaf}/8`,
              target: { repository: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf },
              location: { entry: 'RLZ-78JFRR' },
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
    await selectInvariant();

    await waitFor(() =>
      expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0),
    );
    // Legacy CanvasPanel and second composer are replaced.
    expect(screen.queryByTestId('canvas-panel')).toBeNull();
    expect(screen.queryByTestId('canvas-say')).toBeNull();

    // Top-line switch shows stale count.
    const switchEl = screen.getByTestId('review-explanation-switch');
    const toolbar = screen.getByTestId('review-top-line');
    expect(toolbar.contains(switchEl)).toBe(true);
    const stableControls = [
      'review-tree-toggle',
      'review-chat-toggle',
      'review-explanation-switch',
      'switch-review',
      'switch-explanation',
      'review-code-context',
      'review-crumb',
    ].map((name) => [name, screen.getByTestId(name)] as const);
    const expectStableControls = () => {
      for (const [name, control] of stableControls) {
        expect(screen.getAllByTestId(name)).toHaveLength(1);
        expect(screen.getByTestId(name)).toBe(control);
        expect(toolbar.contains(control)).toBe(true);
      }
    };
    expectStableControls();
    await waitFor(() =>
      expect(screen.getByTestId('switch-explanation').getAttribute('data-stale')).toBe('2'),
    );
    // Only what is on screen (D569): the Review tab's badge is one `/stale` read for the comparison
    // on screen, and neither the seat state nor the revision list is read for it.
    expect(explainerReads(explainerAsked, 'stale')).toBe(1);
    expect(explainerReads(explainerAsked, 'state')).toBe(0);
    expect(explainerReads(explainerAsked, 'revisions')).toBe(0);
    const staleQuery = explainerAsked.find((url) => url.pathname.endsWith('/stale'))!.searchParams;
    expect(staleQuery.get('comparisonNumber')).not.toBeNull();

    const fullContext = screen.getByRole('radio', { name: 'Full context' });
    fireEvent.click(fullContext);
    expect(fullContext.getAttribute('aria-checked')).toBe('true');

    // Both tabs retain the one toolbar; Explanation disables the source-code choice in place.
    fireEvent.click(screen.getByTestId('switch-explanation'));
    expect(await screen.findByTestId('explanation-view')).toBeTruthy();
    // Opening the Explanation tab reads the full state and the revisions once each, for the same
    // comparison; the badge asks nothing more.
    await waitFor(() => expect(explainerReads(explainerAsked, 'revisions')).toBe(1));
    expect(explainerReads(explainerAsked, 'state')).toBe(1);
    expect(explainerReads(explainerAsked, 'stale')).toBe(1);
    const stateQuery = explainerAsked.find((url) => url.pathname.endsWith('/state'))!.searchParams;
    expect(stateQuery.get('comparisonNumber')).toBe(staleQuery.get('comparisonNumber'));
    expect(screen.getByTestId('review-tree')).toBeTruthy();
    expect(document.querySelector('[data-reading-head]')).toBeNull();
    expect(fullContext.hasAttribute('disabled')).toBe(true);
    expect(fullContext.getAttribute('aria-checked')).toBe('true');
    expect(screen.queryByTestId('review-canvas-slot')).toBeNull();
    expect(
      screen.getByTestId('review-center-column').querySelector('[data-reading-spacer]'),
    ).toBeNull();
    expectStableControls();
    const treeToggle = screen.getByTestId('review-tree-toggle');
    expect(treeToggle.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(treeToggle);
    expect(treeToggle.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(treeToggle);
    expect(treeToggle.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(screen.getByTestId('review-chat-toggle'));
    expect(chatToggle).toHaveBeenCalledOnce();

    // The current frame requests evidence through its bound channel. The host's exact
    // destination switches back to Review and selects RLZ-78JFRR; no lower claims list exists.
    const frame = await screen.findByTestId('explanation-frame');
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: (frame as HTMLIFrameElement).contentWindow,
          data: {
            schema: 'ar-canvas-message/v1',
            protocolVersion: 1,
            type: 'open-evidence',
            requestId: 'open-RLZ-78JFRR',
            session: 'sess-8',
            revision: 8,
            comparison: `review-comparison:${PLACE.master}/${PLACE.leaf}/8`,
            payload: { reference: 'RLZ-78JFRR' },
          },
        }),
      );
    });
    await waitFor(() => expect(evidenceRequests).toHaveLength(1));
    expect(evidenceRequests[0]).toMatchObject({
      type: 'open-evidence',
      session: 'sess-8',
      revision: 8,
      payload: { reference: 'RLZ-78JFRR' },
    });

    await waitFor(() =>
      expect(screen.getByTestId('switch-review').getAttribute('aria-selected')).toBe('true'),
    );
    expectStableControls();
    expect(screen.getByTestId('review-canvas-slot')).toBeTruthy();
    expect(
      screen.getByTestId('review-center-column').querySelector('[data-reading-spacer]'),
    ).not.toBeNull();
    expect(document.querySelector('[data-reading-head]')).toBeNull();
    expect(fullContext.hasAttribute('disabled')).toBe(false);
    expect(fullContext.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(screen.getByRole('radio', { name: 'Changes only' }));
    await waitFor(() => {
      const selectedCards = screen
        .getAllByTestId('review-expression-card')
        .filter((card) => card.dataset.selected === 'true');
      expect(selectedCards).toHaveLength(1);
      expect(selectedCards[0].dataset.entries).toBe('RLZ-78JFRR');
    });
    expect(window.location.hash).toContain('sel=b%3A');
    // Switching back and forth re-reads nothing: the opened tab's reads stay current on their own.
    expect(explainerReads(explainerAsked, 'state')).toBe(1);
    expect(explainerReads(explainerAsked, 'revisions')).toBe(1);
    expect(explainerReads(explainerAsked, 'stale')).toBe(1);
  });

  it('mounts PlannedReview when the leaf is in planning mode (/planned)', async () => {
    stubIntersection();
    servePage();
    // The page reads the planned view only for a leaf the task list says is planning (MIK-R211 v2
    // rule 7), so this test states that fact the way the served projection does.
    seedTaskEntries([
      taskEntry({
        id: PLACE.leaf,
        stage: 'planned',
        reference: {
          repository: PLACE.repo,
          path: `${PLACE.master}/201_the-explainer-builds-on-real-data-previewed-live.json`,
        },
      }),
    ]);
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
    expect(within(planned).getByTestId('planned-top-line').textContent).toBe(
      'planned · no changes',
    );
    expect(within(planned).getByTestId('planned-packet-body').textContent).toContain(
      'Requirement packet body content.',
    );
    expect(within(planned).getByTestId('planned-scope-address')).toBeTruthy();
  });

  it('asks no leaf for the planned view unless the task list says it is planning', async () => {
    // MIK-R211 v2 rule 7, failing before: the page asked `/planned` for every leaf it opened and the
    // route answered 409 `not-a-planning-leaf`, one refusal in the log per opened review.
    stubIntersection();
    servePage();
    seedTaskEntries([
      taskEntry({
        id: PLACE.leaf,
        stage: 'started',
        reference: {
          repository: PLACE.repo,
          path: `${PLACE.master}/201_the-explainer-builds-on-real-data-previewed-live.json`,
        },
      }),
    ]);
    const baseFetch = globalThis.fetch;
    const asked: string[] = [];
    const reads: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string | URL, init?: RequestInit) => {
        const url = new URL(String(address), 'http://localhost');
        if (url.pathname === `${EXPLAINER_BASE}/planned`) asked.push(url.pathname);
        if (url.pathname.startsWith('/api/review')) reads.push(url.pathname);
        return baseFetch(address, init);
      }),
    );

    renderPage();
    // The positive control is the test above: a planning leaf's page does make this read. Here the
    // served page has resolved -- the surface read it, which is what enables any planned read -- and
    // the planned route was still never asked. The opening drawn is the condition (not a wall-clock
    // pause), and one flushed act lets the effects that would ask run first.
    await waitFor(() => expect(reads.length).toBeGreaterThan(0));
    await waitForOpening();
    await act(async () => undefined);
    expect(asked).toEqual([]);
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
    expect(drawn[0].judgment?.hoverText).toContain(
      'Risk: High (stale) — Modifies shared lock order',
    );
    expect(drawn[0].judgment?.hoverText).toContain('Risk: Low (stale) — Merged logging hunk');
    expect(drawn[0].judgment?.hoverText).toContain('Complexity: Deep — Crosses two state machines');
    expect(drawn[0].judgment!.hoverText.indexOf('Risk: High')).toBeLessThan(
      drawn[0].judgment!.hoverText.indexOf('Complexity: Deep'),
    );

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
        const judged = {
          state: 'ok',
          seat: { state: 'live' },
          categories,
          grades,
          stale: { parts: [], staleGrades: [targetHighId] },
        };
        // The Review tab's chips come from the badge's cheap read, which carries the judgments.
        if (url.pathname === `${EXPLAINER_BASE}/stale`) return staleAnswerOf(judged);
        if (url.pathname === `${EXPLAINER_BASE}/state`) {
          return new Response(JSON.stringify(judged), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          });
        }
        if (url.pathname === `${EXPLAINER_BASE}/revisions`) {
          return new Response(JSON.stringify({ state: 'ok', revisions: [] }), { status: 200 });
        }
        return baseFetch(address, init);
      }),
    );

    treeOrderStore.getState().setOrder('judgment:Risk');
    renderPage();
    await selectInvariant();

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
  it('names the newer comparison in the top line badge when explainer work exists, and renews live deterministic reads when no explainer work exists', async () => {
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
    let stateReads = 0;
    let staleReads = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string | URL, init?: RequestInit) => {
        const url = new URL(String(address), 'http://localhost');
        const stateBody = {
          state: 'ok',
          seat: { state: explainerHasWork ? 'live' : 'none' },
          explanation: explainerHasWork
            ? { revision: 8, at: '2026-10-19T12:36:00Z', entrypoint: 'index.html' }
            : null,
          // The badge is drawn only while something is stale (F18); the newer comparison is
          // one of its lines.
          stale: explainerHasWork
            ? {
                total: 1,
                parts: [{ id: 'look', title: 'Where to look first', reason: 'changed in #8' }],
              }
            : { parts: [] },
          parts: [],
          claims: [],
          moved: null,
        };
        if (url.pathname === `${EXPLAINER_BASE}/stale`) {
          staleReads += 1;
          return staleAnswerOf(stateBody);
        }
        if (url.pathname === `${EXPLAINER_BASE}/state`) {
          stateReads += 1;
          return new Response(JSON.stringify(stateBody), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          });
        }
        if (url.pathname === `${EXPLAINER_BASE}/revisions`) {
          return new Response(JSON.stringify({ state: 'ok', revisions: [] }), { status: 200 });
        }
        return baseFetch(address, init);
      }),
    );

    const view = renderPage();
    await selectInvariant();
    await waitFor(() =>
      expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0),
    );
    const badge = await screen.findByTestId('explanation-stale-badge');
    expect(badge.getAttribute('data-newer')).toBeNull();
    const intentReadsWithWork = asked.filter((u) => u.pathname === '/api/review/intent').length;
    const treeReadsWithWork = asked.filter((u) => u.pathname === '/api/review/trees').length;

    // 1. Emit review.explainer.moved while explainer work exists -> the top line's badge names the
    // newer comparison immediately, as the watcher published it; no agent wake, and the page keeps
    // its reads (the explainer's page is what an Update would renew, not the deterministic view).
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

    await waitFor(() =>
      expect(screen.getByTestId('explanation-stale-badge').getAttribute('data-newer')).toBe('true'),
    );
    fireEvent.click(screen.getByTestId('explanation-stale-mark'));
    expect(screen.getByTestId('newer-comparison').textContent).toBe(
      'A newer comparison exists: task moved: #8 -> #9, 3 blocks changed',
    );
    expect(asked.filter((u) => u.pathname === '/api/review/intent')).toHaveLength(
      intentReadsWithWork,
    );
    expect(asked.filter((u) => u.pathname === '/api/review/trees')).toHaveLength(treeReadsWithWork);
    const staleReadsBeforeClear = staleReads;

    // Emit cleared event -> the badge drops the newer comparison, and its staleness is read again
    // (the Review tab's badge read; the seat state is never read for it).
    act(() => {
      FakeEventSource.latest?.emit('review.explainer.moved', {
        repo: PLACE.repo,
        master: PLACE.master,
        leaf: PLACE.leaf,
        kind: 'cleared',
        cleared: true,
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId('explanation-stale-badge').getAttribute('data-newer')).toBeNull();
      expect(screen.queryByTestId('newer-comparison')).toBeNull();
      expect(staleReads).toBeGreaterThan(staleReadsBeforeClear);
    });
    expect(stateReads).toBe(0);
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

// MIK-R211 rules 3, 4 and 6, in the mounted review page: the Explanation view draws the first
// request where ✦ Explain was pressed, says nothing about staleness before a revision exists, and
// the press opens the chat column instead of toggling it shut.
describe('✦ Explain pressed in the Explanation view (MIK-R211 rules 4 and 6)', () => {
  it('draws the request in the view itself, opens the chat column without closing it, and draws no stale list yet', async () => {
    stubIntersection();
    servePage();
    const baseFetch = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string | URL, init?: RequestInit) => {
        const url = new URL(String(address), 'http://localhost');
        if (url.pathname === `${EXPLAINER_BASE}/state`) {
          // The developer's own first load: no seat, no page, and staleness the host cannot compute
          // because there is no retained revision to compute it against.
          return new Response(
            JSON.stringify({
              state: 'ok',
              seat: { state: 'missing' },
              retired: false,
              retiredAt: null,
              binding: {
                comparison_owner: 'agents_remember.application.review_tree_comparison',
                comparison_number: 8,
                comparison_reference: `review-comparison:${PLACE.master}/${PLACE.leaf}/8`,
              },
              explanation: null,
              building: null,
              build: null,
              stale: { parts: [], total: 0, unavailable: 'the leaf has no retained revision' },
              parts: [],
              claims: [],
              moved: null,
              limit: null,
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          );
        }
        if (url.pathname === `${EXPLAINER_BASE}/revisions`) {
          return new Response(JSON.stringify({ state: 'ok', pinned: null, revisions: [] }), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          });
        }
        return baseFetch(address, init);
      }),
    );

    const chatToggle = vi.fn();
    const chatExpand = vi.fn();
    renderPage({ chat: { open: false, toggle: chatToggle, expand: chatExpand } });
    await selectInvariant();
    await waitFor(() =>
      expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0),
    );

    fireEvent.click(screen.getByTestId('switch-explanation'));
    const explanation = await screen.findByTestId('explanation-view');
    // No revision yet: no "Stale parts" block, and no red "Staleness cannot be read".
    expect(within(explanation).queryByTestId('stale-parts')).toBeNull();
    expect(explanation.textContent).not.toContain('Staleness cannot be read');

    fireEvent.click(within(explanation).getByTestId('explain-change-set'));

    // Something shows, where he pressed: the request's form, in the Explanation view itself.
    const form = await screen.findByTestId('explanation-request-form');
    expect(explanation.contains(form)).toBe(true);
    expect(within(form).getByTestId('explanation-request-send')).toBeTruthy();
    expect(within(explanation).queryByTestId('explanation-empty')).toBeNull();

    // The chat column is opened, not toggled: a toggle from the state the press started in closed
    // the very column the ask had just opened.
    expect(chatExpand).toHaveBeenCalled();
    expect(chatToggle).not.toHaveBeenCalled();
  });
});
