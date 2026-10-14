// The dashboard's write path for the attention queue (leaf-28 S5.2): dismiss ONE lifecycle-bound
// attention-queue item. POSTs to the serving layer's return channel (`POST /api/actions/{verb}`)
// with verb `dismiss`; the server records a compact lifecycle acknowledgement. The read-only
// store stays read-only: this is a fire-and-report action, not persistent state — the SSE delta
// removes the item on the next tick. 202 dismissed, anything else / network failure → error.

export type AttentionDismissStatus = "dismissed" | "error";

export interface AttentionDismissTarget {
  itemId: string;
  kind: string;
  lifecycleId?: string | null;
}

export async function postAttentionDismiss(
  item: AttentionDismissTarget,
): Promise<AttentionDismissStatus> {
  try {
    const body: Record<string, string> = { itemId: item.itemId, kind: item.kind };
    if (item.lifecycleId) body.target = item.lifecycleId;
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
