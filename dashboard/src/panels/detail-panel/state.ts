import { useEffect,useState } from 'react';
import { useDashboard } from '../../data/store';
import { qualifiedLeafKey } from '../../data/taskIdentity';
import { useTaskDocumentBody } from '../../data/useTaskDocumentBody';
import type { TaskDocumentRef,TaskEntry } from '../../types/projection';

export interface DetailPanelProps {
  selectedId: string | null;
  onOpenLifecycle?: (id: string) => void;
  onViewTask?: (context: ViewedTaskContext | undefined) => void;
}
export interface ViewedTaskContext {
  taskDocumentRef: TaskDocumentRef;
  leafKey?: string;
}

export function resolveViewedLeafKey(entry: TaskEntry | undefined): string | undefined {
  return entry && entry.summary?.kind !== 'master' ? qualifiedLeafKey(entry) : undefined;
}

export function useDetailPanelState({ selectedId, onOpenLifecycle, onViewTask }: DetailPanelProps) {
  const tasks = useDashboard((state) => state.tasks);
  const activeWorktreeGroups = useDashboard((state) => state.activeWorktreeGroups);
  const [local, setLocal] = useState<{ source: string | null; key: string }>();
  const key = local?.source === selectedId ? local.key : selectedId;
  const entry = key?.startsWith('taskdoc:') ? tasks[key.slice('taskdoc:'.length)] : undefined;
  const body = useTaskDocumentBody(entry);
  const reference = entry?.reference;
  const leafKey = resolveViewedLeafKey(entry);
  useEffect(() => {
    onViewTask?.(
      reference ? { taskDocumentRef: reference, ...(leafKey ? { leafKey } : {}) } : undefined,
    );
  }, [reference, leafKey, onViewTask]);
  const jump = (next: string): void => {
    setLocal({ source: selectedId, key: next });
    onOpenLifecycle?.(next);
  };
  return {
    entry,
    tasks,
    document: body.document,
    taskDocumentBodyState: body.state,
    jump,
    activeWorktreeGroups,
    parent: entry?.parent ? tasks[entry.parent] : undefined,
  };
}
