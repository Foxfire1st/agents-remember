// A fetch router for the review page over L181 #8's captured card read (l89.capture-provenance.json):
// INV-1ZKP4R with 74 entries (15 changed, 31 unchanged, 28 not comparable) and WP5's outline of it
// (changeSetOrder.test-utils.ts). The task context and the selected subject's answer are the L37
// captures (l86.capture-provenance.json) moved onto comparison #8; the summary, the leaf-wide trees
// answer and the lane (one added file of ADDED_LINES lines without an invariant) are composed here
// and named so. Every request is recorded, so a test counts the page's reads.
import { fireEvent, render } from '@testing-library/react';
import { vi } from 'vitest';

import type { ReviewEntryListResult, ReviewResult } from '../../../data/review';
import type { ReviewTreeComparison, ReviewTreesResult } from '../../../data/reviewTrees';
import { L181_SUBJECT, l181Answer, l181Outline } from '../changeSetOrder.test-utils';
import { captured } from '../l86.test-utils';
import { ReviewSurface } from '../ReviewSurface';

export const PLACE = {
  repo: 'agents-remember',
  master: '260928_maintained-invariant-knowledge',
  leaf: '260928-MIK-L181',
};
const LATEST = 8;
export const LANE_PATH = 'dashboard/src/panels/review/reading/added.ts';
const ADDED_LINES = 45;
export { L181_SUBJECT };

const task = captured<ReviewResult>('l86.task.captured.json');
const member = captured<ReviewResult>('l86.member.captured.json');
const cardPaths = [
  ...new Set((l181Answer.entries ?? []).flatMap((entry) => [entry.before.path, entry.after.path]).filter(Boolean)),
] as string[];

const comparisonAt = (number: number): ReviewTreeComparison => ({ ...l181Answer.comparison!, number });

// A captured answer moved onto comparison #n, with the card and lane paths in its change inventory.
function movedAnswer(body: ReviewResult, number: number): ReviewResult {
  const moved = structuredClone(body);
  const payload = moved.payload!;
  payload.limitations = payload.limitations.map((token) =>
    token.startsWith('review:trees:') ? `review:trees:${number}` : token,
  );
  const first = payload.source.inventory.entries[0];
  for (const path of [LANE_PATH, ...cardPaths]) payload.source.inventory.entries.push({ ...first, path });
  return moved;
}

const SIDE = { repository_id: 'agents-remember', master: PLACE.master, leaf_id: PLACE.leaf };

const ENTRIES: ReviewEntryListResult = {
  ...SIDE,
  state: 'entries',
  operation: 'review-intent-entries',
  outline: l181Outline,
  entries: l181Outline.invariants.map((row) => ({
    selector_kind: 'invariant',
    selector_id: row.selector_id,
    label: row.id,
    presence: 'both',
  })),
  total_subjects: 3,
  invariant_total: 3,
  family_total: 0,
};

const summary = (number: number) => ({
  ...SIDE,
  state: 'counted',
  operation: 'review-intent-summary',
  comparison: number,
  counts: {
    added: 0,
    removed: 0,
    invariants: { after_only: 0, before_only: 0 },
    guarantees: { after_only: 0, before_only: 0 },
    realization_only: 1,
    membership_only: 0,
    unresolved: 0,
  },
  attribution: { state: 'counted', changed_total: 4, attributed: 3, unexplained: 1, attribution_unknown: 0, unmeasured: [] },
});

const leafTrees = (number: number): ReviewTreesResult => ({
  ...l181Answer,
  comparison: comparisonAt(number),
  entries: undefined,
  code_changes: { state: 'facts', invariants: [] },
});

const cards = (number: number): ReviewTreesResult => ({ ...l181Answer, comparison: comparisonAt(number) });

const laneFile = {
  path: LANE_PATH,
  status: 'added',
  content: 'text',
  bucket: 'unexplained',
  reason: 'no recorded entry claims it',
  counts: { hunks: 1, linked: 0, unexplained: 1, attribution_unknown: 0 },
  unknown_reasons: [],
};
const destination = (files: unknown[]) => ({ files, bucket_files: files.length, attributed_files: 0, hunks: files.length, non_text: 0 });

const lane = (number: number) => ({
  ...SIDE,
  state: 'trees',
  comparison: comparisonAt(number),
  lane: {
    state: 'measured',
    detail: 'composed for the review-page tests',
    changed_total: 4,
    attributed: 3,
    unexplained: 1,
    attribution_unknown: 0,
    unexplained_changes: destination([laneFile]),
    unknown_attribution: destination([]),
    unmeasured: [],
    paths: [{ path: LANE_PATH, bucket: 'unexplained' }],
  },
});

const sides = [
  { side: 'before', knowledge: 'available', entries: [] },
  { side: 'after', knowledge: 'available', entries: [] },
];

function classification(path: string, number: number) {
  const added = path === LANE_PATH;
  const hunks = added
    ? [{ before: { start: 0, count: 0 }, after: { start: 1, count: ADDED_LINES }, classification: 'unexplained', links: [], unknown: [] }]
    : [];
  return {
    ...SIDE,
    state: 'trees',
    comparison: comparisonAt(number),
    file_classification: {
      ...laneFile,
      path,
      bucket: added ? 'unexplained' : 'attributed',
      mode_change: false,
      sides,
      hunks,
      counts: { hunks: hunks.length, linked: 0, unexplained: hunks.length, attribution_unknown: 0 },
    },
  };
}

const ADDED_TEXT = Array.from({ length: ADDED_LINES }, (_, index) => `export const line${index + 1} = ${index + 1};`).join('\n');

function treesReply(url: URL): unknown {
  const number = Number(url.searchParams.get('comparison') ?? LATEST);
  const file = url.searchParams.get('file');
  if (file !== null) return classification(file, number);
  if (url.searchParams.has('lane')) return lane(number);
  return url.searchParams.has('invariants') ? cards(number) : leafTrees(number);
}

function intentReply(url: URL): unknown {
  return url.searchParams.get('selectorId') ? movedAnswer(member, LATEST) : movedAnswer(task, LATEST);
}

const fileDiff = (url: URL) => ({
  scope: 'comparison',
  kind: 'code',
  path: url.searchParams.get('path'),
  language: 'typescript',
  before: null,
  after: { content: ADDED_TEXT },
});

const ROUTES: Record<string, (url: URL) => unknown> = {
  '/api/review/intent/summary': (url) => summary(Number(url.searchParams.get('comparison') ?? LATEST)),
  '/api/review/intent/entries': () => ENTRIES,
  '/api/review/intent': intentReply,
  '/api/review/trees': treesReply,
  '/api/changeset/file-diff': fileDiff,
};

export const isSelectionRead = (url: URL) => url.pathname === '/api/review/intent' && url.searchParams.has('selectorId');

// Serves the page. `hold` keeps the answers of the matching requests until `release`.
export function servePage(hold: (url: URL) => boolean = () => false) {
  const asked: URL[] = [];
  const held: (() => void)[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL) => {
      const url = new URL(String(address), 'http://localhost');
      asked.push(url);
      const route = ROUTES[url.pathname];
      if (!route) return new Response('{}', { status: 404 });
      const response = new Response(JSON.stringify(route(url)), { status: 200, headers: { 'content-type': 'application/json' } });
      if (!hold(url)) return response;
      return new Promise<Response>((resolve) => held.push(() => resolve(response)));
    }),
  );
  return { asked, release: () => held.splice(0).forEach((go) => go()) };
}

// The review page's own reads: the review and change-set routes (the canvas has its own).
export const pageReads = (asked: readonly URL[]) =>
  asked.filter((url) => url.pathname.startsWith('/api/review/') || url.pathname.startsWith('/api/changeset/'));

export const fileReads = (asked: readonly URL[]) =>
  pageReads(asked).filter((url) => url.searchParams.has('file') || url.pathname === '/api/changeset/file-diff');

// An IntersectionObserver the test drives: nothing is in view until `show`.
export function stubIntersection() {
  const observed = new Map<Element, IntersectionObserverCallback>();
  class Observer {
    readonly root = null;
    readonly rootMargin = '';
    readonly thresholds = [];
    constructor(private readonly callback: IntersectionObserverCallback) {}
    observe(node: Element) {
      observed.set(node, this.callback);
    }
    unobserve(node: Element) {
      observed.delete(node);
    }
    disconnect() {}
    takeRecords() {
      return [];
    }
  }
  vi.stubGlobal('IntersectionObserver', Observer);
  return {
    show(node: Element) {
      const callback = observed.get(node);
      callback?.([{ isIntersecting: true, target: node } as unknown as IntersectionObserverEntry], {} as IntersectionObserver);
    },
  };
}

// An IntersectionObserver that reports every observed block in view at once, as a browser does at the
// first paint for the blocks inside the root's viewport and its margin. Nothing scrolls.
export function stubInViewAtFirstPaint() {
  class Observer {
    readonly root = null;
    readonly rootMargin = '';
    readonly thresholds = [];
    constructor(private readonly callback: IntersectionObserverCallback) {}
    observe(node: Element) {
      queueMicrotask(() =>
        this.callback([{ isIntersecting: true, target: node } as unknown as IntersectionObserverEntry], this as unknown as IntersectionObserver),
      );
    }
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  }
  vi.stubGlobal('IntersectionObserver', Observer);
}

// The added lane file's text, in place of the composed one (call after `servePage`).
export function serveLaneText(text: string) {
  const served = globalThis.fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL, init?: RequestInit) => {
      const url = new URL(String(address), 'http://localhost');
      if (url.pathname !== '/api/changeset/file-diff') return served(address, init);
      await served(address, init);
      const body = { ...fileDiff(url), after: { content: text } };
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
}

// The selected subject's answer as `edit` changes it (call after `servePage`).
export function serveSelectedAnswer(edit: (payload: NonNullable<ReviewResult['payload']>) => void) {
  const served = globalThis.fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL, init?: RequestInit) => {
      const response = await served(address, init);
      if (!isSelectionRead(new URL(String(address), 'http://localhost'))) return response;
      const body = (await response.json()) as ReviewResult;
      edit(body.payload!);
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
}

// The reader's first gesture on the page: in-view reads start after it (rule 2).
export function engage() {
  fireEvent.wheel(document.querySelector('[data-review-scroller]')!, { deltaY: 120 });
}

export function renderPage(props: Partial<React.ComponentProps<typeof ReviewSurface>> = {}) {
  return render(<ReviewSurface {...PLACE} onBack={() => undefined} {...props} />);
}
