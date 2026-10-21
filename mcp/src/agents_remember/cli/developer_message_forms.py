"""The written form of each reference the developer's tray sends (MIK-R188 rule 2; L89 W28).

A pick of an object is a reference, never a copy of the object's text. The dashboard sends the
structured reference (``dashboard/src/tray/types.ts`` ``TrayReference``, the block identity of
``panels/review/blockIdentity.ts``); this module renders, one function per kind, the lines the
receiving agent reads. The first line of every form is the call that opens the object: an AR
``knowledge_read``, a read-only Git call, or the read of a file. The lines after it name the
object exactly.

The roots a form needs come from this server's own settings: a repository's code checkout for
``git -C``, its memory checkout for ``knowledge_read``'s ``memoryRoot`` (and for ``git -C`` on the
memory trees), the coordination root for a task document, and the File Viewer's own scope
resolution for a file line. The trees and pin refs of a review comparison are objects of both
repositories, so any checkout of the repository reads them. A reference whose root cannot be
named is refused before anything is filed (:class:`UnresolvableReference`).

A reference made on a review comparison (a review block, and an invariant or family that names
its ``comparison``) is read from that leaf's memory: ``knowledge_read`` names the leaf's memory
worktree while it exists, resolved as the dashboard's review reads resolve it, else the
repository's memory root once the leaf has landed. A reference without a comparison keeps the
repository's memory root.

The revision id of an invariant, a family or a review block's invariant is the record's revision
in the memory tree the reference names: the tree itself for an invariant or a family, the tree of
the block's side for a block (MIK-R188 rule 2). The record file is looked up in that tree and read
there (``git ls-tree``, ``git show``), never in a checkout, so an invariant only the leaf added or
only the base holds still gets its file and its revision. The id is the one ``knowledge_read``
hands out for that revision. A tree that holds no such record at a revision is refused before
anything is filed, and a revision the dashboard sent that is not the tree's is refused as
contradictory: no revision is named that the tree does not record, and none is put in its place.

A reference names one exact object (rule 2). One whose own parts disagree -- a pin ref that is not
its comparison's ``refs/ar/review/<task>/<leaf>/<n>``, block or hunk trees that are not its
comparison's, an invariant or family at a memory tree its comparison does not name or of another
repository -- is refused as contradictory before anything is filed
(:class:`ContradictoryReference`).
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)
from pydantic.alias_generators import to_camel

from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.review_trees import REVIEW_REF_NAMESPACE
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.scope import _resolve_within, _UnknownScope, resolve_scope
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_RECORD_FOLDERS = {"invariant": "invariants", "family": "families"}


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or _CONTROL.search(value)
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("must be a relative path inside its root")
    return value


def _segment(value: str) -> str:
    if value in {".", ".."} or "/" in value or "\\" in value or _CONTROL.search(value):
        raise ValueError("must be one path segment")
    return value


TreeId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")]
Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:+-]*$", max_length=200)]
Segment = Annotated[str, StringConstraints(min_length=1, max_length=200), AfterValidator(_segment)]
RelativePath = Annotated[str, StringConstraints(max_length=4096), AfterValidator(_relative_path)]
Line = Annotated[int, Field(ge=0)]


class _Wire(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, alias_generator=to_camel
    )


class ComparisonTrees(_Wire):
    code_base: TreeId
    code_candidate: TreeId
    memory_base: TreeId
    memory_candidate: TreeId


class ComparisonRef(_Wire):
    repository: Segment
    task: Segment
    leaf: Segment
    number: Line
    pin_ref: Annotated[str, StringConstraints(max_length=1000)]
    trees: ComparisonTrees


class LineRange(_Wire):
    start: Line
    end: Line


class BlockIdentity(_Wire):
    kind: Literal["entry"]
    entry: Name
    side: Literal["after", "before"]
    path: RelativePath
    range: LineRange | None
    trees: ComparisonTrees


class LaneSpan(_Wire):
    start: Line
    count: Line


class HunkTrees(_Wire):
    code_base: TreeId
    code_candidate: TreeId


class HunkIdentity(_Wire):
    kind: Literal["hunk"]
    path: RelativePath
    before: LaneSpan
    after: LaneSpan
    trees: HunkTrees


class WholeFile(_Wire):
    kind: Literal["file"]
    path: RelativePath


class InvariantReference(_Wire):
    kind: Literal["invariant"]
    repository: Segment
    id: Name
    revision_id: Name | None = None
    memory_tree: TreeId
    # The review comparison the invariant was picked on, when it was (L89 P-T2).
    comparison: ComparisonRef | None = None


class FamilyReference(_Wire):
    kind: Literal["family"]
    repository: Segment
    id: Name
    revision_id: Name | None = None
    memory_tree: TreeId
    comparison: ComparisonRef | None = None


class ReviewBlockReference(_Wire):
    kind: Literal["review-block"]
    comparison: ComparisonRef
    block: BlockIdentity
    invariant: Name
    invariant_revision_id: Name | None = None


class CodeWithoutInvariantReference(_Wire):
    kind: Literal["code-without-invariant"]
    comparison: ComparisonRef
    hunk: Annotated[HunkIdentity | WholeFile, Field(discriminator="kind")]


class TaskReference(_Wire):
    kind: Literal["task"]
    reference: TaskDocumentRef
    id: Annotated[str, StringConstraints(max_length=200)]


class FileLineReference(_Wire):
    kind: Literal["file-line"]
    repository: Segment
    scope: Segment
    path: RelativePath
    start: Annotated[int, Field(ge=1)]
    end: Annotated[int, Field(ge=1)]


class ChangeSetReference(_Wire):
    kind: Literal["change-set"]
    comparison: ComparisonRef


TrayReference = Annotated[
    InvariantReference
    | FamilyReference
    | ReviewBlockReference
    | CodeWithoutInvariantReference
    | TaskReference
    | FileLineReference
    | ChangeSetReference,
    Field(discriminator="kind"),
]


class UnresolvableReference(Exception):
    """A reference whose root or record this server cannot name; nothing is filed or sent."""


class ContradictoryReference(Exception):
    """A reference whose own parts name different objects; nothing is filed or sent."""


# The leaf memory worktree of (repository, master folder, leaf id) while it exists, else None;
# the dashboard's own resolver (``cli.dashboard._review_memory_root``) is the one composed.
LeafMemoryRoot = Callable[[str, str, str], Path | None]


class FormRoots:
    """The roots the written forms name, from this server's own settings."""

    def __init__(
        self, config: McpRuntimeConfig, leaf_memory_root: LeafMemoryRoot | None = None
    ) -> None:
        self._config = config
        self._leaf_memory_root = leaf_memory_root

    def code_root(self, repository: str) -> Path:
        try:
            return require_repo(self._config, repository).path
        except AuthorityError as error:
            raise UnresolvableReference(str(error)) from error

    def memory_root(self, repository: str) -> Path:
        try:
            root = require_repo(self._config, repository).memory_root
        except AuthorityError as error:
            raise UnresolvableReference(str(error)) from error
        if root is None:
            raise UnresolvableReference(f"repository {repository!r} has no memory root")
        return root

    def comparison_memory_root(self, repository: str, comparison: ComparisonRef) -> Path:
        """The leaf's memory worktree while it exists, else the repository's memory root."""

        if self._leaf_memory_root is not None:
            leaf = self._leaf_memory_root(comparison.repository, comparison.task, comparison.leaf)
            if leaf is not None:
                return leaf
        return self.memory_root(repository)

    def task_document(self, ref: TaskDocumentRef) -> tuple[Path, str, str]:
        """The document's Markdown file (its JSON when it has none), its id and its title."""

        try:
            resolved = TaskDocumentTopology(self._config.coordination_root).resolve(ref)
        except (TaskDocumentRefError, OSError, ValueError) as error:
            raise UnresolvableReference(f"task document {ref.key}: {error}") from error
        markdown = resolved.path.with_suffix(".md")
        path = markdown if markdown.is_file() else resolved.path
        return path, resolved.document.id, resolved.document.title

    def file_in_scope(self, repository: str, scope: str, path: str) -> tuple[Path, Path]:
        """The File Viewer checkout of one scope and the file's absolute path in it."""

        try:
            resolved = resolve_scope(self._config, repository, scope)
            return resolved.code_root, _resolve_within(resolved.code_root, path)
        except (AuthorityError, _UnknownScope, ValueError, OSError) as error:
            raise UnresolvableReference(
                f"file {path!r} in scope {scope!r} of {repository!r}: {error}"
            ) from error


def written_form(reference: Any, roots: FormRoots) -> tuple[str, ...]:
    """The lines of one reference: first the call that opens it, then the object's names."""

    contradiction = _contradiction(reference)
    if contradiction is not None:
        raise ContradictoryReference(contradiction)
    render: Callable[[Any, FormRoots], tuple[str, ...]] = _FORMS[reference.kind]
    return render(reference, roots)


def _contradiction(reference: Any) -> str | None:
    """Why one reference's parts name different objects (MIK-R188 rule 2), else None."""

    comparison: ComparisonRef | None = getattr(reference, "comparison", None)
    if comparison is None:
        return None
    pin = f"{REVIEW_REF_NAMESPACE}/{comparison.task}/{comparison.leaf}/{comparison.number}"
    if comparison.pin_ref != pin:
        return (
            f"its pin ref {comparison.pin_ref!r} is not {pin}, the pin of comparison "
            f"#{comparison.number} of {comparison.task}/{comparison.leaf}"
        )
    if isinstance(reference, InvariantReference | FamilyReference):
        return _record_contradiction(reference, comparison)
    return _trees_contradiction(reference, comparison.trees)


def _record_contradiction(
    reference: InvariantReference | FamilyReference, comparison: ComparisonRef
) -> str | None:
    trees = comparison.trees
    if reference.repository != comparison.repository:
        return (
            f"its {reference.kind} is of {reference.repository!r} and its comparison of "
            f"{comparison.repository!r}"
        )
    if reference.memory_tree not in {trees.memory_base, trees.memory_candidate}:
        return (
            f"its memory tree {reference.memory_tree} is neither memory tree of its comparison "
            f"({trees.memory_base}..{trees.memory_candidate})"
        )
    return None


def _trees_contradiction(reference: Any, trees: ComparisonTrees) -> str | None:
    if isinstance(reference, ReviewBlockReference) and reference.block.trees != trees:
        return "its block's trees are not the four trees of its comparison"
    hunk = reference.hunk if isinstance(reference, CodeWithoutInvariantReference) else None
    if isinstance(hunk, HunkIdentity) and (hunk.trees.code_base, hunk.trees.code_candidate) != (
        trees.code_base,
        trees.code_candidate,
    ):
        return "its hunk's code trees are not the code trees of its comparison"
    return None


def _git(root: Path, *arguments: str) -> str:
    return "git -C " + " ".join(shlex.quote(part) for part in (root.as_posix(), *arguments))


def _knowledge_read(memory: Path, view: str, record: str, revision: tuple[str, str]) -> str:
    key, value = revision
    return (
        f"knowledge_read memoryRoot={shlex.quote(memory.as_posix())} view={view} "
        f"recordId={record} {key}={value}"
    )


def _record_file_in_tree(memory: Path, tree: str, folder: str, record: str) -> str | None:
    """The record's file in one memory tree (a read-only ``git ls-tree``), when it holds one."""

    try:
        listed = run_git(
            memory,
            ["ls-tree", "--name-only", tree, f"knowledge/{folder}/"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return None
    names = [
        path
        for path in listed.stdout.splitlines()
        if PurePosixPath(path).name == f"{record}.json"
        or (PurePosixPath(path).name.startswith(f"{record}-") and path.endswith(".json"))
    ]
    return sorted(names)[0] if names else None


def _record_at(memory: Path, tree: str, view: str, record: str) -> tuple[str, str]:
    """The record's file in one memory tree and its revision id there (read-only Git).

    The id is the one ``knowledge_read`` hands out for the record's revision in that tree. A tree
    that holds no such record at a revision is refused: no revision is fabricated (rule 2).
    """

    found = _record_file_in_tree(memory, tree, _RECORD_FOLDERS[view], record)
    document: object = None
    if found is not None:
        try:
            shown = run_git(
                memory,
                ["show", f"{tree}:{found}"],
                GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
            )
            document = json.loads(shown.stdout) if shown.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            document = None
    held = document if isinstance(document, dict) and document.get("id") == record else {}
    revision = held.get("revision")
    if found is None or not isinstance(revision, int) or isinstance(revision, bool):
        raise UnresolvableReference(
            f"the memory tree {tree} holds no {view} {record} at a revision"
        )
    return found, text_uuid("revision", f"{record}@{revision}")


def _checked_revision(view: str, record: str, tree: str, held: str, sent: str | None) -> str:
    """The tree's revision id; one the dashboard sent must be that one (rule 2)."""

    if sent is not None and sent != held:
        raise ContradictoryReference(
            f"its revision {sent} is not the revision {held} the memory tree {tree} holds of "
            f"{view} {record}"
        )
    return held


def _record_form(
    reference: InvariantReference | FamilyReference, roots: FormRoots
) -> tuple[str, ...]:
    comparison = reference.comparison
    memory = (
        roots.comparison_memory_root(reference.repository, comparison)
        if comparison is not None
        else roots.memory_root(reference.repository)
    )
    view, record, tree = reference.kind, reference.id, reference.memory_tree
    found, held = _record_at(memory, tree, view, record)
    revision = _checked_revision(view, record, tree, held, reference.revision_id)
    key = "invariantRevisionId" if view == "invariant" else "familyRevisionId"
    return (
        _knowledge_read(memory, view, record, (key, revision)),
        _git(memory, "show", f"{tree}:{found}"),
        f"names: {view} {record}, revision {revision}, memory tree {tree}"
        + (f", {_comparison_names(comparison)}" if comparison is not None else ""),
    )


def _comparison_names(comparison: ComparisonRef) -> str:
    trees = comparison.trees
    return (
        f"comparison #{comparison.number} of {comparison.task}/{comparison.leaf}, "
        f"pin {comparison.pin_ref}, code trees {trees.code_base}..{trees.code_candidate}, "
        f"memory trees {trees.memory_base}..{trees.memory_candidate}"
    )


def _review_block_form(reference: ReviewBlockReference, roots: FormRoots) -> tuple[str, ...]:
    comparison = reference.comparison
    memory = roots.comparison_memory_root(comparison.repository, comparison)
    code = roots.code_root(comparison.repository)
    block = reference.block
    after = block.side == "after"
    tree = block.trees.code_candidate if after else block.trees.code_base
    # The invariant's record and revision at the memory tree of the block's side: an entry only
    # the base records names a record the candidate may no longer hold.
    memory_tree = block.trees.memory_candidate if after else block.trees.memory_base
    invariant = reference.invariant
    found, held = _record_at(memory, memory_tree, "invariant", invariant)
    revision = _checked_revision(
        "invariant", invariant, memory_tree, held, reference.invariant_revision_id
    )
    span = (
        f"lines {block.range.start}-{block.range.end}"
        if block.range is not None
        else "no range resolved on that side"
    )
    return (
        _knowledge_read(memory, "invariant", invariant, ("invariantRevisionId", revision)),
        _git(memory, "show", f"{memory_tree}:{found}"),
        _git(code, "show", f"{tree}:{block.path}"),
        f"names: review block, entry {block.entry}, {block.side} side, {block.path} {span}, "
        f"invariant {invariant} revision {revision} at memory tree {memory_tree}, "
        f"{_comparison_names(comparison)}",
    )


def _code_without_invariant_form(
    reference: CodeWithoutInvariantReference, roots: FormRoots
) -> tuple[str, ...]:
    comparison = reference.comparison
    code = roots.code_root(comparison.repository)
    hunk = reference.hunk
    trees = comparison.trees
    where = (
        f"hunk -{hunk.before.start},{hunk.before.count} +{hunk.after.start},{hunk.after.count}"
        if isinstance(hunk, HunkIdentity)
        else "the whole file"
    )
    return (
        _git(code, "diff", trees.code_base, trees.code_candidate, "--", hunk.path),
        f"names: code without an invariant, {hunk.path}, {where}, {_comparison_names(comparison)}",
    )


def _task_form(reference: TaskReference, roots: FormRoots) -> tuple[str, ...]:
    path, document_id, title = roots.task_document(reference.reference)
    return (
        f"read {shlex.quote(path.as_posix())}",
        f'names: task {document_id} "{title}", document {reference.reference.key}',
    )


def _file_line_form(reference: FileLineReference, roots: FormRoots) -> tuple[str, ...]:
    if reference.end < reference.start:
        raise UnresolvableReference(f"file line range {reference.start}-{reference.end} is empty")
    checkout, path = roots.file_in_scope(reference.repository, reference.scope, reference.path)
    span = f"lines {reference.start}-{reference.end}"
    return (
        f"read {shlex.quote(path.as_posix())} {span}",
        f"names: file {reference.path} {span} of {reference.repository} "
        f"(scope {reference.scope}, checkout {checkout.as_posix()}, its working tree)",
    )


def _change_set_form(reference: ChangeSetReference, roots: FormRoots) -> tuple[str, ...]:
    comparison = reference.comparison
    trees = comparison.trees
    return (
        _git(roots.code_root(comparison.repository), "diff", trees.code_base, trees.code_candidate),
        _git(
            roots.memory_root(comparison.repository),
            "diff",
            trees.memory_base,
            trees.memory_candidate,
        ),
        f"names: change set, {_comparison_names(comparison)}",
    )


_FORMS: dict[str, Callable[[Any, FormRoots], tuple[str, ...]]] = {
    "invariant": _record_form,
    "family": _record_form,
    "review-block": _review_block_form,
    "code-without-invariant": _code_without_invariant_form,
    "task": _task_form,
    "file-line": _file_line_form,
    "change-set": _change_set_form,
}

# The call each kind's form opens with (MIK-R188 rule 2's "Opened with" column).
OPENING_CALLS = {
    "invariant": "knowledge_read",
    "family": "knowledge_read",
    "review-block": "knowledge_read",
    "code-without-invariant": "git -C",
    "task": "read",
    "file-line": "read",
    "change-set": "git -C",
}
