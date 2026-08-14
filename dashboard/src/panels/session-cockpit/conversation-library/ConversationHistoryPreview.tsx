// Read-only history preview (design §4.4). Rendered with the SAME block grammar as the live timeline
// but clearly labeled `history preview · not active` and stripped of every write affordance — no
// composer, interaction answer, queue, model/effort set, or interrupt. It is a preview, never a live
// AR session, and reading it never mutates any live conversation.

import { css } from "../../../../styled-system/css";
import type { HistoricalConversationPage } from "../../../data/conversation-library/types";
import { ConversationItemView } from "../conversation/ConversationItemView";

const wrap = css({ display: "flex", flexDirection: "column", minHeight: "0", flex: "1", gap: "0.3rem" });
const label = css({
  fontSize: "0.62rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "amber",
  flexShrink: 0,
});
const scroll = css({ flex: "1", minHeight: "0", overflowY: "auto", overflowX: "hidden" });
const rowShell = css({
  paddingBlock: "0.35rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "color-mix(in oklch, token(colors.grid) 45%, transparent)",
});
const placeholder = css({ fontSize: "0.74rem", color: "muted", padding: "0.5rem 0.2rem" });
const partialNote = css({ fontSize: "0.62rem", color: "muted", fontStyle: "italic", flexShrink: 0 });

export function ConversationHistoryPreview({
  page,
  loading,
  error,
}: {
  page?: HistoricalConversationPage;
  loading: boolean;
  error?: string;
}) {
  if (error !== undefined) {
    return (
      <div className={placeholder} role="alert" data-testid="history-preview-error">
        {error}
      </div>
    );
  }
  if (loading) {
    return (
      <div className={placeholder} data-testid="history-preview-loading">
        loading preview…
      </div>
    );
  }
  if (page === undefined) {
    return (
      <div className={placeholder} data-testid="history-preview-empty">
        Select a conversation to preview it here.
      </div>
    );
  }
  // Print the reason from the capability that is actually unsupported (F13) — never a supported
  // capability's reason text.
  const { completeness, toolCompleteness } = page.historicalCapabilities;
  const partialReason =
    toolCompleteness.state !== "supported"
      ? toolCompleteness.reason
      : completeness.state !== "supported"
        ? completeness.reason
        : null;
  return (
    <div className={wrap} data-testid="history-preview">
      <span className={label}>history preview · not active</span>
      {partialReason !== null ? (
        <span className={partialNote} data-testid="history-preview-partial">
          {partialReason}
        </span>
      ) : null}
      <div className={scroll} role="list" aria-label="History preview (read only)">
        {page.items.map((item) => (
          <div className={rowShell} role="listitem" key={item.itemId} data-testid="history-preview-item">
            <ConversationItemView item={item} />
          </div>
        ))}
      </div>
    </div>
  );
}
