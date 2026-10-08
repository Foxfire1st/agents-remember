import type { SeriesNode, TaskDocNode } from "../../types/projection";

// One row's progress figure. Abandoned sub-task rows (a retired master's row is one) will not run:
// they are neither done nor part of the total, and the figure names them beside it instead, so a
// master whose remaining rows are all Completed reads complete ("5/5, 1 abandoned"). The same rule
// as `tasks/document.py::series_done` / `series_total` / `series_abandoned` on the server.
export type RowProgress = { done: number; total: number; abandoned?: number };

export function taskStepProgress(doc: TaskDocNode): RowProgress {
  return {
    done: doc.stepsDone,
    total: doc.stepsTotal,
  };
}

export function subTaskProgress(items: TaskDocNode["subTasks"]): RowProgress {
  const abandoned = items.filter((item) => item.status.toLowerCase() === "abandoned").length;
  return {
    done: items.filter((item) => item.status.toLowerCase() === "completed").length,
    total: items.length - abandoned,
    abandoned,
  };
}

export function seriesProgress(series: SeriesNode): RowProgress {
  return { done: series.doneCount, total: series.totalCount, abandoned: series.abandonedCount };
}

export function progressHint(progress: RowProgress, discardedCount = 0): string {
  const completed = progress.total > 0 ? `${progress.done}/${progress.total}` : "";
  const abandoned = progress.abandoned ? `${progress.abandoned} abandoned` : "";
  const figure = [completed, abandoned].filter(Boolean).join(", ");
  const discarded = discardedCount > 0 ? `${discardedCount} discarded` : "";
  return [figure, discarded].filter(Boolean).join(" · ");
}
