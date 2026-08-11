import type { RefObject } from "react";

import type { HarnessId } from "../../data/conversation/types";
import type { OpenSession } from "../../data/sessions";
import { EmptyStateBackdrop } from "../EmptyStateBackdrop";
import { ConversationReconnect } from "./conversation/ConversationReconnect";
import { ConversationSurface } from "./conversation/ConversationSurface";
import { TerminalDiagnosticsDrawer } from "./conversation/TerminalDiagnosticsDrawer";
import { ConversationLibrarySurface } from "./conversation-library/ConversationLibrarySurface";
import { PtySurface } from "./PtySurface";
import { body, keptHidden, pool } from "./chatsStageStyles";

export function EmptyChatStage() {
  return (
    <div className={body} data-testid="chats-stage-body" data-mode="empty">
      <EmptyStateBackdrop src="/assets/sc2-adjutant-boomerang.mp4">
        Select a chat to inspect it — or start one from the chat rail.
      </EmptyStateBackdrop>
    </div>
  );
}

export function ConversationPool({
  poolIds,
  sessionId,
  epochState,
  viewActive,
  showLibrary,
  terminalFocused,
  connect,
  onToggleDiagnostics,
}: {
  poolIds: string[];
  sessionId: string | undefined;
  epochState: "resolving" | "ready" | "failed";
  viewActive: boolean;
  showLibrary: boolean;
  terminalFocused: boolean;
  connect: (refresh: boolean) => Promise<void>;
  onToggleDiagnostics: (open: boolean) => void;
}) {
  return (
    <div
      className={terminalFocused ? keptHidden : pool}
      aria-hidden={terminalFocused ? true : undefined}
      data-testid="conversation-layer"
    >
      {poolIds.map((id) => {
        const isFocused = id === sessionId;
        return (
          <div
            key={id}
            className={isFocused ? body : keptHidden}
            aria-hidden={isFocused ? undefined : true}
            data-testid={`conversation-keepalive-${id}`}
          >
            {isFocused && epochState === "failed" ? (
              <ConversationReconnect
                phase="projection-failed"
                onRetry={() => void connect(true)}
                onShowDiagnostics={() => onToggleDiagnostics(true)}
              />
            ) : (
              <ConversationSurface
                sessionId={id}
                visible={isFocused && viewActive && !showLibrary}
                scrollGeometryActive={isFocused && viewActive && !showLibrary}
                onRetry={() => void connect(true)}
                onShowDiagnostics={() => onToggleDiagnostics(true)}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

export function PtyLayer({
  box,
  terminalFocused,
  ptyFocus,
  onVisibleCols,
}: {
  box: {
    ref: RefObject<HTMLDivElement | null>;
    frozenStyle?: { width: number; height: number; flex: "none" };
  };
  terminalFocused: boolean;
  ptyFocus: OpenSession | undefined;
  onVisibleCols?: (cols: number | null) => void;
}) {
  return (
    <div
      ref={box.ref}
      className={terminalFocused ? body : keptHidden}
      style={!terminalFocused ? box.frozenStyle : undefined}
      aria-hidden={terminalFocused ? undefined : true}
      data-testid="pty-layer"
    >
      <PtySurface
        focused={ptyFocus}
        onVisibleCols={terminalFocused ? onVisibleCols : undefined}
        hidden={!terminalFocused}
      />
    </div>
  );
}

export function LibraryAndDiagnostics({
  showLibrary,
  harnessId,
  focused,
  controlled,
  diagnosticsOpen,
  onCloseLibrary,
  onToggleDiagnostics,
  onSessionOpened,
}: {
  showLibrary: boolean;
  harnessId: HarnessId | null;
  focused: OpenSession;
  controlled: boolean;
  diagnosticsOpen: boolean;
  onCloseLibrary: () => void;
  onToggleDiagnostics: (open: boolean) => void;
  onSessionOpened: (sessionId: string) => void;
}) {
  return (
    <>
      {showLibrary && harnessId !== null ? (
        <ConversationLibrarySurface
          harnessId={harnessId}
          launchContext={
            focused.taskDocumentRef
              ? { taskDocumentRef: focused.taskDocumentRef, seatRole: focused.seatRole }
              : undefined
          }
          onOpened={(arSessionId) => {
            onCloseLibrary();
            onSessionOpened(arSessionId);
          }}
          onBack={onCloseLibrary}
        />
      ) : null}
      {!showLibrary && controlled ? (
        <TerminalDiagnosticsDrawer
          focused={focused}
          open={diagnosticsOpen}
          onClose={() => onToggleDiagnostics(false)}
        />
      ) : null}
    </>
  );
}
