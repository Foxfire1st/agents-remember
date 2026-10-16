/** The parent-wake handlers contributed to the host, with explicit steering sends. */
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
      { id: event.agent.id, parentAgentId: event.agent.parentAgentId },
      turns,
    );
  });

  server.on("agent.turn_ended", (event, context) => {
    void (async () => {
      try {
        const records = readRoleAgents(process.env.PASEO_HOME);
        await noteTurnEnded(
          { id: event.agent.id, parentAgentId: event.agent.parentAgentId },
          event.timeline,
          records,
          turns,
          { sendMessage: (id, text) => context.paseo.agents.ref(id).send(text, { activeTurnBehavior: "steer" }) },
        );
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
        await notePermissionResolved(event.agent.id, turns, {
          sendMessage: (id, text) => context.paseo.agents.ref(id).send(text, { activeTurnBehavior: "steer" }),
        });
      } catch (error) {
        console.error("agents-remember permission_resolved handler failed", error);
      }
    })();
  });

  server.on("agent.archived", (event) => {
    noteAgentArchived(event.agent.id, turns);
  });

}
