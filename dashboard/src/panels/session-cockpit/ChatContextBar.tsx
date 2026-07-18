import { useMemo, useState } from "react";

import { css } from "../../../styled-system/css";
import {
  attachSeatRole,
  notifySessionCatalogChanged,
  sessionStore,
  type OpenSession,
} from "../../data/sessions";
import { buildTaskTree, leafIdFromKey, leafTitleForKey } from "../../data/taskIdentity";
import { attachSessionToLeaf } from "../../data/terminal";
import type { TaskDocNode } from "../../types/projection";
import { LeafAttachPicker } from "../LeafAttachPicker";

const bar = css({
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
  cursor: "pointer",
  _hover: { background: "rgba(232, 193, 112, 0.1)" },
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
});
const badge = css({
  minWidth: 0,
  maxWidth: "24rem",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "muted",
  fontSize: "0.68rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.16rem",
});
const refusal = css({ color: "red", fontSize: "0.68rem" });

export interface ChatContextBarProps {
  focused?: OpenSession;
  selectedLifecycleId?: string;
  selectedLeafKey?: string;
  taskDocuments: TaskDocNode[];
  contextMaster?: string;
  onLaunchChat: () => void;
  onLaunchTerminal: () => void;
}

/**
 * The small product-duty bar for the canonical Chats cockpit. It deliberately keeps the old
 * lifecycle attachment semantics honest: launch inheritance is sent to the server, while attaching
 * an already-open legacy row remains a local route preference because no server endpoint exists.
 * Leaf attach/move is different: it always crosses the authoritative server boundary first.
 */
export function ChatContextBar({
  focused,
  selectedLifecycleId,
  selectedLeafKey,
  taskDocuments,
  contextMaster,
  onLaunchChat,
  onLaunchTerminal,
}: ChatContextBarProps) {
  const [leafAttachError, setLeafAttachError] = useState<string | null>(null);
  const leafTree = useMemo(() => buildTaskTree(taskDocuments), [taskDocuments]);
  const pickerContextMaster =
    contextMaster ?? (selectedLeafKey ? selectedLeafKey.split("/").filter(Boolean)[1] : undefined);
  const running = focused !== undefined && (focused.status ?? "running") === "running";

  const attachLifecycleLocally = () => {
    if (!focused || !running || focused.lifecycleId || !selectedLifecycleId) return;
    sessionStore.getState().setLifecycle(focused.id, selectedLifecycleId);
  };

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

  const leafLabel = focused?.leafKey
    ? (leafTitleForKey(taskDocuments, focused.leafKey) ?? leafIdFromKey(focused.leafKey))
    : null;

  return (
    <div className={bar} data-testid="chats-context-bar" aria-label="Chat launch and routing">
      <button type="button" className={action} onClick={onLaunchChat} data-testid="chats-new-chat">
        ＋ Chat
      </button>
      <button
        type="button"
        className={action}
        onClick={onLaunchTerminal}
        data-testid="chats-new-terminal"
      >
        ＋ Terminal
      </button>
      {selectedLifecycleId && focused?.lifecycleId === selectedLifecycleId ? (
        <span className={badge}>task {selectedLifecycleId}</span>
      ) : selectedLifecycleId && focused && running && !focused.lifecycleId ? (
        <button
          type="button"
          className={action}
          onClick={attachLifecycleLocally}
          data-testid="chats-attach-lifecycle"
          title="Routes this existing row locally; only new launches inherit the task on the server."
        >
          Route locally to {selectedLifecycleId}
        </button>
      ) : focused?.lifecycleId ? (
        <span className={badge}>task {focused.lifecycleId}</span>
      ) : null}
      {leafLabel ? (
        <span className={badge} data-testid="chats-leaf-badge">
          leaf {leafLabel}
        </span>
      ) : null}
      {focused && running && leafTree.length > 0 ? (
        <LeafAttachPicker
          tree={leafTree}
          contextMaster={pickerContextMaster}
          onPick={(leafKey, seatRole) => void attachLeaf(leafKey, seatRole)}
          testId="chats-attach-leaf-picker"
          label={focused.leafKey ? "Move leaf" : "Attach to leaf"}
          align="left"
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
    </div>
  );
}
