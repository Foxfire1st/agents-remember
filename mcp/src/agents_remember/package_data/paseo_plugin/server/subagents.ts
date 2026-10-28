/**
 * MIK-R190 rules 1 to 3: the provider subagents the host reports for AR's role
 * agents.
 *
 * The plugin subscribes once per boot to `agent.provider_subagents.update`, the
 * one event the host sends every session for every subagent of every agent
 * (rules 1 and 3's first step), and keeps the subagents whose last `upsert` says
 * `running`, with their `createdAt`. It never polls and holds no timer.
 *
 * The traffic it does not keep is skipped rather than counted: a `timeline` row
 * and an update for a parent outside `role-agents.json` change nothing and cause
 * no write, because `host-activity.json` publishes its `ignoredEvents` with every
 * write and counting them would rewrite the file once per update.
 *
 * The stores are bounded like the plugin's other stores: at most
 * `SUBAGENTS_PER_AGENT_LIMIT` running subagents per agent and
 * `SUBAGENTS_TOTAL_LIMIT` in all. A count beyond a bound is published as the
 * bound, so the publication never grows with the host's traffic.
 *
 * Rule 3's second step lists the running subagents through the bridge, which is
 * the only place that can ask `listProviderSubagents`. `merge` lays that list
 * over what the events said, and an update received since the reconciliation
 * began wins over the list: the two are ordered by the one fact each carries,
 * never by arrival time.
 *
 * L184-R1-F08: the reconciliation is explicit and bounded — one integer
 * generation, no history. Beginning one drops every earlier decision, so a
 * connection gap cannot keep an absent child alive; a reply that names an older
 * generation than the current one is refused rather than applied.
 */
import type { PluginHandlerContext } from "@getpaseo/plugin/server";
import type { TurnEndedFacts } from "./turn_ended.ts";

type Paseo = PluginHandlerContext["paseo"];

/** The one host event this module follows. */
export const SUBAGENT_EVENT = "agent.provider_subagents.update";

/** Rule 1's bounds: what one agent, and the whole publication, may hold. */
export const SUBAGENTS_PER_AGENT_LIMIT = 64;
export const SUBAGENTS_TOTAL_LIMIT = 4096;

/**
 * How many subagent ids an update may decide for one agent before the next list
 * merge. Rule 3's race is decided inside one snapshot call, so this bounds one
 * call's traffic and never a host's history.
 */
const SUBAGENTS_DECIDED_LIMIT = 512;

/** What the publication carries for one agent: absent at 0, oldest start at >0. */
export interface SubagentCounts {
  readonly count: number;
  readonly since?: string;
}

/** One subagent as the bridge's `listProviderSubagents` reports it. */
export interface ListedSubagent {
  readonly id: string;
  readonly createdAt: string;
  readonly status: string;
}

/** The whole list one agent's read answered. */
export interface ListedSubagents {
  readonly agentId: string;
  readonly subagents: readonly ListedSubagent[];
}

type SubagentUpdate =
  | {
      readonly kind: "upsert";
      readonly subagent: {
        readonly id: string;
        readonly parentAgentId: string;
        readonly status: string;
        readonly createdAt: string;
        /**
         * MIK-R216 rule 1: the provider the helper itself runs on. An ACP client
         * emits no such event, so seeing one is how a provider that does report
         * its helpers is told apart from one that does not.
         */
        readonly provider?: string;
      };
    }
  | { readonly kind: "timeline"; readonly parentAgentId: string; readonly subagentId: string }
  | { readonly kind: "remove"; readonly parentAgentId: string; readonly subagentId: string };

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/**
 * The event's payload, read as the protocol shapes it: an `upsert` carries the
 * whole subagent, a `remove` names it, and a `timeline` row carries one message
 * of it. Anything else is no subagent update and is skipped.
 */
export function subagentUpdateOf(message: unknown): SubagentUpdate | null {
  const carrier = asRecord(message);
  const payload = carrier === null ? null : asRecord(carrier["payload"]);
  if (payload === null) return null;
  if (carrier?.["type"] !== undefined && carrier["type"] !== SUBAGENT_EVENT) return null;
  const kind = payload["kind"];
  if (kind === "upsert") {
    const subagent = asRecord(payload["subagent"]);
    if (subagent === null) return null;
    const id = text(subagent["id"]);
    const parentAgentId = text(subagent["parentAgentId"]);
    const status = text(subagent["status"]);
    const createdAt = text(subagent["createdAt"]);
    if (id === null || parentAgentId === null || status === null || createdAt === null) return null;
    const provider = text(subagent["provider"]);
    return {
      kind: "upsert",
      subagent: {
        id,
        parentAgentId,
        status,
        createdAt,
        ...(provider === null ? {} : { provider }),
      },
    };
  }
  const parentAgentId = text(payload["parentAgentId"]);
  const subagentId = text(payload["subagentId"]);
  if (parentAgentId === null || subagentId === null) return null;
  if (kind === "timeline") return { kind: "timeline", parentAgentId, subagentId };
  if (kind === "remove") return { kind: "remove", parentAgentId, subagentId };
  return null;
}

/** The running subagents of AR's role agents, as one bounded store. */
export class SubagentTracker {
  private readonly running = new Map<string, Map<string, string>>();
  /** The subagent ids an update decided since the reconciliation began, per agent. */
  private readonly decided = new Map<string, Set<string>>();
  /** L184-R1-F08: the current reconciliation, one integer and no history. */
  private generation = 0;
  /** Rule 5's "after the subagents finish": who wants to know a count reached zero. */
  private readonly watchers = new Map<string, Set<() => void>>();
  private total = 0;
  private readonly records: () => Readonly<Record<string, TurnEndedFacts>>;
  /**
   * MIK-R216 rule 1: told once per provider whose helper event arrives, whatever
   * the helper's parent is. The plugin's ACP verdict for that provider is then
   * false for the rest of the host's life.
   */
  private readonly onNativeProvider: (provider: string) => void;

  constructor(
    records: () => Readonly<Record<string, TurnEndedFacts>>,
    onNativeProvider: (provider: string) => void = () => {},
  ) {
    this.records = records;
    this.onNativeProvider = onNativeProvider;
  }

  /** The seat's running providers now, as rule 4 and rule 5 read them. */
  count(agentId: string): number {
    return this.running.get(agentId)?.size ?? 0;
  }

  /**
   * Rule 5: the seat's last running subagent may end while the seat is in no
   * turn, and that moment is what starts its wait. The listener runs once for the
   * fall from a count above 0 to 0.
   */
  watch(agentId: string, listener: () => void): void {
    let listeners = this.watchers.get(agentId);
    if (listeners === undefined) {
      listeners = new Set<() => void>();
      this.watchers.set(agentId, listeners);
    }
    listeners.add(listener);
  }

  unwatch(agentId: string, listener: () => void): void {
    const listeners = this.watchers.get(agentId);
    if (listeners === undefined) return;
    listeners.delete(listener);
    if (listeners.size === 0) this.watchers.delete(agentId);
  }

  private emptied(agentId: string): void {
    const listeners = this.watchers.get(agentId);
    if (listeners === undefined) return;
    for (const listener of [...listeners]) {
      try {
        listener();
      } catch {
        // One listener's failure never keeps another seat's wait from starting.
      }
    }
  }

  /** What the publication carries for this agent. */
  counts(agentId: string): SubagentCounts {
    const kept = this.running.get(agentId);
    if (kept === undefined || kept.size === 0) return { count: 0 };
    let since: string | undefined;
    for (const createdAt of kept.values()) {
      if (since === undefined || createdAt < since) since = createdAt;
    }
    return since === undefined ? { count: kept.size } : { count: kept.size, since };
  }

  /**
   * One host update. `true` when the published count or start changed, so the
   * caller writes the publication only for a change (rule 2).
   */
  noteUpdate(message: unknown): boolean {
    const update = subagentUpdateOf(message);
    if (update === null) return false;
    // A timeline row is traffic of a subagent already counted; it never changes a count.
    if (update.kind === "timeline") return false;
    // MIK-R216 rule 1: a provider that reports a helper at all is off the ACP
    // list, whoever the helper's parent is — the parent is read only afterwards,
    // and a parent outside the role record still proves the provider reports.
    if (update.kind === "upsert" && update.subagent.provider !== undefined) {
      try {
        this.onNativeProvider(update.subagent.provider);
      } catch {
        // A verdict store that fails to write is not the host's traffic to fix.
      }
    }
    const records = this.records();
    if (update.kind === "upsert") {
      const parent = update.subagent.parentAgentId;
      if (!Object.hasOwn(records, parent)) return false;
      const running = update.subagent.status === "running" ? update.subagent.createdAt : undefined;
      this.decide(parent, update.subagent.id);
      return this.apply(parent, update.subagent.id, running);
    }
    if (!Object.hasOwn(records, update.parentAgentId)) return false;
    this.decide(update.parentAgentId, update.subagentId);
    return this.apply(update.parentAgentId, update.subagentId, undefined);
  }

  /** The parent's archive or close, and the parent leaving the record, drop its subagents. */
  dropAgent(agentId: string): boolean {
    this.decided.delete(agentId);
    const kept = this.running.get(agentId);
    if (kept === undefined) return false;
    this.running.delete(agentId);
    this.total -= kept.size;
    if (kept.size > 0) this.emptied(agentId);
    return kept.size > 0;
  }

  /**
   * L184-R1-F08: begin one reconciliation. Its list is truth; only the events
   * recorded from now on may win over it, so every decision of an earlier
   * generation stops applying. The cost is one integer and the decisions the
   * current race still holds — bounded, never a history.
   */
  beginReconcile(): number {
    this.generation += 1;
    this.decided.clear();
    return this.generation;
  }

  /**
   * Rule 3's second step: one agent's complete list, laid under the events.
   *
   * The list is the truth for every id it does not report running: an id it
   * omits stops counting, whatever an earlier generation said. Only an update
   * recorded inside this reconciliation wins over the list, including one that
   * removed a child the list still reports.
   *
   * `generation` names the reconciliation this reply answers. A reply begun
   * before a newer reconciliation is refused: it is older than the list truth
   * now in hand and may not overwrite it. Without a generation the call is
   * itself a new reconciliation, which is what a direct list answer means — the
   * plugin's own RPC handler never leaves it undefined, so a reply from the wire
   * is always bound to a reconciliation the snapshot actually began
   * (L184-R1-F08).
   */
  merge(list: ListedSubagents, generation?: number): boolean {
    const at = generation ?? this.beginReconcile();
    if (at !== this.generation) return false;
    const records = this.records();
    if (!Object.hasOwn(records, list.agentId)) return false;
    const before = this.counts(list.agentId);
    const decided = this.decided.get(list.agentId);
    const previous = this.running.get(list.agentId);
    // L184-R1-F12: this agent's own entries are replaced, so only the rest of
    // the fleet is elsewhere — and every entry kept below must take its room.
    const elsewhere = this.total - (previous?.size ?? 0);
    let room = Math.max(0, SUBAGENTS_TOTAL_LIMIT - elsewhere);
    const held = new Map<string, string>();
    if (decided !== undefined && previous !== undefined) {
      // This race's own decisions keep the fate the events gave them.
      for (const [id, createdAt] of previous) {
        if (!decided.has(id)) continue;
        if (held.size >= SUBAGENTS_PER_AGENT_LIMIT || room <= 0) continue;
        held.set(id, createdAt);
        room -= 1;
      }
    }
    for (const entry of list.subagents) {
      if (entry.status !== "running") continue;
      if (decided?.has(entry.id) === true) continue;
      if (held.has(entry.id)) continue;
      if (held.size >= SUBAGENTS_PER_AGENT_LIMIT || room <= 0) continue;
      held.set(entry.id, entry.createdAt);
      room -= 1;
    }
    if (held.size > 0) this.running.set(list.agentId, held);
    else this.running.delete(list.agentId);
    this.total = elsewhere + held.size;
    // This agent's race is over: its list has applied its answer.
    this.decided.delete(list.agentId);
    const after = this.counts(list.agentId);
    if (before.count > 0 && after.count === 0) this.emptied(list.agentId);
    return after.count !== before.count || after.since !== before.since;
  }

  /** Every list of one snapshot answer, in one call and under one generation. */
  mergeAll(lists: readonly ListedSubagents[], generation?: number): boolean {
    const at = generation ?? this.beginReconcile();
    if (at !== this.generation) return false;
    let changed = false;
    for (const list of lists) {
      if (this.merge(list, at)) changed = true;
    }
    return changed;
  }

  /**
   * Agents the role record no longer holds keep no subagents. Read the record
   * once per publication rather than once per agent.
   */
  prune(records: Readonly<Record<string, TurnEndedFacts>>): boolean {
    let changed = false;
    for (const agentId of [...this.running.keys()]) {
      if (Object.hasOwn(records, agentId)) continue;
      if (this.dropAgent(agentId)) changed = true;
    }
    for (const agentId of [...this.decided.keys()]) {
      if (!Object.hasOwn(records, agentId)) this.decided.delete(agentId);
    }
    return changed;
  }

  private decide(agentId: string, subagentId: string): void {
    let ids = this.decided.get(agentId);
    if (ids === undefined) {
      ids = new Set<string>();
      this.decided.set(agentId, ids);
    }
    ids.delete(subagentId);
    ids.add(subagentId);
    while (ids.size > SUBAGENTS_DECIDED_LIMIT) {
      const oldest = ids.values().next();
      if (oldest.done === true) break;
      ids.delete(oldest.value);
    }
  }

  /** One subagent's fate, and whether the published count or start moved. */
  private apply(agentId: string, subagentId: string, createdAt: string | undefined): boolean {
    const before = this.counts(agentId);
    if (createdAt === undefined) {
      const kept = this.running.get(agentId);
      if (kept === undefined || !kept.delete(subagentId)) return false;
      this.total -= 1;
      if (kept.size === 0) this.running.delete(agentId);
    } else {
      let kept = this.running.get(agentId);
      if (kept?.has(subagentId) === true) {
        kept.delete(subagentId);
        this.total -= 1;
      }
      if (kept !== undefined && kept.size >= SUBAGENTS_PER_AGENT_LIMIT) return false;
      if (this.total >= SUBAGENTS_TOTAL_LIMIT) return false;
      if (kept === undefined) {
        kept = new Map<string, string>();
        this.running.set(agentId, kept);
      }
      kept.set(subagentId, createdAt);
      this.total += 1;
    }
    const after = this.counts(agentId);
    if (before.count > 0 && after.count === 0) this.emptied(agentId);
    return after.count !== before.count || after.since !== before.since;
  }
}

/** What the subscription's host object must offer: the plugin's own API, one call. */
export type SubagentEventSource = Pick<Paseo, "observeEvents">;

/**
 * Rule 1's one subscription per plugin boot, and rule 3's lost-connection case:
 * the client re-establishes a subscription after a lost connection and fires its
 * `snapshot` again, so a second snapshot means updates may have been missed.
 *
 * L184-R1-F11: the host hands a `PaseoApi` to the first lifecycle hook as well as
 * to the snapshot RPC, and a boot with no dashboard never calls the RPC, so the
 * subscription is taken from whichever of the two arrives first.
 */
export class SubagentSubscription {
  private owned: ReturnType<SubagentEventSource["observeEvents"]> | undefined;
  /** The readiness of the handle `owned` names; `false` once that handle failed. */
  private readiness: Promise<boolean> | undefined;
  private first = true;
  private readonly tracker: SubagentTracker;
  private readonly onChanged: () => void;
  private readonly onRestarted: () => void;
  private readonly onError: (error: unknown) => void;

  constructor(
    tracker: SubagentTracker,
    onChanged: () => void,
    onRestarted: () => void,
    onError: (error: unknown) => void = () => {},
  ) {
    this.tracker = tracker;
    this.onChanged = onChanged;
    this.onRestarted = onRestarted;
    this.onError = onError;
  }

  /**
   * Subscribe once, from the first host API the plugin is handed, and answer
   * whether this boot's subscription is ready.
   *
   * Rule 3's first step calls this before it answers the snapshot RPC, so no
   * update falls between the list and the subscription. L184-R1-F11: a handle
   * whose readiness fails is released and not retained — the client releases its
   * own failed handles, so keeping the entry would leave every later attach
   * without a working subscription. A second call while one is attached returns
   * the same readiness instead of subscribing again.
   */
  attach(paseo: SubagentEventSource): Promise<boolean> {
    if (this.owned !== undefined && this.readiness !== undefined) return this.readiness;
    const owned = paseo.observeEvents([SUBAGENT_EVENT], { notifications: false });
    this.owned = owned;
    owned.subscribe({
      snapshot: () => {
        if (this.first) {
          this.first = false;
          return;
        }
        this.onRestarted();
      },
      update: (message) => {
        if (this.tracker.noteUpdate(message)) this.onChanged();
      },
      error: (error) => this.onError(error),
    });
    const readiness = owned.ready.then(
      () => true,
      (error: unknown) => {
        this.onError(error);
        // A handle that never became ready holds nothing: let it go, so the next
        // hook or RPC may still take the one subscription.
        if (this.owned === owned) {
          this.owned = undefined;
          this.readiness = undefined;
          void owned.release().catch((failed: unknown) => this.onError(failed));
        }
        return false;
      },
    );
    this.readiness = readiness;
    return readiness;
  }

  /** Rule 1: the plugin's cleanup releases the subscription. */
  release(): void {
    const owned = this.owned;
    this.owned = undefined;
    this.readiness = undefined;
    if (owned === undefined) return;
    void owned.release().catch((error: unknown) => this.onError(error));
  }
}
