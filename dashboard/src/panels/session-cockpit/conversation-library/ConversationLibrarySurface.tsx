// The in-stage prior-conversation browser (design §4.4). It is NOT a route, tab, or second Chats
// destination — it opens in the stage over the live conversation (whose store keeps updating). On
// open it focuses its own heading (tabIndex=-1), never a result row; async list/preview updates never
// steal focus (§14.1). Two columns when width permits, stacked at the ~80-col floor. `Open as new
// chat` is the sole resume action; a successful open focuses the new rail row only after catalog proof.

import { useEffect, useRef } from "react";

import { css } from "../../../../styled-system/css";
import { harnessLabel } from "../../../data/conversation/format";
import type { HarnessId } from "../../../data/conversation/types";
import {
  loadLibraryList,
  loadLibraryPreview,
  useConversationLibrary,
  type LibraryDeps,
} from "../../../data/conversation-library/store";
import { ConversationHistoryPreview } from "./ConversationHistoryPreview";
import { ConversationLibraryList } from "./ConversationLibraryList";
import { OpenConversationAction } from "./OpenConversationAction";

const surface = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  gap: "0.4rem",
  overflow: "hidden",
  // Establish the container context so the stacked-flow query below actually matches (F22).
  containerType: "inline-size",
});
const headRow = css({ display: "flex", alignItems: "baseline", gap: "0.5rem", flexShrink: 0 });
const heading = css({
  fontSize: "0.72rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "ink",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "2px" },
});
const back = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const columns = css({
  display: "flex",
  gap: "0.6rem",
  flex: "1",
  minHeight: "0",
  overflow: "hidden", // children clip/scroll within the stage; nothing bleeds past the flex box (F8)
  // NO flex-wrap (F23): a wrapping flex container is multi-line, so each line's cross-size is sized
  // to content — the columns then grow to full content height inside this clipped box and the interior
  // `overflow-y:auto` scrollers never engage, leaving `Open as new chat`/`Load more` unreachable past
  // the fold. `nowrap` keeps a single flex line whose cross-size is the definite container height, so
  // each column (min-height:0) hands its own overflow to its interior scroller. Stacking at the narrow
  // floor is owned entirely by the `@container` query below (F22 established the container context).
  // V10 — the two columns (16rem list + 20rem preview + gaps) crush below ~56rem of surface: the list
  // falls to a ~180px sliver and preview prose splits mid-word. Stack to one column before that, so the
  // narrow surface (900px window with the rail collapsed, or the ~1000px sweep) reads as a single flow.
  "@container (max-width: 56rem)": { flexDirection: "column" },
});
const listCol = css({ flex: "1 1 16rem", minWidth: "0", minHeight: "0", overflow: "hidden", display: "flex", flexDirection: "column" });
const previewCol = css({ flex: "2 1 20rem", minWidth: "0", minHeight: "0", overflow: "hidden", display: "flex", flexDirection: "column", gap: "0.3rem" });

export function ConversationLibrarySurface({
  harnessId,
  cwd,
  launchContext,
  onOpened,
  onBack,
  deps,
}: {
  harnessId: HarnessId;
  cwd?: string;
  launchContext?: { leafKey?: string; seatRole?: string };
  onOpened: (arSessionId: string) => void;
  onBack: () => void;
  deps?: LibraryDeps;
}) {
  const list = useConversationLibrary((state) => state.list);
  const preview = useConversationLibrary((state) => state.preview);
  const selectedKey = useConversationLibrary((state) => state.selectedKey);
  const headingRef = useRef<HTMLHeadingElement>(null);

  // Open focuses the heading itself, not a row (§14.1). Load the list for this harness/scope once.
  useEffect(() => {
    headingRef.current?.focus();
    void loadLibraryList(harnessId, { cwd }, deps);
    // Intentionally scoped to identity/scope changes; loaders + deps are stable module functions.
  }, [harnessId, cwd, deps]);

  const selectedRow = list?.rows.find((row) => row.conversationKey === selectedKey);

  // §4.4 return paths: Escape (when not typing) returns to the current chat, consuming the focus
  // token via onBack (F16). The Back button and the palette return command share the same onBack.
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== "Escape") return;
    const target = event.target as HTMLElement;
    if (
      target.tagName === "INPUT" ||
      target.tagName === "TEXTAREA" ||
      target.isContentEditable
    ) {
      return;
    }
    event.preventDefault();
    onBack();
  };

  return (
    <div
      className={surface}
      data-testid="conversation-library-surface"
      data-region="stage"
      onKeyDown={onKeyDown}
    >
      <div className={headRow}>
        <h3 className={heading} tabIndex={-1} ref={headingRef} data-testid="library-heading">
          {harnessLabel(harnessId)} history
        </h3>
        <button type="button" className={back} onClick={onBack} data-testid="library-back">
          ← back to current chat
        </button>
      </div>
      <div className={columns}>
        <div className={listCol}>
          <ConversationLibraryList
            harnessId={harnessId}
            rows={list?.rows ?? []}
            selectedKey={selectedKey}
            nextCursor={list?.nextCursor ?? null}
            loading={list?.loading ?? false}
            error={list?.error}
            onSelect={(row) => void loadLibraryPreview(harnessId, row.conversationKey, deps)}
            onLoadMore={() =>
              void loadLibraryList(harnessId, { cwd, cursor: list?.nextCursor ?? undefined }, deps)
            }
          />
        </div>
        <div className={previewCol}>
          <ConversationHistoryPreview
            page={preview?.page}
            loading={preview?.loading ?? false}
            error={preview?.error}
          />
          {selectedRow !== undefined ? (
            <OpenConversationAction
              harnessId={harnessId}
              row={selectedRow}
              cwd={cwd}
              launchContext={launchContext}
              deps={deps}
              onOpened={onOpened}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
