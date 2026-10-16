// Dismiss one current attention item by its stable id and kind. A failed durable
// write leaves the item visible and reports that the click did not hold.

export type AttentionDismissStatus = 'dismissed' | 'error';

export interface AttentionDismissTarget {
  itemId: string;
  kind: string;
}

export async function postAttentionDismiss(
  item: AttentionDismissTarget,
): Promise<AttentionDismissStatus> {
  try {
    const body: Record<string, string> = { itemId: item.itemId, kind: item.kind };
    const res = await fetch(`/api/actions/dismiss`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return res.status === 202 ? 'dismissed' : 'error';
  } catch {
    return 'error';
  }
}
