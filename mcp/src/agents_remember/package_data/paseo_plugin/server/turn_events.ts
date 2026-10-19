/**
 * The parent-wake handlers contributed to the host.
 *
 * MIK-R172@v2 rule 6: the send still asks for `steer`, but a line is only sent to
 * a parent that is mid-turn when the product knows that parent's provider keeps
 * its running turn under a steer (`TURN_ENDED_STEERING_PROVIDERS`, empty today);
 * otherwise the line is held and this handler sends it when that turn ends.
 *
 * MIK-R183: a leaf seat's completed turn with no hand-off, which rule 3 leaves
 * silent, is sent a message of its own after a 60-second wait, checked against
 * the host's state at the send; at most twice in a row, and every silent end
 * from the third on gives its parent the marked line. The plugin's rows for it —
 * seat messages, marked lines, cancelled waits, skipped sends and left-alone
 * ends — go to its own file `<PASEO_HOME>/agents-remember/turn-ends.jsonl`
 * (rule 7), not to console output, which reaches no file, and not to
 * `host-activity.json`, whose readers forbid extra keys. A write that fails is
 * named in `turn-ends.error.json`. A cleanup clears every pending wait, so a
 * reload starts afresh (rule 8).
 */
import type { PluginLifecycleRegistration } from "@getpaseo/plugin/server";
import { readRoleAgents } from "./role_agents.ts";
import { createTurnEndLog } from "./turn_log.ts";
import {
  TURN_ENDED_STEERING_PROVIDERS,
  cleanupSeatWaits,
  createTurnEndedTracker,
  noteAgentArchived,
  notePermissionRequested,
  notePermissionResolved,
  noteTurnEnded,
  noteTurnStarted,
  type HostAgentState,
} from "./turn_ended.ts";

/**
 * Rule 3: `@getpaseo/protocol`'s `PARENT_AGENT_ID_LABEL`. Spelled here rather than
 * imported, so the plugin keeps no runtime dependency on a package the host already
 * owns; the value is asserted in the tests.
 */
const PARENT_AGENT_ID_LABEL = "paseo.parent-agent-id";

/**
 * Rule 3/4's load bound: one read walks at most this many pages of this many
 * agents. An incomplete read supplies no host facts, so the decision leaves the
 * seat alone instead of waking it from a partial view of the fleet.
 */
const HOST_STATE_PAGE_LIMIT = 8;
const HOST_STATE_PAGE_SIZE = 200;
const HOST_STATE_AGENT_LIMIT = HOST_STATE_PAGE_LIMIT * HOST_STATE_PAGE_SIZE;

export interface ParentWakeOptions {
  /** The daemon home; the environment's `PASEO_HOME` when absent. */
  readonly home?: string;
}

/** The host's agents, as rule 3 and rule 4's send read them. */
async function readHostState(
  paseo: { agents: { list: (args: unknown) => Promise<unknown> } },
): Promise<readonly HostAgentState[] | null> {
  try {
    const states: HostAgentState[] = [];
    const seen = new Set<string>();
    let cursor: string | undefined;
    // Rule 3/4: a walk that stops on a cap is not a completed read. It returns
    // null, so the decision treats the host's facts as unavailable and sends
    // nothing, rather than taking partial data for the whole fleet.
    let complete = false;
    for (let pages = 0; pages < HOST_STATE_PAGE_LIMIT; pages += 1) {
      if (states.length >= HOST_STATE_AGENT_LIMIT) break;
      const page = (await paseo.agents.list({
        filter: { includeArchived: true },
        page: cursor === undefined ? { limit: HOST_STATE_PAGE_SIZE } : { limit: HOST_STATE_PAGE_SIZE, cursor },
      })) as {
        entries?: ReadonlyArray<{ agent?: Record<string, unknown> }>;
        pageInfo?: { hasMore?: boolean; nextCursor?: string | null };
      };
      for (const entry of page.entries ?? []) {
        const agent = entry.agent;
        if (agent === undefined || typeof agent["id"] !== "string") continue;
        // Rule 3: the host shows a parent link only as the `paseo.parent-agent-id`
        // label, so a child is found by it — also one whose turn began before this
        // plugin loaded.
        const labels = agent["labels"];
        const parentAgentId =
          labels !== null && typeof labels === "object"
            ? (() => {
                const value = (labels as Record<string, unknown>)[PARENT_AGENT_ID_LABEL];
                return typeof value === "string" && value !== "" ? value : null;
              })()
            : null;
        states.push({
          id: agent["id"],
          parentAgentId,
          inTurn: Boolean(agent["activeTurn"]),
          permissionWait: Array.isArray(agent["pendingPermissions"]) && agent["pendingPermissions"].length > 0,
          archived: Boolean(agent["archivedAt"]) || agent["status"] === "closed",
        });
      }
      const next = page.pageInfo?.nextCursor ?? undefined;
      if (page.pageInfo?.hasMore !== true) {
        complete = true;
        break;
      }
      // F06: no cursor or a repeated one stops progress, not pagination. Neither
      // proves that the unread fleet holds no active child or permission wait.
      if (next === undefined || seen.has(next)) break;
      seen.add(next);
      cursor = next;
    }
    return complete ? states : null;
  } catch {
    // Unavailable host facts leave the seat alone; the parent's line keeps its rule.
    return null;
  }
}

export function registerParentWakeEvents(
  server: Pick<PluginLifecycleRegistration, "on">,
  options: ParentWakeOptions = {},
): () => void {
  const log = createTurnEndLog(options.home ?? process.env.PASEO_HOME);
  const turns = createTurnEndedTracker(TURN_ENDED_STEERING_PROVIDERS, (row) => log.append(row));

  server.on("agent.turn_started", (event) => {
    noteTurnStarted(
      { id: event.agent.id, parentAgentId: event.agent.parentAgentId, provider: event.agent.provider },
      turns,
    );
  });

  server.on("agent.turn_ended", (event, context) => {
    void (async () => {
      try {
        const records = readRoleAgents(process.env.PASEO_HOME);
        const outcome = await noteTurnEnded(
          { id: event.agent.id, parentAgentId: event.agent.parentAgentId, provider: event.agent.provider },
          event.timeline,
          records,
          turns,
          { sendMessage: (id, text) => context.paseo.agents.ref(id).send(text, { activeTurnBehavior: "steer" }) },
          {
            // The ended turn's id scopes the rows the decision reads
            // (L172-R1-F01) and its outcome decides whether the end can be
            // silent (rule 3).
            turnId: event.turnId,
            outcome: event.outcome?.kind,
            // Rule 3/4: the host's own state at this end and at the send.
            readHost: () => readHostState(context.paseo as unknown as { agents: { list: (args: unknown) => Promise<unknown> } }),
          },
        );
        // MIK-R172 rule 5: an agent the agent list does not hold is named in
        // the host log once, with the reason, instead of being skipped silently.
        if (outcome.kind === "none" && outcome.log !== undefined) console.warn(outcome.log);
      } catch (error) {
        console.error("agents-remember turn_ended handler failed", error);
      }
    })();
  });

  server.on("agent.permission_requested", (event) => {
    notePermissionRequested(event.agent.id, turns);
  });

  server.on("agent.permission_resolved", (event, context) => {
    void (async () => {
      try {
        await notePermissionResolved(
          event.agent.id,
          turns,
          { sendMessage: (id, text) => context.paseo.agents.ref(id).send(text, { activeTurnBehavior: "steer" }) },
          // A line held for a departed parent is re-resolved at this send.
          readRoleAgents(process.env.PASEO_HOME),
        );
      } catch (error) {
        console.error("agents-remember permission_resolved handler failed", error);
      }
    })();
  });

  server.on("agent.archived", (event) => {
    noteAgentArchived(event.agent.id, turns);
  });

  // Rule 8: the plugin's cleanup clears every pending wait, so a reload starts
  // afresh and no wait fires after it.
  return () => cleanupSeatWaits(turns);
}
