import { framingOrigin, installBridge, isListedParent, type BridgeClient, type EmbedEntry } from "./bridge";
import { bootstrapEmbed, loadState, takeOwnLookBack, watchAppWrites } from "./load";
import type { PluginPage } from "./page";

// What the client part does in a page, decided here and nowhere else: who the parent is, whether
// it is trusted, and which of the two things follows. The entry file only hands over the page,
// the plugin's client context and the call that reads the embed list.
//
// Of the page this file reads one thing itself, `location.origin` (the origin the embed list is
// checked against; page.ts lists the browser objects). Everything else unsupported is in the
// files it calls, which say so.

/**
 * Start the client part in `page`. Returns the function that stops it.
 *
 *   - A standalone tab (no parent): the user's own look is taken back. The list is not read.
 *   - A frame whose parent the embed list pairs with this page's origin: the AR look and the
 *     control channel. The list is read on every evaluation and has the last word.
 *   - A frame under any other parent: as a standalone tab, and no channel.
 *   - A frame whose parent is unknown, or a list that cannot be read: nothing at all. Without the
 *     list no parent is trusted.
 *
 * In the first three cases the app's writes of its stored settings are watched from then on (the
 * record of who wrote them); in the last case nothing in the page is touched.
 */
export function startClientPart(
  page: PluginPage,
  client: BridgeClient,
  readEmbedList: () => Promise<readonly EmbedEntry[]>,
): () => void {
  let live = true;
  let removeBridge: (() => void) | null = null;
  let stopWatching: (() => void) | null = null;
  const load = loadState(page);
  // The app's writes are watched from the moment the page is known to be a listed frame or not
  // one; nothing is recorded before that, so nothing is wrapped before that either.
  const watch = () => {
    if (live && !stopWatching && load.usersLook !== null) stopWatching = watchAppWrites(page);
  };
  const dropBridge = () => {
    removeBridge?.();
    removeBridge = null;
  };
  const ownLook = () => {
    takeOwnLookBack(page);
    watch();
  };
  const embed = (parentOrigin: string) => {
    if (!live || removeBridge) return;
    const leaving = bootstrapEmbed(page, parentOrigin);
    watch();
    if (!leaving) removeBridge = installBridge(client, page, parentOrigin);
  };

  // An earlier evaluation in this page settled what the page is: its writes are watched at once.
  watch();
  const parentOrigin = framingOrigin(page, load.carriedParent);
  if (parentOrigin === null) {
    ownLook();
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
          ownLook();
        }
      })
      .catch(() => {
        // The list could not be read: nothing changes.
      });
  }

  return () => {
    live = false;
    dropBridge();
    stopWatching?.();
    stopWatching = null;
  };
}
