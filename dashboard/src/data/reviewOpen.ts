// One open of a leaf's review page (D569): the opening's six reads carry one id, so the server
// observes the comparison's inputs once for all six (serving/review_open_observation.py) instead of
// once per read and several times within each. The six are rule 2's (reading/useChangeSet.ts and
// the top bar's badge): the task context, the outline, the summary, the leaf-wide overview, the
// lane and `/stale`.
//
// * An open is minted by the first opening read of a leaf, with a fresh id; an opening read of
//   another leaf mints the next one.
// * Each of the six names carries the id once: a second read of the same name (a superseded or
//   repeated read) carries none, and the server observes for it, so a read the page makes later
//   never borrows the open's snapshot.
// * The sixth of them to start is marked as the open's last: the server drops the open's stamps
//   when that request ends. Reads still running keep the stamps they already hold, and no later
//   read names the id. No request is added to the opening's six.
// * Any read the page makes outside these is sent exactly as before.
// * The opening reads also name the page (one id per page load), and so does the page's review
//   stream (data/stream.ts): the server serves an open whole -- its background preparation waits
//   between the open's reads as well as during them -- until the open ends, the page opens again,
//   or the page's stream closes because the page left (D585).
// * Once the page has drawn a comparison, it names the order it drew that comparison's invariants
//   in (`sendDrawnOrder`), so the background prepares their details in the page's own order (D585).
//   It is no seventh read: it is sent only after the open's six have started, and again only when
//   the drawn order changes.

export const OPEN_HEADER = 'X-Review-Open';
export const LAST_MARK = 'last';
export const PAGE_HEADER = 'X-Review-Page';

export type OpeningRead = 'intent' | 'entries' | 'summary' | 'overview' | 'files' | 'stale';
const OPENING_READS = 6;

const LEAF_KEYS = ['repo', 'master', 'leaf'];
const STALE = /\/api\/explainer\/([^/]+)\/([^/]+)\/([^/]+)\/stale$/;

interface Classified {
  name: OpeningRead;
  leaf: string;
}

// Exactly the keys a read sends, so a subject read or a focused trees read is never an opening read.
function onlyKeys(params: URLSearchParams, keys: readonly string[]): boolean {
  const sent = [...params.keys()];
  return sent.length === keys.length && keys.every((key) => params.has(key));
}

function leafOf(params: URLSearchParams): string {
  return LEAF_KEYS.map((key) => params.get(key) ?? '').join('/');
}

// The opening's review reads by path and the exact query keys each sends; the trees reads are told
// apart by their one view or lane value.
const RULES: readonly {
  name: OpeningRead;
  path: string;
  keys: readonly string[];
  value?: [string, string];
}[] = [
  { name: 'intent', path: '/api/review/intent', keys: LEAF_KEYS },
  { name: 'entries', path: '/api/review/intent/entries', keys: LEAF_KEYS },
  { name: 'summary', path: '/api/review/intent/summary', keys: [...LEAF_KEYS, 'comparison'] },
  {
    name: 'overview',
    path: '/api/review/trees',
    keys: [...LEAF_KEYS, 'comparison', 'view'],
    value: ['view', 'overview'],
  },
  {
    name: 'files',
    path: '/api/review/trees',
    keys: [...LEAF_KEYS, 'comparison', 'lane'],
    value: ['lane', 'files'],
  },
];

// Which opening read `url` is, if any.
export function openingReadOf(url: string): Classified | null {
  const { pathname: path, searchParams: params } = new URL(url, 'http://review.invalid');
  const stale = STALE.exec(path);
  if (stale) return { name: 'stale', leaf: stale.slice(1, 4).map(decodeURIComponent).join('/') };
  const rule = RULES.find(
    (one) =>
      path.endsWith(one.path) &&
      onlyKeys(params, one.keys) &&
      (one.value === undefined || params.get(one.value[0]) === one.value[1]),
  );
  return rule ? { name: rule.name, leaf: leafOf(params) } : null;
}

interface Open {
  leaf: string;
  id: string;
  named: Set<OpeningRead>;
}

let current: Open | null = null;
// The last open of a leaf, and whether its six reads have all started.
let opened: { leaf: string; id: string; complete: boolean } | null = null;
let pendingOrder: { leaf: string; body: Omit<DrawnOrder, 'open'> } | null = null;
let lastSent: string | null = null;

interface DrawnOrder {
  repo: string;
  master: string;
  leaf: string;
  comparison: number;
  open: string;
  order: string[];
}

function freshId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
    return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

let page: string | null = null;

// This page load's id: its opening reads and its review stream name it.
export function reviewPageId(): string {
  page ??= freshId();
  return page;
}

// The header an opening read carries, or none; the read is then counted as started.
export function openingHeader(url: string): Record<string, string> | undefined {
  const read = openingReadOf(url);
  if (read === null) return undefined;
  if (current?.leaf !== read.leaf) {
    current = { leaf: read.leaf, id: freshId(), named: new Set() };
    opened = { leaf: read.leaf, id: current.id, complete: false };
  }
  const open = current;
  if (open.named.has(read.name)) return undefined;
  open.named.add(read.name);
  const named = { [PAGE_HEADER]: reviewPageId() };
  if (open.named.size < OPENING_READS) return { [OPEN_HEADER]: open.id, ...named };
  current = null; // the open's six have started: the next opening read is the next open
  if (opened?.id === open.id) opened.complete = true;
  queueMicrotask(flushDrawnOrder); // after the sixth is on its way, never before it
  return { [OPEN_HEADER]: `${open.id}; ${LAST_MARK}`, ...named };
}

// `fetch`, with the open's header when `url` is one of the opening's reads; otherwise exactly the
// call the read made before.
export function openingFetch(url: string, init?: RequestInit): Promise<Response> {
  const header = openingHeader(url);
  if (header === undefined) return init === undefined ? fetch(url) : fetch(url, init);
  const headers = new Headers(init?.headers);
  for (const [name, value] of Object.entries(header)) headers.set(name, value);
  return fetch(url, { ...init, headers });
}

function flushDrawnOrder(): void {
  const pending = pendingOrder;
  if (!pending || !opened?.complete || opened.leaf !== pending.leaf) return;
  pendingOrder = null;
  const body: DrawnOrder = { ...pending.body, open: opened.id };
  const key = JSON.stringify(body);
  if (key === lastSent) return;
  lastSent = key;
  // Fire and forget: the order only steers background work, and its answer carries nothing; a send
  // that cannot even start changes nothing the page shows.
  try {
    void Promise.resolve(
      fetch('/api/review/trees/order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: key,
      }),
    ).catch(() => undefined);
  } catch {
    // nothing to steer with: the queue keeps its own order
  }
}

// The order the page drew one comparison's invariants in (their selector ids), named after the
// leaf's open has sent its six; an unchanged order is not sent again.
export function sendDrawnOrder(
  scope: { repo: string; master: string; leaf: string },
  comparison: number,
  order: readonly string[],
): void {
  if (order.length === 0) return;
  const leaf = [scope.repo, scope.master, scope.leaf].join('/');
  pendingOrder = { leaf, body: { ...scope, comparison, order: [...order] } };
  flushDrawnOrder();
}

// Tests start each case with no open in progress.
export function resetReviewOpenForTests(): void {
  current = null;
  opened = null;
  pendingOrder = null;
  lastSent = null;
}
