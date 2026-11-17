// The bottom tab's memory: what Operations was showing when another tab took the screen, and
// whether that view can still be shown. Kept out of the cockpit shell so the tab rule is one
// small unit with one test surface.
import { useCallback, useRef } from 'react';

import { dashboardStore } from '../data/store';
import type { TaskArtifactReaderTarget as NotesReaderTarget } from '../data/taskArtifacts';
import type { ChangeSetTarget } from '../panels/changeset/ChangeSetViewer';
import { dirName } from '../panels/detail-panel/model';
import type { EnclosureNode, TaskEntry } from '../types/projection';
import { reviewChatTask } from './reviewChat';

export interface TabMemory<T extends string = string> {
  view: T;
  changeSet: ChangeSetTarget | null;
  notes: NotesReaderTarget | null;
  notesOpen: boolean;
  address: { park: () => void; unpark: () => void };
}

// A bottom tab change. Every tab keeps the page it was last on: Chats, Knowledge and the
// File Viewer stay mounted, and so does the view Operations last showed — the task document, an
// open change set (the diff viewer, the intent review) or the notes reader — hidden while another
// tab is on screen; returning restores it, its file and its filter. A view whose task the task
// list no longer holds is gone, and Operations falls back to the task document. Pressing
// Operations on Operations itself still leaves that view for the task document, as before.
export function useTabChange<T extends string>(
  tab: TabMemory<T>,
  setChangeSet: (target: ChangeSetTarget | null) => void,
  setNotesOpen: (open: boolean) => void,
  setView: (view: T) => void,
): (next: T) => void {
  // One identity for the session: the mode bar is behind a memo gate (tab-switch CPU).
  const latest = useRef(tab);
  latest.current = tab;
  return useCallback(
    (next: T) => {
      const memory = latest.current;
      if (memory.view === 'operations' && next === 'operations') {
        // A press on the tab while it is open returns to the task view, as today.
        clearOperationsView({ setChangeSet, setNotesOpen });
      } else if (memory.view === 'operations' && next !== 'operations' && memory.changeSet?.review) {
        memory.address.park();
      } else if (next === 'operations') {
        restoreOperationsView(memory, { setChangeSet, setNotesOpen });
      }
      setView(next);
    },
    [setChangeSet, setNotesOpen, setView],
  );
}

function clearOperationsView(actions: {
  setChangeSet: (target: ChangeSetTarget | null) => void;
  setNotesOpen: (open: boolean) => void;
}): void {
  actions.setNotesOpen(false);
  actions.setChangeSet(null);
}

// Returning to Operations restores what it last showed. A view whose task the task list no longer
// holds is gone, so the page gives way to the task document.
function restoreOperationsView(
  memory: TabMemory,
  actions: {
    setChangeSet: (target: ChangeSetTarget | null) => void;
    setNotesOpen: (open: boolean) => void;
  },
): void {
  const { changeSet, notes, notesOpen, address } = memory;
  if (changeSet) {
    if (changeSetTaskGone(changeSet)) actions.setChangeSet(null);
    else if (changeSet.review) address.unpark();
  }
  if (notesOpen && notes && notesTaskGone(notes)) actions.setNotesOpen(false);
}

export function changeSetTaskGone(changeSet: ChangeSetTarget): boolean {
  const { tasks, enclosures, generatedAt } = dashboardStore.getState();
  if (generatedAt === null) return false;
  if (changeSet.leaf) {
    return reviewChatTask(changeSet, tasks, true).state === 'unknown';
  }
  if (changeSet.master) {
    // A series master net has no leaf; it is still owned by the master's task document.
    return !Object.values(tasks).some((task) => {
      const reference = task.reference;
      if (!reference || reference.repository !== changeSet.repo) return false;
      return dirName(reference.path) === changeSet.master && reference.path.endsWith('/task.json');
    });
  }
  if (changeSet.scope) {
    return !scopeTaskKnown(changeSet.repo, changeSet.scope, Object.values(tasks), Object.values(enclosures));
  }
  return false;
}

function scopeTaskKnown(
  repo: string,
  scope: string,
  tasks: TaskEntry[],
  enclosures: EnclosureNode[],
): boolean {
  const owners = new Set(tasks.flatMap((task) => {
    const reference = task.reference;
    return reference?.repository === repo
      ? [JSON.stringify([dirName(reference.path), task.id])]
      : [];
  }));
  // An enclosure can remain projected after its task disappears. Its aliases identify the
  // enclosure; only the current task population establishes that its named owner still exists.
  const isOwnedScope = (enclosure: EnclosureNode) =>
    enclosure.repoName === repo &&
    [enclosure.enclosure, enclosure.enclosureId, enclosure.worktreeGroup].includes(scope) &&
    owners.has(JSON.stringify([enclosure.taskName, enclosure.leafId || enclosure.taskId]));
  return enclosures.some(isOwnedScope) || tasks.some((task) => task.worktrees.some(isOwnedScope));
}

// The notes reader's task is the series it reads: a master the task list no longer holds has no
// note to return to, so the view gives way to the task document.
function notesTaskGone(target: NotesReaderTarget): boolean {
  const { tasks, generatedAt } = dashboardStore.getState();
  if (generatedAt === null) return false;
  return !Object.values(tasks).some((task) => {
    const reference = task.reference;
    if (!reference || reference.repository !== target.repo) return false;
    return reference.path.split('/').includes(target.master);
  });
}
