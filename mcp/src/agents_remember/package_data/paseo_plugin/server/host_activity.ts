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
 * turn — and neither the subagent fields nor `permissionPrompts` ever move
 * `eventAt` (MIK-R190 rule 2): `runningSubagents` is absent at 0 and
 * `subagentsSince` is the oldest running subagent's `createdAt`, absent at 0.
 * `permissionPrompts` is the ids of the native prompts still unanswered — never
 * the prompt's arguments — absent when none are, and bounded by
 * PERMISSION_PROMPTS_LIMIT. An agent the host reports waiting on a prompt it did
 * not name keeps `state: 'permission'` with no ids: its identity set is
 * incomplete, so the publication itself is incomplete and the consumer retains
 * the last identity it read. The product's model admits exactly these five keys
 * and still forbids every other.
 */
export interface PublishedAgentActivity {
  state: HostActivityState;
  eventAt?: string;
  runningSubagents?: number;
  subagentsSince?: string;
  permissionPrompts?: string[];
}

/**
 * The publication stays bounded: an agent with more outstanding prompts than
 * this publishes the first 32 in sorted order and marks the publication incomplete.
 * The count in `permissions` still decides the state, so a capped list never moves
 * an agent out of 'permission' or proves an omitted prompt answered.
 */
export const PERMISSION_PROMPTS_LIMIT = 32;

/**
 * How many resolved prompt ids one agent's facts remember. A listing read before
 * a resolution can still carry the id, and a publication must never resurrect an
 * occurrence the host already ended, so the ids this boot saw resolved are kept
 * as tombstones. They are never published; the bound only keeps the memory finite.
 */
const RESOLVED_PROMPT_MEMORY = 256;

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
  /** Exactly the ids of the outstanding native prompts the events and listings named. */
  permissions: Set<string>;
  /** Outstanding prompts the host showed without a usable identity: counted, never named. */
  unnamed: number;
  /** The identity set is not exactly known, so no publication carrying it is complete. */
  identityIncomplete: boolean;
  /** Ids this boot saw resolved: an observation must not resurrect them. */
  resolved: Set<string>;
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
  if (facts.permissions.size > 0 || facts.unnamed > 0) return "permission";
  return facts.turnActive ? "in-turn" : "idle";
}

/**
 * A prompt identity is only usable when the host named it exactly. A missing,
 * empty or non-string id stays unnamed: the publisher records that a prompt is
 * outstanding without inventing the id that would make it an occurrence.
 */
function promptId(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const id = value.trim();
  return id.length > 0 ? id : undefined;
}

function emptyFacts(revision: number, eventAt?: string): AgentFacts {
  return {
    turnActive: false,
    permissions: new Set<string>(),
    unnamed: 0,
    identityIncomplete: false,
    resolved: new Set<string>(),
    ended: false,
    ...(eventAt ? { eventAt } : {}),
    revision,
  };
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
  /** Reconciliations in flight: what an event means depends on whether a read is open. */
  private listingDepth = 0;
  /**
   * Ids requested while a listing was in flight, each with the event sequence it
   * arrived at. A page cannot have seen a prompt whose sequence is after the
   * sequence the listing started at, so that id is not evidence of a miss.
   */
  private readonly requestedDuringListing = new Map<string, Map<string, number>>();
  /** Unnamed prompt arrivals and answers during a listing: counted, each with its sequence. */
  private readonly unnamedRequestedDuringListing = new Map<string, number[]>();
  private readonly unnamedResolvedDuringListing = new Map<string, number[]>();
  /** Agents whose whole prompt set a listing-time turn start, archive, close or recreate cleared, and the sequence. */
  private readonly clearedDuringListing = new Map<string, number>();

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
      ? {
        ...previous,
        permissions: new Set(previous.permissions),
        resolved: new Set(previous.resolved),
        eventAt,
        revision: this.revision,
      }
      : emptyFacts(this.revision, eventAt);
    const id = promptId(requestId);
    // The sequence this event occupies, so a listing in flight can say which of its
    // facts the event outranks and which of its own the event cannot have changed.
    const sequence = this.revision;
    if (this.listingDepth > 0) {
      if (event === "agent.permission_requested") {
        if (id === undefined) this.markSequence(this.unnamedRequestedDuringListing, agentId, sequence);
        else this.markRequested(agentId, id, sequence);
      } else if (event === "agent.permission_resolved" && id === undefined) {
        this.markSequence(this.unnamedResolvedDuringListing, agentId, sequence);
      }
    }
    switch (event) {
      case "agent.turn_started":
        facts.turnActive = true;
        facts.turnStartedAt = eventAt;
        facts.permissions.clear();
        facts.unnamed = 0;
        facts.identityIncomplete = false;
        facts.ended = false;
        // A new turn ends the prompts of the old one: a snapshot read before it
        // must not add them back.
        if (this.listingDepth > 0) this.clearedDuringListing.set(agentId, sequence);
        break;
      case "agent.turn_ended":
        facts.turnActive = false;
        break;
      case "agent.permission_requested":
        if (id === undefined) {
          // A prompt exists but the host did not name it. Publishing it as an id
          // would invent an occurrence; dropping it would report an idle agent.
          facts.unnamed = Math.min(Number.MAX_SAFE_INTEGER, facts.unnamed + 1);
          facts.identityIncomplete = true;
        } else {
          facts.permissions.add(id);
          facts.resolved.delete(id);
        }
        break;
      case "agent.permission_resolved":
        if (id === undefined) {
          if (facts.unnamed > 0) facts.unnamed -= 1;
          else if (facts.permissions.size > 0) facts.identityIncomplete = true;
        } else {
          facts.permissions.delete(id);
          this.rememberResolved(facts, id);
        }
        // An agent with nothing outstanding has an exactly known (empty) identity again.
        if (facts.permissions.size === 0 && facts.unnamed === 0) facts.identityIncomplete = false;
        break;
      case "agent.archived":
      case "agent.closed":
        facts.turnActive = false;
        facts.permissions.clear();
        facts.unnamed = 0;
        facts.identityIncomplete = false;
        facts.ended = true;
        if (this.listingDepth > 0) this.clearedDuringListing.set(agentId, sequence);
        break;
      case "agent.created":
        facts.turnActive = false;
        facts.permissions.clear();
        facts.unnamed = 0;
        facts.identityIncomplete = false;
        facts.ended = false;
        if (this.listingDepth > 0) this.clearedDuringListing.set(agentId, sequence);
        break;
    }
    this.agents.set(agentId, facts);
    this.publish(records);
  }

  async reconcile(paseo: HostActivityReader): Promise<void> {
    const began = this.revision;
    this.listingDepth += 1;
    try {
      const listed = new Map<string, AgentFacts>();
      const cursors = new Set<string>();
      let cursor: string | undefined;
      for (;;) {
        const page = await paseo.agents.list({
          filter: { includeArchived: true },
          page: { limit: 200, ...(cursor ? { cursor } : {}) },
        });
        const records = this.records();
        for (const { agent } of page.entries) {
          if (Object.hasOwn(records, agent.id)) listed.set(agent.id, snapshotFacts(agent, began));
          else this.ignoredEvents = Math.min(Number.MAX_SAFE_INTEGER, this.ignoredEvents + 1);
        }
        if (!page.pageInfo.hasMore) break;
        const next = page.pageInfo.nextCursor;
        if (!next || cursors.has(next)) throw new Error("host activity listing did not advance");
        cursors.add(next);
        cursor = next;
      }
      const reconciled = new Map<string, AgentFacts>();
      for (const [id, observation] of listed) {
        const current = this.agents.get(id);
        reconciled.set(id, current ? this.reconcileAgent(id, current, observation, began) : observation);
      }
      for (const [id, current] of this.agents) {
        // An event during the listing is fresher than a listing that never named the
        // agent; with no such event the host's record bounds the retained rows.
        if (!reconciled.has(id) && current.revision > began) reconciled.set(id, current);
      }
      this.agents.clear();
      for (const [id, facts] of reconciled) this.agents.set(id, facts);
      this.complete = true;
      this.publish();
    } finally {
      this.listingDepth -= 1;
      if (this.listingDepth === 0) {
        this.requestedDuringListing.clear();
        this.unnamedRequestedDuringListing.clear();
        this.unnamedResolvedDuringListing.clear();
        this.clearedDuringListing.clear();
      }
    }
  }

  /**
   * Merge one agent's listing observation with the facts its events established.
   * A listing is authoritative for every event that arrived at or before the
   * sequence the listing started at — it is the only observation that can prove a
   * prompt gone. It is not evidence about anything later: an id requested while
   * the read was open is not in its snapshot, so the publication keeps that id and
   * refuses to call the identity set whole until a listing that started after the
   * request settles it. An observation that could not name every prompt proves
   * nothing about the ones it did not name, and a cleared set may not be refilled.
   */
  private reconcileAgent(id: string, current: AgentFacts, listed: AgentFacts, began: number): AgentFacts {
    const permissions = new Set<string>();
    let unnamed = listed.unnamed;
    let identityIncomplete = listed.identityIncomplete;
    // An event that arrived after this listing requested its page owns the whole set.
    const cleared = (this.clearedDuringListing.get(id) ?? -1) > began;
    if (cleared) {
      for (const known of current.permissions) permissions.add(known);
      unnamed = current.unnamed;
      identityIncomplete = current.identityIncomplete;
    } else if (listed.identityIncomplete) {
      for (const known of current.permissions) permissions.add(known);
      for (const named of listed.permissions) permissions.add(named);
      unnamed = Math.max(current.unnamed, listed.unnamed);
      identityIncomplete = true;
    } else {
      for (const named of listed.permissions) permissions.add(named);
    }
    // A resolved id is ended for this boot, whenever the listing was read.
    for (const gone of current.resolved) permissions.delete(gone);
    if (!cleared && !listed.identityIncomplete) {
      // The event outranks this page: keep the identity it named even though the
      // page did not carry it, and mark what the page could not have seen.
      for (const requested of this.requestedSince(id, began)) {
        if (current.resolved.has(requested)) continue;
        permissions.add(requested);
        if (!listed.permissions.has(requested)) identityIncomplete = true;
      }
      const unnamedRaced =
        this.unnamedSince(this.unnamedRequestedDuringListing, id, began) -
        this.unnamedSince(this.unnamedResolvedDuringListing, id, began);
      if (unnamedRaced > 0) {
        unnamed = Math.max(unnamed, unnamedRaced);
        identityIncomplete = true;
      }
    }
    if (current.revision > began) {
      // Events arrived while the read was open: they own the state and the time, and
      // the listing only contributes identities the events did not settle.
      return { ...current, permissions, unnamed, identityIncomplete };
    }
    const merged: AgentFacts = {
      ...listed,
      permissions,
      unnamed,
      identityIncomplete,
      resolved: current.resolved,
      revision: began,
    };
    // A known timestamp survives a dashboard reconnect to this same plugin boot,
    // only while the host still reports exactly the state that event published.
    if (publicationState(merged) === publicationState(current)) {
      if (current.eventAt) merged.eventAt = current.eventAt;
      if (current.turnStartedAt) merged.turnStartedAt = current.turnStartedAt;
    }
    return merged;
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

  /** Ids requested after `began`, each with the sequence it arrived at. */
  private markRequested(agentId: string, id: string, sequence: number): void {
    let known = this.requestedDuringListing.get(agentId);
    if (known === undefined) {
      known = new Map<string, number>();
      this.requestedDuringListing.set(agentId, known);
    }
    known.set(id, sequence);
  }

  private markSequence(marks: Map<string, number[]>, agentId: string, sequence: number): void {
    const known = marks.get(agentId);
    if (known === undefined) marks.set(agentId, [sequence]);
    else known.push(sequence);
  }

  private requestedSince(agentId: string, began: number): string[] {
    const known = this.requestedDuringListing.get(agentId);
    if (known === undefined) return [];
    const ids: string[] = [];
    for (const [id, sequence] of known) if (sequence > began) ids.push(id);
    return ids;
  }

  private unnamedSince(marks: Map<string, number[]>, agentId: string, began: number): number {
    let count = 0;
    for (const sequence of marks.get(agentId) ?? []) if (sequence > began) count += 1;
    return count;
  }

  private rememberResolved(facts: AgentFacts, id: string): void {
    facts.resolved.add(id);
    while (facts.resolved.size > RESOLVED_PROMPT_MEMORY) {
      const oldest = facts.resolved.values().next();
      if (oldest.done) break;
      facts.resolved.delete(oldest.value);
    }
  }

  private identityComplete(): boolean {
    for (const facts of this.agents.values()) {
      if (facts.identityIncomplete || facts.unnamed > 0 || facts.permissions.size > PERMISSION_PROMPTS_LIMIT) return false;
    }
    return true;
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
      // Only the ids of unanswered native prompts, never their arguments. Sorted so a
      // publication does not depend on event order, and capped so it stays bounded;
      // `permissions.size` alone still decides the state. A prompt the host did not
      // name contributes no id: the publication is incomplete instead.
      const prompts = [...facts.permissions].sort().slice(0, PERMISSION_PROMPTS_LIMIT);
      published[id] = {
        state,
        ...(eventAt ? { eventAt } : {}),
        ...(prompts.length > 0 ? { permissionPrompts: prompts } : {}),
        ...(counts !== undefined && counts.count > 0 ? { runningSubagents: counts.count } : {}),
        ...(counts !== undefined && counts.count > 0 && counts.since !== undefined
          ? { subagentsSince: counts.since }
          : {}),
      };
    }
    this.writtenSubagents = this.subagentSignature();
    this.write({
      bootId: this.currentBootId,
      // An identity set this publication could not name whole cannot be called
      // complete: the consumer keeps the last occurrence it read instead of
      // treating the gap as an answer.
      complete: this.complete && this.identityComplete(),
      agents: published,
      ignoredEvents: this.ignoredEvents,
    });
  }
}

function snapshotFacts(agent: ActivityAgentSnapshot, revision: number): AgentFacts {
  const ended = Boolean(agent.archivedAt) || agent.status === "closed";
  // The host gives a turn's own start, and an archive's own time. It gives no idle
  // transition time in its listing. Permission waits retain the active turn's start.
  const eventAt = ended ? agent.archivedAt : agent.activeTurn?.startedAt;
  const facts: AgentFacts = {
    turnActive: Boolean(agent.activeTurn),
    permissions: new Set<string>(),
    unnamed: 0,
    identityIncomplete: false,
    resolved: new Set<string>(),
    ended,
    ...(agent.activeTurn?.startedAt ? { turnStartedAt: agent.activeTurn.startedAt } : {}),
    ...(typeof eventAt === "string" ? { eventAt } : {}),
    revision,
  };
  const pending: unknown = agent.pendingPermissions;
  if (!Array.isArray(pending)) {
    // The listing did not describe this agent's prompts, which proves nothing about them.
    facts.identityIncomplete = true;
    return facts;
  }
  for (const request of pending as { id?: unknown }[]) {
    const id = promptId((request as { id?: unknown } | null | undefined)?.id);
    if (id === undefined) {
      facts.unnamed = Math.min(Number.MAX_SAFE_INTEGER, facts.unnamed + 1);
      facts.identityIncomplete = true;
      continue;
    }
    facts.permissions.add(id);
  }
  return facts;
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
