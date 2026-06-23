export type OperatorInboxPostStatus = "posted" | "error";

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
