import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import type { TurnEndedFacts } from "./turn_ended.ts";

/**
 * The per-agent facts the role start publishes (MIK-R117 rule 11): one
 * record per started role agent at `<PASEO_HOME>/agents-remember/role-agents.json`,
 * the way provisioning publishes the embed list. One stat-keyed cache is replaced
 * when registration changes the file, so a new start needs no reload. A missing file
 * reads as no records; a malformed file fails visibly like the embed list.
 */
export function roleAgentsPath(home: string): string {
  return join(home, "agents-remember", "role-agents.json");
}

function isFacts(value: unknown): value is TurnEndedFacts {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record["role"] === "string" &&
    typeof record["task"] === "string" &&
    typeof record["reportPath"] === "string"
  );
}

let cached: { path: string; generation: string; facts: Record<string, TurnEndedFacts> } | undefined;

export function readRoleAgents(home: string | undefined): Record<string, TurnEndedFacts> {
  if (home === undefined || home === "") return {};
  const path = roleAgentsPath(home);
  let text: string;
  let generation: string;
  try {
    const stat = statSync(path, { bigint: true });
    generation = [stat.dev, stat.ino, stat.size, stat.mtimeNs, stat.ctimeNs].join(":");
    if (cached?.path === path && cached.generation === generation) return cached.facts;
    text = readFileSync(path, "utf8");
  } catch (error) {
    cached = undefined;
    if ((error as { code?: string }).code === "ENOENT") return {};
    throw error;
  }
  const document: unknown = JSON.parse(text);
  if (typeof document !== "object" || document === null) {
    throw new Error(`malformed role-agents file at ${roleAgentsPath(home)}`);
  }
  const agents = (document as { agents?: unknown }).agents;
  if (typeof agents !== "object" || agents === null || Array.isArray(agents)) {
    throw new Error(`malformed role-agents file at ${roleAgentsPath(home)}`);
  }
  const facts: Record<string, TurnEndedFacts> = {};
  for (const [agentId, record] of Object.entries(agents)) {
    if (!isFacts(record)) {
      throw new Error(`malformed role-agents file at ${roleAgentsPath(home)}`);
    }
    facts[agentId] = { role: record.role, task: record.task, reportPath: record.reportPath };
  }
  cached = { path, generation, facts };
  return facts;
}
