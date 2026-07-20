// The one Chats stage body (design §4.1, §12.1). A THIN composition: it selects between the default
// structured ConversationSurface, the in-stage history library, and the legacy-raw terminal, and it
// owns the default-off terminal-diagnostics drawer. It copies no panel's state — the composer,
// interaction bar, queue, header, and status line remain their own authorities and are rendered by
// SessionsView around this body. For a controlled session the structured surface is the default and
// the PTY is only a read-only diagnostic; a legacy-raw session keeps its interactive PTY as the
// primary body, honestly labeled.

import { useCallback, useEffect, useRef, useState } from "react";

import { css } from "../../../styled-system/css";
import { readSubmissionAuthority } from "../../data/submissionLifecycleClient";
import {
  connectConversation,
  disconnectConversation,
} from "../../data/conversation/store";
import type { HarnessId } from "../../data/conversation/types";
import type { OpenSession } from "../../data/sessions";
import { ConversationReconnect } from "./conversation/ConversationReconnect";
import { ConversationSurface } from "./conversation/ConversationSurface";
import { TerminalDiagnosticsDrawer } from "./conversation/TerminalDiagnosticsDrawer";
import { ConversationLibrarySurface } from "./conversation-library/ConversationLibrarySurface";
import { isControlledSession } from "./lifecycleCopy";
import { PtySurface } from "./PtySurface";

const body = css({ display: "flex", flexDirection: "column", flex: "1", minHeight: "0", minWidth: "0", gap: "0.3rem" });
const legacyLabel = css({
  fontSize: "0.66rem",
  letterSpacing: "0.04em",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "color-mix(in oklch, token(colors.amber) 45%, transparent)",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.12rem",
  flexShrink: 0,
});
// The live surface stays mounted (its store keeps updating) but goes inert behind the library (§4.4).
const hiddenBehind = css({ display: "none" });

function harnessOf(session: OpenSession): HarnessId | null {
  if (session.harness === "codex" || session.harness === "claude" || session.harness === "pi") {
    return session.harness;
  }
  return null;
}

export function ChatsStageBody({
  focused,
  onVisibleCols,
  libraryOpen,
  onCloseLibrary,
  diagnosticsOpen,
  onToggleDiagnostics,
  onSessionOpened,
}: {
  focused?: OpenSession;
  onVisibleCols?: (cols: number | null) => void;
  libraryOpen: boolean;
  onCloseLibrary: () => void;
  diagnosticsOpen: boolean;
  onToggleDiagnostics: (open: boolean) => void;
  onSessionOpened: (sessionId: string) => void;
}) {
  const controlled = focused !== undefined && isControlledSession(focused);
  const sessionId = focused?.id;
  const [epochState, setEpochState] = useState<"resolving" | "ready" | "failed">("resolving");
  const generationRef = useRef(0);

  const connect = useCallback(
    async (refresh: boolean, attempt = 0) => {
      if (sessionId === undefined || !controlled) return;
      const generation = attempt === 0 ? ++generationRef.current : generationRef.current;
      setEpochState("resolving");
      try {
        const descriptor = await readSubmissionAuthority(sessionId, undefined, { refresh });
        if (generationRef.current !== generation) return;
        connectConversation(sessionId, descriptor.bridgeEpoch);
        setEpochState("ready");
      } catch {
        if (generationRef.current !== generation) return;
        // One bounded auto-retry before escalating to the alarm state (F20): a just-launched chat
        // routinely loses the epoch resolve to session startup (submission-authority 503). Fail-loud
        // is preserved — a second failure still escalates to the visible projection-failed banner.
        if (attempt === 0) {
          window.setTimeout(() => {
            if (generationRef.current === generation) void connect(true, 1);
          }, 800);
          return;
        }
        setEpochState("failed");
      }
    },
    [sessionId, controlled],
  );

  useEffect(() => {
    if (sessionId === undefined || !controlled) return undefined;
    void connect(false);
    return () => disconnectConversation(sessionId);
  }, [sessionId, controlled, connect]);

  if (focused === undefined) return null;

  // Legacy raw: the native PTY is the primary interactive body, honestly labeled (§4.3).
  if (!controlled) {
    return (
      <div className={body} data-testid="chats-stage-body" data-mode="legacy-raw">
        <span className={legacyLabel} data-testid="legacy-terminal-label">
          legacy terminal · structured conversation unavailable
        </span>
        <PtySurface focused={focused} onVisibleCols={onVisibleCols} />
      </div>
    );
  }

  const harnessId = harnessOf(focused);
  const showLibrary = libraryOpen && harnessId !== null;

  return (
    <div className={body} data-testid="chats-stage-body" data-mode={showLibrary ? "library" : "active-conversation"}>
      <div className={showLibrary ? hiddenBehind : body}>
        {epochState === "failed" ? (
          <ConversationReconnect
            phase="projection-failed"
            onRetry={() => void connect(true)}
            onShowDiagnostics={() => onToggleDiagnostics(true)}
          />
        ) : sessionId !== undefined ? (
          <ConversationSurface
            sessionId={sessionId}
            onRetry={() => void connect(true)}
            onShowDiagnostics={() => onToggleDiagnostics(true)}
          />
        ) : null}
      </div>
      {showLibrary && harnessId !== null ? (
        <ConversationLibrarySurface
          harnessId={harnessId}
          launchContext={focused.leafKey ? { leafKey: focused.leafKey, seatRole: focused.seatRole } : undefined}
          onOpened={(arSessionId) => {
            onCloseLibrary();
            onSessionOpened(arSessionId);
          }}
          onBack={onCloseLibrary}
        />
      ) : null}
      {/* The diagnostics drawer belongs to the active conversation; while the in-stage library
          overlay is up it is not rendered, so the two surfaces can never overlay (F8). */}
      {!showLibrary ? (
        <TerminalDiagnosticsDrawer
          focused={focused}
          open={diagnosticsOpen}
          onClose={() => onToggleDiagnostics(false)}
        />
      ) : null}
    </div>
  );
}
