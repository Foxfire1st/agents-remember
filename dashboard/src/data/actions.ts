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
