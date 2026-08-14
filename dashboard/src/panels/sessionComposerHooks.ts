import { insertNewlineAndIndent } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { Compartment, EditorState, Prec } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { vim } from "@replit/codemirror-vim";
import { useCallback, useEffect, useMemo, useRef } from "react";

import {
  interactionAnswerIsLocked,
  representPendingInteraction,
  submitInteractionAnswer,
} from "../data/interactionAnswer";
import {
  keepWaitingForSubmit,
  releaseSubmitDraft,
  submissionGate,
  submitSessionDraft,
} from "../data/submitClient";
import { sessionCockpitStore, useSessionCockpit } from "../data/sessionCockpitStore";
import { latestActiveSubmit, serverConfirmedQueued } from "../data/submitMachine";
import {
  dismissWithdrawnRecovery,
  restoreWithdrawnRecovery,
  withdrawLastQueuedSubmission,
} from "../data/submissionWithdrawal";
import {
  bindingFor,
  codeMirrorBinding,
  keymapCommandIsActive,
  useEffectiveKeymap,
} from "../data/keymap/preferences";
import { retryRouteFailure } from "../data/submitClient";
import type { OpenSession } from "../data/sessions";
import { composerTheme } from "./sessionComposerStyles";

export interface ComposerCallbackRefs {
  onSlashAtLineStart?: () => void;
  onEscape?: () => void;
}

export function useComposerInteraction(session: OpenSession) {
  const cockpit = useSessionCockpit((state) => state.perSession[session.id]);
  const pendingInteraction = representPendingInteraction(
    session.controlPendingInteraction,
  );
  const answerInteractionId =
    pendingInteraction?.mode === "composer"
      ? pendingInteraction.view.interactionId
      : undefined;
  const answerMode = answerInteractionId !== undefined;
  const answerState = cockpit?.interactionAnswer;
  const matchingAnswerState =
    answerState?.interactionId === answerInteractionId
      ? answerState
      : undefined;
  const answerLocked = answerInteractionId
    ? interactionAnswerIsLocked(session.id, answerInteractionId)
    : false;
  return {
    answerInteractionId,
    answerMode,
    matchingAnswerState,
    answerLocked,
  };
}

export function useComposerStore(session: OpenSession) {
  const cockpit = useSessionCockpit((state) => state.perSession[session.id]);
  const draft = cockpit?.composer ?? { draft: "", draftRevision: 0 };
  const queue = cockpit?.queue ?? [];
  const submitHistory = cockpit?.submitHistory ?? [];
  // Queued-grace honesty: a bare queued receipt is usually already dispatching on the
  // claude path (withdraw answers not-withdrawable), so the queue surfaces — preview, the
  // "N queued · yours" count, the alt+↑ hint — show an entry only once the authority's own
  // lifecycle word confirms the pre-dispatch queued state on its submit record.
  const confirmedQueue = queue.filter((entry) =>
    serverConfirmedQueued(
      submitHistory.find((record) => record.requestId === entry.requestId),
    ),
  );
  const withdrawalRecovery =
    cockpit?.withdrawal?.phase === "recovery" ? cockpit.withdrawal : undefined;
  const composerHistory = submitHistory.filter(
    (record) => record.source === "composer",
  );
  const latest = composerHistory[composerHistory.length - 1];
  const activeSubmit = latestActiveSubmit(submitHistory);
  const gate = submissionGate(session);
  return {
    draft,
    confirmedQueue,
    withdrawalRecovery,
    latest,
    activeSubmit,
    gate,
  };
}

export function useComposerSubmit(
  session: OpenSession,
  answerInteractionId: string | undefined,
  setNotice: (notice: string | null) => void,
  editorRef: React.RefObject<EditorView | null>,
  composingRef: React.RefObject<boolean>,
) {
  const submit = useCallback(() => {
    const view = editorRef.current;
    if (view?.composing || composingRef.current) return;
    const current =
      sessionCockpitStore.getState().perSession[session.id]?.composer ?? {
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
      if (outcome.status !== "blocked") return;
      // Playwright-measured: a send deferred only because native control is still
      // booting used to echo the gate reason a second time — visually identical to the standing
      // gate line, so the press read as silence. Acknowledge it in the status family's own
      // vocabulary ("sending…"/"delivering…", "composer draft unchanged").
      setNotice(
        session.controlState === "starting" ||
          session.controlState === "disconnected"
          ? "connecting… · composer draft unchanged"
          : outcome.reason,
      );
    });
  }, [answerInteractionId, session, setNotice, editorRef, composingRef]);
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
  }, [session.id, setNotice, editorRef]);
  const popBackRef = useRef(popBack);
  popBackRef.current = popBack;
  return { submit, submitRef, popBack, popBackRef };
}

export function useComposerKeymap(
  effectiveKeymap: ReturnType<typeof useEffectiveKeymap>,
  submitRef: React.RefObject<() => void>,
  popBackRef: React.RefObject<() => void>,
  callbackRef: React.RefObject<ComposerCallbackRefs>,
) {
  return useMemo(
    () =>
      effectiveKeymap.bindings
        .filter((entry) => entry.zones.includes("composer"))
        .flatMap((entry) => {
          if (!keymapCommandIsActive(effectiveKeymap, entry.commandId)) return [];
          const run = (currentView: EditorView) => {
            if (currentView.composing) return false;
            if (entry.commandId === "composer.submit") submitRef.current();
            else if (entry.commandId === "composer.popBack") popBackRef.current();
            else if (entry.commandId === "focus.stageHeader") {
              callbackRef.current.onEscape?.();
            } else return false;
            return (
              entry.commandId !== "focus.stageHeader" ||
              callbackRef.current.onEscape !== undefined
            );
          };
          return [{ key: codeMirrorBinding(entry.chord), run }];
        })
        // With plain Enter bound to send, Shift+Enter is the explicit
        // newline — bound here at the same precedence so no profile/default can shadow it.
        .concat([{ key: "Shift-Enter", run: insertNewlineAndIndent }]),
    [effectiveKeymap, submitRef, popBackRef, callbackRef],
  );
}

function composerEditorExtensions({
  editable,
  profileCompartment,
  keymapCompartment,
  syncingDraftRef,
  composingRef,
  sessionId,
  callbackRef,
  initialEditableRef,
  initialAriaLabelRef,
  initialComposerProfileRef,
  initialComposerKeymapRef,
}: {
  editable: Compartment;
  profileCompartment: Compartment;
  keymapCompartment: Compartment;
  syncingDraftRef: React.RefObject<boolean>;
  composingRef: React.RefObject<boolean>;
  sessionId: string;
  callbackRef: React.RefObject<ComposerCallbackRefs>;
  initialEditableRef: React.RefObject<boolean>;
  initialAriaLabelRef: React.RefObject<string>;
  initialComposerProfileRef: React.RefObject<string>;
  initialComposerKeymapRef: React.RefObject<ReturnType<typeof useComposerKeymap>>;
}) {
  return [
    markdown(),
    EditorView.lineWrapping,
    composerTheme,
    editable.of(EditorView.editable.of(initialEditableRef.current)),
    EditorView.contentAttributes.of({
      "aria-label": initialAriaLabelRef.current,
      "data-focus-target": "true",
    }),
    EditorView.updateListener.of((update) => {
      if (!update.docChanged || syncingDraftRef.current) return;
      sessionCockpitStore
        .getState()
        .setComposerDraft(sessionId, update.state.doc.toString());
    }),
    profileCompartment.of(initialComposerProfileRef.current === "vim" ? vim() : []),
    // House commands stay above editor-profile keys. Escape is deliberately absent in
    // Vim mode so the plugin owns insert→normal; F6 remains the global focus escape.
    keymapCompartment.of(
      Prec.highest(keymap.of(initialComposerKeymapRef.current)),
    ),
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
        if (event.isComposing || currentView.composing || event.key !== "/") {
          return false;
        }
        const head = currentView.state.selection.main.head;
        const line = currentView.state.doc.lineAt(head);
        if (head !== line.from) return false;
        if (!callbackRef.current.onSlashAtLineStart) return false;
        event.preventDefault();
        callbackRef.current.onSlashAtLineStart();
        return true;
      },
    }),
  ];
}

function useComposerInitialRefs({
  draft,
  ariaLabel,
  gate,
  effectiveKeymap,
  composerKeymap,
}: {
  draft: { draft: string; draftRevision: number };
  ariaLabel: string;
  gate: { editable: boolean; ready: boolean; reason?: string };
  effectiveKeymap: ReturnType<typeof useEffectiveKeymap>;
  composerKeymap: ReturnType<typeof useComposerKeymap>;
}) {
  // The EditorView captures the configuration that exists at (re)creation time; live
  // draft/editable/profile/keymap updates flow through the compartments in the effects
  // below. Refs make that initial-only contract explicit to exhaustive-deps.
  const initialDraftRef = useRef(draft.draft);
  initialDraftRef.current = draft.draft;
  const initialAriaLabelRef = useRef(ariaLabel);
  initialAriaLabelRef.current = ariaLabel;
  const initialEditableRef = useRef(gate.editable);
  initialEditableRef.current = gate.editable;
  const initialComposerProfileRef = useRef(effectiveKeymap.composerProfile);
  initialComposerProfileRef.current = effectiveKeymap.composerProfile;
  const initialComposerKeymapRef = useRef(composerKeymap);
  initialComposerKeymapRef.current = composerKeymap;
  return {
    initialDraftRef,
    initialAriaLabelRef,
    initialEditableRef,
    initialComposerProfileRef,
    initialComposerKeymapRef,
  };
}

export function useComposerEditor({
  session, frameRef, editorRef, syncingDraftRef, composingRef, editable,
  profileCompartment, keymapCompartment, draft, gate, ariaLabel, effectiveKeymap,
  composerKeymap, callbackRef,
}: {
  session: OpenSession; frameRef: React.RefObject<HTMLDivElement | null>;
  editorRef: React.RefObject<EditorView | null>; syncingDraftRef: React.RefObject<boolean>;
  composingRef: React.RefObject<boolean>; editable: Compartment;
  profileCompartment: Compartment; keymapCompartment: Compartment;
  draft: { draft: string; draftRevision: number };
  gate: { editable: boolean; ready: boolean; reason?: string }; ariaLabel: string;
  effectiveKeymap: ReturnType<typeof useEffectiveKeymap>;
  composerKeymap: ReturnType<typeof useComposerKeymap>;
  callbackRef: React.RefObject<ComposerCallbackRefs>;
}) {
  const initial = useComposerInitialRefs({
    draft,
    ariaLabel,
    gate,
    effectiveKeymap,
    composerKeymap,
  });

  useEffect(() => {
    const parent = frameRef.current;
    if (!parent) return undefined;
    const view = new EditorView({
      parent,
      state: EditorState.create({
        doc: initial.initialDraftRef.current,
        extensions: composerEditorExtensions({
          editable,
          profileCompartment,
          keymapCompartment,
          syncingDraftRef,
          composingRef,
          sessionId: session.id,
          callbackRef,
          initialEditableRef: initial.initialEditableRef,
          initialAriaLabelRef: initial.initialAriaLabelRef,
          initialComposerProfileRef: initial.initialComposerProfileRef,
          initialComposerKeymapRef: initial.initialComposerKeymapRef,
        }),
      }),
    });
    editorRef.current = view;
    return () => {
      editorRef.current = null;
      view.destroy();
    };
    // Recreate only when the session identity changes; draft/control updates use compartments.
  }, [
    session.id, editable, profileCompartment, keymapCompartment, frameRef, editorRef,
    syncingDraftRef, composingRef, callbackRef,
    initial.initialDraftRef, initial.initialEditableRef, initial.initialAriaLabelRef,
    initial.initialComposerProfileRef, initial.initialComposerKeymapRef,
  ]);

  return useComposerEditorSync({
    draft,
    editorRef,
    syncingDraftRef,
    editable,
    gate,
    effectiveKeymap,
    composerKeymap,
    profileCompartment,
    keymapCompartment,
  });
}

function useComposerEditorSync({
  draft,
  editorRef,
  syncingDraftRef,
  editable,
  gate,
  effectiveKeymap,
  composerKeymap,
  profileCompartment,
  keymapCompartment,
}: {
  draft: { draft: string; draftRevision: number };
  editorRef: React.RefObject<EditorView | null>;
  syncingDraftRef: React.RefObject<boolean>;
  editable: Compartment;
  gate: { editable: boolean; ready: boolean; reason?: string };
  effectiveKeymap: ReturnType<typeof useEffectiveKeymap>;
  composerKeymap: ReturnType<typeof useComposerKeymap>;
  profileCompartment: Compartment;
  keymapCompartment: Compartment;
}) {
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
  }, [draft.draft, editorRef, syncingDraftRef]);

  useEffect(() => {
    editorRef.current?.dispatch({
      effects: editable.reconfigure(EditorView.editable.of(gate.editable)),
    });
  }, [editable, gate.editable, editorRef]);

  useEffect(() => {
    editorRef.current?.dispatch({
      effects: [
        profileCompartment.reconfigure(
          effectiveKeymap.composerProfile === "vim" ? vim() : [],
        ),
        // Reconfiguration also applies live user overrides without recreating the editor/draft.
        keymapCompartment.reconfigure(
          Prec.highest(keymap.of(composerKeymap)),
        ),
      ],
    });
  }, [
    composerKeymap,
    effectiveKeymap.composerProfile,
    keymapCompartment,
    profileCompartment,
    editorRef,
  ]);
}

export function useComposerRecovery(
  session: OpenSession,
  withdrawalRecovery: ReturnType<typeof useComposerStore>["withdrawalRecovery"],
  draft: { draft: string; draftRevision: number },
  setNotice: (notice: string | null) => void,
  editorRef: React.RefObject<EditorView | null>,
) {
  const recoverWithdrawn = useCallback(() => {
    if (!withdrawalRecovery) return;
    const expectedRevision =
      sessionCockpitStore.getState().perSession[session.id]?.composer
        .draftRevision ?? 0;
    const nextNotice = restoreWithdrawnRecovery(
      session.id,
      withdrawalRecovery.requestId,
      expectedRevision,
    );
    setNotice(nextNotice);
    if (nextNotice.includes("restored for editing")) {
      window.requestAnimationFrame(() => editorRef.current?.focus());
    }
  }, [withdrawalRecovery, session.id, setNotice, editorRef]);

  const keepCurrentDraft = useCallback(() => {
    if (!withdrawalRecovery) return;
    setNotice(
      dismissWithdrawnRecovery(
        session.id,
        withdrawalRecovery.requestId,
        draft.draftRevision,
      ),
    );
  }, [withdrawalRecovery, session.id, draft.draftRevision, setNotice]);
  return { recoverWithdrawn, keepCurrentDraft };
}

export function useComposerView({
  session,
  store,
  interaction,
  queuedSetHint,
  effectiveKeymap,
  setNotice,
}: {
  session: OpenSession;
  store: ReturnType<typeof useComposerStore>;
  interaction: ReturnType<typeof useComposerInteraction>;
  queuedSetHint: string | null | undefined;
  effectiveKeymap: ReturnType<typeof useEffectiveKeymap>;
  setNotice: (notice: string | null) => void;
}) {
  // Group the hint by concern with ONE separator convention (interpunct)
  // and move the honest-boundary capability wall into a tooltip (progressive disclosure). The
  // boundary copy stays present — structured, not a mixed-separator wall.
  // On a legacy-raw terminal seat native submission is unsupported (typing bypasses the
  // /submit queue), so the markdown/reliable-submit/text-only claims contradict the pane — derive the
  // hint from the seat instead of stating a controlled-composer capability that does not apply here.
  // `draft saved` is an exception cue, shown only when a non-empty draft actually exists.
  // The footer shows EXCEPTION CUES ONLY
  // (draft saved, queued counts). Static capability facts (markdown, keymap profile, reliable
  // submit, text only) are one-fact-one-place: they live in the tooltip below and the keys
  // reference, never as standing chrome. Raw terminal seats no longer mount this composer.
  const rawTerminalSeat = session.kind === "terminal";
  const footerHint = (
    rawTerminalSeat
      ? []
      : [
          store.draft.draft.length > 0 ? "draft saved" : null,
          queuedSetHint,
          store.confirmedQueue.length > 0
            ? `${store.confirmedQueue.length} queued · yours`
            : null,
        ]
  )
    .filter(Boolean)
    .join(" · ");
  const capabilityDetail =
    `markdown · ${effectiveKeymap.composerProfile} keys · text only · ` +
    "reliable path: receipts + reconcile · terminal lines join the same queue without receipts";

  const retry = () => {
    if (store.latest?.phase !== "route-error") return;
    void retryRouteFailure(
      session.id,
      store.latest.requestId,
      store.draft.draft,
    ).then((outcome) => {
      if (outcome.notice) setNotice(outcome.notice);
    });
  };
  const keyLabel =
    bindingFor(effectiveKeymap, "composer.submit")?.label ?? "ctrl+↵";
  const sendDisabled =
    store.draft.draft.length === 0 ||
    (interaction.answerMode
      ? interaction.answerLocked
      : store.activeSubmit !== undefined || !store.gate.ready);
  return { footerHint, capabilityDetail, retry, keyLabel, sendDisabled };
}

export function useComposerStatusHandlers(
  session: OpenSession,
  store: ReturnType<typeof useComposerStore>,
  setNotice: (notice: string | null) => void,
) {
  const onKeepWaiting = useCallback(() => {
    if (store.latest) {
      void keepWaitingForSubmit(session.id, store.latest.requestId);
    }
  }, [store.latest, session.id]);
  const onRelease = useCallback(() => {
    if (store.latest) {
      releaseSubmitDraft(session.id, store.latest.requestId);
    }
  }, [store.latest, session.id]);
  const onCopyRequestId = useCallback(() => {
    void navigator.clipboard
      .writeText(store.latest?.requestId ?? "")
      .catch(() => setNotice("could not copy requestId"));
  }, [store.latest, setNotice]);
  return { onKeepWaiting, onRelease, onCopyRequestId };
}
