import { randomUUID } from "node:crypto";
import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { PluginHandlerContext, PluginLifecycleRegistration } from "@getpaseo/plugin/server";
import { readRoleAgents } from "./role_agents.ts";
import type { SubagentTracker } from "./subagents.ts";
import type { TurnEndedFacts } from "./turn_ended.ts";

export type HostActivityState = "in-turn" | "permission" | "idle" | "ended";

/**
 * One published agent. `state` and `eventAt` keep their meaning — the agent's
 * turn — and the two subagent fields never move `eventAt` (MIK-R190 rule 2):
 * `runningSubagents` is absent at 0 and `subagentsSince` is the oldest running
 * subagent's `createdAt`, absent at 0. The product's model admits exactly these
 * four keys and still forbids every other.
 */
export interface PublishedAgentActivity {
  state: HostActivityState;
  eventAt?: string;
  runningSubagents?: number;
  subagentsSince?: string;
}

export interface HostActivityPublication {
  bootId: string;
  complete: boolean;
  agents: Record<string, PublishedAgentActivity>;
  ignoredEvents: number;
}

type NativeAgentSnapshot = Awaited<
  ReturnType<PluginHandlerContext["paseo"]["agents"]["list"]>
>["entries"][number]["agent"];
export type ActivityAgentSnapshot = Pick<
  NativeAgentSnapshot, "id" | "status" | "archivedAt" | "activeTurn" | "pendingPermissions"
>;

export interface HostActivityReader {
  agents: {
    list(options: {
      filter: { includeArchived: boolean };
      page: { limit: number; cursor?: string };
    }): Promise<{
      entries: { agent: ActivityAgentSnapshot }[];
      pageInfo: { hasMore: boolean; nextCursor: string | null };
    }>;
  };
}

interface AgentFacts {
  turnActive: boolean;
  permissions: Set<string>;
  ended: boolean;
  eventAt?: string;
  turnStartedAt?: string;
  revision: number;
}

export type HostActivityEvent =
  | "agent.turn_started"
  | "agent.turn_ended"
  | "agent.permission_requested"
  | "agent.permission_resolved"
  | "agent.archived"
  | "agent.created"
  | "agent.closed";

export function hostActivityPath(home: string): string {
  return join(home, "agents-remember", "host-activity.json");
}

function publicationState(facts: AgentFacts): HostActivityState {
  if (facts.ended) return "ended";
  if (facts.permissions.size > 0) return "permission";
  return facts.turnActive ? "in-turn" : "idle";
}

/** One replaceable publication, bounded by the product's existing role-agent record.
 * A boot is unusable until its whole host read succeeds. Events received during that
 * read win over the snapshot; an idle snapshot has no invented event timestamp. */
export class HostActivityPublisher {
  private currentBootId: string;
  private complete = false;
  private revision = 0;
  private ignoredEvents = 0;
  private readonly agents = new Map<string, AgentFacts>();
  /** The subagent fields as last written, so a repeated count writes nothing (rule 2). */
  private writtenSubagents = "";

  private readonly records: () => Readonly<Record<string, TurnEndedFacts>>;
  private readonly write: (publication: HostActivityPublication) => void;
  private readonly now: () => string;
  private readonly subagents: SubagentTracker | undefined;

  constructor(
    records: () => Readonly<Record<string, TurnEndedFacts>>,
    write: (publication: HostActivityPublication) => void,
    now: () => string = () => new Date().toISOString(),
    bootId: string = randomUUID(),
    subagents?: SubagentTracker,
  ) {
    this.records = records;
    this.write = write;
    this.now = now;
    this.currentBootId = bootId;
    this.subagents = subagents;
    this.publish();
  }

  get bootId(): string {
    return this.currentBootId;
  }

  note(event: HostActivityEvent, agentId: string, eventAt = this.now(), requestId?: string): void {
    this.revision++;
    const records = this.records();
    if (!Object.hasOwn(records, agentId)) {
      this.ignoredEvents = Math.min(Number.MAX_SAFE_INTEGER, this.ignoredEvents + 1);
      this.publish(records);
      return;
    }
    const previous = this.agents.get(agentId);
    const facts: AgentFacts = previous
      ? { ...previous, permissions: new Set(previous.permissions), eventAt, revision: this.revision }
      : { turnActive: false, permissions: new Set<string>(), ended: false, eventAt, revision: this.revision };
    switch (event) {
      case "agent.turn_started":
        facts.turnActive = true;
        facts.turnStartedAt = eventAt;
        facts.permissions.clear();
        facts.ended = false;
        break;
      case "agent.turn_ended":
        facts.turnActive = false;
        break;
      case "agent.permission_requested":
        if (requestId) facts.permissions.add(requestId);
        break;
      case "agent.permission_resolved":
        if (requestId) facts.permissions.delete(requestId);
        break;
      case "agent.archived":
      case "agent.closed":
        facts.turnActive = false;
        facts.permissions.clear();
        facts.ended = true;
        break;
      case "agent.created":
        facts.turnActive = false;
        facts.permissions.clear();
        facts.ended = false;
        break;
    }
    this.agents.set(agentId, facts);
    this.publish(records);
  }

  async reconcile(paseo: HostActivityReader): Promise<void> {
    const began = this.revision;
    const snapshot = new Map<string, AgentFacts>();
    const cursors = new Set<string>();
    let cursor: string | undefined;
    for (;;) {
      const page = await paseo.agents.list({
        filter: { includeArchived: true },
        page: { limit: 200, ...(cursor ? { cursor } : {}) },
      });
      const records = this.records();
      for (const { agent } of page.entries) {
        if (Object.hasOwn(records, agent.id)) snapshot.set(agent.id, snapshotFacts(agent, began));
        else this.ignoredEvents = Math.min(Number.MAX_SAFE_INTEGER, this.ignoredEvents + 1);
      }
      if (!page.pageInfo.hasMore) break;
      const next = page.pageInfo.nextCursor;
      if (!next || cursors.has(next)) throw new Error("host activity listing did not advance");
      cursors.add(next);
      cursor = next;
    }
    for (const [id, facts] of this.agents) {
      if (facts.revision > began) snapshot.set(id, facts);
      // A known timestamp survives a dashboard reconnect to this same plugin boot,
      // only while the host still reports exactly the state that event published.
      else {
        const listed = snapshot.get(id);
        if (listed && publicationState(listed) === publicationState(facts)) {
          listed.eventAt = facts.eventAt;
          if (facts.turnStartedAt) listed.turnStartedAt = facts.turnStartedAt;
        }
      }
    }
    this.agents.clear();
    for (const [id, facts] of snapshot) this.agents.set(id, facts);
    this.complete = true;
    this.publish();
  }

  unload(): void {
    this.complete = false;
    this.publish();
  }

  /**
   * Rule 3: a subscription the client re-established after a lost connection may
   * have missed updates, so the publication becomes incomplete under a new boot
   * id and the product reconciles as at a boot. A subagent count survives it: it
   * is a fact of the host, not of the boot.
   */
  invalidate(): void {
    this.currentBootId = randomUUID();
    this.complete = false;
    this.publish();
  }

  /**
   * Rule 1/2: write the publication for a subagent change only. An unchanged
   * count writes nothing, and no subagent field ever moves `eventAt`.
   */
  publishSubagents(): void {
    if (this.subagentSignature() === this.writtenSubagents) return;
    this.publish();
  }

  private subagentSignature(): string {
    if (this.subagents === undefined) return "";
    const records = this.records();
    const parts: string[] = [];
    for (const agentId of Object.keys(records).sort()) {
      const counts = this.subagents.counts(agentId);
      if (counts.count === 0) continue;
      parts.push(`${agentId}:${counts.count}:${counts.since ?? ""}`);
    }
    return parts.join("|");
  }

  private publish(records = this.records()): void {
    this.subagents?.prune(records);
    const published: Record<string, PublishedAgentActivity> = {};
    for (const [id, facts] of this.agents) {
      if (!Object.hasOwn(records, id)) {
        this.agents.delete(id);
        continue;
      }
      const state = publicationState(facts);
      const eventAt = state === "in-turn" || state === "permission" ? facts.turnStartedAt : facts.eventAt;
      const counts = this.subagents?.counts(id);
      published[id] = {
        state,
        ...(eventAt ? { eventAt } : {}),
        ...(counts !== undefined && counts.count > 0 ? { runningSubagents: counts.count } : {}),
        ...(counts !== undefined && counts.count > 0 && counts.since !== undefined
          ? { subagentsSince: counts.since }
          : {}),
      };
    }
    this.writtenSubagents = this.subagentSignature();
    this.write({ bootId: this.currentBootId, complete: this.complete, agents: published, ignoredEvents: this.ignoredEvents });
  }
}

function snapshotFacts(agent: ActivityAgentSnapshot, revision: number): AgentFacts {
  const ended = Boolean(agent.archivedAt) || agent.status === "closed";
  // The host gives a turn's own start, and an archive's own time. It gives no idle
  // transition time in its listing. Permission waits retain the active turn's start.
  const eventAt = ended ? agent.archivedAt : agent.activeTurn?.startedAt;
  return {
    turnActive: Boolean(agent.activeTurn),
    ...(agent.activeTurn?.startedAt ? { turnStartedAt: agent.activeTurn.startedAt } : {}),
    permissions: new Set(agent.pendingPermissions.map(request => request.id)),
    ended,
    ...(typeof eventAt === "string" ? { eventAt } : {}),
    revision,
  };
}

export function createHostActivityPublisher(
  home: string,
  subagents?: SubagentTracker,
): HostActivityPublisher {
  const target = hostActivityPath(home);
  return new HostActivityPublisher(
    () => readRoleAgents(home),
    publication => {
      mkdirSync(dirname(target), { recursive: true });
      const staging = `${target}.tmp`;
      writeFileSync(staging, JSON.stringify(publication) + "\n", "utf8");
      renameSync(staging, target);
    },
    undefined,
    undefined,
    subagents,
  );
}

/** Kept separate from parent-wake handlers: each catches its own write error. */
export function registerHostActivityEvents(
  server: Pick<PluginLifecycleRegistration, "on">,
  publisher: HostActivityPublisher,
  subagents?: SubagentTracker,
): void {
  const events: HostActivityEvent[] = [
    "agent.turn_started", "agent.turn_ended", "agent.permission_requested",
    "agent.permission_resolved", "agent.archived", "agent.created", "agent.closed",
  ];
  for (const kind of events) {
    server.on(kind, event => {
      try {
        const requestId = "requestId" in event ? event.requestId : "request" in event ? event.request.id : undefined;
        const eventAt = "archivedAt" in event ? event.archivedAt : undefined;
        // Rule 1: the parent's archive or close drops its subagents.
        if (kind === "agent.archived" || kind === "agent.closed") subagents?.dropAgent(event.agent.id);
        publisher.note(kind, event.agent.id, eventAt, requestId);
      } catch (error) {
        console.error("agents-remember host activity publication failed", error);
      }
    });
  }
}
