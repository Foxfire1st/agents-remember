// The native conversation list (design §4.4). Server-native cursor paging (never infinite local
// indexing). Rows show a boundary-truncated title with the full value on hover (A5), the safe native
// id suffix, a humanized last-activity age (A4), and the granular historical-completeness badge. A
// row is selectable for preview only — selecting a row never opens or activates anything. A row's
// `agents` render as indented child rows that select/preview/open through the
// exact same flow — the child's key is minted server-side; the page's `agentsNote` renders verbatim
// when the server reports (partial) agent unavailability.

import { Fragment } from "react";

import { css } from "../../../../styled-system/css";
import { harnessLabel, humanizeAge, truncateMiddle } from "../../../data/conversation/format";
import type { HarnessId } from "../../../data/conversation/types";
import type {
  ConversationLibraryAgentRow,
  ConversationLibraryRow,
  LibraryConversationKey,
  LibraryListCursor,
} from "../../../data/conversation-library/types";

const list = css({ display: "flex", flexDirection: "column", gap: "0.2rem", minWidth: "0", overflowY: "auto" });
const row = css({
  display: "grid",
  gap: "0.1rem",
  textAlign: "left",
  font: "inherit",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  padding: "0.3rem 0.4rem",
  cursor: "pointer",
  minWidth: "0",
  _hover: { borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  "&[data-selected='true']": { borderColor: "amber", background: "color-mix(in oklch, token(colors.amber) 8%, transparent)" },
});
const title = css({ fontSize: "0.76rem", color: "ink", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
const meta = css({ display: "flex", gap: "0.4rem", alignItems: "baseline", fontSize: "0.62rem", color: "muted", minWidth: "0" });
const badge = css({
  fontSize: "0.58rem",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.24rem",
  color: "muted",
  flex: "none",
});
const idSuffix = css({ fontFamily: "mono", color: "dormant" });
const more = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.5rem",
  paddingBlock: "0.1rem",
  cursor: "pointer",
  alignSelf: "center",
  marginTop: "0.2rem",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const emptyCopy = css({ fontSize: "0.72rem", color: "muted", padding: "0.4rem 0.2rem" });
// Indented sub-agent child row: the same row grammar, nested under its parent.
const agentChild = css({
  marginInlineStart: "2ch",
  borderStyle: "dashed",
});
const agentsNote = css({ fontSize: "0.64rem", color: "muted", padding: "0.2rem 0.2rem" });

function completenessLabel(row: ConversationLibraryRow): string {
  const complete = row.capabilities.completeness.state === "supported";
  return complete ? "full history" : "partial history";
}

/**
 * One agent child, promoted to the SAME row shape the select/preview/open flow already consumes:
 * its key + identity digest are its own (server-minted); history capabilities are the harness's
 * read-path capabilities, inherited from the parent row (the wire carries none per child).
 */
function agentChildRow(
  parent: ConversationLibraryRow,
  agent: ConversationLibraryAgentRow,
): ConversationLibraryRow {
  return {
    conversationKey: agent.conversationKey,
    identityDigest: agent.identityDigest,
    title: agent.title,
    safeNativeIdSuffix: agent.safeNativeIdSuffix,
    lastActivityAt: agent.lastActivityAt,
    capabilities: parent.capabilities,
    agents: [],
  };
}

export function ConversationLibraryList({
  harnessId,
  rows,
  selectedKey,
  nextCursor,
  loading,
  error,
  agentsNote: agentsNoteText,
  onSelect,
  onLoadMore,
}: {
  harnessId: HarnessId;
  rows: readonly ConversationLibraryRow[];
  selectedKey: LibraryConversationKey | null;
  nextCursor: LibraryListCursor | null;
  loading: boolean;
  error?: string;
  /** The page's sub-agent availability note, rendered verbatim when present. */
  agentsNote?: string | null;
  onSelect: (row: ConversationLibraryRow) => void;
  onLoadMore: () => void;
}) {
  if (error !== undefined) {
    return (
      <div className={emptyCopy} role="alert" data-testid="library-list-error">
        {error}
      </div>
    );
  }
  if (rows.length === 0 && loading) {
    return (
      <div className={emptyCopy} data-testid="library-list-loading">
        loading {harnessLabel(harnessId)} history…
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className={emptyCopy} data-testid="library-list-empty">
        No {harnessLabel(harnessId)} conversations in this project scope.
      </div>
    );
  }
  return (
    <div className={list} data-testid="library-list">
      {agentsNoteText != null && agentsNoteText !== "" ? (
        <div className={agentsNote} data-testid="library-agents-note">
          {agentsNoteText}
        </div>
      ) : null}
      {rows.map((entry) => (
        <Fragment key={entry.conversationKey}>
          <button
            type="button"
            className={row}
            data-selected={entry.conversationKey === selectedKey ? "true" : "false"}
            onClick={() => onSelect(entry)}
            data-testid="library-row"
          >
            <span className={title} title={entry.title}>
              {truncateMiddle(entry.title, 60)}
            </span>
            <span className={meta}>
              <span className={badge}>{completenessLabel(entry)}</span>
              {entry.safeNativeIdSuffix ? (
                <span className={idSuffix}>…{entry.safeNativeIdSuffix}</span>
              ) : null}
              <span>{humanizeAge(entry.lastActivityAt)}</span>
            </span>
          </button>
          {(entry.agents ?? []).map((agent) => (
            <button
              type="button"
              key={agent.conversationKey}
              className={`${row} ${agentChild}`}
              data-selected={agent.conversationKey === selectedKey ? "true" : "false"}
              onClick={() => onSelect(agentChildRow(entry, agent))}
              data-testid="library-agent-row"
            >
              <span className={title} title={agent.title}>
                {truncateMiddle(agent.title, 60)}
              </span>
              <span className={meta}>
                <span className={badge}>agent</span>
                {agent.role ? <span className={badge}>{agent.role}</span> : null}
                {agent.safeNativeIdSuffix ? (
                  <span className={idSuffix}>…{agent.safeNativeIdSuffix}</span>
                ) : null}
                <span>{humanizeAge(agent.lastActivityAt)}</span>
              </span>
            </button>
          ))}
        </Fragment>
      ))}
      {nextCursor !== null ? (
        <button type="button" className={more} onClick={onLoadMore} disabled={loading} data-testid="library-load-more">
          {loading ? "loading…" : "Load more"}
        </button>
      ) : null}
    </div>
  );
}
