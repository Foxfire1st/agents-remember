import type { TaskDocumentBodyState } from '../../data/useTaskDocumentBody';

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
