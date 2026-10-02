import type { PluginClientContext, PluginScreenProps } from "@getpaseo/plugin/client";
import { useEffect } from "react";
import { View } from "react-native";
import { OPEN_SCREEN_ID, framingOrigin, installBridge, isListedParent } from "./client/bridge";
import {
  THEME_ID,
  applyEmbedLook,
  closeSidebarAtLoad,
  reloadOnce,
  restoreStandaloneLook,
} from "./client/look";
import { arEmbedList } from "./shared/rpc";

// AR plugin, client part (PNT-R05). Installed and loaded by `agents-remember paseo provision`.
//
// Supported Paseo interfaces used here: the theme contribution, the contributed screen with its
// `navigation` prop, `client.openScreen`, `client.rpc` and `client.paseo`. Everything that reaches
// the page (storage, DOM, `message` events) is unsupported and is kept in client/look.ts and
// client/bridge.ts, which say what they rely on.
//
// The AR look and the control channel apply only inside a frame whose parent origin the embed
// list pairs with this page's origin. Anywhere else the plugin changes nothing, except that it
// gives a standalone tab its own look back when the browser shares storage with such a frame.

// One page load = one document; the entry is evaluated again on every workspace switch and
// plugin reload, so what must happen once per load is flagged on the document's global.
const BOOTSTRAPPED = "__arPluginBootstrapped";
const TRUSTED_PARENT = "__arPluginTrustedParent";
// Where the app sends a deep link while it does not know the serving daemon yet (a first visit).
const BOUNCE_PATHS = ["/welcome", "/open-project"];

// The plugin tsconfig deliberately has no DOM lib; reach the browser globals untyped.
const web = globalThis as any;

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

/** The URL this page was asked to load, even if the app has since redirected away from it. */
function requestedUrl(): string {
  try {
    const entry = web.performance.getEntriesByType("navigation")[0];
    const url = new web.URL(entry?.name ?? web.location.href);
    if (url.origin === web.location.origin && url.pathname.startsWith("/h/")) return url.href;
  } catch {
    // fall through
  }
  return web.location.href;
}

/**
 * Once per page load inside a listed frame: store the AR look and repair a first-visit deep
 * link. Both take effect only when the page loads, so this may replace the page, once. Returns
 * whether the page is leaving.
 */
function bootstrapEmbed(): boolean {
  if (web[BOOTSTRAPPED]) return false;
  web[BOOTSTRAPPED] = true;
  const changed = applyEmbedLook();
  const requested = requestedUrl();
  const bounced = requested !== web.location.href && BOUNCE_PATHS.includes(web.location.pathname);
  if ((changed.length > 0 || bounced) && reloadOnce(requested)) return true;
  closeSidebarAtLoad();
  return false;
}

function takeOwnLookBack(): void {
  if (restoreStandaloneLook()) reloadOnce(web.location.href);
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

  if (typeof web.window === "undefined" || typeof web.document === "undefined") return () => {};

  let live = true;
  let removeBridge: (() => void) | null = null;
  const dropBridge = () => {
    removeBridge?.();
    removeBridge = null;
  };
  const embed = (parentOrigin: string) => {
    if (!live || removeBridge || bootstrapEmbed()) return;
    removeBridge = installBridge(client, parentOrigin);
  };

  const parentOrigin = framingOrigin();
  if (parentOrigin === null) {
    takeOwnLookBack();
  } else if (parentOrigin !== undefined) {
    // A re-evaluation in the same page reuses the answer this page already verified, so the
    // channel has no gap; the list is then asked again and has the last word.
    if (web[TRUSTED_PARENT] === parentOrigin) embed(parentOrigin);
    client
      .rpc(arEmbedList, {})
      .then((answer) => {
        if (!live) return;
        if (isListedParent(answer.embed, parentOrigin)) {
          web[TRUSTED_PARENT] = parentOrigin;
          embed(parentOrigin);
        } else {
          web[TRUSTED_PARENT] = undefined;
          dropBridge();
          takeOwnLookBack();
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
