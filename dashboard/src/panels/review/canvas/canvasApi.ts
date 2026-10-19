/**
 * The review page's host, from the panel's side (MIK-R104 rules 5-7, MIK-R105 rules 2, 8).
 *
 * Every call is explicit and typed: the panel reads the saved-first state, opens one session for the
 * frame it shows, asks for one authoring start (never on mount), and disposes the channel it opened.
 * A refusal is surfaced with its reason, never swallowed: "why is there no page?" must be answerable.
 */

export interface CanvasFrameState {
  state: 'ok' | 'refused';
  reason?: string;
  detail?: string;
  frame?: { repository: string; master: string; leaf: string; frame: string };
  opening: {
    state: 'ready' | 'unavailable';
    revision: number | null;
    detail: string;
    entrypoint?: string | null;
    comparison?: string | null;
    /** The ready manifest, as the host's own `reopen_frame` answer carries it. */
    manifest?: {
      entrypoint?: { path?: string } | null;
      binding?: { comparison_reference?: string } | null;
    } | null;
  };
  staleness: { state: 'current' | 'stale' | 'unavailable'; changed?: string[]; detail?: string };
  presenter: {
    state: string;
    revision: number | null;
    detail: string;
    entrypoint?: string | null;
    comparison?: string | null;
    attempts: CanvasAttempt[];
    live: CanvasAttempt | null;
  };
  launch: {
    state: 'ok' | 'refused';
    detail: string;
    harness?: string | null;
    model?: string | null;
  };
  design: { source: string; available: boolean; words: number; sha256: string | null };
  policy: {
    scripts: boolean;
    policyDigest: string;
    proof: {
      state: string;
      browser?: string | null;
      browserVersion?: string | null;
      detail: string;
    };
    limits: Record<string, { value: number; unit: string; enforced_by: string; where: string }>;
    containerRequired: string[];
  };
}

/**
 * The comparison the reviewer is reading, in the identities this panel actually holds (L104-B11).
 *
 * `reference` is the review payload's own `comparison.reference`, carried verbatim; `number` is the
 * lineage number the payload's own limitations name, and `history` says which record the reviewer is
 * reading. The panel sends all three with every read, channel and start so a request can name the
 * exact comparison on screen instead of whichever comparison the frame's latest revision happens to
 * bind. The host route currently takes only repository/master/leaf/frame and ignores these fields
 * (reported to the server owner); the panel also uses them to bind its own display, so a page built
 * for comparison 7 is never presented as the page for comparison 8.
 */
export interface CanvasComparison {
  reference: string;
  number?: number;
  history?: string;
}

/** One review target: the frame that holds the page, and the comparison on screen. */
export interface CanvasTarget {
  repository: string;
  master: string;
  leaf: string;
  frame: string;
  comparison?: CanvasComparison;
}

export interface CanvasActivity {
  at: string;
  kind: string;
  text: string;
}

export interface CanvasConversation {
  at: string;
  role: string;
  comparison: string;
  revision: number | null;
  text: string;
}

export interface CanvasAttempt {
  attempt_id: string;
  state: 'started' | 'running' | 'published' | 'failed' | 'cancelled';
  comparison: { comparison_reference: string; comparison_number: number };
  focus: string;
  created_at: string;
  working_directory: string;
  published_revision: number | null;
  design_source: string;
  design_sha256: string;
  activity: CanvasActivity[];
  conversation: CanvasConversation[];
  detail: string | null;
  session_id?: string | null;
  host_status?: { state: string; detail?: string };
}

export interface CanvasSession {
  state: 'ok';
  session: string;
  revision: number;
  comparison: string;
  entrypoint: string;
  scripts: boolean;
  sandbox: string;
  pageUrl: string;
}

export interface EvidenceDestination {
  state: 'opened' | 'unavailable' | 'refused';
  operation: string;
  reference?: string;
  kind?: string | null;
  comparison?: string;
  target?: Record<string, string>;
  location?: Record<string, unknown>;
  detail?: string;
}

export class CanvasRefusal extends Error {
  readonly reason: string;

  constructor(reason: string, detail: string) {
    super(detail);
    this.reason = reason;
  }
}

type Envelope<T> = T & { state?: string; reason?: string; detail?: string };

async function answerBody(response: Response): Promise<Envelope<unknown> | undefined> {
  if (!response || typeof response.json !== 'function') return undefined;
  try {
    return (await response.json()) as Envelope<unknown>;
  } catch {
    return undefined;
  }
}

function refusalOf(response: Response, body: Envelope<unknown> | undefined): CanvasRefusal | null {
  if (!response.ok || body?.state === 'refused') {
    return new CanvasRefusal(
      body?.reason ?? `http-${response.status}`,
      body?.detail ?? response.statusText,
    );
  }
  return null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  });
  // A host that answered nothing is a read that failed, not a panel that may crash: the refusal is
  // stated where the panel can show it.
  const body = await answerBody(response);
  if (!body) throw new CanvasRefusal('no-answer', `the host answered nothing for ${url}`);
  const refusal = refusalOf(response, body);
  if (refusal) throw refusal;
  return body as T;
}

const API = '/api/canvas';

export function frameUrl(repository: string, master: string, leaf: string, frame: string) {
  return `${API}/frames/${encodeURIComponent(repository)}/${encodeURIComponent(master)}/${encodeURIComponent(leaf)}/${encodeURIComponent(frame)}`;
}

/**
 * The comparison a request names, as query parameters (`read`) or as body fields (`start`).
 *
 * A read has no body, so the same three facts travel in its query string; the host ignores unknown
 * query parameters and body fields alike today, which is why the panel binds its own display as well
 * (see `CanvasComparison`). Absent facts are omitted rather than sent empty.
 */
export function comparisonQuery(comparison?: CanvasComparison): string {
  const params = new URLSearchParams();
  if (!comparison) return '';
  params.set('comparison', comparison.reference);
  if (comparison.number !== undefined) params.set('comparisonNumber', String(comparison.number));
  if (comparison.history) params.set('history', comparison.history);
  return `?${params.toString()}`;
}

export function comparisonBody(comparison?: CanvasComparison): Record<string, string | number> {
  if (!comparison) return {};
  return {
    comparison: comparison.reference,
    ...(comparison.number === undefined ? {} : { comparisonNumber: comparison.number }),
    ...(comparison.history ? { history: comparison.history } : {}),
  };
}

export function readFrameState(target: CanvasTarget): Promise<CanvasFrameState> {
  const url = frameUrl(target.repository, target.master, target.leaf, target.frame);
  return request<unknown>(`${url}${comparisonQuery(target.comparison)}`).then(asFrameState);
}

/**
 * The saved-first answer, or a refusal naming it as unreadable.
 *
 * A body that is not an object, or that declares no state at all, is not an answer about a page: the
 * panel states that it could not read the page instead of drawing one from fields that are not
 * there. A body that declares a state but lacks the frame's own fields is still handed on -- the
 * panel's phase is `unavailable` for it, and every field it reads is guarded, so a wrong answer is
 * shown as unreadable rather than crashing the review.
 */
function asFrameState(body: unknown): CanvasFrameState {
  const value = body as { state?: unknown } | null;
  if (!value || typeof value !== 'object' || typeof value.state !== 'string') {
    throw new CanvasRefusal(
      'unreadable-answer',
      'the host answered something that is not the saved page',
    );
  }
  return value as CanvasFrameState;
}

export function openSession(
  target: CanvasTarget & { revision: number; entrypoint?: string | null },
): Promise<CanvasSession> {
  return request<CanvasSession>(`${API}/sessions`, {
    method: 'POST',
    body: JSON.stringify({
      repository: target.repository,
      master: target.master,
      leaf: target.leaf,
      frame: target.frame,
      revision: target.revision,
      ...(target.entrypoint ? { entrypoint: target.entrypoint } : {}),
      ...comparisonBody(target.comparison),
    }),
  });
}

export function revokeSession(session: string, reason: string): Promise<{ revoked: boolean }> {
  return request(`${API}/sessions/${encodeURIComponent(session)}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

export function generate(
  input: CanvasTarget & {
    requestId: string;
    focus: string;
    regenerate: boolean;
    /** The retained revision a regeneration starts from; absent for a first generation. */
    baseRevision?: number | null;
  },
): Promise<{ attempt: CanvasAttempt }> {
  return request(`${frameUrl(input.repository, input.master, input.leaf, input.frame)}/generate`, {
    method: 'POST',
    body: JSON.stringify({
      requestId: input.requestId,
      focus: input.focus,
      activation: 'explicit',
      regenerate: input.regenerate,
      ...(input.baseRevision === undefined || input.baseRevision === null
        ? {}
        : { baseRevision: input.baseRevision }),
      ...comparisonBody(input.comparison),
    }),
  });
}

export function attemptOperation(
  input: CanvasTarget,
  attempt: string,
  operation: 'cancel' | 'resume' | 'say' | 'publish' | 'replace',
  body: Record<string, unknown>,
): Promise<{ attempt: CanvasAttempt }> {
  return request(
    `${API}/attempts/${encodeURIComponent(input.repository)}/${encodeURIComponent(input.master)}/${encodeURIComponent(input.leaf)}/${encodeURIComponent(input.frame)}/${encodeURIComponent(attempt)}/${operation}`,
    { method: 'POST', body: JSON.stringify(body) },
  );
}

export function sendFrameMessage(session: string, body: string): Promise<EvidenceDestination> {
  return request(`${API}/sessions/${encodeURIComponent(session)}/messages`, {
    method: 'POST',
    body,
  });
}
