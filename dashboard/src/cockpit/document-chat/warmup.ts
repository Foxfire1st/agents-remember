import { useEffect, useState } from "react";

/** What the host-start warmup read says: the one line and who is still pending. */
export interface WarmupAgentState {
  state: string;
  reason: string | null;
}

export interface WarmupStatus {
  line: string | null;
  pending: string[];
  running: boolean;
  inFlight: string[];
  /** Every candidate agent's state and reason, so the pane names failures and skips. */
  agents: Record<string, WarmupAgentState>;
  stoppedForMemory: boolean;
}

/** How often a running loading is asked again; an idle host is asked rarely. */
const RUNNING_REFRESH_MS = 10_000;
const IDLE_REFRESH_MS = 30_000;

function warmupAgentState(value: unknown): WarmupAgentState | null {
  if (typeof value !== "object" || value === null) return null;
  const row = value as { state?: unknown; reason?: unknown };
  if (typeof row.state !== "string") return null;
  return { state: row.state, reason: typeof row.reason === "string" ? row.reason : null };
}

function warmupAgents(value: unknown): Record<string, WarmupAgentState> {
  const agents: Record<string, WarmupAgentState> = {};
  if (typeof value === "object" && value !== null) {
    for (const [id, state] of Object.entries(value)) {
      const parsed = warmupAgentState(state);
      if (parsed) agents[id] = parsed;
    }
  }
  return agents;
}

function warmupStatus(value: unknown): WarmupStatus | null {
  if (typeof value !== "object" || value === null) return null;
  const row = value as {
    line?: unknown;
    pending?: unknown;
    running?: unknown;
    inFlight?: unknown;
    agents?: unknown;
    stoppedForMemory?: unknown;
  };
  if (row.line !== null && typeof row.line !== "string") return null;
  const pending = Array.isArray(row.pending) ? row.pending.filter((id): id is string => typeof id === "string") : [];
  const inFlight = Array.isArray(row.inFlight) ? row.inFlight.filter((id): id is string => typeof id === "string") : [];
  const agents = warmupAgents(row.agents);
  return { line: row.line, pending, running: row.running === true, inFlight, agents, stoppedForMemory: row.stoppedForMemory === true };
}

/** One read of the host-start warmup status; null when the backend does not answer. */
export async function readWarmupStatus(signal?: AbortSignal): Promise<WarmupStatus | null> {
  let response: Response;
  try {
    response = await fetch("/api/role-launch/warmup", { signal });
  } catch {
    return null;
  }
  if (!response.ok) return null;
  try {
    return warmupStatus(await response.json());
  } catch {
    return null;
  }
}

/**
 * The host-start loading while it runs (MIK-R145 rule 6): one line with the count,
 * and the agents not yet loaded so the pane can say so. Asked rarely while idle and
 * nothing is asked while the chat is hidden; a backend that does not answer (an older
 * build) leaves the line absent, as before.
 */
export function useWarmupStatus(active: boolean): WarmupStatus | null {
  const [status, setStatus] = useState<WarmupStatus | null>(null);
  useEffect(() => {
    if (!active) return undefined;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      const next = await readWarmupStatus(controller.signal).catch(() => null);
      if (controller.signal.aborted) return;
      setStatus(next);
      timer = setTimeout(() => void read(), next?.running ? RUNNING_REFRESH_MS : IDLE_REFRESH_MS);
    };
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [active]);
  return active ? status : null;
}
