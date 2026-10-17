/**
 * The parent-wake handlers contributed to the host.
 *
 * MIK-R172@v2 rule 6: the send still asks for `steer`, but a line is only sent to
 * a parent that is mid-turn when the product knows that parent's provider keeps
 * its running turn under a steer (`TURN_ENDED_STEERING_PROVIDERS`, empty today);
 * otherwise the line is held and this handler sends it when that turn ends.
 */
import type { PluginLifecycleRegistration } from "@getpaseo/plugin/server";
import { readRoleAgents } from "./role_agents.ts";
import {
  createTurnEndedTracker,
  noteAgentArchived,
  notePermissionRequested,
  notePermissionResolved,
  noteTurnEnded,
  noteTurnStarted,
} from "./turn_ended.ts";

export function registerParentWakeEvents(server: Pick<PluginLifecycleRegistration, "on">): void {
  const turns = createTurnEndedTracker();

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
          // The ended turn's id scopes the rows the line reads (L172-R1-F01).
          event.turnId,
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

}
