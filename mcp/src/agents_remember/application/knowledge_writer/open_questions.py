"""A ``raise`` row's question, appended to the leaf's task document through ``task_doc`` (MIK-R14).

:class:`TaskDocOpenQuestions` is the writer's :class:`~.reconsideration.OpenQuestions` over the
task owner. The strict leaf lookup selects the document; ``put_open_question`` reads and
publishes its keyed append/replacement inside the task owner's own publication. Intervening
questions are preserved, reworded reasons replace the earlier text, and ``check`` is a dry run.

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

    @property
    def destination(self) -> str:
        found = strict_leaf_doc(self.task_root, self.leaf)
        return str(found[0]) if found is not None else str(self.task_root)

    def check(self, key: str, question: str) -> str | None:
        return self._edit({"key": key, "text": question}, dry_run=True)

    def append(self, key: str, question: str) -> str | None:
        return self._edit({"key": key, "text": question}, dry_run=False)

    def append_many(self, raised: list[tuple[str, str]]) -> str | None:
        return self._edit(
            {"questions": [{"key": key, "text": text} for key, text in raised]}, dry_run=False
        )

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

    def _edit(self, fields: dict[str, Any], *, dry_run: bool) -> str | None:
        current = self._current()
        if isinstance(current, str):
            return current
        slug, _questions = current
        try:
            result = task_doc_tool(
                self.config,
                TaskDocTarget(self.repo_id, contract_path=str(self.contract_path), slug=slug),
                operation="put_open_question",
                edit=TaskDocEdit(fields=fields),
                call=TaskDocCall(dry_run=dry_run),
            )
        except (AgentsRememberError, ValueError, OSError) as error:
            return f"task_doc refused the openQuestions edit: {error}"
        if result.get("ok") is not True:
            return f"task_doc refused the openQuestions edit: {result.get('code', 'refused')}: {result.get('error') or result.get('detail')}"
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

    def append_many(self, raised: list[tuple[str, str]]) -> str | None:
        del raised
        return self.reason
