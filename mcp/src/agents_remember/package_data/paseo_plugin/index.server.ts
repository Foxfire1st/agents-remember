import type { PluginServerContext } from "@getpaseo/plugin/server";
import { readEmbedList } from "./server/embed";
import { readRoleAgents } from "./server/role_agents";
import {
  createTurnEndedTracker,
  noteAgentArchived,
  notePermissionRequested,
  notePermissionResolved,
  noteTurnEnded,
  noteTurnStarted,
} from "./server/turn_ended";
import { arEmbedList } from "./shared/rpc";

// Installed and loaded by `agents-remember paseo provision` (PNT-R01). The daemon-side behaviour
// arrives with PNT-R04. Reading the embed list at load proves the plugin can reach what provision
// published, and a malformed file fails the load visibly. The one handler hands that list to the
// client part, which decides from it which parent pages may frame and steer the app (PNT-R05).
//
// MIK-R117 rule 11: the turn-end handlers below wake a role agent's parent with one silent line
// when the turn ends with no return. The decision lives in `server/turn_ended.ts` (pure, tested
// without a host); the role start publishes the per-agent facts it reads in
// `server/role_agents.ts`. A handler never throws out of the host loop.
export default function contribute(server: PluginServerContext) {
  const embed = readEmbedList();
  console.log(`agents-remember plugin loaded; embed entries: ${embed.length}`);
  server.handle(arEmbedList, () => ({ embed }));

  const turns = createTurnEndedTracker();

  server.on("agent.turn_started", (event) => {
    noteTurnStarted(
      { id: event.agent.agentId, parentAgentId: event.agent.parentAgentId },
      turns,
    );
  });

  server.on("agent.turn_ended", (event, context) => {
    void (async () => {
      try {
        const records = readRoleAgents(process.env.PASEO_HOME);
        await noteTurnEnded(
          { id: event.agent.agentId, parentAgentId: event.agent.parentAgentId },
          event.timeline,
          records,
          turns,
          context.paseo,
        );
      } catch (error) {
        console.error("agents-remember turn_ended handler failed", error);
      }
    })();
  });

  server.on("agent.permission_requested", (event) => {
    notePermissionRequested(event.agent.agentId, turns);
  });

  server.on("agent.permission_resolved", (event, context) => {
    void (async () => {
      try {
        await notePermissionResolved(event.agent.agentId, turns, context.paseo);
      } catch (error) {
        console.error("agents-remember permission_resolved handler failed", error);
      }
    })();
  });

  server.on("agent.archived", (event) => {
    noteAgentArchived(event.agent.agentId, turns);
  });

  return () => {};
}
