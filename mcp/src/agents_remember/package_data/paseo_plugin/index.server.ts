import type { PluginServerContext } from "@getpaseo/plugin/server";
import { readEmbedList } from "./server/embed";
import { arEmbedList } from "./shared/rpc";

// Installed and loaded by `agents-remember paseo provision` (PNT-R01). The daemon-side behaviour
// arrives with PNT-R04. Reading the embed list at load proves the plugin can reach what provision
// published, and a malformed file fails the load visibly. The one handler hands that list to the
// client part, which decides from it which parent pages may frame and steer the app (PNT-R05).
export default function contribute(server: PluginServerContext) {
  const embed = readEmbedList();
  console.log(`agents-remember plugin loaded; embed entries: ${embed.length}`);
  server.handle(arEmbedList, () => ({ embed }));
  return () => {};
}
