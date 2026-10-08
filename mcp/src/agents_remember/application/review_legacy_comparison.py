"""Reopening a closed leaf in a converted repository that recorded no tree comparison (MIK-R25 rule 4).

Once a repository's official memory line is converted, the reviewer reads no database. A closed
leaf that recorded a tree comparison reopens from it
(:func:`~agents_remember.application.review_tree_comparison.reopen_review_trees`). The two other
closed leaves are answered here:

* **A comparison recorded before the conversion** -- a dataset comparison generation. It keeps its
  code sides, read from the generation's own record (its manifest, not its retained snapshots), and
  its knowledge sides report ``legacy-unavailable``. The conversion happens at the cutover
  (MIK-R37) on this master's line, or at a crossing sync on another line.
* **A leaf with no comparison record but a recorded committed range.** When its recorded memory
  endpoints are converted, the four recorded committed trees are the comparison (no ref is needed
  between committed endpoints). When they are unconverted, the code sides are kept and the
  knowledge sides are ``legacy-unavailable``.

A knowledge side that is unavailable names no file, so no read can reach a database in its place.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    refusal,
)
from agents_remember.application.review_comparison_generation import (
    COMPARISON_MANIFEST_NAME,
    ComparisonGenerationManifest,
    generation_directory,
    read_generation_refs,
    read_manifest,
)
from agents_remember.application.review_tree_comparison import (
    ReviewTrees,
    TreeSideUnreadable,
    converted_base_side,
    reopened_trees,
    review_task_id,
    tree_limitations,
    tree_resolution,
    tree_sides_refusal,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.serving.changeset_endpoints import (
    RecordedEndpointAbsent,
    RecordedRange,
    recorded_committed_range,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "LEGACY_UNAVAILABLE",
    "knowledge_unavailable_detail",
    "knowledge_unavailable_limitations",
    "knowledge_unavailable_refusal",
    "legacy_comparison_resolution",
]

LEGACY_UNAVAILABLE = "legacy-unavailable"
_LEGACY_DETAIL = (
    "this comparison was recorded before the repository's conversion to text knowledge; its "
    "knowledge side was a database, which the reviewer no longer reads"
)


def legacy_comparison_resolution(
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    *,
    generation_id: str | None = None,
) -> ReviewCandidateResolution | ReviewRefusal:
    """A closed leaf of a converted repository with no tree comparison record, resolved or refused."""

    manifest = _latest_manifest(contract, generation_id)
    if isinstance(manifest, ReviewRefusal):
        return manifest
    if manifest is not None:
        repository = Path(manifest.source.code_repository_root)
        return _legacy(
            contract,
            code=(repository, manifest.source.baseline_code_tree_id),
            candidate=manifest.source.candidate_code_tree_id,
            detail=f"{_LEGACY_DETAIL} (comparison generation {manifest.generation_id})",
        )
    try:
        code_range = recorded_committed_range(contract, memory=False)
    except RecordedEndpointAbsent as absent:
        unrecorded = absent.kind == "not-recorded"
        return refusal(
            "candidate_not_live" if unrecorded else "candidate_unresolved",
            f"leaf {contract.leaf_id} records no comparison and no readable committed range: {absent}"
            if unrecorded
            else f"the recorded source range cannot resolve: {absent}",
            next_action=(
                "record the leaf's landed commit in its enclosure contract, or open the live review; no branch tip is substituted"
                if unrecorded
                else "restore the recorded commit in the repository the enclosure names"
            ),
            offending_input="code",
        )
    code_tree = _tree(code_range.repository, code_range.head_commit)
    if code_tree is None:
        return refusal(
            "candidate_unresolved",
            f"the recorded code commit {code_range.head_commit} does not resolve to a tree",
            next_action="restore the recorded commit in the repository the enclosure names",
            offending_input="code",
        )
    trees = _recorded_trees(config, contract, code_range, code_tree)
    if isinstance(trees, ReviewTrees):
        return tree_resolution(contract.repo_name, contract, trees)
    return _legacy(
        contract,
        code=(code_range.repository, code_range.base_commit),
        candidate=code_tree,
        detail=trees,
    )


def _latest_manifest(
    contract: WorktreeContract, generation_id: str | None
) -> ComparisonGenerationManifest | ReviewRefusal | None:
    """The leaf's newest (or named) generation record, read as a record: no snapshot is opened."""

    leaf_id = contract.leaf_id or contract.task_name
    if generation_id is not None:
        path = generation_directory(contract.task_root, leaf_id, generation_id)
        try:
            return read_manifest(path / COMPARISON_MANIFEST_NAME)
        except KnowledgeStorageError as error:
            return refusal(
                "comparison_refused",
                f"the comparison generation {generation_id} of leaf {leaf_id} cannot be read: {error}",
                next_action="name a generation this leaf recorded",
                offending_input=generation_id,
            )
    refs = read_generation_refs(contract.task_root, leaf_id)
    if not refs:
        return None
    try:
        return read_manifest(refs[-1].directory / COMPARISON_MANIFEST_NAME)
    except KnowledgeStorageError:
        return None


def _recorded_trees(
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    code_range: RecordedRange,
    code_tree: str,
) -> ReviewTrees | str:
    """The recorded committed endpoints as four trees when the memory is converted, else why not."""

    try:
        memory_range = recorded_committed_range(contract, memory=True)
    except RecordedEndpointAbsent as absent:
        return f"{_LEGACY_DETAIL}: {absent}"
    repository = memory_range.repository
    memory_base_tree = _tree(repository, memory_range.base_commit)
    memory_head_tree = _tree(repository, memory_range.head_commit)
    if memory_base_tree is None or memory_head_tree is None:
        return f"{_LEGACY_DETAIL}: the recorded memory endpoints do not resolve"
    if _git(repository, "cat-file", "-t", f"{memory_head_tree}:{LAYOUT_MARKER_PATH}") != "blob":
        return _LEGACY_DETAIL
    base_tree = _tree(code_range.repository, code_range.base_commit)
    if base_tree is None:
        return f"{_LEGACY_DETAIL}: the recorded code base does not resolve"
    try:
        converted = converted_base_side(
            config.coordination_root,
            repository,
            memory_range.base_commit,
            trees=(memory_base_tree, memory_head_tree),
            code=(code_range.repository, code_range.base_commit),
        )
    except TreeSideUnreadable as error:
        return f"the converted base cannot be produced: {error.detail}"
    record = ReviewTreeComparisonRecord(
        task_id=review_task_id(contract.task_root),
        leaf_id=contract.leaf_id or contract.task_name,
        number=0,
        code_base=_committed(code_range.repository, base_tree, code_range.base_commit),
        code_candidate=_committed(code_range.repository, code_tree, code_range.head_commit),
        memory_base=_committed(repository, memory_base_tree, memory_range.base_commit),
        memory_candidate=_committed(repository, memory_head_tree, memory_range.head_commit),
        converted_base=converted,
        recorded_at="recorded-committed-range",
    )
    return reopened_trees(config.coordination_root, record, None)


def _legacy(
    contract: WorktreeContract,
    *,
    code: tuple[Path, str],
    candidate: str,
    detail: str,
) -> ReviewCandidateResolution:
    repository, base = code
    absent = contract.task_root / ".review-knowledge-unavailable"
    return ReviewCandidateResolution(
        repository_id=contract.repo_name,
        leaf_id=contract.leaf_id,
        baseline_database=absent / "before",
        candidate_database=absent / "after",
        baseline_code_root=repository,
        candidate_code_root=repository,
        baseline_code_tree_id=base,
        candidate_code_tree_id=candidate,
        contract=contract,
        candidate_identity=None,
        knowledge_unavailable=tuple(
            (side, LEGACY_UNAVAILABLE, detail) for side in ("before", "after")
        ),
    )


# -- what the surface declares -----------------------------------------------------------------------


def knowledge_unavailable_refusal(resolved: ReviewCandidateResolution) -> ReviewRefusal | None:
    """The refusal a resolution with an unreadable knowledge side earns, or ``None``."""

    if resolved.trees is not None:
        return tree_sides_refusal(resolved.trees)
    if not resolved.knowledge_unavailable:
        return None
    return refusal(
        "candidate_dataset_absent",
        "; ".join(
            f"the {side} knowledge side is {state}: {detail}"
            for side, state, detail in resolved.knowledge_unavailable
        ),
        next_action=(
            "open the source review, which keeps both code sides; the knowledge of a comparison "
            "recorded before the conversion is not read, and today's knowledge is not substituted"
        ),
        offending_input="; ".join(
            f"{side}:{state}" for side, state, _ in resolved.knowledge_unavailable
        ),
    )


def knowledge_unavailable_limitations(resolved: ReviewCandidateResolution) -> tuple[str, ...]:
    """The declared facts of a tree comparison or a legacy one; none when every side was read."""

    if resolved.trees is not None:
        return tree_limitations(resolved.trees)
    if not resolved.knowledge_unavailable:
        return ()
    return (
        "history:legacy-comparison",
        *(f"history:intent:{side}:{state}" for side, state, _ in resolved.knowledge_unavailable),
    )


def knowledge_unavailable_detail(resolved: ReviewCandidateResolution) -> str | None:
    """Why a legacy or partly unavailable comparison compared no knowledge operand, or ``None``."""

    refused = knowledge_unavailable_refusal(resolved)
    return None if refused is None else refused.detail


def _committed(repository: Path, tree: str, commit: str) -> ReviewTreeSide:
    return ReviewTreeSide(repository=str(repository), tree=tree, commit=commit)


def _tree(repository: Path, commit: str) -> str | None:
    return _git(repository, "rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}")


def _git(repository: Path, *args: str) -> str | None:
    result = run_git(repository, list(args))
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None
