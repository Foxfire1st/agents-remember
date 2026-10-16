import type { PluginServerContext } from "@getpaseo/plugin/server";
import { readEmbedList } from "./server/embed";
import { createHostActivityPublisher, registerHostActivityEvents } from "./server/host_activity";
import { registerParentWakeEvents } from "./server/turn_events";
import { arEmbedList, arHostActivitySnapshot } from "./shared/rpc";

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

  let activity: ReturnType<typeof createHostActivityPublisher> | undefined;
  try {
    if (!process.env.PASEO_HOME) throw new Error("PASEO_HOME is not set");
    activity = createHostActivityPublisher(process.env.PASEO_HOME);
    registerHostActivityEvents(server, activity);
  } catch (error) {
    console.error("agents-remember host activity initialization failed", error);
  }
  server.handle(arHostActivitySnapshot, async (_input, context) => {
    if (!activity) throw new Error("host activity publication is unavailable");
    await activity.reconcile(context.paseo);
    return { bootId: activity.bootId };
  });

  registerParentWakeEvents(server);

  return () => {
    try {
      activity?.unload();
    } catch (error) {
      console.error("agents-remember host activity unload publication failed", error);
    }
  };
}
