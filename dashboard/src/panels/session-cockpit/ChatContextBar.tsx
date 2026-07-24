import { useMemo, useState } from "react";

import { css } from "../../../styled-system/css";
import {
  attachSeatRole,
  createSession,
  notifySessionCatalogChanged,
  sessionStore,
  terminalOpenFailureMessage,
  type OpenSession,
} from "../../data/sessions";
import { buildTaskTree } from "../../data/taskIdentity";
import { attachSessionToLeaf } from "../../data/terminal";
import type { TaskDocNode } from "../../types/projection";
import { LeafAttachPicker } from "../LeafAttachPicker";

const launchBar = css({
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  gap: "0.35rem",
  paddingBlockEnd: "0.35rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  flexShrink: 0,
});
const sessionActions = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.35rem",
  minWidth: "0",
});
const action = css({
  font: "inherit",
  fontSize: "0.7rem",
  letterSpacing: "0.04em",
  color: "amber",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  paddingInline: "0.5rem",
  paddingBlock: "0.18rem",
  whiteSpace: "nowrap",
  cursor: "pointer",
  transition: "transform 120ms cubic-bezier(0.23, 1, 0.32, 1)",
  _hover: { background: "rgba(232, 193, 112, 0.1)" },
  _active: { transform: "scale(0.97)" },
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
  // V8 — a library-level affordance that stays put: disable-with-reason rather than unmount, so the
  // toolbar never reflows (no teleport / muscle-memory loss) when focus moves to an ineligible row.
  _disabled: {
    color: "muted",
    borderColor: "grid",
    cursor: "not-allowed",
    opacity: 0.55,
    _hover: { background: "transparent" },
  },
});
const refusal = css({ color: "red", fontSize: "0.68rem" });

export interface ChatContextBarProps {
  selectedLifecycleId?: string;
  onLaunchChat: () => void;
  onSessionOpened: (sessionId: string) => void;
}

/**
 * The rail owns only chat creation and navigation. Actions that mutate or inspect the focused
 * session live beside that session's stage header in ChatSessionActions below.
 */
export function ChatContextBar({
  selectedLifecycleId,
  onLaunchChat,
  onSessionOpened,
}: ChatContextBarProps) {
  const [sessionOpenError, setSessionOpenError] = useState<string | null>(null);

  const launchTerminal = async () => {
    setSessionOpenError(null);
    const result = await createSession("Terminal", "terminal", undefined, selectedLifecycleId);
    if (result.outcome === "failed") {
      setSessionOpenError(terminalOpenFailureMessage(result));
      return;
    }
    onSessionOpened(result.session.id);
  };

  return (
    <div className={launchBar} data-testid="chats-context-bar" aria-label="Create chat sessions">
      <button
        type="button"
        className={action}
        onClick={onLaunchChat}
        data-testid="chats-new-chat"
        aria-label="New chat — choose Claude, Codex, or Pi"
      >
        ＋ Chat
      </button>
      <button
        type="button"
        className={action}
        onClick={() => void launchTerminal()}
        data-testid="chats-new-terminal"
      >
        ＋ Terminal
      </button>
      {sessionOpenError ? (
        <span className={refusal} role="alert" data-testid="chats-session-open-error">
          {sessionOpenError}
        </span>
      ) : null}
    </div>
  );
}

export interface ChatSessionActionsProps {
  focused?: OpenSession;
  selectedLeafKey?: string;
  taskDocuments: TaskDocNode[];
  contextMaster?: string;
  /** Open the in-stage native history browser for the focused controlled session. */
  onBrowseHistory: () => void;
}

/**
 * Actions whose object is the focused session. Keeping them in the stage header makes ownership
 * explicit: the rail chooses a session; this cluster inspects or routes that selected session.
 */
export function ChatSessionActions({
  focused,
  selectedLeafKey,
  taskDocuments,
  contextMaster,
  onBrowseHistory,
}: ChatSessionActionsProps) {
  const [leafAttachError, setLeafAttachError] = useState<string | null>(null);
  const leafTree = useMemo(() => buildTaskTree(taskDocuments), [taskDocuments]);
  const pickerContextMaster =
    contextMaster ?? (selectedLeafKey ? selectedLeafKey.split("/").filter(Boolean)[1] : undefined);
  const running = focused !== undefined && (focused.status ?? "running") === "running";

  const attachLeaf = async (leafKey: string, seatRole: string) => {
    if (!focused || !running || !leafKey || focused.leafKey === leafKey) return;
    setLeafAttachError(null);
    const result = await attachSessionToLeaf(focused.id, leafKey, seatRole);
    if (result === "ok") {
      sessionStore.getState().applyLeafAssignment(focused.id, leafKey, seatRole);
      notifySessionCatalogChanged("leaf", focused.id);
      return;
    }
    setLeafAttachError(
      result === "leaf-taken"
        ? `leaf already has a ${seatRole} seat`
        : "could not attach to leaf",
    );
  };

  return (
    <span
      className={sessionActions}
      data-testid="chats-session-actions"
      aria-label="Selected chat actions"
    >
      {/* Stable placement preserves muscle memory across focus changes; ineligible sessions keep a
          disabled control with the reason in its title instead of teleporting the toolbar. */}
      <button
        type="button"
        className={action}
        onClick={onBrowseHistory}
        disabled={!(focused && running && focused.harness)}
        data-testid="chats-browse-history"
        title={
          focused && running && focused.harness
            ? "Browse this harness's prior conversations and open one as a new chat"
            : "Browse history needs a running harness chat focused"
        }
      >
        Browse history
      </button>
      {focused && running && leafTree.length > 0 ? (
        <LeafAttachPicker
          tree={leafTree}
          contextMaster={pickerContextMaster}
          onPick={(leafKey, seatRole) => void attachLeaf(leafKey, seatRole)}
          testId="chats-attach-leaf-picker"
          label={focused.leafKey ? "Move leaf" : "Attach to leaf"}
          align="right"
          seatRole={attachSeatRole(focused)}
          roleOptions={focused.kind === "terminal" ? ["terminal"] : undefined}
        />
      ) : null}
      {leafAttachError ? (
        <span
          className={refusal}
          role="alert"
          data-testid="chats-leaf-attach-error"
        >
          {leafAttachError}
        </span>
      ) : null}
    </span>
  );
}
