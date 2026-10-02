import type { PluginClientContext, PluginScreenProps } from "@getpaseo/plugin/client";
import { useEffect } from "react";
import { View } from "react-native";
import { OPEN_SCREEN_ID } from "./client/bridge";
import { THEME_ID } from "./client/look";
import { currentPage } from "./client/page";
import { startClientPart } from "./client/start";
import { arEmbedList } from "./shared/rpc";

// AR plugin, client part (PNT-R05). Installed and loaded by `agents-remember paseo provision`.
//
// This file only wires. It uses supported Paseo interfaces: the theme contribution, the
// contributed screen with its `navigation` prop, and `client.rpc` for the embed list. It reads no
// page object; it hands the page, the client context and the list call to `startClientPart`.
// Through that context the client part also uses `client.openScreen` and the public SDK
// (`client.paseo.agents.ref(id).refresh()`, `client.paseo.workspaces.ref(id).refresh()`), which
// are supported too.
//
// Everything that reaches the page is unsupported and lives under client/. Each file lists what
// it relies on:
//   client/page.ts    the browser objects themselves, and seeing the app's own storage writes
//   client/look.ts    stored settings and chrome: theme, fonts, sidebar, header row; who wrote them
//   client/load.ts    once per page load: first-visit repair, the one reload, which look runs
//   client/bridge.ts  the control channel with the dashboard and who the parent page is
//   client/start.ts   the decision: which parent is trusted and what follows (nothing unsupported)
//
// The AR look and the control channel apply only inside a frame whose parent origin the embed
// list pairs with this page's origin. Anywhere else the plugin changes nothing, except that it
// gives a standalone tab its own look back when the browser shares storage with such a frame,
// and records there, as in the frame, which kind of page the app's settings writes come from.
//
// Type check: `npm install && npm run typecheck` in a copy of this directory (it needs the dev
// dependencies of package.json, which the repository does not carry: no gate can install them
// without the network). The files under client/ import nothing from Paseo and are type-checked
// and tested by the dashboard's own gate (dashboard/src/cockpit/paseoPluginClient.test.ts and
// paseoPluginLook.test.ts). This file and the server part are in no gate.

/**
 * Supported navigation: a contributed screen receives its params and the client-owned
 * `navigation` prop, and forwards to the agent or workspace it was opened for.
 */
function OpenTarget({ params, navigation, theme }: PluginScreenProps) {
  const { agentId, workspaceId, at } = params;
  useEffect(() => {
    if (!navigation) return;
    if (agentId) navigation.openAgent({ agentId });
    else if (workspaceId) navigation.openWorkspace({ workspaceId });
  }, [agentId, workspaceId, at, navigation]);
  return <View style={{ flex: 1, backgroundColor: theme.colors.surface0 }} />;
}

export default function contribute(client: PluginClientContext) {
  client.addTheme({
    id: THEME_ID,
    name: "Agents Remember",
    appearance: "dark",
    colors: {
      background: "#070e16",
      foreground: "#ece4cf",
      raised: "#0f171f",
      control: "#1a222b",
      border: "#262f38",
      accent: "#ffb330",
      mutedForeground: "#95a0ab",
      ring: "#44e7ef",
    },
  });
  client.addScreen({ id: OPEN_SCREEN_ID, title: "Opening", Component: OpenTarget });

  const page = currentPage();
  if (!page) return () => {};
  return startClientPart(page, client, () => client.rpc(arEmbedList, {}).then((answer) => answer.embed));
}
