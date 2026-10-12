// MIK-R94 rule 2: the developer's direct-execution grant control. POST a give or
// take-back to the serving layer (`POST /api/direct-execution-grant`), which writes
// the same grant record the architect's function writes. The payload mirrors that
// function's shape; the reader keeps the server's words for refusal display.
import type { DirectGrantNode } from "../types/projection";

export type DirectGrantAction = "give" | "revoke";

export interface DirectGrantOutcome {
  ok: boolean;
  state: "granted" | "revoked" | "unchanged" | "refused";
  /** The server's own words: `summary` on success, `detail` on refusal. */
  text: string;
  grant: DirectGrantNode | null;
}

export async function postDirectExecutionGrant(
  repository: string,
  path: string,
  action: DirectGrantAction,
): Promise<DirectGrantOutcome> {
  let payload: {
    ok?: boolean;
    state?: DirectGrantOutcome["state"];
    summary?: string;
    detail?: string;
    grant?: DirectGrantNode | null;
  } | null;
  try {
    const res = await fetch("/api/direct-execution-grant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repository, path, action }),
    });
    payload = (await res.json().catch(() => null)) as typeof payload;
  } catch {
    return {
      ok: false,
      state: "refused",
      text: "The grant control could not reach the dashboard connection.",
      grant: null,
    };
  }
  if (!payload || typeof payload.ok !== "boolean") {
    return {
      ok: false,
      state: "refused",
      text: "The grant control answered without a grant payload.",
      grant: null,
    };
  }
  return {
    ok: payload.ok,
    state: payload.state ?? "refused",
    text: payload.ok
      ? (payload.summary ?? "The grant is recorded.")
      : (payload.detail ?? "The grant change was refused."),
    grant: payload.grant ?? null,
  };
}
