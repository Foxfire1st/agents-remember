"""Author a weakness' recorded place and closure without changing its observation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from agents_remember.application.knowledge_writer.code_anchors import AnchorResolutionError
from agents_remember.application.knowledge_writer.handoff import read_locator
from agents_remember.memory.conversion.code_objects import CodeObjects

if TYPE_CHECKING:
    from agents_remember.application.knowledge_writer.authoring import Authoring


def author_weakness(
    authoring: Authoring,
    document: dict[str, Any],
    where: str,
    before: Mapping[str, Any] | None,
) -> None:
    """Fill the anchor and check the authored closure at the writer's admitted code root."""

    _weakness_anchor(authoring, document, where, before)
    _closure(authoring, document, where, before)


def _weakness_anchor(
    authoring: Authoring, document: dict[str, Any], where: str, before: Mapping[str, Any] | None
) -> None:
    """MIK-R118 rule 2: the weakness' file and symbol anchor, recorded at C.

    The curator authors ``{path, locator}``; ``blob`` and ``content`` are the candidate's own
    values and are filled here. A weakness without a file is refused with its field: there is
    no place to record it.

    An anchor C cannot resolve keeps the place it was recorded at -- the file or symbol the
    cause was removed from, which is exactly when a weakness is closed (MIK-R118 rule 7). The
    stored ``anchor`` and ``seen`` data are never rewritten to make a resolution succeed, and
    a *new* open mark still refuses: it names a place that must resolve at C.
    """

    anchor = document.get("anchor")
    if not isinstance(anchor, Mapping):
        authoring.problem(f"{where}.anchor", "a weakness anchors to a file: {path, locator}")
        return
    path = anchor.get("path")
    if not isinstance(path, str) or not path:
        authoring.problem(f"{where}.anchor.path", "a weakness names the file it occurs in")
        return
    locator = read_locator(anchor.get("locator"))
    if isinstance(locator, str):
        authoring.problem(f"{where}.anchor.locator", locator)
        return
    try:
        resolved = authoring.code.resolve(path, locator.document)
    except AnchorResolutionError as error:
        stored = before.get("anchor") if isinstance(before, Mapping) else None
        if isinstance(stored, Mapping) and stored.get("path") == path:
            # The place this weakness was recorded at, kept as recorded: the file may have
            # been deleted or the symbol removed, and a closure still names where it was.
            document["anchor"] = dict(stored)
            return
        authoring.problem(f"{where}.anchor", f"{path} ({locator.describe()}): {error}")
        return
    document["anchor"] = {**resolved.to_document(), "path": path}


def _closure(
    authoring: Authoring,
    document: Mapping[str, Any],
    where: str,
    before: Mapping[str, Any] | None,
) -> None:
    """Check a supplied commit or bind a new deferred closure to the current task."""

    closed = document.get("closed")
    if document.get("state") != "closed" or not isinstance(closed, Mapping):
        return
    commit = closed.get("commit")
    if commit is not None:
        if isinstance(commit, str) and CodeObjects(authoring.code.root).commit(commit) != commit:
            authoring.problem(
                f"{where}.closed.commit",
                "a closure commit is the full ID of a real commit in the code repository",
            )
        return
    if before is not None and before.get("state") == "closed" and before.get("closed") == closed:
        return
    owner = authoring.owner
    task = owner.id if owner.kind == "leaf" else owner.task
    if closed.get("task") != task:
        authoring.problem(
            f"{where}.closed.task",
            f"a closure without a commit names the running task {task}",
        )
