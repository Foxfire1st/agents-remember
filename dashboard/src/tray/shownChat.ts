// What the shell shows, as far as the tray needs it (MIK-R188 rules 3, 7 and 9).
//
// - `chat`: the chat the chat column shows. The shell sets it from the column's frame while the
//   column is open on a live chat, and sets null for a placeholder, a closed column and pages
//   without a chat column. An object pick makes this chat the tray's default target (rule 7).
// - `page`: the page's name, written into a highlighted passage's provenance ("from Operations").
// - `repository`: the page's repository. A target without a task files its message under it.
import { useEffect } from 'react';
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

export interface ShownChat {
  agentId: string;
  // The chat's name in the tray, for example "Manager · 260928-MIK". Optional: the seat list
  // names it when the shell does not.
  label?: string;
}

interface ShownState {
  chat: ShownChat | null;
  page: string | null;
  repository: string | null;
}

export const shownStore = createStore<ShownState>(() => ({ chat: null, page: null, repository: null }));

export function setShownChat(chat: ShownChat | null): void {
  const current = shownStore.getState().chat;
  if (current?.agentId === chat?.agentId && current?.label === chat?.label) return;
  shownStore.setState({ chat });
}

export function setShownPage(page: string | null, repository?: string | null): void {
  const current = shownStore.getState();
  const nextRepository = repository === undefined ? current.repository : repository;
  if (current.page === page && current.repository === nextRepository) return;
  shownStore.setState({ page, repository: nextRepository });
}

export const shownChatId = (): string | null => shownStore.getState().chat?.agentId ?? null;

export const useShownChat = (): ShownChat | null => useStore(shownStore, (state) => state.chat);

// For a chat column: publishes the chat it shows while it shows one (null for a placeholder or a
// hidden column). Two columns publish (the rail's and the Chats page's); each takes back only its
// own chat, so whichever is shown stands, whatever order their effects run in.
export function usePublishShownChat(chat: ShownChat | null): void {
  const agentId = chat?.agentId ?? null;
  const label = chat?.label;
  useEffect(() => {
    if (!agentId) return;
    setShownChat({ agentId, ...(label ? { label } : {}) });
    return () => {
      if (shownStore.getState().chat?.agentId === agentId) setShownChat(null);
    };
  }, [agentId, label]);
}

// For the shell and the pages that know their repository: the page's name and repository.
// `undefined` leaves the repository as a page set it (the File Viewer names its own).
export function usePublishShownPage(page: string | null, repository: string | null | undefined): void {
  useEffect(() => setShownPage(page, repository), [page, repository]);
}

// For a page that names its own repository while it is shown (the File Viewer's Repo picker).
export function usePublishPageRepository(page: string, repository: string | null, shown: boolean): void {
  useEffect(() => {
    if (shown) setShownPage(page, repository);
  }, [page, repository, shown]);
}
