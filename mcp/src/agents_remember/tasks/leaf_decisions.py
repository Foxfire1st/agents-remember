"""The task owner's answers about a leaf's task document for MIK-R11: its declaration and decisions.

Both answers go through one **strict** lookup, :func:`strict_leaf_doc`, which fails closed on
identity doubt instead of skipping what it cannot read:

* it is :func:`~agents_remember.tasks.leaf_doc.resolve_terminal_leaf_doc` -- two documents claiming
  the leaf are ambiguous, and an unreadable document whose file stem is the leaf's is refused;
* and, beyond it, an unreadable document whose JSON still names the leaf (its ``id`` or an
  ``enclosures[].leafId``) is refused too, since that is the leaf's document in a broken state.

An unreadable JSON file that names no leaf (a preview or other sibling artifact) is not the leaf's
document and is left alone, exactly as the terminal resolver leaves it.

**What the lookup records as read (MIK-R42).** To find the leaf's document the lookup opens every
``*.json`` of the folder, but a sibling it merely rules out is not an input of anything computed
from the leaf's document: only the leaf's own document is recorded
(:func:`~agents_remember.kernel.recorded_reads.recorded_reads`), with the bytes parsed. A write to a
sibling that does not claim the leaf therefore invalidates nothing; a sibling that begins to claim
the leaf changes the answer of the next lookup (two claims are ambiguous; one claim is a different
document), which every keyed caller makes again before it uses anything it kept. A sibling that
could not be read cannot be ruled out, so its failed read stays recorded, and a lookup that cannot
establish the document records everything it opened. With no claimant it also records the JSON
listing it consumed, so a document missing during the lookup cannot reappear unnoticed.

* :func:`leaf_decision_refusal` -- a planned ``dropped`` history row cites one decision entry of
  the leaf's task document by its ``at``; the knowledge writer asks this at write time and refuses
  the row on any answer but "resolved". Two entries sharing that ``at`` are ambiguous.
* The worklist reads the declaration through :func:`strict_leaf_doc` and turns a
  :class:`LeafDocumentUnresolved` into an ``incomplete`` run naming the document (never an absent
  declaration).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.kernel.recorded_reads import (
    observed_json_files,
    observed_text,
    recorded_reads,
    replay_reads,
)
from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.leaf_doc import TerminalLeafResolutionError, resolve_terminal_leaf_doc
from agents_remember.tasks.store import read_task_doc

__all__ = ["LeafDocumentUnresolved", "leaf_decision_refusal", "strict_leaf_doc"]


class LeafDocumentUnresolved(ValueError):
    """The leaf's task document cannot be established: unreadable, or claimed twice."""


def strict_leaf_doc(task_root: Path, leaf_id: str) -> tuple[Path, TaskDocument] | None:
    """The leaf's one task document, ``None`` when it has none, or :class:`LeafDocumentUnresolved`."""

    try:
        with recorded_reads() as opened:
            paths = observed_json_files(task_root)
            found = resolve_terminal_leaf_doc(task_root, leaf_id)
            broken = _unreadable_claims(paths, leaf_id)
    except TerminalLeafResolutionError as error:
        replay_reads(opened)
        raise LeafDocumentUnresolved(str(error)) from error
    if broken:
        replay_reads(opened)
        raise LeafDocumentUnresolved(
            f"the task document of leaf {leaf_id!r} cannot be read: {'; '.join(broken)}"
        )
    if found is None:
        replay_reads(opened)
        return None
    claimed = found[0].resolve(strict=False)
    # A sibling read in full and ruled out is not an input. One that could not be read cannot be
    # ruled out, so its failed read stays recorded for every caller that keeps a result.
    replay_reads(
        {
            path: seen
            for path, seen in opened.items()
            if Path(path).resolve(strict=False) == claimed or not seen.startswith("sha256:")
        }
    )
    return found


def _unreadable_claims(paths: tuple[Path, ...], leaf_id: str) -> list[str]:
    want = leaf_id.strip().lower()
    broken: list[str] = []
    for path in paths:
        try:
            read_task_doc(path)
        except (OSError, ValueError) as error:
            if _names_leaf(path, want):
                broken.append(f"{path.name}: {str(error).splitlines()[0]}")
    return broken


def _names_leaf(path: Path, want: str) -> bool:
    try:
        raw: Any = json.loads(observed_text(path))
    except (OSError, ValueError):
        return False  # not JSON at all: nothing identifies it (a stem match is the resolver's)
    if not isinstance(raw, dict) or raw.get("kind") == "master":
        return False
    listed = raw.get("enclosures")
    enclosures: list[Any] = listed if isinstance(listed, list) else []
    return str(raw.get("id") or "").strip().lower() == want or any(
        isinstance(one, dict) and str(one.get("leafId") or "").strip().lower() == want
        for one in enclosures
    )


def leaf_decision_refusal(task_root: Path, leaf_id: str, at: str) -> str | None:
    """``None`` when the leaf's task document holds exactly one decision at ``at``; else why not."""

    try:
        found = strict_leaf_doc(task_root, leaf_id)
    except LeafDocumentUnresolved as error:
        return str(error)
    if found is None:
        return f"the leaf {leaf_id} has no task document under {task_root}"
    path, document = found
    matches = [decision for decision in document.decisions if decision.at == at]
    if len(matches) == 1:
        return None
    if not matches:
        return f"{path.name} has no decision entry at {at!r}"
    return (
        f"{path.name} has {len(matches)} decision entries at {at!r}, so the citation is ambiguous"
    )
