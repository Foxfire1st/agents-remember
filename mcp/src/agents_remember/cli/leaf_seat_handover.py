"""The hand-over's ``leafSeats``: a leaf seat's siblings and how to message each of them.

A leaf's seats talk to one another directly, so each leaf seat's hand-over names the seats of
its leaf and the exact ``role_message`` arguments that reach each sibling. The seats are the
leaf's own: a Worker, a Reviewer and a Curator for an ordinary leaf, a Consolidator and a
Reviewer for a consolidation task, read from the leaf's task document. No sibling agent id is
named; a message addressed by role and task reaches whichever agent holds the seat then.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents_remember.tasks.consolidation_task import leaf_seat_roles
from agents_remember.tasks.document_refs import ResolvedTaskDocument


def leaf_seats(
    role: str, task: ResolvedTaskDocument, seat_selection: Mapping[str, Any]
) -> dict[str, Any]:
    """The ``leafSeats`` block of the hand-over of seat ``role`` on the leaf ``task``."""

    roles = leaf_seat_roles(task.document)
    return {
        "roles": list(roles),
        "roleMessageArguments": {
            sibling: {
                "role": sibling,
                # A leaf of a standalone master has no sprint to name; the master and the
                # leaf are the whole selection then.
                **(
                    {"sprint_document_ref": seat_selection["sprintDocumentRef"]}
                    if seat_selection.get("sprintDocumentRef") is not None
                    else {}
                ),
                "master_document_ref": seat_selection["masterDocumentRef"],
                "task_document_ref": task.ref.model_dump(mode="json"),
            }
            for sibling in roles
            if sibling != role
        },
    }


__all__ = ["leaf_seats"]
