// One CM6 markdown composer for every session surface: per-session drafts live in the
// cockpit store, Ctrl+Enter submits through /submit, Enter remains a newline, and Alt+Up performs
// an epoch-bound atomic server withdrawal before restoring text. No component here owns a
// PTY connection or paste helper; raw xterm typing remains the only raw-stdin path.
// The store/editor/keymap machinery lives in sessionComposerHooks.ts, the render rows in
// sessionComposerParts.tsx, and the shared styles in sessionComposerStyles.ts.

import { Compartment } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";

import { useEffectiveKeymap } from "../data/keymap/preferences";
import type { OpenSession } from "../data/sessions";
import type { ConversationInterrupt } from "./session-cockpit/conversation/useConversationControls";
import {
  useComposerEditor,
  useComposerInteraction,
  useComposerKeymap,
  useComposerRecovery,
  useComposerStore,
  useComposerStatusHandlers,
  useComposerSubmit,
  useComposerView,
  type ComposerCallbackRefs,
} from "./sessionComposerHooks";
import {
  ComposerView,
} from "./sessionComposerParts";

export interface SessionComposerHandle {
  submit(): void;
  popBack(): void;
  focus(): void;
  getDraft(): string;
  getElement(): HTMLElement | null;
}

export interface SessionComposerProps {
  session: OpenSession;
  queuedSetHint?: string | null;
  onSlashAtLineStart?: () => void;
  onEscape?: () => void;
  ariaLabel?: string;
  /** The shared exact-turn interrupt — renders the ⏹ stop beside send while a turn works. */
  interrupt?: ConversationInterrupt;
  /** The view-owned working signal (SSE-preferred) that mounts/unmounts the stop control. */
  turnWorking?: boolean;
}

export const SessionComposer = forwardRef<
  SessionComposerHandle,
  SessionComposerProps
>(function SessionComposer(
  {
    session,
    queuedSetHint,
    onSlashAtLineStart,
    onEscape,
    ariaLabel = `Message ${session.label}`,
    interrupt,
    turnWorking = false,
  },
  forwardedRef,
) {
  const frameRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<EditorView | null>(null);
  const syncingDraftRef = useRef(false);
  const composingRef = useRef(false);
  // A compartment is a plain extension key: one instance can be re-used across editor
  // instances, and the EditorView itself is recreated per session identity below.
  const editable = useMemo(() => new Compartment(), []);
  const profileCompartment = useMemo(() => new Compartment(), []);
  const keymapCompartment = useMemo(() => new Compartment(), []);
  const effectiveKeymap = useEffectiveKeymap();
  const callbackRef = useRef<ComposerCallbackRefs>({ onSlashAtLineStart, onEscape });
  callbackRef.current = { onSlashAtLineStart, onEscape };
  const [notice, setNotice] = useState<string | null>(null);
  const store = useComposerStore(session);
  const interaction = useComposerInteraction(session);
  useEffect(() => setNotice(null), [session.id]);
  const { submit, submitRef, popBackRef } = useComposerSubmit(session, interaction.answerInteractionId, setNotice, editorRef, composingRef);
  const composerKeymap = useComposerKeymap(effectiveKeymap, submitRef, popBackRef, callbackRef);
  useImperativeHandle(
    forwardedRef,
    () => ({
      submit: () => submitRef.current(), popBack: () => popBackRef.current(),
      focus: () => editorRef.current?.focus(), getDraft: () => editorRef.current?.state.doc.toString() ?? "", getElement: () => frameRef.current,
    }),
    [submitRef, popBackRef],
  );
  useComposerEditor({
    session, frameRef, editorRef, syncingDraftRef, composingRef, editable, profileCompartment,
    keymapCompartment, draft: store.draft, gate: store.gate, ariaLabel, effectiveKeymap, composerKeymap, callbackRef,
  });
  const { recoverWithdrawn, keepCurrentDraft } = useComposerRecovery(session, store.withdrawalRecovery, store.draft, setNotice, editorRef);
  const view = useComposerView({ session, store, interaction, queuedSetHint, effectiveKeymap, setNotice });
  const statusHandlers = useComposerStatusHandlers(session, store, setNotice);

  return (
    <ComposerView
      session={session} store={store} interaction={interaction} notice={notice}
      frameRef={frameRef} view={view} interrupt={interrupt} turnWorking={turnWorking}
      profile={effectiveKeymap.composerProfile} onSend={submit} onRetry={view.retry}
      onRecover={recoverWithdrawn} onKeep={keepCurrentDraft}
      onKeepWaiting={statusHandlers.onKeepWaiting}
      onRelease={statusHandlers.onRelease}
      onCopyRequestId={statusHandlers.onCopyRequestId}
    />
  );
});
