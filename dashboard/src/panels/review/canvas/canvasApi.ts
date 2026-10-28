/** The current revision frame's shared session and evidence protocol. */

/** The comparison identity used to admit an evidence destination. */
export interface CanvasComparison {
  reference: string;
  number?: number;
  history?: string;
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

export function revokeSession(session: string, reason: string): Promise<{ revoked: boolean }> {
  return request(`${API}/sessions/${encodeURIComponent(session)}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

export function sendFrameMessage(session: string, body: string): Promise<EvidenceDestination> {
  return request(`${API}/sessions/${encodeURIComponent(session)}/messages`, {
    method: 'POST',
    body,
  });
}
