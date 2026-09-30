"""A ``raise`` row's question, appended to the leaf's task document through ``task_doc`` (MIK-R14).

:class:`TaskDocOpenQuestions` is the writer's :class:`~.reconsideration.OpenQuestions` over the
task owner. It is a read-modify-write that preserves every existing question:

1. the leaf's one task document is found strictly (:func:`~agents_remember.tasks.leaf_decisions.
   strict_leaf_doc`: two documents claiming the leaf, or the leaf's document in an unreadable state,
   are named refusals);
2. a question whose key is already present is not appended again (a rerun of the same ``raise``);
3. otherwise ``task_doc`` ``set_field`` publishes ``openQuestions`` as the existing questions plus
   the new one -- a ``NORMATIVE_INTENT`` field, so the change is the developer's to answer. ``check``
   is the same edit as a dry run: the task owner validates the whole publication and writes nothing.

Any refusal of the task owner is returned as the reason, never raised, so the writer refuses the
``raise`` and the item stays open. :class:`UnavailableOpenQuestions` is the port when no task owner
can be reached (no MCP authority settings): every question is refused with that reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.tasks.leaf_decisions import LeafDocumentUnresolved, strict_leaf_doc

__all__ = ["TaskDocOpenQuestions", "UnavailableOpenQuestions"]


@dataclass(frozen=True)
class TaskDocOpenQuestions:
    """The leaf task document's ``openQuestions``, edited only through ``task_doc``."""

    config: McpRuntimeConfig
    repo_id: str
    contract_path: Path
    task_root: Path
    leaf: str

    def check(self, key: str, question: str) -> str | None:
        return self._edit(key, question, dry_run=True)

    def append(self, key: str, question: str) -> str | None:
        return self._edit(key, question, dry_run=False)

    def _current(self) -> tuple[str, list[Any]] | str:
        """The leaf document's slug and its questions as documents, or why it cannot be read."""

        try:
            found = strict_leaf_doc(self.task_root, self.leaf)
        except LeafDocumentUnresolved as error:
            return str(error)
        if found is None:
            return f"the leaf {self.leaf} has no task document under {self.task_root}"
        path, document = found
        questions = [
            one if isinstance(one, str) else one.model_dump(mode="json", exclude_none=True)
            for one in document.openQuestions
        ]
        return path.stem, questions

    def _edit(self, key: str, question: str, *, dry_run: bool) -> str | None:
        current = self._current()
        if isinstance(current, str):
            return current
        slug, questions = current
        if any(isinstance(one, str) and one.startswith(key) for one in questions):
            return None
        try:
            task_doc_tool(
                self.config,
                TaskDocTarget(self.repo_id, contract_path=str(self.contract_path), slug=slug),
                operation="set_field",
                edit=TaskDocEdit(fields={"openQuestions": [*questions, question]}),
                call=TaskDocCall(dry_run=dry_run),
            )
        except (AgentsRememberError, ValueError, OSError) as error:
            return f"task_doc refused the openQuestions edit: {error}"
        return None


@dataclass(frozen=True)
class UnavailableOpenQuestions:
    """No task owner can be reached: every question is refused, naming why."""

    reason: str

    def check(self, key: str, question: str) -> str | None:
        del key, question
        return self.reason

    def append(self, key: str, question: str) -> str | None:
        del key, question
        return self.reason
