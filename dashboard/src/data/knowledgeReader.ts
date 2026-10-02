// The path-based knowledge reader (MIK-R29): application/knowledge_reader, served by
// serving/knowledge_reader.py at /api/knowledge/reader/<view>.
//
// The reader browses one repository's knowledge at one memory tree -- `published` (the default:
// the published memory-tree selection), any memory commit, or `leaf:<scope>` (a live leaf's
// candidate) -- and needs no task. Every view is addressed by the same values its URL carries
// (`#knowledge?repo&commit&path|id|census|view`), so a view can be shared and every link is a
// navigation.
//
// Answers are typed by `state`: a view (`view`, `census`, `present`), `not-converted` (the tree
// keeps its database reads; the reader serves converted trees only), `not-found`, `unavailable`
// and `invalid-request`. A partial index, an unavailable file or an unverifiable state is named on
// the part of the view it affects -- never rendered as an empty result.

import { fetchWithTimeout } from './fetchWithTimeout';

export const KNOWLEDGE_HASH = '#knowledge';
export const READER_TIMEOUT_MS = 120_000;

export type ReaderView = 'path' | 'subtree' | 'record' | 'census' | 'without-proof' | 'code';

export interface ReaderAddress {
  repo: string;
  commit: string;
  view: ReaderView;
  path?: string;
  id?: string;
  census?: string;
  locator?: string;
  blob?: string;
}

export type InvariantState = 'stale' | 'unverifiable' | 'unrealized' | 'current';
export type EntryState = 'current' | 'stale' | 'unverifiable';

export interface ReaderSelection {
  repo: string;
  commit: string;
  kind?: 'published' | 'commit' | 'leaf';
  memoryRevision?: string | null;
  treeKey?: string;
  indexState?: 'complete' | 'partial';
  problems?: { path: string; detail: string }[];
  codeTree?: { repositoryRoot: string; treeId: string } | null;
  // Where the code tree comes from: a memory commit's Code-Commit pairing, or HEAD of the scope's
  // checkout (published and a leaf; a leaf's uncommitted code is not included -- `codeNote`).
  codeSource?: 'paired-code-commit' | 'checkout-head' | 'none';
  codeNote?: string;
  // The memory commit a working-tree selection equals when it is clean: a view pinned to it is
  // reproducible when shared (`published` moves).
  pinnedCommit?: string | null;
}

export interface ReaderAnswer {
  state: string;
  view?: string;
  selection?: ReaderSelection;
  detail?: string;
}

export interface RecordSummary {
  id: string;
  kind?: string;
  status?: string;
  revision?: number | null;
  title?: string;
  missing?: boolean;
}

export interface Anchor {
  path?: string | null;
  locator: { kind: string; name?: string; start?: number; end?: number };
  blob?: string;
  content?: string;
}

export interface ReferenceTarget {
  kind: string;
  id?: string;
  path?: string | null;
  anchor?: Anchor;
  record?: RecordSummary;
  requirement?: Record<string, unknown>;
  document?: Record<string, unknown>;
  text?: string;
}

export interface ReferenceItem {
  number: string;
  note?: string | null;
  targets: ReferenceTarget[];
}

export interface FileRead {
  path: string;
  state: 'present' | 'absent' | 'unavailable';
  text?: string;
  detail?: string;
}

export interface EntryView {
  id: string;
  kind: 'realization' | 'proof';
  invariant: string;
  path: string;
  sidecar: string;
  anchor?: Anchor;
  role?: string | null;
  rationale?: string | null;
  facet?: string | null;
  state?: EntryState | null;
  reason?: string | null;
}

export interface InvariantGroup extends RecordSummary {
  statement?: string | null;
  state?: InvariantState | null;
  families: string[];
  entries: EntryView[];
}

export interface FamilyAtPath extends RecordSummary {
  guarantee?: string | null;
  member: boolean;
  via: string[];
  routes: string[];
  members: RecordSummary[];
  staleMembers: string[];
  otherLocations: { path: string; entries: { id: string; kind: string; invariant: string }[] }[];
}

export interface LinkTarget {
  kind: 'record' | 'route' | 'code' | 'requirement';
  id?: string;
  path?: string | null;
  anchor?: Anchor;
  requirement?: Record<string, unknown>;
}

export interface Alternative {
  index: number;
  option: string;
  status: 'chosen' | 'rejected' | 'deferred';
  reason: string;
  reconsiderWhen?: string | null;
  reconsiderOn: LinkTarget[];
}

export interface DecisionView {
  id: string;
  revision?: number;
  storedStatus?: string;
  derivedStatus?: 'active' | 'under_reconsideration' | 'superseded';
  context?: string;
  alternatives?: Alternative[];
  consequences?: string[];
  decider?: string;
  supersedes?: RecordSummary[];
  supersededBy?: RecordSummary[];
  governs?: { relation: string; target: LinkTarget }[];
  unreadable?: string;
}

export interface LinkingRecord {
  record: RecordSummary;
  links: {
    relation: string;
    targetKind: string;
    target: string;
    detail: Record<string, unknown>;
  }[];
  decision?: DecisionView | null;
}

export interface CurrentnessBlock {
  codeTree?: { repositoryRoot: string; treeId: string } | null;
  counts?: Record<InvariantState, number>;
  unverifiableReason?: string | null;
}

export interface PathViewAnswer extends ReaderAnswer {
  kind: 'file' | 'directory';
  path: string;
  prose: FileRead;
  references: { path: string; state: FileRead['state']; detail?: string; items: ReferenceItem[] };
  testFile: boolean;
  invariants: InvariantGroup[];
  families: FamilyAtPath[];
  records: LinkingRecord[];
  currentness: CurrentnessBlock;
  // A directory's view is bounded: its immediate children that hold knowledge, with their entry
  // counts, and the size of its subtree (listed in pages by the `subtree` view).
  children?: { name: string; path: string; entries: number }[];
  subtree?: { entries: number; view: 'subtree' };
}

export interface SubtreeRow {
  id: string;
  kind: 'realization' | 'proof';
  invariant: string;
  path: string;
  locator?: Anchor['locator'] | null;
  role?: string | null;
  facet?: string | null;
}

export interface SubtreeAnswer extends ReaderAnswer {
  directory?: string;
  path?: string;
  rows?: SubtreeRow[];
  states?: Record<string, InvariantState | null>;
  statesProblem?: string | null;
  page?: { start: number; rowsOnPage: number; total: number; returned: number; remaining: number };
  continuation?: string | null;
  code?: string;
}

export interface TreeChild {
  name: string;
  path: string;
  kind: 'file' | 'dir';
  inCode: boolean;
  onboarding: boolean;
  entries: number;
}

export interface TreeAnswer extends ReaderAnswer {
  directory: string;
  code: { state: string; detail?: string };
  children: TreeChild[];
}

export interface IncomingLink {
  source: string;
  sourceKind: string;
  relation: string;
  targetKind: string;
  target: string;
  detail: Record<string, unknown>;
  originPath: string;
  sourceRecord?: RecordSummary;
  // The record a `history_row` source is about: a row has no page of its own.
  sourceSubject?: RecordSummary;
  targetRecord?: RecordSummary;
}

export interface TimelineEvent {
  source: 'record' | 'history' | 'entries';
  commit: string | null;
  date: string | null;
  subject?: string | null;
  change?: string;
  path?: string;
  meaning?: { field: string; before: unknown; after: unknown }[];
  owner?: string;
  ownerKind?: string;
  closed?: boolean;
  row?: string;
  disposition?: string;
  document?: Record<string, unknown>;
  entry?: string;
  entryKind?: string;
  invariant?: string;
  before?: { path?: string; sidecar?: string; anchor?: Anchor } | null;
  after?: { path?: string; sidecar?: string; anchor?: Anchor } | null;
}

export interface Timeline {
  sources: Record<string, { state: string; events?: number; detail?: string }>;
  events: TimelineEvent[];
}

export interface StatesHeader {
  codeTree?: CurrentnessBlock['codeTree'];
  unverifiableReason?: string | null;
}

export interface RecordViewAnswer extends ReaderAnswer {
  record: RecordSummary & { path: string; document: Record<string, unknown> };
  prose: FileRead;
  outgoing: { relation: string; target: LinkTarget; alternative?: number }[];
  outgoingState?: { state: string; detail?: string };
  incoming: IncomingLink[];
  invariant?: {
    state?: InvariantState | null;
    currentness: StatesHeader;
    realizations: EntryView[];
    proofs: EntryView[];
    families: RecordSummary[];
    linked: LinkingRecord[];
  };
  family?: {
    currentness: StatesHeader;
    members: (RecordSummary & { statement?: string | null; state?: InvariantState | null })[];
    routes: string[];
    staleMembers: string[];
    locations: { path: string; entries: EntryView[] }[];
  };
  decision?: DecisionView | null;
  facet?: Record<string, unknown>;
  timeline: Timeline;
}

export interface CensusReport {
  census: string;
  baseline?: Record<string, unknown> | null;
  inventory: { sources: number; artifacts: number; unroutedSources: number; routes: number };
  counts: Record<string, number>;
  measures: {
    name: string;
    state: string;
    numerator?: number;
    denominator?: number;
    value?: number | null;
    reason?: string;
  }[];
  dispositions: Record<string, number>;
  routeStatuses: Record<string, number>;
  routes: {
    route: string;
    status: string;
    governingCensus?: string | null;
    history: { status: string; tree?: string; provenance?: { at?: string } }[];
  }[];
}

export interface CensusAnswer extends ReaderAnswer {
  censuses: CensusReport[];
  known?: string[];
  problems?: { path: string; field: string; message: string }[];
}

export interface WithoutProofAnswer extends ReaderAnswer {
  path: string | null;
  total: number;
  invariants: (RecordSummary & { statement?: string | null; realizationPaths: string[] })[];
}

export interface CodeAnswer extends ReaderAnswer {
  path: string;
  blob?: string;
  language?: string;
  text?: string;
  locator?: { state: 'resolved' | 'unresolved'; lines?: [number, number]; detail?: string };
}

export interface SelectionOptions {
  state: string;
  repo: string;
  default?: string;
  detail?: string;
  published?: { commit: string; memoryRoot: string | null; converted: boolean };
  commits?: { commit: string; date: string; subject: string; converted: boolean }[];
  commitsState?: { state: string; detail?: string };
  leaves?: {
    commit: string;
    leafId?: string | null;
    taskName?: string | null;
    branch?: string | null;
  }[];
}

export interface RecordListAnswer extends ReaderAnswer {
  kinds: Record<string, RecordSummary[]>;
}

// --- addresses ----------------------------------------------------------------------------------

const VIEWS: readonly ReaderView[] = [
  'path',
  'subtree',
  'record',
  'census',
  'without-proof',
  'code',
];
const SUBJECT_KEYS = ['path', 'id', 'census', 'locator', 'blob'] as const;

function viewOf(params: URLSearchParams): ReaderView {
  const named = params.get('view');
  if (named && (VIEWS as readonly string[]).includes(named)) return named as ReaderView;
  return params.get('id') ? 'record' : 'path';
}

/** The address a `#knowledge?…` hash names, or `null` for any other hash. */
export function parseReaderHash(hash: string): ReaderAddress | null {
  if (hash !== KNOWLEDGE_HASH && !hash.startsWith(`${KNOWLEDGE_HASH}?`)) return null;
  const params = new URLSearchParams(hash.slice(KNOWLEDGE_HASH.length + 1));
  const address: ReaderAddress = {
    repo: params.get('repo') ?? '',
    commit: params.get('commit') ?? 'published',
    view: viewOf(params),
  };
  for (const key of SUBJECT_KEYS) {
    const value = params.get(key);
    if (value !== null) address[key] = value;
  }
  return address;
}

/** The shareable hash of an address: commit and path or ID always spelled out. */
export function readerHash(address: ReaderAddress): string {
  const params = new URLSearchParams({ repo: address.repo, commit: address.commit });
  const implied = address.id ? 'record' : 'path';
  if (address.view !== implied) params.set('view', address.view);
  for (const key of SUBJECT_KEYS) {
    const value = address[key];
    if (value !== undefined && value !== '') params.set(key, value);
  }
  return `${KNOWLEDGE_HASH}?${params.toString()}`;
}

// --- reads --------------------------------------------------------------------------------------

export class ReaderTransportError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** One reader GET. A typed answer (any JSON body with `state`) is returned whatever its status;
 * only a transport failure, or a process composed without the reader (503), throws. */
export async function readerGet<T extends { state: string }>(
  view: string,
  params: Record<string, string | undefined>,
  base = '',
): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, value);
  }
  const url = `${base}/api/knowledge/reader/${encodeURIComponent(view)}?${query.toString()}`;
  const response = await fetchWithTimeout(url, READER_TIMEOUT_MS);
  const body = (await response.json().catch(() => null)) as (T & { detail?: string }) | null;
  if (body && typeof body.state === 'string') return body;
  throw new ReaderTransportError(
    response.status,
    body?.detail ?? `the reader answered ${response.status} without a typed body`,
  );
}

/** The request of an address's own view. */
export function readAddress(address: ReaderAddress, base = ''): Promise<ReaderAnswer> {
  const { repo, commit } = address;
  switch (address.view) {
    case 'record':
      return readerGet('record', { repo, commit, id: address.id }, base);
    case 'census':
      return readerGet('census', { repo, commit, census: address.census }, base);
    case 'without-proof':
      return readerGet('without-proof', { repo, commit, path: address.path }, base);
    case 'subtree':
      return readSubtree(address, undefined, base);
    case 'code':
      return readerGet(
        'code',
        { repo, commit, path: address.path, locator: address.locator, blob: address.blob },
        base,
      );
    default:
      return readerGet('path', { repo, commit, path: address.path ?? '' }, base);
  }
}

/** One page of a directory's subtree; `continuation` is the previous page's token. */
export function readSubtree(
  address: ReaderAddress,
  continuation?: string,
  base = '',
): Promise<SubtreeAnswer> {
  const { repo, commit } = address;
  return readerGet('subtree', { repo, commit, path: address.path ?? '.', continuation }, base);
}

/** The address that opens a code anchor at its locator. */
export function codeAddress(
  at: Pick<ReaderAddress, 'repo' | 'commit'>,
  anchor: Anchor,
  path: string,
): ReaderAddress {
  return {
    repo: at.repo,
    commit: at.commit,
    view: 'code',
    path,
    locator: JSON.stringify(anchor.locator),
    blob: anchor.blob,
  };
}

export function locatorLabel(anchor: Anchor | undefined): string {
  const locator = anchor?.locator;
  if (!locator) return '';
  if (locator.kind === 'symbol') return locator.name ?? '';
  if (locator.kind === 'line_range') return `L${locator.start}–${locator.end}`;
  return 'whole file';
}
