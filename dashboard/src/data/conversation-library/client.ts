// HTTP client for the landed native-library routes (library/api.py). List/read are "or-null" reads;
// open/open-status/open-reconcile return the typed OpenConversationOperation as evidence. The open
// requestId is caller-stable and reused across status/reconcile — a lost response is reconciled
// under the SAME id, never retried under a fresh one (§9.4, invariant 27).

import type { FetchLike } from "../conversation/client";
import type { HarnessId } from "../conversation/types";
import type {
  ConversationLibraryPage,
  HistoricalConversationPage,
  LibraryConversationKey,
  LibraryListCursor,
  LibraryReadCursor,
  LibraryRouteError,
  OpenConversationOperation,
} from "./types";

function libraryBase(base: string, harnessId: HarnessId): string {
  return `${base}/api/harnesses/${encodeURIComponent(harnessId)}/conversations`;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

export interface ListQuery {
  cwd?: string;
  cursor?: LibraryListCursor | null;
  limit?: number;
}

export async function fetchLibraryList(
  harnessId: HarnessId,
  query: ListQuery = {},
  base = "",
  fetchImpl: FetchLike = fetch,
): Promise<ConversationLibraryPage | null> {
  const params: string[] = [];
  if (query.cwd) params.push(`cwd=${encodeURIComponent(query.cwd)}`);
  if (query.cursor) params.push(`cursor=${encodeURIComponent(query.cursor)}`);
  if (query.limit !== undefined) params.push(`limit=${query.limit}`);
  const url = `${libraryBase(base, harnessId)}${params.length > 0 ? `?${params.join("&")}` : ""}`;
  try {
    const response = await fetchImpl(url, { headers: { Accept: "application/json" } });
    if (!response.ok) return null;
    return (await response.json()) as ConversationLibraryPage;
  } catch {
    return null;
  }
}

export async function fetchLibraryRead(
  harnessId: HarnessId,
  conversationKey: LibraryConversationKey,
  query: { before?: LibraryReadCursor | null; limit?: number } = {},
  base = "",
  fetchImpl: FetchLike = fetch,
): Promise<HistoricalConversationPage | null> {
  const params: string[] = [];
  if (query.before) params.push(`before=${encodeURIComponent(query.before)}`);
  if (query.limit !== undefined) params.push(`limit=${query.limit}`);
  const url = `${libraryBase(base, harnessId)}/${encodeURIComponent(conversationKey)}${
    params.length > 0 ? `?${params.join("&")}` : ""
  }`;
  try {
    const response = await fetchImpl(url, { headers: { Accept: "application/json" } });
    if (!response.ok) return null;
    return (await response.json()) as HistoricalConversationPage;
  } catch {
    return null;
  }
}

export type OpenResult =
  | { ok: true; operation: OpenConversationOperation }
  | { ok: false; error: LibraryRouteError };

function parseOpen(body: unknown, httpStatus: number): OpenResult {
  if (body !== null && typeof body === "object" && "outcome" in body && "phase" in body) {
    return { ok: true, operation: body as OpenConversationOperation };
  }
  const record = (body ?? {}) as { status?: unknown; detail?: unknown; capabilityState?: unknown };
  return {
    ok: false,
    error: {
      status: typeof record.status === "string" ? record.status : "transport",
      detail: typeof record.detail === "string" ? record.detail : `HTTP ${httpStatus}`,
      httpStatus,
      capabilityState:
        typeof record.capabilityState === "string" ? record.capabilityState : undefined,
    },
  };
}

export interface OpenRequestBody {
  requestId: string;
  expectedIdentityDigest: string;
  cwd?: string;
  launchContext?: { leafKey?: string; seatRole?: string };
}

async function postOpen(
  route: "open" | "open-status" | "open-reconcile",
  harnessId: HarnessId,
  conversationKey: LibraryConversationKey,
  body: OpenRequestBody | { requestId: string },
  base: string,
  fetchImpl: FetchLike,
): Promise<OpenResult> {
  const url = `${libraryBase(base, harnessId)}/${encodeURIComponent(conversationKey)}/${route}`;
  try {
    const response = await fetchImpl(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return parseOpen(await readJson(response), response.status);
  } catch {
    return { ok: false, error: { status: "transport", detail: "network", httpStatus: 0 } };
  }
}

export function openConversation(
  harnessId: HarnessId,
  conversationKey: LibraryConversationKey,
  body: OpenRequestBody,
  base = "",
  fetchImpl: FetchLike = fetch,
): Promise<OpenResult> {
  return postOpen("open", harnessId, conversationKey, body, base, fetchImpl);
}

export function openStatus(
  harnessId: HarnessId,
  conversationKey: LibraryConversationKey,
  requestId: string,
  base = "",
  fetchImpl: FetchLike = fetch,
): Promise<OpenResult> {
  return postOpen("open-status", harnessId, conversationKey, { requestId }, base, fetchImpl);
}

export function openReconcile(
  harnessId: HarnessId,
  conversationKey: LibraryConversationKey,
  requestId: string,
  base = "",
  fetchImpl: FetchLike = fetch,
): Promise<OpenResult> {
  return postOpen("open-reconcile", harnessId, conversationKey, { requestId }, base, fetchImpl);
}
