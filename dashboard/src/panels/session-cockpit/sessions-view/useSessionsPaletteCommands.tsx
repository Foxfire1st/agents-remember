// All palette-command registration for the sessions view, in one hook: launch, model/effort
// control, the structured-Chats stage toggles, the rail/attention/bulk-end/triage commands, and
// the exact-turn interrupt chord. The registry is owned by the view; this hook only registers.
import {
  useEffect,
  type Dispatch,
  type RefObject,
  type SetStateAction,
} from "react";

import { type CommandRegistry } from "../../../data/commands";
import {
  attentionRollup,
  buildRailModel,
  interactionPromptPreview,
  jumpToAttentionTarget,
  waitingSeats,
} from "../../../data/railModel";
import {
  sessionPendingInteractionPayload,
  type OpenSession,
} from "../../../data/sessions";
import {
  pendingInteractionAgentLabel,
} from "../../../data/interactionAnswer";
import { sessionCockpitStore } from "../../../data/sessionCockpitStore";
import { endLanded } from "../SessionRail";
import { type LaunchPrefill } from "../LaunchFlow";
import { useConversationInterrupt } from "../conversation/useConversationControls";

function useLaunchPaletteCommand(
  registry: CommandRegistry,
  setLaunch: (launch: { open: boolean; prefill?: LaunchPrefill }) => void,
): void {
  // The launch command — the palette is the flow's entry point (design §7.1).
  useEffect(() => {
    return registry.register({
      id: "session.launch",
      title: "Launch session…",
      keywords: ["launch", "new", "open", "harness", "model", "effort"],
      run: () => setLaunch({ open: true }),
    });
  }, [registry, setLaunch]);
}

function useModelEffortPaletteCommands(
  registry: CommandRegistry,
  focused: (OpenSession & { liveTurnWorking?: boolean }) | undefined,
  setControlPopoverOpen: (open: boolean) => void,
): void {
  // The ModelEffortControl's palette surface — the SAME popover the header trigger opens.
  useEffect(() => {
    const controlAvailable = () =>
      focused !== undefined &&
      focused.harness !== undefined &&
      (focused.status ?? "running") === "running";
    const disposers = [
      registry.register({
        id: "control.setModel",
        title: "Set model…",
        keywords: ["model", "switch", "change", "capability"],
        when: controlAvailable,
        run: () => setControlPopoverOpen(true),
      }),
      registry.register({
        id: "control.setEffort",
        title: "Set effort…",
        keywords: ["effort", "thinking", "reasoning", "capability"],
        when: controlAvailable,
        run: () => setControlPopoverOpen(true),
      }),
    ];
    return () => {
      for (const dispose of disposers) dispose();
    };
  }, [registry, focused, setControlPopoverOpen]);
}

function useChatsStagePaletteCommands(
  registry: CommandRegistry,
  focused: (OpenSession & { liveTurnWorking?: boolean }) | undefined,
  deps: {
    openChatsLibrary: () => void;
    closeChatsLibrary: () => void;
    toggleChatsDiagnostics: (open: boolean) => void;
    chatsInterruptRef: RefObject<ReturnType<typeof useConversationInterrupt>>;
    chatsLibraryOpenRef: RefObject<boolean>;
    chatsDiagnosticsOpenRef: RefObject<boolean>;
  },
): void {
  // The structured-Chats stage toggles are discoverable palette commands (design
  // §12.6) — browse native history in-stage, and the default-off terminal-diagnostics drawer. Both
  // gate on a focused controlled session.
  useEffect(() => {
    const controlled = () =>
      focused !== undefined &&
      focused.harness !== undefined &&
      (focused.status ?? "running") === "running";
    const disposers = [
      registry.register({
        id: "conversation.browseHistory",
        title: "Browse conversation history…",
        keywords: ["history", "browse", "prior", "resume", "open", "library"],
        when: controlled,
        run: () => deps.openChatsLibrary(),
      }),
      registry.register({
        id: "conversation.terminalDiagnostics",
        title: "Toggle terminal diagnostics",
        keywords: ["terminal", "diagnostics", "runner", "log", "pty"],
        when: controlled,
        run: () => deps.toggleChatsDiagnostics(!deps.chatsDiagnosticsOpenRef.current),
      }),
      // §4.4 return path: a palette return command consuming the same library focus token.
      registry.register({
        id: "conversation.backToChat",
        title: "Back to current chat",
        keywords: ["back", "return", "close history", "current chat"],
        when: () => deps.chatsLibraryOpenRef.current,
        run: () => deps.closeChatsLibrary(),
      }),
      // §9.5: the exact-turn interrupt — palette command + the Control+Shift+. chord both dispatch
      // this. `when` gates it to an interruptible working turn, so it never offers a dead stop.
      registry.register({
        id: "conversation.stop",
        title: "Stop turn",
        keywords: ["stop", "interrupt", "cancel", "turn", "abort"],
        chord: "ctrl+shift+.",
        when: () => deps.chatsInterruptRef.current.available,
        run: () => deps.chatsInterruptRef.current.onStop?.(),
      }),
    ];
    return () => {
      for (const dispose of disposers) dispose();
    };
  }, [registry, focused, deps]);
}

function registerTriageCommands(
  registry: CommandRegistry,
  disposers: Array<() => void>,
  sessions: OpenSession[],
  focusSession: (id: string | null) => void,
  rootRef: RefObject<HTMLDivElement | null>,
): void {
  for (const seat of waitingSeats(sessions)) {
      // Parent's singular slot first, else the first sub-agent entry; the title names WHO
      // asks when the payload carries the adapter-bound agent label.
      const payload = sessionPendingInteractionPayload(seat);
      const rawPreview = interactionPromptPreview(payload, 60);
      const asker = pendingInteractionAgentLabel(payload);
      const preview =
        rawPreview !== undefined && asker !== undefined ? `${asker}: ${rawPreview}` : rawPreview;
      disposers.push(
        registry.register({
          id: `triage.${seat.id}`,
          title: `Answer pending question — ${seat.label}${preview ? `: “${preview}”` : ""}`,
          keywords: ["answer", "question", "pending", "input"],
          // Answering was the user's explicit intent — focus the seat's InteractionBar
          // (the palette invoked it; this is the invoked action, not a focus steal).
          run: () => {
            focusSession(seat.id);
            window.requestAnimationFrame(() =>
              rootRef.current
                ?.querySelector<HTMLElement>(
                  '[data-testid="interaction-bar"] button',
                )
                ?.focus(),
            );
          },
        }),
      );
  }
}

function useRailPaletteCommands(
  registry: CommandRegistry,
  treeView: boolean,
  rollup: ReturnType<typeof attentionRollup>,
  sessions: OpenSession[],
  model: ReturnType<typeof buildRailModel>,
  focusSession: (id: string | null) => void,
  rootRef: RefObject<HTMLDivElement | null>,
): void {
  // Palette commands (dynamic titles carry the HONEST preview counts + names): the tree
  // toggle, jump-to-attention, bulk end at sprint + master level, and question triage.
  useEffect(() => {
    const disposers = [
      registry.register({
        id: "rail.treeToggle",
        title: treeView
          ? "Rail: show role hierarchy"
          : "Rail: show orchestration tree",
        keywords: ["tree", "spawn", "provenance", "hierarchy", "rail"],
        run: () =>
          sessionCockpitStore.getState().setOrchestrationTreeView(!treeView),
      }),
      registry.register({
        id: "attention.jump",
        title: "Jump to attention",
        keywords: ["attention", "next", "triage"],
        when: () => jumpToAttentionTarget(rollup, sessions) !== null,
        run: () => {
          const target = jumpToAttentionTarget(rollup, sessions);
          if (target) focusSession(target);
        },
      }),
    ];
    const allLanded = [
      ...model.masters.flatMap((master) => master.completed),
      ...model.completedUnattached,
    ];
    if (allLanded.length > 0) {
      disposers.push(
        registry.register({
          id: "sessions.endCompleted",
          title: `End ${allLanded.length} completed — sprint: ${allLanded.map((s) => s.label).join(", ")}`,
          keywords: ["end", "completed", "bulk", "cleanup"],
          run: () => void endLanded(allLanded),
        }),
      );
    }
    for (const master of model.masters) {
      if (master.completed.length === 0) continue;
      disposers.push(
        registry.register({
          id: `sessions.endDone.${master.key}`,
          title: `End ${master.completed.length} done — ${master.label}: ${master.completed.map((s) => s.label).join(", ")}`,
          keywords: ["end", "done", "bulk", master.label],
          run: () => void endLanded(master.completed),
        }),
      );
    }
    registerTriageCommands(registry, disposers, sessions, focusSession, rootRef);
    return () => {
      for (const dispose of disposers) dispose();
    };
  }, [registry, model, rollup, sessions, treeView, focusSession, rootRef]);
}

export function useSessionsPaletteCommands({
  registry,
  focused,
  setLaunch,
  setControlPopoverOpen,
  openChatsLibrary,
  closeChatsLibrary,
  toggleChatsDiagnostics,
  chatsInterruptRef,
  chatsLibraryOpenRef,
  chatsDiagnosticsOpenRef,
  treeView,
  rollup,
  sessions,
  model,
  focusSession,
  rootRef,
}: {
  registry: CommandRegistry;
  focused: (OpenSession & { liveTurnWorking?: boolean }) | undefined;
  setLaunch: Dispatch<SetStateAction<{ open: boolean; prefill?: LaunchPrefill }>>;
  setControlPopoverOpen: Dispatch<SetStateAction<boolean>>;
  openChatsLibrary: () => void;
  closeChatsLibrary: () => void;
  toggleChatsDiagnostics: (open: boolean) => void;
  chatsInterruptRef: RefObject<ReturnType<typeof useConversationInterrupt>>;
  chatsLibraryOpenRef: RefObject<boolean>;
  chatsDiagnosticsOpenRef: RefObject<boolean>;
  treeView: boolean;
  rollup: ReturnType<typeof attentionRollup>;
  sessions: OpenSession[];
  model: ReturnType<typeof buildRailModel>;
  focusSession: (id: string | null) => void;
  rootRef: RefObject<HTMLDivElement | null>;
}) {
  // The launch command — the palette is the flow's entry point (design §7.1).
  useLaunchPaletteCommand(registry, setLaunch);

  // The ModelEffortControl's palette surface — the SAME popover the header trigger opens.
  useModelEffortPaletteCommands(registry, focused, setControlPopoverOpen);

  // The structured-Chats stage toggles are discoverable palette commands (design
  // §12.6) — browse native history in-stage, and the default-off terminal-diagnostics drawer. Both
  // gate on a focused controlled session.
  useChatsStagePaletteCommands(registry, focused, {
    openChatsLibrary,
    closeChatsLibrary,
    toggleChatsDiagnostics,
    chatsInterruptRef,
    chatsLibraryOpenRef,
    chatsDiagnosticsOpenRef,
  });

  // Palette commands (dynamic titles carry the HONEST preview counts + names): the tree
  // toggle, jump-to-attention, bulk end at sprint + master level, and question triage.
  useRailPaletteCommands(
    registry,
    treeView,
    rollup,
    sessions,
    model,
    focusSession,
    rootRef,
  );
}
