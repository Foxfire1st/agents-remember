"""The short answers ``read_ar_files`` carries beside what it names.

What the tool answers for a path today stays as it is; a path the tool names and whose file exists
gets one more field, ``brief``: the short answer of the file's brief, with its rules' texts, or the
one sentence that nothing is recorded at the file. The answer reports the disk brief's age while
presenting the knowledge from the selected memory tree.

The answers are measured at the code the caller's read observed (its working tree when it holds
edits), and fitted inside the tool's answer bound before the answer leaves
(:func:`~agents_remember.application.file_briefs.short_answer.fit`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from agents_remember.application.file_briefs.facts import BriefSource, CodeSide
from agents_remember.application.file_briefs.folder import brief_location, tree_named
from agents_remember.application.file_briefs.short_answer import (
    NOTHING_RECORDED,
    BriefLocation,
    Candidate,
    counts_notice,
    counts_only,
    fit,
    whole,
)
from agents_remember.application.file_briefs.triggers import (
    WRITING_FAILURES,
    head_commit,
)
from agents_remember.application.knowledge_currentness import CodeTree
from agents_remember.application.tool_response import ReplyFit
from agents_remember.memory.knowledge_index import KnowledgeIndex
from agents_remember.models.read_files import NamedFile, ShortBrief
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import bound_for, strip_envelope, truncate_lists

BRIEF_FIELD: Final = "brief"
COMPACT_NOTICE: Final = (
    "Open each current brief under memoryRoot with your own file tool for the full story."
)
COVERING_WRITE_NOTICE: Final = (
    "A brief marked missing or unknown or naming an older tree renews on the next "
    "write covering the file or the whole folder."
)
# The initial fit leaves room for delivery metadata. The completed reply measures it directly.
FINISHING_ROOM: Final = 160


@dataclass(frozen=True)
class BriefAge:
    """What the saved brief's header says about its age."""

    state: Literal["missing", "unknown", "older"]
    tree: str | None = None

    def sentence(self) -> str:
        if self.state == "older":
            return f"A write covering this file or folder renews the brief naming older tree {self.tree}."
        age = (
            "The brief on disk is missing"
            if self.state == "missing"
            else "The brief's tree is unknown"
        )
        return f"{age} and the next write covering this file or the whole folder renews it."

    def compact_value(self) -> str:
        return f"older {self.tree}" if self.state == "older" else self.state


@dataclass(frozen=True)
class ReadFilesReply:
    """The application result and an optional fitter for its delivered reply."""

    payload: dict[str, Any]
    fit_reply: ReplyFit | None = None


@dataclass
class BatchBriefs:
    """The brief source of one batch read, and what its briefs met on the way."""

    memory_root: Path
    source: BriefSource
    locate: Callable[[Path], str]
    """How the caller opens a location: relative to its working folder when it lies there."""
    candidates: list[Candidate] = field(default_factory=list)
    compact_briefs: dict[str, dict[str, str]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def answer_row(self, row: dict[str, Any], rel: str) -> None:
        """Add the short answer of ``rel`` to its row, or the sentence that nothing is recorded."""

        try:
            brief = self.source.assemble(rel)
        except WRITING_FAILURES as error:
            self.problems.append(f"the brief of {rel} could not be made ({error})")
            return
        if brief is None:
            row[BRIEF_FIELD] = {"none": NOTHING_RECORDED}
            return
        file = brief_location(self.memory_root, rel)
        age = _brief_age(file, self.source.memory_tree)
        location = BriefLocation(
            path=self.locate(file),
            current=age is None,
            freshness=age.sentence() if age is not None else None,
        )
        self.candidates.append(
            Candidate(row=row, whole=whole(brief, location), counts=counts_only(brief, location))
        )
        compact = {"path": file.relative_to(self.memory_root).as_posix()}
        if age is not None:
            compact["freshness"] = age.compact_value()
        self.compact_briefs[row["path"]] = compact

    def prepare_reply(self, payload: dict[str, Any]) -> ReadFilesReply:
        """Keep the naming rows available when delivery metadata changes the space left."""

        rows = payload["files"]
        positions = {id(row): position for position, row in enumerate(rows)}
        legacy = [dict(row) for row in rows]
        overviews = payload.get("overviews")
        indexed = [(positions[id(candidate.row)], candidate) for candidate in self.candidates]
        self.fit_into(payload)

        def fit_reply(answer: dict[str, Any], bound: int) -> dict[str, Any]:
            if count_response_tokens(answer) <= bound:
                return answer
            fitted = dict(answer)
            fitted["files"] = [_normalized_row(row) for row in legacy]
            fitted.pop("briefsLeftOut", None)
            if overviews is not None:
                fitted["overviews"] = overviews
            candidates = [
                Candidate(
                    row=fitted["files"][position],
                    whole=_normalized_brief(candidate.whole),
                    counts=_normalized_brief(candidate.counts),
                )
                for position, candidate in indexed
            ]
            self._fit(fitted, candidates, bound=bound)
            return truncate_lists(fitted)

        return ReadFilesReply(payload=payload, fit_reply=fit_reply)

    def fit_into(self, payload: dict[str, Any]) -> None:
        """Fit every short answer of the batch inside the tool's bound, in the order asked."""

        self._fit(payload, self.candidates, bound=bound_for("read_ar_files") - FINISHING_ROOM)

    def _fit(self, payload: dict[str, Any], candidates: list[Candidate], *, bound: int) -> None:
        def _counts_rows() -> list[dict[str, Any]]:
            return [
                candidate.row[BRIEF_FIELD]
                for candidate in candidates
                if "counts" in candidate.row[BRIEF_FIELD]
            ]

        def fits() -> bool:
            rows = _counts_rows()
            if rows:
                notice = counts_notice(
                    len(rows), all_current=all(bool(r.get("current")) for r in rows)
                )
                answer = {**payload, "briefsLeftOut": notice}
            else:
                answer = payload
            return _answer_tokens(answer) <= bound

        count_only = fit(candidates, fits=fits, field=BRIEF_FIELD)
        if count_only is None:
            self._compact(payload)
        elif count_only > 0:
            rows = _counts_rows()
            payload["briefsLeftOut"] = counts_notice(
                count_only, all_current=all(bool(r.get("current")) for r in rows)
            )

    def _compact(self, payload: dict[str, Any]) -> None:
        """Keep every path addressable when the legacy rows with brief counts cannot fit."""

        rows: list[dict[str, Any]] = payload["files"]
        payload["files"] = [_compact_row(row, self.compact_briefs.get(row["path"])) for row in rows]
        payload.pop("overviews", None)
        payload["briefsLeftOut"] = COMPACT_NOTICE
        if any("freshness" in brief for brief in self.compact_briefs.values()):
            payload["briefsLeftOut"] += f" {COVERING_WRITE_NOTICE}"


def _normalized_row(row: dict[str, Any]) -> dict[str, Any]:
    return NamedFile.model_validate(row).model_dump(mode="json", exclude_none=True)


def _normalized_brief(brief: dict[str, Any]) -> dict[str, Any]:
    return ShortBrief.model_validate(brief).model_dump(mode="json", exclude_none=True)


def _brief_age(file: Path, memory_tree: str) -> BriefAge | None:
    """Read the saved brief's age from its first line without changing it."""

    try:
        tree = tree_named(file)
    except FileNotFoundError:
        return BriefAge("missing")
    if tree == memory_tree:
        return None
    return BriefAge("unknown") if tree is None else BriefAge("older", tree)


def _answer_tokens(payload: dict[str, Any]) -> int:
    return count_response_tokens(truncate_lists(strip_envelope(payload)))


def _compact_row(row: dict[str, Any], brief: dict[str, str] | None) -> dict[str, Any]:
    compact = {"path": row["path"]}
    if "exists" in row:
        compact["exists"] = row["exists"]
    if "refusalCode" in row:
        compact["refusalCode"] = row["refusalCode"]
        return compact
    compact["cardStatus"] = row.get("cardStatus", "missing")
    if brief is not None:
        compact[BRIEF_FIELD] = brief
    return compact


@contextmanager
def batch_briefs(  # noqa: PLR0913 - one batch's memory, index, code and folder
    *,
    memory_root: Path,
    index_path: Path | None,
    tree_key: str | None,
    code_tree: CodeTree | None,
    code_state: str,
    locate: Callable[[Path], str],
) -> Iterator[BatchBriefs | None]:
    """The briefs of one batch read, or ``None`` when the memory has no converted tree to read."""

    if index_path is None or tree_key is None:
        yield None
        return
    index = KnowledgeIndex(index_path, expected_key=tree_key)
    try:
        code = CodeSide(
            tree=code_tree,
            commit=None if code_tree is None else head_commit(code_tree.repository),
            state=code_state,
        )
        yield BatchBriefs(
            memory_root=memory_root,
            source=BriefSource(index=index, memory_root=memory_root, code=code),
            locate=locate,
        )
    finally:
        index.close()


def briefs_problem(batch: BatchBriefs | None) -> str | None:
    """Report failures to assemble briefs for this read."""

    return " ".join(batch.problems) if batch is not None and batch.problems else None


__all__ = ["BRIEF_FIELD", "BatchBriefs", "ReadFilesReply", "batch_briefs", "briefs_problem"]
