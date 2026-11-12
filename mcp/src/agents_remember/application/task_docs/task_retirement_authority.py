"""Keep retained retirement proofs owned by the explicit retirement operation."""

from agents_remember.tasks import SubTaskRef, TaskDocument
from agents_remember.tasks.retired_rows import retired_row_message

from .task_doc_route_review import TaskDocError


def retirement_only_refusal(document_id: str, numbers: list[str], action: str) -> TaskDocError:
    return TaskDocError(retired_row_message(document_id, numbers, action))


def require_retirement_proofs_unchanged(
    original: TaskDocument | None, candidate: TaskDocument, action: str = "this edit"
) -> None:
    before = _retired_rows(original)
    after = _retired_rows(candidate)
    # A retired row is kept exactly as the retire operation wrote it: proof, name, scope, status.
    changed = sorted(
        number for number in before.keys() | after.keys() if before.get(number) != after.get(number)
    )
    if changed:
        raise retirement_only_refusal(candidate.id, changed, action)


def _retired_rows(document: TaskDocument | None) -> dict[str, SubTaskRef]:
    if document is None:
        return {}
    return {row.number: row for row in document.subTasks if row.retirement is not None}


def require_row_not_retired(document: TaskDocument, number: str, action: str) -> None:
    """Refuse an operation that targets one row when that row records a retirement."""

    if any(row.number == number and row.retirement is not None for row in document.subTasks):
        raise retirement_only_refusal(document.id, [number], action)
