import type { PluginServerContext } from "@getpaseo/plugin/server";
import { readEmbedList } from "./server/embed";

// Installed and loaded by `agents-remember paseo provision` (PNT-R01). It registers nothing yet:
// the daemon-side behaviour arrives with PNT-R04. Reading the embed list at load proves the
// plugin can reach what provision published, and a malformed file fails the load visibly.
export default function contribute(_server: PluginServerContext) {
  const embed = readEmbedList();
  console.log(`agents-remember plugin loaded; embed entries: ${embed.length}`);
  return () => {};
}
