// The cockpit shell's navigation actions: the Operations document opener, the bell's chat
// target, and the one callback set the shell's pages use. Kept out of the shell component so
// each action stays a small unit and the shell keeps its layout responsibilities.
import { useCallback, useRef } from 'react';

import type { TaskArtifactReaderTarget as NotesReaderTarget } from '../data/taskArtifacts';
import type { ChangeSetTarget } from '../panels/changeset/ChangeSetViewer';
import { seatChatTarget, type AgentChatTarget } from './roleChatTarget';
import type { AttentionAgentTarget } from '../panels/AttentionQueue';
import { useTabChange, type TabMemory } from './tabMemory';

function useOpenDocument<T extends string>(
  setChangeSet: (target: ChangeSetTarget | null) => void,
  setNotesOpen: (open: boolean) => void,
  setSelectedId: (id: string | null) => void,
  setView: (view: T) => void,
): (id: string) => void {
  return useCallback(
    (id: string) => {
      setChangeSet(null);
      setNotesOpen(false);
      setSelectedId(id);
      setView('operations' as T);
    },
    [setChangeSet, setNotesOpen, setSelectedId, setView],
  );
}

// The bell's way into a chat (L190-R1-F09). It is the shell's one agent navigation: the row's
// exact seat becomes the Chats view's request, and the view it needs is Chats — through the same
// `changeView` every tab uses. The request is what the Chats pane's frame target is built from;
// nothing here writes the observed-chat channel, so the chat on screen changes only when the
// frame answers that it shows the agent.
function useOpenAgentChat<T extends string>(
  changeView: (next: T) => void,
  setAgentChat: (target: AgentChatTarget | null) => void,
): (seat: AttentionAgentTarget) => void {
  // One identity for the whole session: the handler crosses two memo gates (the rail's list and
  // the bell panel), so a function rebuilt per render would re-render both on every shell state
  // change — the tab-switch cost the 260721 memo gate exists to prevent.
  const latest = useRef({ changeView, setAgentChat });
  latest.current = { changeView, setAgentChat };
  return useCallback((seat: AttentionAgentTarget) => {
    latest.current.setAgentChat(seatChatTarget(seat));
    latest.current.changeView('chats' as T);
  }, []);
}

export function useCockpitShellActions<T extends string>(
  setChatRail: (value: boolean) => void,
  setChangeSet: (target: ChangeSetTarget | null) => void,
  setNotes: (
    target:
      NotesReaderTarget | null | ((cur: NotesReaderTarget | null) => NotesReaderTarget | null),
  ) => void,
  setNotesOpen: (open: boolean) => void,
  setView: (view: T) => void,
  setSelectedId: (id: string | null) => void,
  setAgentChat: (target: AgentChatTarget | null) => void,
  tab: TabMemory<T>,
): {
  setRailView: (next: 'river' | 'chat') => void;
  open: (id: string) => void;
  openAgentChat: (seat: AttentionAgentTarget) => void;
  changeView: (next: T) => void;
  openChangeSet: (target: ChangeSetTarget) => void;
  openNotes: (target: NotesReaderTarget) => void;
  closeChangeSet: () => void;
  closeNotes: () => void;
  selectNote: (path: string) => void;
} {
  const setRailView = useCallback(
    (next: 'river' | 'chat') => setChatRail(next === 'chat'),
    [setChatRail],
  );
  const open = useOpenDocument(setChangeSet, setNotesOpen, setSelectedId, setView);
  const changeView = useTabChange(tab, setChangeSet, setNotesOpen, setView);
  const openAgentChat = useOpenAgentChat(changeView, setAgentChat);
  const openChangeSet = useCallback(
    (target: ChangeSetTarget) => {
      setNotesOpen(false);
      setChangeSet(target);
    },
    [setChangeSet, setNotesOpen],
  );
  const openNotes = useCallback(
    (target: NotesReaderTarget) => {
      setChangeSet(null);
      setNotes(target);
      setNotesOpen(true);
    },
    [setChangeSet, setNotes, setNotesOpen],
  );
  const closeChangeSet = useCallback(() => setChangeSet(null), [setChangeSet]);
  const closeNotes = useCallback(() => setNotesOpen(false), [setNotesOpen]);
  const selectNote = useCallback(
    (path: string) =>
      setNotes((cur) => {
        // A note selection leaves the folder-open state: the reader shows the note.
        if (cur?.kind === 'notes') return { ...cur, path, folder: false };
        if (cur?.kind === 'requirements') return { ...cur, path };
        return cur;
      }),
    [setNotes],
  );
  return {
    setRailView,
    open,
    openAgentChat,
    changeView,
    openChangeSet,
    openNotes,
    closeChangeSet,
    closeNotes,
    selectNote,
  };
}
