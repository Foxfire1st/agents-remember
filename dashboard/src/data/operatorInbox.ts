export type OperatorInboxPostStatus = "posted" | "error";
export type OperatorInboxDismissStatus = "dismissed" | "not-found" | "error";

export interface OperatorInboxPostRequest {
  lifecycleId?: string;
  agentId?: string;
  gateId?: string;
  ask: string;
  response: string;
}

export async function postOperatorInbox(
  request: OperatorInboxPostRequest,
  base = "",
): Promise<OperatorInboxPostStatus> {
  try {
    const response = await fetch(`${base}/api/operator-inbox`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    return response.ok ? "posted" : "error";
  } catch {
    return "error";
  }
}

export async function dismissOperatorInboxEntry(
  entryId: string,
  base = "",
): Promise<OperatorInboxDismissStatus> {
  try {
    const response = await fetch(`${base}/api/operator-inbox/${entryId}/dismiss`, {
      method: "POST",
    });
    if (response.status === 200) return "dismissed";
    if (response.status === 404) return "not-found";
    return "error";
  } catch {
    return "error";
  }
}
