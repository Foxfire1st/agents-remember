import type { PaseoFrameUnavailableReason } from "../paseoFrameModel";
import { useEffect, useState } from "react";
import { documentAgents, type DocumentAgent } from "./model";
import type { RoleLaunchSelection } from "../roleLaunchModel";

type AgentAnswer = { scope: string; agents: DocumentAgent[] | null; detail?: string; reason?: PaseoFrameUnavailableReason };

function unreadableSource(scope: string, value: { detail?: string; unavailable?: boolean }): AgentAnswer {
  return { scope, agents: null, reason: value.unavailable ? "unreachable" : "backend",
    detail: value.detail ?? "The document's agents could not be read. Retry when the host is available." };
}

export function useDocumentAgents(selection: RoleLaunchSelection, active: boolean, revision: number, requestId?: string) {
  const scope = JSON.stringify({ ...selection, ...(requestId ? { requestId } : {}) });
  const [answer, setAnswer] = useState<AgentAnswer | null>(null);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const read = async (offset = 0) => {
      try {
        const response = await fetch("/api/role-launch/document-chats", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...JSON.parse(scope), offset }), signal: controller.signal });
        const value = await response.json() as { agents?: unknown; detail?: string; unavailable?: boolean; nextOffset?: number };
        if (controller.signal.aborted) return;
        if (!response.ok || value.unavailable) setAnswer(unreadableSource(scope, value));
        else if (typeof value.nextOffset === "number") { timer = setTimeout(() => void read(value.nextOffset), 0); return; }
        else setAnswer({ scope, agents: documentAgents(value.agents) });
      } catch { if (!controller.signal.aborted) setAnswer({ scope, agents: null, reason: "backend", detail: "The document's agents could not be read. Check the dashboard connection." }); }
      if (!controller.signal.aborted) timer = setTimeout(() => void read(), 5000);
    };
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [scope, active, revision]);
  return answer?.scope === scope ? answer : null;
}
