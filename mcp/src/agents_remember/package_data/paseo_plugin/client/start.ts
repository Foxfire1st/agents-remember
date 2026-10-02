import { framingOrigin, installBridge, isListedParent, type BridgeClient, type EmbedEntry } from "./bridge";
import { bootstrapEmbed, loadState, takeOwnLookBack, watchAppWrites } from "./load";
import type { PluginPage } from "./page";

// What the client part does in a page, decided here and nowhere else: who the parent is, whether
// it is trusted, and which of the two things follows. The entry file only hands over the page,
// the plugin's client context and the call that reads the embed list.
//
// This file relies on nothing unsupported itself; the files it calls do, and say so.

/**
 * Start the client part in `page`. Returns the function that stops it.
 *
 *   - A standalone tab (no parent): the user's own look is taken back. The list is not read.
 *   - A frame whose parent the embed list pairs with this page's origin: the AR look and the
 *     control channel. The list is read on every evaluation and has the last word.
 *   - A frame under any other parent: as a standalone tab, and no channel.
 *   - A frame whose parent is unknown, or a list that cannot be read: nothing at all. Without the
 *     list no parent is trusted.
 */
export function startClientPart(
  page: PluginPage,
  client: BridgeClient,
  readEmbedList: () => Promise<readonly EmbedEntry[]>,
): () => void {
  let live = true;
  let removeBridge: (() => void) | null = null;
  const stopWatching = watchAppWrites(page);
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
    // channel has no gap; the list is then asked again.
    if (load.trustedParent === parentOrigin) embed(parentOrigin);
    Promise.resolve()
      .then(readEmbedList)
      .then((list) => {
        if (!live) return;
        if (isListedParent(list, parentOrigin, page.location.origin)) {
          load.trustedParent = parentOrigin;
          embed(parentOrigin);
        } else {
          load.trustedParent = null;
          dropBridge();
          takeOwnLookBack(page);
        }
      })
      .catch(() => {
        // The list could not be read: nothing changes.
      });
  }

  return () => {
    live = false;
    dropBridge();
    stopWatching();
  };
}
