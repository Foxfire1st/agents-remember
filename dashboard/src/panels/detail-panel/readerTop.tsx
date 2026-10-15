import type { TaskDocumentBodyState } from '../../data/useTaskDocumentBody';
import type { TaskDocNode } from '../../types/projection';
import type { ChangeSetTarget } from '../changeset/ChangeSetViewer';
import { docForm, type MasterDocView } from './model';
import {
  DocHead,
  ReaderHead,
  ObjectiveLead,
  FixedMarkdownBlock,
  type ChainLink,
  type HeadCount,
  type DocView,
} from './skeletons';

export function OverviewTop({
  doc,
  chain,
  counts,
  bodyState,
  onJump,
  openView,
  onOpenChangeSet,
}: {
  doc: TaskDocNode | MasterDocView;
  chain: ChainLink[];
  counts: HeadCount[];
  bodyState: TaskDocumentBodyState | undefined;
  onJump: (id: string) => void;
  openView: (view: DocView) => void;
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
}) {
  return (
    <>
      <DocHead doc={doc} chain={chain} onJump={onJump} openView={openView} counts={counts} />
      <TaskBodyNotice state={bodyState} />
      <ReaderHead doc={doc} bodyState={bodyState} kind="master" onOpenChangeSet={onOpenChangeSet} />
      <ObjectiveLead text={doc.objective} openView={openView} />
      {docForm(doc) !== 'sprint' ? (
        <FixedMarkdownBlock title="Non-goals" block="nonGoals" text={doc.nonGoals} />
      ) : null}
    </>
  );
}

export function TaskBodyNotice({ state }: { state: TaskDocumentBodyState | undefined }) {
  if (state === 'loading') {
    return (
      <p className="muted" role="status">
        Loading complete task document…
      </p>
    );
  }
  if (state === 'unavailable') {
    return (
      <p className="muted">
        Full task document details are unavailable; showing the available summary.
      </p>
    );
  }
  return null;
}
