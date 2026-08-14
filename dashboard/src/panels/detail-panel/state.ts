import { useEffect, useState } from 'react';

import { useDashboard } from '../../data/store';
import {
  findLifecycleEnclosure,
  groupEnclosuresByLifecycle,
  parseTaskSelection,
  qualifiedLeafKey,
  taskDocumentRefForDoc,
  type TaskSelection,
} from '../../data/taskIdentity';
import type { TaskDocumentRef } from '../../types/terminalCatalog';
import { useTaskDocumentBody } from '../../data/useTaskDocumentBody';
import type {
  Analytics,
  EnclosureNode,
  LifecycleProjection,
  SeriesNode,
  TaskDocNode,
} from '../../types/projection';
import { displayedLeafDoc, displayedReaderDoc } from './model';

export interface DetailPanelProps {
  selectedId: string | null;
  onOpenLifecycle?: (id: string) => void;
  onViewTask?: (context: ViewedTaskContext | undefined) => void;
}

export interface ViewedTaskContext {
  taskDocumentRef: TaskDocumentRef;
  leafKey?: string;
}

function resolveSelectedTaskDoc(
  selection: TaskSelection | null,
  allDocs: TaskDocNode[],
): TaskDocNode | undefined {
  if (selection?.kind !== 'taskdoc') return undefined;
  return allDocs.find((doc) => doc.docPath === selection.docPath);
}

function resolveLifecycleId(
  selection: TaskSelection | null,
  selectedTaskDoc: TaskDocNode | undefined,
): string | undefined {
  if (selection?.kind === 'lifecycle') return selection.lifecycleId;
  return selectedTaskDoc?.lifecycleId;
}

export function resolveDirectDocs(
  lifecycle: LifecycleProjection | undefined,
  selectedTaskDoc: TaskDocNode | undefined,
  allDocs: TaskDocNode[],
): TaskDocNode[] {
  if (!lifecycle) return [];
  if (selectedTaskDoc?.lifecycleId === lifecycle.id) return [selectedTaskDoc];
  return allDocs.filter((doc) => doc.lifecycleId === lifecycle.id);
}

export function isRootTaskSelection(
  selection: TaskSelection | null,
  lifecycle: LifecycleProjection | undefined,
  selectedEnclosure: EnclosureNode | undefined,
): boolean {
  return (
    selection?.kind === 'lifecycle' &&
    Boolean(lifecycle && selectedEnclosure) &&
    (lifecycle?.id === selectedEnclosure?.taskId || lifecycle?.id === selectedEnclosure?.taskName)
  );
}

export function resolveSelectedSeries(
  selection: TaskSelection | null,
  analytics: Analytics | null | undefined,
  selectedIsRootTask: boolean,
  selectedEnclosure: EnclosureNode | undefined,
): SeriesNode | undefined {
  if (!selection || !analytics) return undefined;
  return analytics.series.find(
    (item) =>
      (selection.kind === 'series' && item.seriesId === selection.seriesId) ||
      (selectedIsRootTask && item.seriesId === selectedEnclosure?.taskName),
  );
}

export function resolveViewedLeafKey(viewedLeafDoc: TaskDocNode | undefined): string | undefined {
  return viewedLeafDoc && viewedLeafDoc.kind !== 'master'
    ? qualifiedLeafKey(viewedLeafDoc)
    : undefined;
}

function useViewedTaskNotification(
  viewedTaskDocumentRef: ReturnType<typeof taskDocumentRefForDoc> | undefined,
  viewedLeafKey: string | undefined,
  onViewTask: DetailPanelProps['onViewTask'],
): void {
  useEffect(() => {
    onViewTask?.(
      viewedTaskDocumentRef
        ? {
            taskDocumentRef: viewedTaskDocumentRef,
            ...(viewedLeafKey ? { leafKey: viewedLeafKey } : {}),
          }
        : undefined,
    );
  }, [viewedLeafKey, viewedTaskDocumentRef, onViewTask]);
}

export function useDetailPanelState({ selectedId, onOpenLifecycle, onViewTask }: DetailPanelProps) {
  const jump = onOpenLifecycle ?? (() => {});
  const lifecycles = useDashboard((s) => s.lifecycles);
  const analytics = useDashboard((s) => s.analytics);
  const enclosures = useDashboard((s) => s.enclosures);
  const activeWorktreeGroups = useDashboard((s) => s.activeWorktreeGroups);
  const providers = useDashboard((s) => s.providers);
  const [openSlug, setOpenSlug] = useState<string | null>(null);
  useEffect(() => setOpenSlug(null), [selectedId]);
  const allDocs = analytics?.taskDocuments ?? [];
  const selection = parseTaskSelection(selectedId, lifecycles, analytics);
  const selectedTaskDoc = resolveSelectedTaskDoc(selection, allDocs);
  const lifecycleId = resolveLifecycleId(selection, selectedTaskDoc);
  const lifecycle = lifecycleId ? lifecycles[lifecycleId] : undefined;
  const selectedEnclosure = lifecycle
    ? findLifecycleEnclosure(
        lifecycle,
        enclosures,
        groupEnclosuresByLifecycle(Object.values(enclosures)),
      )
    : undefined;
  const directDocs = resolveDirectDocs(lifecycle, selectedTaskDoc, allDocs);
  const selectedIsRootTask = isRootTaskSelection(selection, lifecycle, selectedEnclosure);
  const selectedSeries = resolveSelectedSeries(
    selection,
    analytics,
    selectedIsRootTask,
    selectedEnclosure,
  );
  const bodyTargetDoc = displayedReaderDoc({
    allDocs,
    selectedTaskDoc,
    lifecycle,
    selectedSeries,
    openSlug,
  });
  const { documentFor: fullTaskDoc, state: taskDocumentBodyState } =
    useTaskDocumentBody(bodyTargetDoc);
  const viewedLeafDoc = displayedLeafDoc({
    selection,
    allDocs,
    selectedTaskDoc,
    lifecycle,
    selectedSeries,
    openSlug,
  });
  const viewedLeafKey = resolveViewedLeafKey(viewedLeafDoc);
  const viewedTaskDocumentRef = bodyTargetDoc ? taskDocumentRefForDoc(bodyTargetDoc) : undefined;
  useViewedTaskNotification(viewedTaskDocumentRef, viewedLeafKey, onViewTask);

  return {
    jump,
    analytics,
    enclosures,
    activeWorktreeGroups,
    providers,
    openSlug,
    setOpenSlug,
    allDocs,
    selection,
    selectedTaskDoc,
    lifecycle,
    selectedEnclosure,
    directDocs,
    selectedSeries,
    fullTaskDoc,
    taskDocumentBodyState,
    viewedLeafKey,
    viewedTaskDocumentRef,
  };
}
