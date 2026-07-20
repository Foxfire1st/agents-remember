import { markdown } from "@codemirror/lang-markdown";
import { Compartment, EditorState, Prec } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { vim } from "@replit/codemirror-vim";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { Button } from "react-aria-components";

import { css } from "../../styled-system/css";
import {
  interactionAnswerIsLocked,
  representPendingInteraction,
  submitInteractionAnswer,
} from "../data/interactionAnswer";
import {
  keepWaitingForSubmit,
  releaseSubmitDraft,
  retryRouteFailure,
  submissionGate,
  submitSessionDraft,
} from "../data/submitClient";
import { sessionCockpitStore, useSessionCockpit } from "../data/sessionCockpitStore";
import { latestActiveSubmit } from "../data/submitMachine";
import {
  dismissWithdrawnRecovery,
  restoreWithdrawnRecovery,
  withdrawLastQueuedSubmission,
} from "../data/submissionLifecycleClient";
import {
  bindingFor,
  codeMirrorBinding,
  keymapCommandIsActive,
  useEffectiveKeymap,
} from "../data/keymap/preferences";
import type { OpenSession } from "../data/sessions";
import { QueuePreview } from "./session-cockpit/QueuePreview";
import {
  INTERACTION_ANSWERED,
  INTERACTION_ANSWERING,
  INTERACTION_COMPOSER_MODE,
} from "./session-cockpit/lifecycleCopy";

// One CM6 markdown composer for every session surface (FEUI-L5): per-session drafts live in the
// cockpit store, Ctrl+Enter submits through /submit, Enter remains a newline, and Alt+Up performs
// an epoch-bound atomic server withdrawal before restoring text. No component here owns a
// PTY connection or paste helper; raw xterm typing remains the only raw-stdin path.

const dock = css({
  display: "grid",
  gap: "0.28rem",
  flexShrink: 0,
  minWidth: "0",
});
const editorFrame = css({
  minWidth: "0",
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  overflow: "hidden",
  "&[data-answer-mode='true']": { borderColor: "amber" },
  "&[data-disabled='true']": { opacity: "0.62" },
  "& .cm-editor": { minHeight: "2.55rem", maxHeight: "8rem" },
  "& .cm-scroller": { maxHeight: "8rem", overflowY: "auto" },
  "& .cm-content": { minHeight: "2.55rem" },
  "& .cm-focused": {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "-1px",
  },
});
const footer = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.55rem",
  minWidth: "0",
  color: "muted",
  fontSize: "0.66rem",
});
const footerLeft = css({ minWidth: "0", flex: "1", overflowWrap: "anywhere" });
const sendButton = css({
  font: "inherit",
  fontSize: "0.68rem",
  letterSpacing: "0.03em",
  paddingInline: "0.55rem",
  paddingBlock: "0.12rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  color: "amber",
  background: "transparent",
  cursor: "pointer",
  _hover: {
    background: "color-mix(in oklch, token(colors.amber) 10%, transparent)",
  },
  _active: { transform: "scale(0.97)" },
  _focusVisible: {
    outline: "1px solid token(colors.amber)",
    outlineOffset: "1px",
  },
  _disabled: { opacity: "0.45", cursor: "default", transform: "none" },
});
const status = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.45rem",
  flexWrap: "wrap",
  minWidth: "0",
  fontSize: "0.68rem",
  color: "amber",
});
const error = css({ color: "alarm", overflowWrap: "anywhere" });
const secondaryButton = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  background: "transparent",
  cursor: "pointer",
  paddingInline: "0.4rem",
  _hover: { color: "amber", borderColor: "amber" },
});
const recoveryText = css({
  maxHeight: "4rem",
  maxWidth: "100%",
  overflow: "auto",
  whiteSpace: "pre-wrap",
  color: "fg",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "amber",
  paddingLeft: "0.4rem",
});

const composerTheme = EditorView.theme({
  "&": {
    color: "var(--ink)",
    backgroundColor: "transparent",
    fontFamily: "var(--font-mono)",
    fontSize: "0.8rem",
  },
  ".cm-content": { padding: "0.38rem 0.5rem", caretColor: "var(--amber)" },
  ".cm-line": { padding: "0" },
  ".cm-gutters": { display: "none" },
  ".cm-cursor": { borderLeftColor: "var(--amber)" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
    backgroundColor: "color-mix(in oklch, var(--cyan) 24%, transparent)",
  },
});

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
}

export const SessionComposer = forwardRef<SessionComposerHandle, SessionComposerProps>(
  function SessionComposer(
    {
      session,
      queuedSetHint,
      onSlashAtLineStart,
      onEscape,
      ariaLabel = `Message ${session.label}`,
    },
    forwardedRef,
  ) {
    const frameRef = useRef<HTMLDivElement>(null);
    const editorRef = useRef<EditorView | null>(null);
    const syncingDraftRef = useRef(false);
    const composingRef = useRef(false);
    const editable = useMemo(() => new Compartment(), [session.id]);
    const profileCompartment = useMemo(() => new Compartment(), [session.id]);
    const keymapCompartment = useMemo(() => new Compartment(), [session.id]);
    const effectiveKeymap = useEffectiveKeymap();
    const callbackRef = useRef({ onSlashAtLineStart, onEscape });
    callbackRef.current = { onSlashAtLineStart, onEscape };
    const [notice, setNotice] = useState<string | null>(null);
    const cockpit = useSessionCockpit((state) => state.perSession[session.id]);
    const draft = cockpit?.composer ?? { draft: "", draftRevision: 0 };
    const queue = cockpit?.queue ?? [];
    const submitHistory = cockpit?.submitHistory ?? [];
    const withdrawalRecovery =
      cockpit?.withdrawal?.phase === "recovery" ? cockpit.withdrawal : undefined;
    const composerHistory = submitHistory.filter((record) => record.source === "composer");
    const latest = composerHistory[composerHistory.length - 1];
    const activeSubmit = latestActiveSubmit(submitHistory);
    const gate = submissionGate(session);
    const pendingInteraction = representPendingInteraction(session.controlPendingInteraction);
    const answerInteractionId =
      pendingInteraction?.mode === "composer" ? pendingInteraction.view.interactionId : undefined;
    const answerMode = answerInteractionId !== undefined;
    const answerState = cockpit?.interactionAnswer;
    const matchingAnswerState =
      answerState?.interactionId === answerInteractionId ? answerState : undefined;
    const answerLocked = answerInteractionId
      ? interactionAnswerIsLocked(session.id, answerInteractionId)
      : false;

    useEffect(() => setNotice(null), [session.id]);

    const submit = useCallback(() => {
      const view = editorRef.current;
      if (view?.composing || composingRef.current) return;
      const current = sessionCockpitStore.getState().perSession[session.id]?.composer ?? {
        draft: "",
        draftRevision: 0,
      };
      if (current.draft.length === 0) return;
      setNotice(null);
      if (answerInteractionId) {
        void submitInteractionAnswer({
          session,
          interactionId: answerInteractionId,
          answer: current.draft,
          draftRevision: current.draftRevision,
        });
        return;
      }
      void submitSessionDraft(session.id).then((outcome) => {
        if (outcome.status === "blocked") setNotice(outcome.reason);
      });
    }, [answerInteractionId, session]);
    const submitRef = useRef(submit);
    submitRef.current = submit;

    const popBack = useCallback(() => {
      setNotice("withdrawing queued message…");
      void withdrawLastQueuedSubmission(session.id).then((nextNotice) => {
        setNotice(nextNotice);
        if (nextNotice.includes("restored for editing")) {
          window.requestAnimationFrame(() => editorRef.current?.focus());
        }
      });
    }, [session.id]);
    const popBackRef = useRef(popBack);
    popBackRef.current = popBack;

    const composerKeymap = useMemo(
      () =>
        effectiveKeymap.bindings
          .filter((entry) => entry.zones.includes("composer"))
          .flatMap((entry) => {
            if (!keymapCommandIsActive(effectiveKeymap, entry.commandId)) return [];
            const run = (currentView: EditorView) => {
              if (currentView.composing || composingRef.current) return false;
              if (entry.commandId === "composer.submit") submitRef.current();
              else if (entry.commandId === "composer.popBack") popBackRef.current();
              else if (entry.commandId === "focus.stageHeader") callbackRef.current.onEscape?.();
              else return false;
              return entry.commandId !== "focus.stageHeader" || callbackRef.current.onEscape !== undefined;
            };
            return [{ key: codeMirrorBinding(entry.chord), run }];
          }),
      [effectiveKeymap.signature],
    );

    useImperativeHandle(
      forwardedRef,
      () => ({
        submit: () => submitRef.current(),
        popBack: () => popBackRef.current(),
        focus: () => editorRef.current?.focus(),
        getDraft: () => editorRef.current?.state.doc.toString() ?? "",
        getElement: () => frameRef.current,
      }),
      [],
    );

    useEffect(() => {
      const parent = frameRef.current;
      if (!parent) return undefined;
      const view = new EditorView({
        parent,
        state: EditorState.create({
          doc: draft.draft,
          extensions: [
            markdown(),
            EditorView.lineWrapping,
            composerTheme,
            editable.of(EditorView.editable.of(gate.editable)),
            EditorView.contentAttributes.of({
              "aria-label": ariaLabel,
              "data-focus-target": "true",
            }),
            EditorView.updateListener.of((update) => {
              if (!update.docChanged || syncingDraftRef.current) return;
              sessionCockpitStore
                .getState()
                .setComposerDraft(session.id, update.state.doc.toString());
            }),
            profileCompartment.of(
              effectiveKeymap.composerProfile === "vim" ? vim() : [],
            ),
            // House commands stay above editor-profile keys. Escape is deliberately absent in
            // Vim mode so the plugin owns insert→normal; F6 remains the global focus escape.
            keymapCompartment.of(Prec.highest(keymap.of(composerKeymap))),
            EditorView.domEventHandlers({
              compositionstart() {
                composingRef.current = true;
                return false;
              },
              compositionend() {
                composingRef.current = false;
                return false;
              },
              keydown(event, currentView) {
                if (event.isComposing || currentView.composing || event.key !== "/") return false;
                const head = currentView.state.selection.main.head;
                const line = currentView.state.doc.lineAt(head);
                if (head !== line.from) return false;
                if (!callbackRef.current.onSlashAtLineStart) return false;
                event.preventDefault();
                callbackRef.current.onSlashAtLineStart();
                return true;
              },
            }),
          ],
        }),
      });
      editorRef.current = view;
      return () => {
        editorRef.current = null;
        view.destroy();
      };
      // Recreate only when the session identity changes; draft/control updates use compartments.
    }, [session.id, editable, profileCompartment, keymapCompartment]);

    useEffect(() => {
      const view = editorRef.current;
      if (!view) return;
      const current = view.state.doc.toString();
      if (current === draft.draft) return;
      syncingDraftRef.current = true;
      try {
        view.dispatch({
          changes: { from: 0, to: current.length, insert: draft.draft },
        });
      } finally {
        syncingDraftRef.current = false;
      }
    }, [draft.draft]);

    useEffect(() => {
      editorRef.current?.dispatch({
        effects: editable.reconfigure(EditorView.editable.of(gate.editable)),
      });
    }, [editable, gate.editable]);

    useEffect(() => {
      editorRef.current?.dispatch({
        effects: [
          profileCompartment.reconfigure(
            effectiveKeymap.composerProfile === "vim" ? vim() : [],
          ),
          // Reconfiguration also applies live user overrides without recreating the editor/draft.
          keymapCompartment.reconfigure(Prec.highest(keymap.of(composerKeymap))),
        ],
      });
    }, [composerKeymap, effectiveKeymap.composerProfile, keymapCompartment, profileCompartment]);

    // 260718-CHATS-L4 finding A3: group the hint by concern with ONE separator convention (interpunct)
    // and move the honest-boundary capability wall into a tooltip (progressive disclosure). The
    // boundary copy stays present — structured, not a mixed-separator wall.
    const footerHint = [
      "markdown",
      `${effectiveKeymap.composerProfile} keys`,
      "draft saved",
      queuedSetHint,
      queue.some((entry) => entry.state === "queued")
        ? `${queue.filter((entry) => entry.state === "queued").length} queued · yours`
        : null,
    ]
      .filter(Boolean)
      .join(" · ");
    const RELIABLE_SUBMIT_DETAIL =
      "reliable path: receipts + reconcile · terminal lines join the same queue without receipts";

    const retry = () => {
      if (latest?.phase !== "route-error") return;
      void retryRouteFailure(session.id, latest.requestId, draft.draft).then((outcome) => {
        if (outcome.notice) setNotice(outcome.notice);
      });
    };
    const recoverWithdrawn = () => {
      if (!withdrawalRecovery) return;
      const expectedRevision =
        sessionCockpitStore.getState().perSession[session.id]?.composer.draftRevision ?? 0;
      const nextNotice = restoreWithdrawnRecovery(
        session.id,
        withdrawalRecovery.requestId,
        expectedRevision,
      );
      setNotice(nextNotice);
      if (nextNotice.includes("restored for editing")) {
        window.requestAnimationFrame(() => editorRef.current?.focus());
      }
    };
    const keepCurrentDraft = () => {
      if (!withdrawalRecovery) return;
      setNotice(
        dismissWithdrawnRecovery(session.id, withdrawalRecovery.requestId, draft.draftRevision),
      );
    };

    return (
      <div className={dock} data-testid="session-composer">
        <QueuePreview queue={queue} />
        <div
          ref={frameRef}
          className={editorFrame}
          data-kbzone="composer"
          data-answer-mode={answerMode ? "true" : undefined}
          data-disabled={!gate.editable ? "true" : undefined}
          data-composer-profile={effectiveKeymap.composerProfile}
          data-testid="session-composer-editor"
        />
        {answerMode ? (
          <div
            className={status}
            role={matchingAnswerState?.error ? "alert" : "status"}
            data-testid="session-composer-answer-mode"
          >
            <span>{INTERACTION_COMPOSER_MODE}</span>
            {matchingAnswerState?.inflight ? <span>{INTERACTION_ANSWERING}</span> : null}
            {matchingAnswerState?.answeredAt !== undefined ? (
              <span>{INTERACTION_ANSWERED}</span>
            ) : null}
            {matchingAnswerState?.error ? (
              <span className={error}>{matchingAnswerState.error}</span>
            ) : null}
          </div>
        ) : !gate.ready ? (
          <div className={status} role="status" data-testid="session-composer-gate">
            {gate.reason}
          </div>
        ) : null}
        {latest || notice || withdrawalRecovery ? (
          <div
            className={status}
            role={
              ["rejected", "route-error", "generation-lost", "not-found"].includes(
                latest?.phase ?? "",
              )
                ? "alert"
                : undefined
            }
            data-testid="session-composer-status"
          >
            {notice ? <span>{notice}</span> : null}
            {latest?.phase === "sending" ? <span>sending…</span> : null}
            {latest?.phase === "accepted" ? (
              <span>
                delivered ·{" "}
                {latest.clearDraftOnAccept ? "draft released" : "composer draft unchanged"}
              </span>
            ) : null}
            {latest?.phase === "queued" ? (
              <span>
                queued · withdrawable ·{" "}
                {latest.clearDraftOnAccept ? "draft released" : "composer draft unchanged"}
              </span>
            ) : null}
            {latest?.phase === "delivering" ? <span>delivering…</span> : null}
            {latest?.phase === "withdrawn" ? (
              <span>
                {latest.recoveryDismissedAt === undefined
                  ? "withdrawn before dispatch"
                  : "withdrawn before dispatch · current draft kept; withdrawn text dismissed"}
              </span>
            ) : null}
            {withdrawalRecovery ? (
              <>
                <span data-testid="withdrawn-recovery">
                  newer draft preserved · withdrawn message retained
                </span>
                <span className={recoveryText} data-testid="withdrawn-recovery-text">
                  {withdrawalRecovery.text}
                </span>
                <button
                  type="button"
                  className={secondaryButton}
                  data-testid="withdrawn-recovery-replace"
                  onClick={recoverWithdrawn}
                >
                  replace current draft with withdrawn text
                </button>
                <button
                  type="button"
                  className={secondaryButton}
                  data-testid="withdrawn-recovery-keep-current"
                  onClick={keepCurrentDraft}
                >
                  keep current draft
                </button>
              </>
            ) : null}
            {latest?.phase === "generation-lost" ? (
              <span className={error}>
                runner generation changed · retained text was not resent
              </span>
            ) : null}
            {latest?.phase === "not-found" ? (
              <span className={error}>
                submission not retained by this authority · withdrawal unavailable · draft was not
                restored
              </span>
            ) : null}
            {latest?.phase === "ambiguous" || latest?.phase === "reconciling" ? (
              <span>ambiguous · reconciling the same requestId…</span>
            ) : null}
            {latest?.phase === "rejected" || latest?.phase === "unsupported" ? (
              <span className={error}>{latest.detail ?? latest.phase}</span>
            ) : null}
            {latest?.phase === "route-error" ? (
              <>
                <span className={error}>{latest.detail ?? "submit route failed"}</span>
                <button type="button" className={secondaryButton} onClick={retry}>
                  retry same id
                </button>
              </>
            ) : null}
            {latest?.phase === "endgame" ? (
              <>
                <span className={error}>still unresolved</span>
                <button
                  type="button"
                  className={secondaryButton}
                  onClick={() => void keepWaitingForSubmit(session.id, latest.requestId)}
                >
                  keep waiting
                </button>
                <button
                  type="button"
                  className={secondaryButton}
                  onClick={() => {
                    void navigator.clipboard
                      .writeText(latest.requestId)
                      .catch(() => setNotice("could not copy requestId"));
                  }}
                >
                  copy requestId
                </button>
                <button
                  type="button"
                  className={secondaryButton}
                  onClick={() => releaseSubmitDraft(session.id, latest.requestId)}
                >
                  release draft
                </button>
              </>
            ) : null}
          </div>
        ) : null}
        <div className={footer}>
          <span className={footerLeft}>
            {footerHint}
            {" · "}
            <span title={RELIABLE_SUBMIT_DETAIL} data-testid="composer-reliability-note">
              reliable submit
            </span>
            {" · text only"}
          </span>
          <Button
            className={sendButton}
            isDisabled={
              draft.draft.length === 0 ||
              (answerMode ? answerLocked : activeSubmit !== undefined || !gate.ready)
            }
            onPress={submit}
            data-testid="session-composer-send"
          >
            {answerMode
              ? "send answer"
              : `${bindingFor(effectiveKeymap, "composer.submit")?.label ?? "ctrl+↵"} send`}
          </Button>
        </div>
      </div>
    );
  },
);
