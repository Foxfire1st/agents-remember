// The sub-agents area: ONE compact line above the timeline — always, never one row per agent
// (the Claude Code sub-agent navigation model; 260718-CHATS-L7R R5). The line carries the
// tone-colored count chip ("N agents · M running"), plus "viewing <label>" and a back-to-parent
// affordance while an agent view is active. Activating the line (click, Enter/Space, or
// ArrowDown — the line is reached by ArrowDown from anywhere on the conversation surface, and
// ArrowUp from the line returns focus to the timeline) opens the agent menu: a small
// listbox overlay with one option per roster agent (label + status chip + the terminal
// final-message preview), navigated with ArrowUp/ArrowDown (the active option is scrolled into
// view on every active change), selected with Enter/click, dismissed
// with Esc or an outside click (focus returns to the line). Everything renders ONLY from
// projection roster evidence (deriveAgents): no optimistic rows, no polling. Status is never
// color-only: every chip carries its word (§14.2). No transitions — a keyboard-driven open/focus
// change must not animate.

import { useEffect, useRef, useState } from "react";

import { css, cx } from "../../../../styled-system/css";
import type { ConversationAgentView } from "../../../data/conversation/agents";
import type { ConversationAgentStatus } from "../../../data/conversation/types";

const MENU_ID = "conversation-agents-menu";
const optionId = (agentId: string) => `conversation-agent-option-${agentId}`;

const area = css({
  position: "relative",
  flexShrink: 0,
  display: "flex",
  flexDirection: "column",
  minWidth: "0",
});
const lineRow = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.4rem",
  minWidth: "0",
});
const line = css({
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
  display: "inline-flex",
  alignItems: "baseline",
  gap: "0.4rem",
  maxWidth: "100%",
  minWidth: "0",
  overflow: "hidden",
  whiteSpace: "nowrap",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const lineStatic = css({
  fontSize: "0.66rem",
  color: "dormant",
  paddingInline: "0.4rem",
});
const countChip = css({
  flex: "none",
  fontSize: "0.58rem",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  borderWidth: "1px",
  borderStyle: "solid",
  borderRadius: "2px",
  paddingInline: "0.24rem",
});
const countTone = {
  active: css({ color: "cyan", borderColor: "cyan" }),
  idle: css({ color: "muted", borderColor: "grid" }),
};
const viewing = css({
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  minWidth: "0",
});
const backToParent = css({
  flex: "none",
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
const backdrop = css({ position: "fixed", inset: "0", zIndex: "20" });
const menu = css({
  position: "absolute",
  top: "100%",
  left: "0",
  zIndex: "21",
  marginTop: "0.15rem",
  width: "max-content",
  minWidth: "16rem",
  maxWidth: "min(28rem, 90vw)",
  maxHeight: "16rem",
  overflowY: "auto",
  display: "flex",
  flexDirection: "column",
  gap: "0.1rem",
  padding: "0.2rem",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  boxShadow: "0 10px 40px oklch(0 0 0 / 0.5)",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const option = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.4rem",
  minWidth: "0",
  fontSize: "0.68rem",
  color: "ink",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "transparent",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.05rem",
  cursor: "pointer",
  _hover: { borderColor: "grid" },
  "&[aria-selected='true']": { borderColor: "amber", background: "bg" },
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
  const [open, setOpen] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const lineRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const focusedAgent =
    focusedAgentId === null ? undefined : agents.find((agent) => agent.agentId === focusedAgentId);
  // The active option, recomputed against the live roster: a stale id (an agent the roster
  // dropped while the menu was open) resolves to the first option — the honest recompute.
  const resolvedActiveId =
    activeId !== null && agents.some((agent) => agent.agentId === activeId)
      ? activeId
      : (agents[0]?.agentId ?? null);
  const running = agents.filter((agent) => agent.status === "running").length;

  const openMenu = () => {
    if (agents.length === 0) return;
    setActiveId(focusedAgentId ?? agents[0]?.agentId ?? null);
    setOpen(true);
  };
  const closeMenu = (returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) lineRef.current?.focus();
  };
  const selectAgent = (agentId: string) => {
    // Re-selecting the already-viewed agent is a close, not a redundant focus write/announcement.
    if (agentId !== focusedAgentId) onFocusAgent(agentId);
    closeMenu(true);
  };

  // DOM focus lands on the listbox on open; its ring + aria-activedescendant carry the active
  // option from there (no per-option focus movement, nothing animates).
  useEffect(() => {
    if (open) menuRef.current?.focus();
  }, [open]);
  // The active descendant must stay VISIBLE: every active change scrolls its option into
  // view (aria-activedescendant moves no DOM focus, so the browser never does this for us).
  useEffect(() => {
    if (!open || resolvedActiveId === null) return;
    menuRef.current
      ?.querySelector<HTMLElement>(`[id='${optionId(resolvedActiveId)}']`)
      ?.scrollIntoView({ block: "nearest" });
  }, [open, resolvedActiveId]);
  // A roster that empties while the menu is open leaves nothing to select — close honestly.
  useEffect(() => {
    if (open && agents.length === 0) setOpen(false);
  }, [open, agents.length]);

  const onLineKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Enter" || event.key === " " || event.key === "ArrowDown") {
      // preventDefault suppresses the native button-activation click — this toggle owns it.
      event.preventDefault();
      if (open) closeMenu(true);
      else openMenu();
      return;
    }
    if (event.key === "ArrowUp") {
      // Symmetric with ArrowDown entering the line: ArrowUp from the line returns focus
      // to the timeline's tabbable row (the menu owns ArrowUp while open — focus is on
      // the listbox then, so this branch only fires on the closed line).
      event.preventDefault();
      lineRef.current
        ?.closest("[data-testid='conversation-surface']")
        ?.querySelector<HTMLElement>("[data-conversation-item][tabindex='0']")
        ?.focus();
      return;
    }
    if (event.key === "Escape") {
      // The menu owns Esc while it is open; on the closed line, Esc in an agent view returns
      // to the parent conversation (the surface's timeline-driven Esc stays untouched).
      if (open || focusedAgentId === null) return;
      event.preventDefault();
      event.stopPropagation();
      onFocusAgent(null);
    }
  };

  const onMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const ids = agents.map((agent) => agent.agentId);
    const index = resolvedActiveId === null ? -1 : ids.indexOf(resolvedActiveId);
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp": {
        event.preventDefault();
        event.stopPropagation();
        const delta = event.key === "ArrowDown" ? 1 : -1;
        const next = (((index + delta) % ids.length) + ids.length) % ids.length;
        setActiveId(ids[next] ?? null);
        break;
      }
      case "Enter":
        event.preventDefault();
        event.stopPropagation();
        if (resolvedActiveId !== null) selectAgent(resolvedActiveId);
        break;
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        closeMenu(true);
        break;
      case "Tab":
        // Menus dismiss on Tab; focus moves on by the browser's own order.
        closeMenu(false);
        break;
      default:
        break;
    }
  };

  return (
    <div className={area} role="group" aria-label="sub-agents" data-testid="conversation-agents">
      <div className={lineRow}>
        {agents.length === 0 ? (
          <span className={lineStatic} data-testid="conversation-agents-line">
            {summaryText(agents)}
          </span>
        ) : (
          <button
            type="button"
            ref={lineRef}
            className={line}
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-controls={MENU_ID}
            onClick={() => (open ? closeMenu(true) : openMenu())}
            onKeyDown={onLineKeyDown}
            data-agents-line=""
            data-testid="conversation-agents-line"
          >
            <span
              className={cx(countChip, running > 0 ? countTone.active : countTone.idle)}
              data-testid="conversation-agents-count"
            >
              {summaryText(agents)}
            </span>
            {focusedAgent !== undefined ? (
              <span className={viewing} data-testid="conversation-agent-focus-note">
                viewing {focusedAgent.label}
              </span>
            ) : null}
          </button>
        )}
        {focusedAgentId !== null ? (
          <button
            type="button"
            className={backToParent}
            onClick={() => onFocusAgent(null)}
            data-testid="conversation-back-to-parent"
          >
            ← back to parent conversation
          </button>
        ) : null}
      </div>
      {open && resolvedActiveId !== null ? (
        <>
          <div
            className={backdrop}
            onClick={() => closeMenu(true)}
            data-testid="conversation-agents-backdrop"
          />
          <div
            role="listbox"
            id={MENU_ID}
            ref={menuRef}
            className={menu}
            aria-label="agents"
            aria-activedescendant={optionId(resolvedActiveId)}
            tabIndex={-1}
            onKeyDown={onMenuKeyDown}
            data-testid="conversation-agents-menu"
          >
            {agents.map((agent) => (
              <div
                role="option"
                key={agent.agentId}
                id={optionId(agent.agentId)}
                className={option}
                aria-selected={resolvedActiveId === agent.agentId}
                onClick={() => selectAgent(agent.agentId)}
                data-testid="conversation-agent-option"
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
              </div>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
