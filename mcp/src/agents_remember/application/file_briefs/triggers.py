"""Refresh derived briefs after admitted memory writes."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

import apsw

from agents_remember.application.file_briefs.facts import BriefSource, CodeSide
from agents_remember.application.file_briefs.folder import (
    BriefFolder,
    WriteReport,
)
from agents_remember.application.knowledge_currentness import CodeTree
from agents_remember.application.worktree_code_state import observed_code_tree
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.kernel.memory_ignore import briefs_ignored
from agents_remember.kernel.route_index import INDEX_FILE_NAME, ROUTE_OVERVIEW_NAME
from agents_remember.memory.knowledge_index import KnowledgeIndex, MemoryTreeError
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    ONBOARDING_ROOT,
    ROOT_ROUTE_PATH,
    route_sidecar_path,
)
from agents_remember.worktrees.knowledge_validation import PairedCode

# A record file's name starts with its ID: the prefix, a dash, and the ID's own characters.
_RECORD_ID: Final = re.compile(r"^[A-Z]+-[0-9A-Z]+")
_ROUTE_ARTIFACT_NAMES: Final = frozenset(
    {ROUTE_OVERVIEW_NAME, INDEX_FILE_NAME, PurePosixPath(route_sidecar_path(ROOT_ROUTE_PATH)).name}
)

# The failures a writing can meet and reports instead of raising: the filesystem, Git, and an index
# that cannot be read.
WRITING_FAILURES: Final = (
    OSError,
    UnicodeDecodeError,
    ValueError,
    subprocess.SubprocessError,
    GitPreparationError,
    MemoryTreeError,
    apsw.Error,
)


BriefWriting = WriteReport | str
"""A writing's report, or the sentence that says why it could not be done."""


@contextmanager
def brief_source(
    memory_root: Path, index: KnowledgeIndex, code_root: Path
) -> Iterator[BriefSource]:
    """One memory tree measured at the code the checkout holds now, its capture held for the writing."""

    with observed_code_tree(code_root) as observed:
        if observed is None:
            code = CodeSide(tree=None, commit=None, state="unmeasured")
        else:
            code = CodeSide(
                tree=CodeTree(observed.repository, observed.tree_id, observed.object_directory),
                commit=head_commit(code_root),
                state=observed.code_state,
            )
        yield BriefSource(index=index, memory_root=memory_root, code=code)


def refresh_written_memory_folder(
    memory_root: Path, paired_code: PairedCode, coordination_root: Path
) -> BriefWriting | None:
    """Write an admitted folder's briefs from its resulting tree and the operation's code."""

    from agents_remember.application.published_intent import (  # noqa: PLC0415 - shared selection
        select_knowledge_dataset,
    )

    try:
        if not (memory_root / LAYOUT_MARKER_PATH).is_file():
            return None
        if not briefs_ignored(memory_root):
            return _ineffective_ignore()
        selected = select_knowledge_dataset(memory_root, coordination_root=coordination_root)
        tree = selected.memory_tree
        assert tree is not None
        code = CodeSide(
            tree=CodeTree(paired_code.repository, paired_code.commit),
            commit=paired_code.commit,
            state="commit",
        )
        with KnowledgeIndex(selected.database_path, expected_key=tree.tree_key) as index:
            return BriefFolder(memory_root).write_all(BriefSource(index, memory_root, code))
    except WRITING_FAILURES as error:
        return _problem("the briefs could not be written", error)


@dataclass(frozen=True)
class IndexBefore:
    """The index of a memory folder's tree as it stood before a write: its file and tree key."""

    database_path: Path
    tree_key: str


def index_before_write(memory_root: Path, coordination_root: Path) -> IndexBefore | None:
    """Select the index of the memory as it stands, before a write changes it; ``None`` if unreadable.

    A record the write removes, or one whose anchors or ``stands_on`` move, is recorded in the tree
    before the write at files the tree after it no longer names. Their briefs are written again
    only if the write's refresh can still read that earlier tree.
    """

    from agents_remember.application.published_intent import (  # noqa: PLC0415 - the selection imports this module
        select_knowledge_dataset,
    )

    try:
        selected = select_knowledge_dataset(memory_root, coordination_root=coordination_root)
    except WRITING_FAILURES:
        return None
    tree = selected.memory_tree
    if tree is None:
        return None
    return IndexBefore(selected.database_path, tree.tree_key)


def after_memory_files_written(
    memory_root: Path,
    code_root: Path,
    coordination_root: Path,
    memory_files: Iterable[str],
    before: IndexBefore | None = None,
) -> BriefWriting | None:
    """Write the briefs of the source files a write's changed memory files speak about.

    ``memory_files`` are memory-relative: a card or its sidecar (``onboarding/<source path>.md`` or
    ``.json``) names its source file; a record file (``knowledge/<kind>/<ID>-<slug>.json``) names
    the files the record is recorded at in the tree the write left and, given ``before``, in the
    tree before the write, so a removed or moved record leaves no brief that still shows it.
    ``None`` when nothing changed.
    """

    files = sorted(set(memory_files))
    if not files:
        return None
    from agents_remember.application.published_intent import (  # noqa: PLC0415 - the selection imports this module
        select_knowledge_dataset,
    )

    try:
        earlier: set[str] = set()
        if before is not None:
            with KnowledgeIndex(before.database_path, expected_key=before.tree_key) as old:
                earlier = source_paths_of(old, files)
        selected = select_knowledge_dataset(memory_root, coordination_root=coordination_root)
        tree = selected.memory_tree
        if tree is None:
            return None
        with KnowledgeIndex(selected.database_path, expected_key=tree.tree_key) as index:
            paths = source_paths_of(index, files) | earlier
            if not paths:
                return None
            if not briefs_ignored(memory_root):
                return _ineffective_ignore()
            with brief_source(memory_root, index, code_root) as source:
                return BriefFolder(memory_root).write_some(source, paths)
    except WRITING_FAILURES as error:
        return _problem("the briefs of the written files could not be written", error)


def after_citation_fix(
    memory_root: Path, code_root: Path, coordination_root: str | Path, result: dict[str, Any]
) -> None:
    """Write the briefs of the cards a leaf's ``citation_fix`` changed; a failure joins its result.

    A fix that changed no card writes nothing and reads no coordination root.
    """

    authoring = result.get("authoring", {})
    changed = [
        one
        for paths in (
            authoring.get("authoredCards", ()),
            authoring.get("createdSidecars", ()),
            result.get("rewrittenSidecars", ()),
        )
        for one in paths
        if isinstance(one, str)
    ]
    if not changed:
        return
    writing = after_memory_files_written(memory_root, code_root, Path(coordination_root), changed)
    if isinstance(writing, str):
        result["briefsProblem"] = writing


def source_paths_of(index: KnowledgeIndex, memory_files: Iterable[str]) -> set[str]:
    """The source files the changed memory files speak about (see :func:`after_memory_files_written`)."""

    paths: set[str] = set()
    record_ids: set[str] = set()
    for one in memory_files:
        parts = PurePosixPath(one).parts
        if parts[:1] == (ONBOARDING_ROOT,) and one.endswith((".md", ".json")):
            card = PurePosixPath(*parts[1:])
            if card.name not in _ROUTE_ARTIFACT_NAMES:
                paths.add(card.with_suffix("").as_posix())
        elif parts[:1] == ("knowledge",) and len(parts) == 3 and one.endswith(".json"):
            found = _RECORD_ID.match(parts[2])
            if found is not None:
                record_ids.add(found.group(0))
    return paths | records_source_paths(index, record_ids)


def records_source_paths(index: KnowledgeIndex, record_ids: Iterable[str]) -> set[str]:
    """The source files at which ``record_ids`` are recorded in ``index``'s tree.

    A rule is recorded where it is realized or proved; a weakness at its anchor; a decision or a
    facet at the anchors it links to and at the files of the rules it links to; a dependent at the
    files it stands on. A family is recorded at the files of its member rules.
    """

    paths: set[str] = set()
    for record_id in record_ids:
        record = index.record(record_id).value
        if record is None:
            continue
        if record.kind == "invariant":
            paths |= _rule_paths(index, record_id)
            continue
        if record.kind == "family":
            for member in record.document.get("members", ()):
                paths |= _rule_paths(index, member)
            continue
        paths |= _anchored_paths(record.document)
        paths |= _linked_paths(index, record.document)
    return paths


def _rule_paths(index: KnowledgeIndex, invariant: str) -> set[str]:
    """The files at which the rule ``invariant`` is realized or proved."""

    knowledge = index.invariant(invariant).value
    return {one.path for one in (*knowledge.realizations, *knowledge.proofs)}


def _anchored_paths(document: dict[str, Any]) -> set[str]:
    """The file of a record's own anchor and the files it stands on."""

    paths: set[str] = set()
    anchor = document.get("anchor") or {}
    if anchor.get("path"):
        paths.add(str(anchor["path"]))
    for link in document.get("stands_on") or ():
        target = link.get("path") or (link.get("anchor") or {}).get("path")
        if target:
            paths.add(str(target))
    return paths


def _linked_paths(index: KnowledgeIndex, document: dict[str, Any]) -> set[str]:
    """The anchored files a record links to and the files of the rules it links to."""

    paths: set[str] = set()
    for link in document.get("links") or ():
        target = link.get("target")
        if isinstance(target, dict) and target.get("path"):
            paths.add(str(target["path"]))
        elif isinstance(target, str) and target.startswith("INV-"):
            paths |= _rule_paths(index, target)
    return paths


def head_commit(code_root: Path) -> str | None:
    """The commit the code checkout is at, or ``None`` when it has none."""

    result = run_git(code_root, ["rev-parse", "--verify", "--quiet", "HEAD"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _ineffective_ignore() -> str:
    return "Writing briefs requires an effective /briefs/ rule in the memory's .gitignore."


def _problem(what: str, error: BaseException) -> str:
    return f"{what} ({type(error).__name__}: {error})"


__all__ = [
    "WRITING_FAILURES",
    "BriefWriting",
    "IndexBefore",
    "after_citation_fix",
    "after_memory_files_written",
    "brief_source",
    "head_commit",
    "index_before_write",
    "records_source_paths",
    "refresh_written_memory_folder",
    "source_paths_of",
]
