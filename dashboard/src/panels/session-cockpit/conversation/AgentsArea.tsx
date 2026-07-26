// The sub-agents area: the small persistent strip above the timeline with
// one live row per roster-evidenced agent — label, status chip, and the final-message preview
// once terminal. It renders ONLY from projection roster evidence (deriveAgents): no optimistic
// rows, no polling. With no agents, or while the surface is narrow, it collapses to a single
// summary line ("N agents · M running") that expands on click/keyboard activation. Status is
// never color-only: every chip carries its word (§14.2). No transitions — a keyboard-driven
// expand/focus change must not animate.

import { useEffect, useRef, useState } from "react";

import { css, cx } from "../../../../styled-system/css";
import type { ConversationAgentView } from "../../../data/conversation/agents";
import type { ConversationAgentStatus } from "../../../data/conversation/types";

// Below this width the area starts collapsed (the rows yield the width to the timeline).
const NARROW_PX = 560;

const area = css({
  flexShrink: 0,
  display: "flex",
  flexDirection: "column",
  gap: "0.15rem",
  minWidth: "0",
});
const summary = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.05rem",
  cursor: "pointer",
  alignSelf: "flex-start",
  maxWidth: "100%",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const summaryStatic = css({
  fontSize: "0.66rem",
  color: "dormant",
  paddingInline: "0.4rem",
});
const rows = css({ display: "flex", flexDirection: "column", gap: "0.1rem", minWidth: "0" });
const agentRow = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.4rem",
  minWidth: "0",
  font: "inherit",
  fontSize: "0.68rem",
  color: "ink",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.05rem",
  cursor: "pointer",
  textAlign: "left",
  _hover: { borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  "&[aria-current='true']": { borderColor: "amber" },
});
const label = css({
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  minWidth: "0",
});
const statusChip = css({
  flex: "none",
  fontSize: "0.58rem",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  borderWidth: "1px",
  borderStyle: "solid",
  borderRadius: "2px",
  paddingInline: "0.24rem",
});
const statusTone: Record<ConversationAgentStatus, string> = {
  registered: css({ color: "muted", borderColor: "grid" }),
  running: css({ color: "cyan", borderColor: "cyan" }),
  completed: css({ color: "mint", borderColor: "mint" }),
  interrupted: css({ color: "amber", borderColor: "amber" }),
  failed: css({ color: "alarm", borderColor: "alarm" }),
  unknown: css({ color: "dormant", borderColor: "grid" }),
};
const preview = css({
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  minWidth: "0",
  color: "muted",
  fontSize: "0.64rem",
});

function summaryText(agents: readonly ConversationAgentView[]): string {
  if (agents.length === 0) return "0 agents";
  const running = agents.filter((agent) => agent.status === "running").length;
  const noun = agents.length === 1 ? "agent" : "agents";
  return `${agents.length} ${noun} · ${running} running`;
}

function useNarrow(): [React.RefObject<HTMLDivElement | null>, boolean] {
  const ref = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (el === null || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) setNarrow(entry.contentRect.width < NARROW_PX);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, narrow];
}

export function AgentsArea({
  agents,
  focusedAgentId,
  onFocusAgent,
}: {
  agents: readonly ConversationAgentView[];
  /** The effective (roster-recomputed) focus: null = the parent conversation. */
  focusedAgentId: string | null;
  onFocusAgent: (agentId: string | null) => void;
}) {
  const [areaRef, narrow] = useNarrow();
  // null = no explicit operator choice: expanded iff there are agents and the width allows.
  const [override, setOverride] = useState<boolean | null>(null);
  const expanded = agents.length > 0 && (override ?? !narrow);

  return (
    <div className={area} ref={areaRef} role="group" aria-label="sub-agents" data-testid="conversation-agents">
      {agents.length === 0 ? (
        <span className={summaryStatic} data-testid="conversation-agents-summary">
          {summaryText(agents)}
        </span>
      ) : (
        <button
          type="button"
          className={summary}
          aria-expanded={expanded}
          aria-controls="conversation-agents-rows"
          onClick={() => setOverride(!expanded)}
          data-testid="conversation-agents-summary"
        >
          {summaryText(agents)}
        </button>
      )}
      {expanded ? (
        <div className={rows} id="conversation-agents-rows" data-testid="conversation-agents-rows">
          {agents.map((agent) => (
            <button
              type="button"
              key={agent.agentId}
              className={agentRow}
              aria-current={focusedAgentId === agent.agentId ? "true" : undefined}
              aria-label={`view ${agent.label}`}
              title={`view ${agent.label}`}
              onClick={() =>
                onFocusAgent(focusedAgentId === agent.agentId ? null : agent.agentId)
              }
              data-testid="conversation-agent-row"
            >
              <span className={label} data-testid="conversation-agent-label">
                {agent.label}
              </span>
              <span
                className={cx(statusChip, statusTone[agent.status])}
                data-testid="conversation-agent-status"
              >
                {agent.status}
              </span>
              {agent.finalMessage !== undefined ? (
                <span
                  className={preview}
                  title={agent.finalMessage}
                  data-testid="conversation-agent-final"
                >
                  {agent.finalMessage}
                </span>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
