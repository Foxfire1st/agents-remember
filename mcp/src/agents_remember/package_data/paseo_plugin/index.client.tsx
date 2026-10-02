import type { PluginClientContext, PluginScreenProps } from "@getpaseo/plugin/client";
import { useEffect } from "react";
import { View } from "react-native";
import { OPEN_SCREEN_ID, framingOrigin, installBridge, isListedParent } from "./client/bridge";
import { bootstrapEmbed, loadState, takeOwnLookBack } from "./client/load";
import { THEME_ID } from "./client/look";
import { currentPage } from "./client/page";
import { arEmbedList } from "./shared/rpc";

// AR plugin, client part (PNT-R05). Installed and loaded by `agents-remember paseo provision`.
//
// This file uses supported Paseo interfaces only: the theme contribution, the contributed screen
// with its `navigation` prop, `client.openScreen`, `client.rpc` and `client.paseo`. Everything
// that reaches the page is unsupported and lives in four files, each of which lists what it
// relies on:
//   client/page.ts    the browser objects themselves (window, document, location, the storages)
//   client/look.ts    stored settings and chrome: theme, fonts, sidebar, header row
//   client/load.ts    once per page load: first-visit repair and the one reload
//   client/bridge.ts  the control channel with the dashboard and who the parent page is
//
// The AR look and the control channel apply only inside a frame whose parent origin the embed
// list pairs with this page's origin. Anywhere else the plugin changes nothing, except that it
// gives a standalone tab its own look back when the browser shares storage with such a frame.
//
// Type check: `npm install && npm run typecheck` in a copy of this directory (it needs the dev
// dependencies of package.json, which the repository does not carry: no gate can install them
// without the network). The files under client/ import nothing from Paseo and are type-checked
// and tested by the dashboard's own gate (dashboard/src/cockpit/paseoPluginClient.test.ts).

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

  let live = true;
  let removeBridge: (() => void) | null = null;
  const dropBridge = () => {
    removeBridge?.();
    removeBridge = null;
  };
  const embed = (parentOrigin: string) => {
    if (!live || removeBridge || bootstrapEmbed(page, parentOrigin)) return;
    removeBridge = installBridge(client, page, parentOrigin);
  };

  const load = loadState(page);
  const parentOrigin = framingOrigin(page, load.carriedParent);
  if (parentOrigin === null) {
    takeOwnLookBack(page);
  } else if (parentOrigin !== undefined) {
    // A re-evaluation in the same page reuses the answer this page already verified, so the
    // channel has no gap; the list is then asked again and has the last word.
    if (load.trustedParent === parentOrigin) embed(parentOrigin);
    client
      .rpc(arEmbedList, {})
      .then((answer) => {
        if (!live) return;
        if (isListedParent(answer.embed, parentOrigin, page.location.origin)) {
          load.trustedParent = parentOrigin;
          embed(parentOrigin);
        } else {
          load.trustedParent = null;
          dropBridge();
          takeOwnLookBack(page);
        }
      })
      .catch(() => {
        // The list could not be read: without it no parent is trusted and nothing changes.
      });
  }

  return () => {
    live = false;
    dropBridge();
  };
}
