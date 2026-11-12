/**
 * MIK-R190 rules 11 and 12: the one file that says a seat is limited, and until
 * when.
 *
 * `<PASEO_HOME>/agents-remember/provider-limits.json` is the product's, written
 * whole and atomically under a lock; the plugin only reads it. Every fact in it
 * comes from the host's own usage report for that seat (rule 6), never from chat
 * text, so nothing here matches a message or an error string: a seat is limited
 * exactly while `now` is before the `until` of one of its report ids.
 *
 * The read is stat-keyed like `role_agents.ts`: the plugin asks at the send-back's
 * end of the wait, at the parent gate and when a resume fires, and never on a
 * timer of its own (rule 15). A missing or malformed file reads as "nothing is
 * limited", which is rule 14: where Paseo has no report, nothing changes.
 */
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";

export function providerLimitsPath(home: string): string {
  return join(home, "agents-remember", "provider-limits.json");
}

/** One report's fact, as the product stores it. Only `until` is read here. */
export interface ProviderLimitFact {
  readonly until: string;
  readonly sourceLabel: string;
  readonly accountLabel: string;
  readonly windows: readonly { readonly id: string; readonly label: string; readonly resetsAt: string }[];
  readonly observedAt: string;
}

export interface ProviderLimitDocument {
  readonly reports: Record<string, ProviderLimitFact>;
  readonly agents: Record<string, readonly string[]>;
}

const EMPTY: ProviderLimitDocument = { reports: {}, agents: {} };

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

function readDocument(text: string): ProviderLimitDocument {
  const root = asRecord(JSON.parse(text));
  const reports = asRecord(root?.["reports"]);
  const agents = asRecord(root?.["agents"]);
  if (root === null || reports === null || agents === null) {
    throw new Error("malformed provider-limits file");
  }
  const keptReports: Record<string, ProviderLimitFact> = {};
  for (const [id, value] of Object.entries(reports)) {
    const fact = asRecord(value);
    const until = fact?.["until"];
    if (fact === null || typeof until !== "string") continue;
    const windows = Array.isArray(fact["windows"]) ? fact["windows"] : [];
    keptReports[id] = {
      until,
      sourceLabel: typeof fact["sourceLabel"] === "string" ? fact["sourceLabel"] : "",
      accountLabel: typeof fact["accountLabel"] === "string" ? fact["accountLabel"] : "",
      windows: windows.flatMap(entry => {
        const window = asRecord(entry);
        if (window === null) return [];
        const windowId = window["id"];
        const resetsAt = window["resetsAt"];
        if (typeof windowId !== "string" || typeof resetsAt !== "string") return [];
        const label = window["label"];
        return [{ id: windowId, label: typeof label === "string" ? label : windowId, resetsAt }];
      }),
      observedAt: typeof fact["observedAt"] === "string" ? fact["observedAt"] : until,
    };
  }
  const keptAgents: Record<string, readonly string[]> = {};
  for (const [agentId, value] of Object.entries(agents)) {
    if (!Array.isArray(value)) continue;
    keptAgents[agentId] = value.filter((entry): entry is string => typeof entry === "string");
  }
  return { reports: keptReports, agents: keptAgents };
}

let cached: { path: string; generation: string; document: ProviderLimitDocument } | undefined;

/** The file as the product last wrote it; a missing file is no fact at all. */
export function readProviderLimits(home: string | undefined): ProviderLimitDocument {
  if (home === undefined || home === "") return EMPTY;
  const path = providerLimitsPath(home);
  let generation: string;
  try {
    const stat = statSync(path, { bigint: true });
    generation = [stat.dev, stat.ino, stat.size, stat.mtimeNs, stat.ctimeNs].join(":");
    if (cached?.path === path && cached.generation === generation) return cached.document;
  } catch {
    cached = undefined;
    return EMPTY;
  }
  try {
    const document = readDocument(readFileSync(path, "utf8"));
    cached = { path, generation, document };
    return document;
  } catch {
    // A file the product is midway through replacing is no fact either: nothing is limited.
    cached = undefined;
    return EMPTY;
  }
}

/**
 * Rule 11/12: the seat's own `until` while it is limited now, else `null`. A
 * seat mapped to one exhausted report is limited exactly as the account stops
 * every seat on it.
 */
export function limitedUntil(
  document: ProviderLimitDocument,
  agentId: string,
  now: Date,
): string | null {
  const ids = document.agents[agentId];
  if (ids === undefined || ids.length === 0) return null;
  let latest: { iso: string; at: number } | null = null;
  for (const id of ids) {
    const fact = document.reports[id];
    if (fact === undefined) continue;
    const at = Date.parse(fact.until);
    if (!Number.isFinite(at) || at <= now.getTime()) continue;
    if (latest === null || at > latest.at) latest = { iso: fact.until, at };
  }
  return latest === null ? null : latest.iso;
}
