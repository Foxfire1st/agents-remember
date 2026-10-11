import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { TurnEndedFacts } from "./turn_ended.ts";

/**
 * The per-agent facts the role start publishes (MIK-R117 rule 11): one
 * record per started role agent at `<PASEO_HOME>/agents-remember/role-agents.json`,
 * the way provisioning publishes the embed list. The plugin reads it fresh on
 * every turn end, so a restart or a new start needs no reload. A missing file
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

export function readRoleAgents(home: string | undefined): Record<string, TurnEndedFacts> {
  if (home === undefined || home === "") return {};
  let text: string;
  try {
    text = readFileSync(roleAgentsPath(home), "utf8");
  } catch (error) {
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
  return facts;
}
