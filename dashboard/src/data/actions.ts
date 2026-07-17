// Slice 6c Part B: the dashboard's one write path. POST a gate decision to the serving
// layer (`POST /api/actions/{verb}`), which records it as a developer/dashboard-attributed
// GateDecision (6b) — server-enforced at closeout. The read-only store stays read-only;
// this is a fire-and-report action, not persistent state. Status maps the honest outcome:
// 202 recorded, 409 no open / stale gate, anything else / network failure → error.

export type GateDecisionStatus = "idle" | "posting" | "recorded" | "no-open-gate" | "stale-gate" | "error";

export interface GateDecisionOptions {
  gateId?: string;
  note?: string;
}

export async function postGateDecision(
  lifecycleId: string | null | undefined,
  verb: string,
  options: GateDecisionOptions = {},
): Promise<GateDecisionStatus> {
  try {
    const body: Record<string, string> = {};
    if (lifecycleId) body.target = lifecycleId;
    if (options.gateId) body.gateId = options.gateId;
    if (options.note) body.note = options.note;
    const res = await fetch(`/api/actions/${verb}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 202) return "recorded";
    if (res.status === 409) {
      const payload = await res.json().catch(() => null) as { status?: string } | null;
      return payload?.status === "stale-gate" ? "stale-gate" : "no-open-gate";
    }
    return "error";
  } catch {
    return "error";
  }
}

// 260715-FEUI-L6 (R4): the same gate-decision POST, keeping the server's words. The
// InteractionBar must render POST failures VERBATIM (design §7.3 F7), so this variant captures
// the response body instead of collapsing everything past 202/409 into "error".
export interface GateDecisionDetailedResult {
  status: GateDecisionStatus;
  /** The server's own words (response body / HTTP status line / network error message). */
  detail?: string;
}

export async function postGateDecisionDetailed(
  lifecycleId: string | null | undefined,
  verb: string,
  options: GateDecisionOptions = {},
): Promise<GateDecisionDetailedResult> {
  try {
    const body: Record<string, string> = {};
    if (lifecycleId) body.target = lifecycleId;
    if (options.gateId) body.gateId = options.gateId;
    if (options.note) body.note = options.note;
    const res = await fetch(`/api/actions/${verb}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 202) return { status: "recorded" };
    const raw = await res.text().catch(() => "");
    if (res.status === 409) {
      let payload: { status?: string } | null = null;
      try {
        payload = JSON.parse(raw) as { status?: string };
      } catch {
        payload = null;
      }
      return {
        status: payload?.status === "stale-gate" ? "stale-gate" : "no-open-gate",
        detail: raw || `HTTP ${res.status}`,
      };
    }
    return { status: "error", detail: raw || `HTTP ${res.status}` };
  } catch (error) {
    return { status: "error", detail: error instanceof Error ? error.message : String(error) };
  }
}

// Leaf-28 S5.2: dismiss ONE lifecycle-bound attention-queue item. POSTs to the same
// `/api/actions/{verb}` return channel with verb `dismiss`; the server records a compact
// lifecycle acknowledgement, or cancels/deletes the gate for a `gate-open` item. Fire-and-report
// like postGateDecision: the SSE delta removes the item on the next tick. 202 dismissed,
// anything else / network failure → error.

export type AttentionDismissStatus = "dismissed" | "error";

export interface AttentionDismissTarget {
  itemId: string;
  kind: string;
  lifecycleId?: string | null;
  gateId?: string;
}

export async function postAttentionDismiss(
  item: AttentionDismissTarget,
): Promise<AttentionDismissStatus> {
  try {
    const body: Record<string, string> = { itemId: item.itemId, kind: item.kind };
    if (item.lifecycleId) body.target = item.lifecycleId;
    if (item.gateId) body.gateId = item.gateId;
    const res = await fetch(`/api/actions/dismiss`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.status === 202 ? "dismissed" : "error";
  } catch {
    return "error";
  }
}
