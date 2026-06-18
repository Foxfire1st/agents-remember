// Slice 6c Part B: the dashboard's one write path. POST a gate decision to the serving
// layer (`POST /api/actions/{verb}`), which records it as a developer/dashboard-attributed
// GateDecision (6b) — server-enforced at closeout. The read-only store stays read-only;
// this is a fire-and-report action, not persistent state. Status maps the honest outcome:
// 202 recorded, 409 no open gate, anything else / network failure → error.

export type GateDecisionStatus = "idle" | "posting" | "recorded" | "no-open-gate" | "error";

export async function postGateDecision(
  lifecycleId: string,
  verb: string,
): Promise<GateDecisionStatus> {
  try {
    const res = await fetch(`/api/actions/${verb}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: lifecycleId }),
    });
    if (res.status === 202) return "recorded";
    if (res.status === 409) return "no-open-gate";
    return "error";
  } catch {
    return "error";
  }
}
