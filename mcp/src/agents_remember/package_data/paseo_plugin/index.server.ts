import type { PluginHandlerContext, PluginServerContext } from "@getpaseo/plugin/server";
import { createAcpProviderSet, type AcpProviderSet } from "./server/acp_providers";
import { readEmbedList } from "./server/embed";
import { createHostActivityPublisher, registerHostActivityEvents } from "./server/host_activity";
import { readRoleAgents } from "./server/role_agents";
import { SubagentSubscription, SubagentTracker } from "./server/subagents";
import { isoWithOffset } from "./server/turn_ended";
import { registerParentWakeEvents } from "./server/turn_events";
import { createTurnEndLog } from "./server/turn_log";
import { arEmbedList, arHostActivitySnapshot, arHostActivitySubagents } from "./shared/rpc";

/**
 * L184-R1-F11: every agent lifecycle hook the host runs hands the plugin the same
 * `PaseoApi`, so the first of them is a usable moment to take the one subscription.
 * A boot whose dashboard never asks for a snapshot is served by a create, a
 * permission, an archive or a close just as well as by a turn.
 */
const ATTACH_HOOKS = [
  "agent.created",
  "agent.turn_started",
  "agent.turn_ended",
  "agent.permission_requested",
  "agent.permission_resolved",
  "agent.archived",
  "agent.closed",
] as const;

// Installed and loaded by `agents-remember paseo provision` (PNT-R01). The daemon-side behaviour
// arrives with PNT-R04. Reading the embed list at load proves the plugin can reach what provision
// published, and a malformed file fails the load visibly. The one handler hands that list to the
// client part, which decides from it which parent pages may frame and steer the app (PNT-R05).
//
// MIK-R117 rule 11: the turn-end handlers below wake a role agent's parent with one silent line
// when the turn ends with no return. MIK-R183: a leaf seat's completed turn with no hand-off is
// sent back to its work instead, after a wait and at most twice in a row, and every silent end from
// the third on reaches the parent with its count; the plugin's own file of those rows is
// `turn-ends.jsonl`, and a write that fails is named in `turn-ends.error.json`, never in
// `host-activity.json`, whose readers forbid extra keys. A reload starts afresh: the cleanup below
// clears every pending wait. The decision lives in `server/turn_ended.ts` (pure, tested without a
// host); the role start publishes the per-agent facts it reads in `server/role_agents.ts`. A handler
// never throws out of the host loop.
//
// MIK-R190 rule 1/3: the same publication carries each role agent's running provider subagents,
// followed through one subscription to `agent.provider_subagents.update`. The subscription is
// taken once, at the first usable host API — an agent lifecycle hook or the snapshot RPC — and the
// RPC waits for it before it reconciles, so no update falls between the list and it; the bridge
// then lists the running subagents of the agents the host holds open and passes them, under the
// snapshot's own reconciliation number, to `ar.host-activity-subagents`, which lays that list
// under what the events said.
export default function contribute(server: PluginServerContext) {
  const embed = readEmbedList();
  console.log(`agents-remember plugin loaded; embed entries: ${embed.length}`);
  server.handle(arEmbedList, () => ({ embed }));

  // L184-R1-F11: the one row file this boot writes — MIK-R183's turn-end rows and
  // MIK-R216's provider verdict row share it.
  const home = process.env.PASEO_HOME;
  const log = createTurnEndLog(home);
  /** MIK-R216 rule 1: the ACP verdict read at this plugin's start. */
  let acp: AcpProviderSet | undefined;
  let activity: ReturnType<typeof createHostActivityPublisher> | undefined;
  let subagents: SubagentTracker | undefined;
  let subscription: SubagentSubscription | undefined;
  /** Rule 8: once the cleanup has run, no hook may take a subscription again. */
  let stopped = false;
  /**
   * L184-R1-F08: the one reconciliation still unanswered. The snapshot RPC begins
   * it and answers its number; a list reply that names no number is bound to this
   * one, and a reply arriving after it was answered has no reconciliation to join
   * rather than beginning a fresh one of its own.
   */
  let pendingReconcile: number | undefined;
  /**
   * L184-R1-F11: the one subscription, taken from whichever host API arrives
   * first — a lifecycle hook or the snapshot RPC. Idempotent, so a boot that
   * reaches both ends with exactly one subscription; a readiness that failed
   * releases its handle, and the next hook or RPC tries again.
   */
  const attach = (paseo: PluginHandlerContext["paseo"] | undefined): Promise<boolean> => {
    if (stopped || subscription === undefined || paseo === undefined) return Promise.resolve(false);
    return subscription.attach(paseo);
  };
  try {
    if (!home) throw new Error("PASEO_HOME is not set");
    // MIK-R216 rule 1: the verdict is read from the installed Paseo at this
    // plugin's start and published for the launch; a provider that reports a
    // helper natively is dropped out of it for the rest of the host's life.
    acp = createAcpProviderSet(home, {
      onUnread: (providerId, reason) => {
        log.append({
          at: isoWithOffset(new Date()),
          agent: providerId,
          role: "",
          task: "",
          kind: "acp-provider-unread",
          count: 0,
          reason,
        });
      },
    });
    subagents = new SubagentTracker(
      () => readRoleAgents(home),
      (provider) => acp?.noteNativeProvider(provider),
    );
    activity = createHostActivityPublisher(home, subagents);
    subscription = new SubagentSubscription(
      subagents,
      () => activity?.publishSubagents(),
      () => {
        // Rule 3: a re-established subscription may have missed updates, so the
        // publication is incomplete again and the next list is a new
        // reconciliation whose truth beats every decision of the gap.
        subagents?.beginReconcile();
        activity?.invalidate();
      },
      error => console.error("agents-remember subagent subscription failed", error),
    );
    registerHostActivityEvents(server, activity, subagents);
  } catch (error) {
    console.error("agents-remember host activity initialization failed", error);
  }

  // L184-R1-F11: a boot whose dashboard never connects still gets its hooks, so
  // the subscription is taken at the first usable one instead of waiting for an
  // RPC that may never come. Every agent lifecycle hook hands the same `PaseoApi`,
  // so a create, a permission or an archive is as good a first hook as a turn; the
  // attach is idempotent, so a boot that reaches several of them holds one.
  if (subscription !== undefined) {
    for (const kind of ATTACH_HOOKS) {
      server.on(kind, (_event, context) => {
        void attach(context?.paseo);
      });
    }
  }

  server.handle(arHostActivitySnapshot, async (_input, context) => {
    if (!activity || !subscription || !subagents) {
      throw new Error("host activity publication is unavailable");
    }
    // Rule 3's first step: subscribe before answering, so the list the bridge
    // reads next is ordered against a subscription that already exists.
    const ready = await subscription.attach(context.paseo);
    // L184-R1-F08: the list the bridge is about to read answers this
    // reconciliation, so it is begun here and the gap's decisions lose to it.
    // Its number goes back in this answer, which is where the reply's own
    // provenance comes from.
    const reconcile = subagents.beginReconcile();
    pendingReconcile = reconcile;
    await activity.reconcile(context.paseo);
    // L184-R1-F11: a subscription that never became ready observed nothing, so
    // this boot is published incomplete rather than as a complete observation.
    if (!ready) activity.invalidate();
    return { bootId: activity.bootId, reconcile };
  });

  server.handle(arHostActivitySubagents, input => {
    if (!subagents || !activity) throw new Error("host activity publication is unavailable");
    // L184-R1-F08: the reply applies under the reconciliation it names, or under
    // the one reconciliation still unanswered when it names none — the one this
    // boot's snapshot began. A reply that names an older number is refused by the
    // tracker, and a reply that arrives after the last reconciliation was answered
    // has none to begin: it is dropped, never applied as a fresh generation of its
    // own. That is what keeps an older list from reviving a child a newer one
    // already consumed.
    const generation = input.reconcile ?? pendingReconcile;
    // The reconciliation it answers is answered: whether it named it or was bound
    // to the one still open, no later reply may join that same reconciliation.
    if (generation !== undefined && generation === pendingReconcile) pendingReconcile = undefined;
    if (generation === undefined) return { changed: false };
    const changed = subagents.mergeAll(input.lists, generation);
    if (changed) activity.publishSubagents();
    return { changed };
  });

  // MIK-R183 rule 8: the returned cleanup clears every pending wait, so no wait
  // fires after a reload and every seat's count begins again at zero.
  // MIK-R216: the same file carries this boot's provider verdicts and its checks,
  // and the ACP verdict is read from the set the start published.
  const stopTurnEnds = registerParentWakeEvents(server, {
    subagents,
    log: (row) => log.append(row),
    acpProvider: (providerId) => acp?.isAcp(providerId) === true,
  });

  return () => {
    stopped = true;
    try {
      stopTurnEnds();
    } catch (error) {
      console.error("agents-remember turn-end cleanup failed", error);
    }
    try {
      subscription?.release();
    } catch (error) {
      console.error("agents-remember subagent subscription cleanup failed", error);
    }
    try {
      activity?.unload();
    } catch (error) {
      console.error("agents-remember host activity unload publication failed", error);
    }
  };
}
