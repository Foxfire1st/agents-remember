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
