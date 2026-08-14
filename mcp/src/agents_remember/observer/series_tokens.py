"""Series token aggregation from linked leaf lifecycles."""

from __future__ import annotations

from pathlib import Path

from agents_remember.observer.projection import (
    LifecycleProjection,
    SeriesNode,
    TaskDocNode,
)


def attach_series_token_totals(
    series: list[SeriesNode],
    task_documents: list[TaskDocNode],
    lifecycles: list[LifecycleProjection],
) -> list[SeriesNode]:
    """Return series nodes with token totals summed from sibling leaf lifecycles."""
    tokens_by_lifecycle = {lifecycle.id: lifecycle.tokens for lifecycle in lifecycles}
    docs_by_file = _task_docs_by_series_file(task_documents)
    updated: list[SeriesNode] = []
    for node in series:
        total = 0
        series_dir = Path(node.docPath).parent.as_posix()
        for ref in node.subTasks:
            doc = docs_by_file.get((series_dir, ref.file))
            if doc and doc.lifecycleId:
                total += tokens_by_lifecycle.get(doc.lifecycleId, 0)
        updated.append(node.model_copy(update={"seriesTokenTotal": total}))
    return updated


def _task_docs_by_series_file(
    task_documents: list[TaskDocNode],
) -> dict[tuple[str, str], TaskDocNode]:
    indexed: dict[tuple[str, str], TaskDocNode] = {}
    for doc in task_documents:
        if doc.kind == "master":
            continue
        path = Path(doc.docPath)
        indexed[(path.parent.as_posix(), path.with_suffix(".md").name)] = doc
    return indexed
