import type { PluginClientContext } from "@getpaseo/plugin/client";

// Installed and loaded by `agents-remember paseo provision` (PNT-R01). It registers nothing yet:
// the theme, the first-visit handling and the dashboard control channel arrive with PNT-R05.
export default function contribute(_client: PluginClientContext) {
  return () => {};
}
