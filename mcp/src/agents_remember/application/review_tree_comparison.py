"""A review comparison as four Git trees, pinned by Git refs and reopened from its tree ids (MIK-R25).

Knowledge is text in Git (D18), so every committed state of both repositories is already a Git
tree. A review comparison is therefore four trees -- code base B, code candidate C, memory base K_B
and memory candidate K_C -- and nothing is copied:

* **Sides.** B is the contract's code base commit. C is the leaf's code worktree captured as a tree
  (the shipped private-index capture the dataset review already binds). K_B is the memory commit the
  worklist pairs with B (:func:`paired_memory_commit`, MIK-R08), so the reviewer and the worklist
  compare the same sides. K_C is the leaf's memory worktree captured the same way.
* **Pinning (rule 1).** An uncommitted candidate is pinned by ``refs/ar/review/<task-id>/<leaf-id>/<n>``
  in its own repository, pointing at the tree itself, before the comparison is published; a side
  that the durable source line already holds is committed and needs no ref. If a pin cannot be
  written the comparison is refused, naming the repository and the ref (Failure and Recovery).
  A comparison whose four trees equal the leaf's latest recorded one reuses that record, so reading
  the reviewer again does not add refs.
* **The converted base (rule 4).** When K_B is unconverted and K_C is converted, K_B is compared as
  its conversion (MIK-R24 rule 7, at the converted-base cache the worklist fills). The converted
  files are written as a Git tree into the memory repository's object store, so both memory sides
  are Git trees and their diff is a Git diff. The record names the conversion's inputs; a reopen
  re-derives it and checks the tree id instead of pinning it. A live read does not write the tree
  again while Git holds it (MIK-R40 rule 3): the conversion is a pure function of its three
  inputs, so when the leaf's latest record names the same inputs, its tree id is this
  conversion's, and it is used once Git confirms it still holds that tree. Otherwise -- no
  record, other inputs, or a tree Git no longer has -- the tree is written exactly as before.
* **Knowledge sides (rule 2).** Each memory side is read through the derived index of its tree
  (MIK-R23), which is itself a dataset of the store's schema, so the landed review composition runs
  over it unchanged (rule 6). No database copy is created, retained or read.
* **Reopen (rule 4).** A comparison reopens from its record's tree ids. A tree Git can no longer
  produce is ``unavailable-history`` and is named; today's tree is never substituted.

The record is ``ar-review-tree-comparison/v1`` under the task's durable reports
(``notes/reports/review-comparisons/<leaf>/<n>.json``). The task's archive hook
(:mod:`agents_remember.application.review_artifact_cleanup`) deletes the refs (rule 5).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, Literal

from pydantic import ValidationError

from agents_remember.application.knowledge_worklist.base_cache import (
    converted_base_files,
    default_base_cache_directory,
)
from agents_remember.application.knowledge_worklist.leaf import paired_memory_commit
from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory.conversion.base import own_paired_code_commit
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.memory.knowledge_index import (
    KnowledgeIndexCache,
    MemoryTreeError,
    default_cache_directory,
)
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_trees import (
    REVIEW_REF_NAMESPACE,
    ReviewCodeSide,
    ReviewConvertedBase,
    ReviewKnowledgeSide,
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
    ReviewTreeSideState,
)
from agents_remember.models.knowledge_files.canonical import parse_json
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.tasks.task_paths import slugify
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember.worktrees.worktree_contract import WorktreeContract

if TYPE_CHECKING:  # pragma: no cover - annotations only; the resolution module imports this one
    from agents_remember.application.review_candidate_resolution import (
        ReviewCandidateResolution,
    )

__all__ = [
    "REVIEW_COMPARISONS_DIRECTORY",
    "ReviewTrees",
    "TreeKnowledge",
    "TreeSideUnreadable",
    "comparison_directory",
    "comparison_records",
    "converted_base_side",
    "live_review_trees",
    "memory_converted",
    "official_line_converted",
    "recheck_memory_candidate",
    "reopen_review_trees",
    "reopened_trees",
    "review_ref",
    "review_task_id",
    "tree_limitations",
    "tree_resolution",
    "tree_sides_refusal",
]

REVIEW_COMPARISONS_DIRECTORY: Final = "review-comparisons"
_ZERO_OBJECT: Final = "0" * 40
# A pin another reader of the same new comparison is creating holds Git's ref lock for moments.
_RECORDING: Final = threading.Lock()
_PIN_ATTEMPTS: Final = 5
_PIN_RETRY_SECONDS: Final = 0.02
_SIDES: Final = ("before", "after")
_PIN_ACTION: Final = (
    "make the named repository writable (or remove the conflicting ref if it is stale), then "
    "reopen the review; a comparison is never published with an unpinned uncommitted candidate"
)


@dataclass(frozen=True)
class TreeKnowledge:
    """One memory side as the reviewer reads it: its wire state, and its index file when readable."""

    wire: ReviewKnowledgeSide
    database: Path | None


@dataclass(frozen=True)
class ReviewTrees:
    """One comparison of four Git trees, and each memory side's index.

    ``record_path`` is where the record lives (``None`` for a comparison between recorded
    committed endpoints that nothing had to record). ``live`` is set for a comparison captured from
    a live enclosure: the memory candidate is then rechecked before the payload is published.
    """

    record: ReviewTreeComparisonRecord
    record_path: Path | None
    memory_repository: Path
    before: TreeKnowledge
    after: TreeKnowledge
    live: bool = False
    memory_worktree: Path | None = field(default=None)
    code_sides: tuple[ReviewCodeSide, ...] = ()

    def sides(self) -> tuple[ReviewKnowledgeSide, ...]:
        return (self.before.wire, self.after.wire)


# -- applicability -------------------------------------------------------------------------------


def official_line_converted(memory_repository: Path | None, official_line: str) -> bool:
    """Whether the official memory line's tip holds the layout marker."""

    if memory_repository is None or not official_line:
        return False
    return _git(memory_repository, "cat-file", "-t", f"{official_line}:{LAYOUT_MARKER_PATH}") == (
        "blob"
    )


def memory_converted(contract: WorktreeContract) -> bool:
    """Whether a leaf's review is a tree review: its memory worktree or its official line converted.

    Before the cutover (MIK-R37) neither holds for any production leaf, so the dataset review runs
    exactly as before.
    """

    worktree = contract.memory_worktree
    if worktree is not None and (worktree / LAYOUT_MARKER_PATH).is_file():
        return True
    return official_line_converted(
        contract.memory_repo_path or worktree, contract.memory_source_branch
    )


def review_task_id(task_root: Path) -> str:
    """The task segment of every review ref: the task's directory name, and nothing read from it.

    Pins (:func:`live_review_trees`), the recorded-range comparison and the archive hook all name
    the namespace this way (R5 ruling): no file content under the task folder -- not even
    ``task.json``'s ``id`` -- can move a pin, or its archival, into another task's namespace.
    """

    return task_root.name


def review_ref(task_id: str, leaf_id: str, number: int) -> str:
    """``refs/ar/review/<task-id>/<leaf-id>/<n>``, refusing a segment that could leave its place."""

    for value in (task_id, leaf_id):
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError(f"a review ref segment must be one path segment, not {value!r}")
    return f"{REVIEW_REF_NAMESPACE}/{task_id}/{leaf_id}/{number}"


def comparison_directory(task_root: Path, leaf_id: str) -> Path:
    """Where one leaf's comparison records live, beside the task's other durable reports."""

    return durable_reports_root(task_root) / REVIEW_COMPARISONS_DIRECTORY / slugify(leaf_id)


def comparison_records(task_root: Path, leaf_id: str) -> tuple[ReviewTreeComparisonRecord, ...]:
    """Every readable record of one leaf, by number. An unreadable file is skipped, not guessed."""

    directory = comparison_directory(task_root, leaf_id)
    if not directory.is_dir():
        return ()
    records: list[ReviewTreeComparisonRecord] = []
    for path in sorted(directory.glob("*.json")):
        try:
            records.append(ReviewTreeComparisonRecord.model_validate_json(path.read_bytes()))
        except (OSError, ValidationError):
            continue
    return tuple(sorted(records, key=lambda record: record.number))


# -- the live comparison ---------------------------------------------------------------------------


def live_review_trees(
    coordination_root: Path, contract: WorktreeContract, code_candidate_tree: str
) -> ReviewTrees | ReviewRefusal | None:
    """Capture, pin and record a live leaf's four trees; ``None`` when its memory is unconverted."""

    worktree = contract.memory_worktree
    memory_repository = contract.memory_repo_path or worktree
    if worktree is None or memory_repository is None or not memory_converted(contract):
        return None
    try:
        drafted = _live_draft(coordination_root, contract, memory_repository, code_candidate_tree)
    except TreeSideUnreadable as error:
        return _unresolved(error.side, error.detail)
    recorded = _record(contract, drafted)
    if isinstance(recorded, ReviewRefusal):
        return recorded
    record, path = recorded
    trees = _open_sides(coordination_root, record, memory_repository, path)
    return replace(trees, live=True, memory_worktree=worktree)


class TreeSideUnreadable(Exception):
    def __init__(self, side: str, detail: str) -> None:
        super().__init__(detail)
        self.side = side
        self.detail = detail


def _live_draft(
    coordination_root: Path,
    contract: WorktreeContract,
    memory_repository: Path,
    code_candidate_tree: str,
) -> ReviewTreeComparisonRecord:
    code_repository = contract.code_repo_path
    base_commit = _require(code_repository, f"{contract.code_base_commit}^{{commit}}", "code base")
    base_tree = _require(code_repository, f"{base_commit}^{{tree}}", "code base")
    try:
        memory_base = paired_memory_commit(
            memory_repository,
            contract.memory_source_branch,
            code_repository,
            base_commit,
        )
    except Exception as error:  # the worklist's own pairing failure, named rather than guessed
        raise TreeSideUnreadable("memory base", str(error)) from error
    memory_base_tree = _require(memory_repository, f"{memory_base}^{{tree}}", "memory base")
    assert contract.memory_worktree is not None
    candidate_tree = _capture(contract.memory_worktree)
    leaf_id = contract.leaf_id or contract.task_name
    conversion = _conversion(
        memory_repository,
        memory_base,
        trees=(memory_base_tree, candidate_tree),
        code=(code_repository, base_commit),
    )
    converted = (
        None
        if conversion is None
        else _converted_base(
            coordination_root,
            memory_repository,
            conversion,
            held=_latest_converted_base(contract.task_root, leaf_id),
        )
    )
    return ReviewTreeComparisonRecord(
        task_id=review_task_id(contract.task_root),
        leaf_id=leaf_id,
        number=1,
        code_base=ReviewTreeSide(
            repository=str(code_repository), tree=base_tree, commit=base_commit
        ),
        code_candidate=_candidate_side(
            code_repository, contract.code_source_branch, code_candidate_tree
        ),
        memory_base=ReviewTreeSide(
            repository=str(memory_repository), tree=memory_base_tree, commit=memory_base
        ),
        memory_candidate=_candidate_side(
            memory_repository, contract.memory_source_branch, candidate_tree
        ),
        converted_base=converted,
        recorded_at=_now(),
    )


def _capture(memory_worktree: Path) -> str:
    try:
        with TemporaryDirectory(prefix="ar-review-memory-") as scratch:
            return worktree_candidate_tree(memory_worktree, Path(scratch) / "index")
    except (RuntimeError, OSError) as error:
        raise TreeSideUnreadable(
            "memory candidate", f"the memory worktree {memory_worktree} cannot be captured: {error}"
        ) from error


def recheck_memory_candidate(trees: ReviewTrees) -> ReviewRefusal | None:
    """Refuse a live comparison whose memory worktree moved while the review was composed."""

    if not trees.live or trees.memory_worktree is None:
        return None
    try:
        current = _capture(trees.memory_worktree)
    except TreeSideUnreadable as error:
        return _unresolved(error.side, error.detail)
    recorded = trees.record.memory_candidate.tree
    if current == recorded:
        return None
    return ReviewRefusal(
        code="candidate_unresolved",
        detail=(
            "the memory candidate moved while the review was being composed: the captured tree "
            f"{recorded} is no longer the memory worktree's tree {current}"
        ),
        next_action="reopen the review so both candidates are captured again",
        offending_input="memory candidate",
        expected=recorded,
        observed=current,
    )


def _candidate_side(repository: Path, durable_line: str, tree: str) -> ReviewTreeSide:
    """A candidate is committed when the durable line's tip holds exactly this tree."""

    tip = _git(repository, "rev-parse", "--verify", "--quiet", f"{durable_line}^{{commit}}")
    if tip is not None and _git(repository, "rev-parse", f"{tip}^{{tree}}") == tree:
        return ReviewTreeSide(repository=str(repository), tree=tree, commit=tip)
    return ReviewTreeSide(repository=str(repository), tree=tree)


def converted_base_side(
    coordination_root: Path,
    memory_repository: Path,
    memory_base: str,
    *,
    trees: tuple[str, str],
    code: tuple[Path, str],
) -> ReviewConvertedBase | None:
    """K_B's conversion when K_B is unconverted and K_C converted (MIK-R24 rule 7), else ``None``.

    ``trees`` is (K_B's tree, K_C's tree); ``code`` is the code repository and B. The converted
    files are written as a Git tree; a live leaf's comparison uses the tree its latest record
    names instead, while Git holds it (:func:`_converted_base`).
    """

    conversion = _conversion(memory_repository, memory_base, trees=trees, code=code)
    if conversion is None:
        return None
    return _converted_base(coordination_root, memory_repository, conversion, held=None)


@dataclass(frozen=True)
class _Conversion:
    """Everything K_B's conversion is a function of: the memory commit, the pinned conversion
    version and the code commit it is anchored at (with the code repository that holds it)."""

    memory_base: str
    version: str
    code_repository: Path
    code_commit: str

    def recorded_as(self, held: ReviewConvertedBase) -> bool:
        """Whether a recorded converted base was made from exactly these inputs."""

        return (held.commit, held.version, held.code_commit) == (
            self.memory_base,
            self.version,
            self.code_commit,
        )


def _conversion(
    memory_repository: Path,
    memory_base: str,
    *,
    trees: tuple[str, str],
    code: tuple[Path, str],
) -> _Conversion | None:
    """The inputs of K_B's conversion, or ``None`` when K_B is compared as it is."""

    base_tree, candidate_tree = trees
    if _has_marker(memory_repository, base_tree):
        return None
    version = _pinned_version(memory_repository, candidate_tree)
    if version is None:
        return None
    code_repository, code_base = code
    own = own_paired_code_commit(memory_repository, memory_base)
    code_commit = own if own is not None and CodeObjects(code_repository).commit(own) else code_base
    return _Conversion(memory_base, version, code_repository, code_commit)


def _converted_base(
    coordination_root: Path,
    memory_repository: Path,
    conversion: _Conversion,
    *,
    held: ReviewConvertedBase | None,
) -> ReviewConvertedBase:
    """K_B's conversion as a Git tree: the held one when it is this conversion, else written."""

    tree = _held_tree(memory_repository, held, conversion)
    if tree is None:
        tree = _converted_tree(
            coordination_root,
            memory_repository,
            conversion.memory_base,
            (conversion.code_repository, conversion.code_commit),
            conversion.version,
        )
    return ReviewConvertedBase(
        commit=conversion.memory_base,
        version=conversion.version,
        code_commit=conversion.code_commit,
        tree=tree,
    )


def _held_tree(
    memory_repository: Path, held: ReviewConvertedBase | None, conversion: _Conversion
) -> str | None:
    """The recorded converted base tree, when it is this request's conversion and Git holds it.

    The conversion is a pure function of its three inputs, so a record made from the same inputs
    names this conversion's tree. A record made from other inputs names another conversion and is
    never used, and a tree Git can no longer produce is written again by the caller, so the answer
    is never a guess: it is the tree Git holds for these exact inputs, or ``None``.
    """

    if held is None or not conversion.recorded_as(held):
        return None
    return held.tree if _missing_tree(memory_repository, held.tree) is None else None


def _latest_converted_base(task_root: Path, leaf_id: str) -> ReviewConvertedBase | None:
    """The converted base of the leaf's readable record with the highest number, if it has one.

    Only that one record file is read: the tree it names is confirmed with Git before it is used,
    and the full list of records is read where the comparison is recorded (:func:`_record`).
    """

    directory = comparison_directory(task_root, leaf_id)
    if not directory.is_dir():
        return None
    numbered = sorted(
        (int(path.stem), path) for path in directory.glob("*.json") if path.stem.isdigit()
    )
    for _number, path in reversed(numbered):
        try:
            return ReviewTreeComparisonRecord.model_validate_json(path.read_bytes()).converted_base
        except (OSError, ValidationError):
            continue
    return None


def _converted_tree(
    coordination_root: Path,
    memory_repository: Path,
    memory_base: str,
    code: tuple[Path, str],
    version: str,
) -> str:
    try:
        files = converted_base_files(
            memory_repository,
            memory_base,
            code=code,
            version=version,
            cache_directory=default_base_cache_directory(coordination_root),
        )
    except (ValueError, OSError) as error:
        raise TreeSideUnreadable(
            "memory base", f"the converted base of {memory_base} cannot be produced: {error}"
        ) from error
    return write_files_tree(memory_repository, files)


def write_files_tree(repository: Path, files: Mapping[str, bytes]) -> str:
    """Write ``files`` as blobs and trees into ``repository``'s object store; return the root tree.

    Only objects are written: no ref, no index and no working tree is touched, and the same files
    always give the same tree id.
    """

    with TemporaryDirectory(prefix="ar-review-tree-") as scratch:
        root = Path(scratch)
        names = sorted(files)
        for position, name in enumerate(names):
            (root / str(position)).write_bytes(files[name])
        hashed = run_git(
            repository,
            ["hash-object", "-w", "--no-filters", "--stdin-paths"],
            GitRunnerOptions(input_text="".join(f"{root / str(i)}\n" for i in range(len(names)))),
        )
    blobs = hashed.stdout.split()
    if hashed.returncode != 0 or len(blobs) != len(names):
        raise TreeSideUnreadable(
            "memory base", f"the converted base cannot be written: {hashed.stderr}"
        )
    return _mktree(repository, dict(zip(names, blobs, strict=True)))


def _mktree(repository: Path, blobs: Mapping[str, str]) -> str:
    children: dict[str, dict[str, str]] = {}
    here: dict[str, str] = {}
    for path, blob in blobs.items():
        head, _, rest = path.partition("/")
        if rest:
            children.setdefault(head, {})[rest] = blob
        else:
            here[head] = blob
    lines = [f"100644 blob {blob}\t{name}" for name, blob in here.items()]
    lines += [f"040000 tree {_mktree(repository, sub)}\t{name}" for name, sub in children.items()]
    made = run_git(repository, ["mktree"], GitRunnerOptions(input_text="\n".join(lines) + "\n"))
    if made.returncode != 0:
        raise TreeSideUnreadable(
            "memory base", f"the converted base cannot be written: {made.stderr}"
        )
    return made.stdout.strip()


# -- recording and pinning ---------------------------------------------------------------------


def _record(
    contract: WorktreeContract, draft: ReviewTreeComparisonRecord
) -> tuple[ReviewTreeComparisonRecord, Path] | ReviewRefusal:
    """Record the comparison, or reuse the latest record of the same four trees.

    Readers of one dashboard that open the same new comparison at once take turns here (MIK-R42):
    the number is one more than the highest that exists, so two of them racing past each other
    would record the same trees twice, under two numbers and two pins.
    """

    with _RECORDING:
        return _recorded(contract, draft)


def _recorded(
    contract: WorktreeContract, draft: ReviewTreeComparisonRecord
) -> tuple[ReviewTreeComparisonRecord, Path] | ReviewRefusal:
    directory = comparison_directory(contract.task_root, draft.leaf_id)
    existing = comparison_records(contract.task_root, draft.leaf_id)
    if existing and existing[-1].same_trees(draft):
        # The same four trees as the latest record: reuse it, re-creating a pin that has gone.
        latest = existing[-1]
        repinned = _pin(latest)
        if repinned is not None:
            return repinned
        return latest, directory / f"{latest.number}.json"
    number = 1 + max(
        [
            *(record.number for record in existing),
            *_ref_numbers(draft),
        ],
        default=0,
    )
    try:
        ref = review_ref(draft.task_id, draft.leaf_id, number)
    except ValueError as error:
        return _pin_refusal(draft.code_candidate.repository, "refs/ar/review/…", str(error))
    record = draft.model_copy(
        update={
            "number": number,
            "code_candidate": _with_ref(draft.code_candidate, ref),
            "memory_candidate": _with_ref(draft.memory_candidate, ref),
        }
    )
    pinned = _pin(record)
    if pinned is not None:
        return pinned
    path = directory / f"{number}.json"
    if path.is_file():
        # A concurrent reader of the very same new comparison recorded it first (MIK-R42): its
        # record is the comparison, and it is the one every reader answers with.
        raced = comparison_records(contract.task_root, draft.leaf_id)
        if raced and raced[-1].number == number and raced[-1].same_trees(draft):
            return raced[-1], path
    atomic_write_bytes(path, canonical_json_bytes(record.model_dump(mode="json", by_alias=True)))
    return record, path


def _with_ref(side: ReviewTreeSide, ref: str) -> ReviewTreeSide:
    return side if side.commit is not None else side.model_copy(update={"ref": ref})


def _ref_numbers(draft: ReviewTreeComparisonRecord) -> list[int]:
    prefix = f"{REVIEW_REF_NAMESPACE}/{draft.task_id}/{draft.leaf_id}/"
    numbers: list[int] = []
    for repository in {draft.code_candidate.repository, draft.memory_candidate.repository}:
        listed = _git(Path(repository), "for-each-ref", "--format=%(refname)", prefix) or ""
        numbers += [
            int(name[len(prefix) :]) for name in listed.split() if name[len(prefix) :].isdigit()
        ]
    return numbers


def _pin(record: ReviewTreeComparisonRecord) -> ReviewRefusal | None:
    """Pin each uncommitted candidate (create-only), or refuse naming the repository and the ref.

    A pin already naming its tree is kept. A ref naming anything else is never moved: the
    comparison is refused. On any failure the pins this call created are removed again.
    """

    made: list[tuple[Path, str]] = []
    for side in (record.code_candidate, record.memory_candidate):
        if side.ref is None:
            continue
        repository = Path(side.repository)
        created, current, reason = _create_pin(repository, side.ref, side.tree)
        if created is None:
            for done_repository, done_ref in made:
                run_git(done_repository, ["update-ref", "-d", done_ref])
            if current is not None:
                reason = f"the ref already names {current}, not the candidate {side.tree}"
            return _pin_refusal(side.repository, side.ref, reason)
        if created:
            made.append((repository, side.ref))
    return None


def _create_pin(repository: Path, ref: str, tree: str) -> tuple[bool | None, str | None, str]:
    """Create one pin if it is absent: ``(True, …)`` made, ``(False, …)`` already named ``tree``.

    Pinning is idempotent (MIK-R42): readers opening the same new comparison at once all try to
    create the same pin, and a ref that already names this very tree is the answer they wanted,
    whoever made it. A brief lock held by another of them is retried. ``None`` is a refusal: the
    ref names something else, or Git failed for another reason (``current`` and the reason say).
    """

    reason = "update-ref failed"
    for attempt in range(_PIN_ATTEMPTS):
        current = _git(repository, "rev-parse", "--verify", "--quiet", ref)
        if current == tree:
            return False, current, ""
        if current is not None:
            return None, current, reason
        result = run_git(repository, ["update-ref", ref, tree, _ZERO_OBJECT])
        if result.returncode == 0:
            return True, None, ""
        reason = result.stderr.strip() or reason
        if "cannot lock ref" not in reason and "already exists" not in reason:
            break
        time.sleep(_PIN_RETRY_SECONDS * (attempt + 1))
    current = _git(repository, "rev-parse", "--verify", "--quiet", ref)
    return (False, current, "") if current == tree else (None, current, reason)


def _pin_refusal(repository: str, ref: str, reason: str) -> ReviewRefusal:
    return ReviewRefusal(
        code="candidate_unresolved",
        detail=(
            f"the uncommitted candidate could not be pinned in {repository} by {ref} ({reason}), "
            "so the comparison is not published"
        ),
        next_action=_PIN_ACTION,
        offending_input=ref,
    )


# -- opening the knowledge sides --------------------------------------------------------------------


def _open_sides(
    coordination_root: Path,
    record: ReviewTreeComparisonRecord,
    memory_repository: Path,
    record_path: Path | None,
    *,
    states: Mapping[str, tuple[ReviewTreeSideState, str]] | None = None,
) -> ReviewTrees:
    before_tree = (
        record.converted_base.tree if record.converted_base is not None else record.memory_base.tree
    )
    trees = {"before": before_tree, "after": record.memory_candidate.tree}
    opened = {
        side: _open_side(
            coordination_root, memory_repository, side, trees[side], (states or {}).get(side)
        )
        for side in _SIDES
    }
    return ReviewTrees(
        record=record,
        record_path=record_path,
        memory_repository=memory_repository,
        before=opened["before"],
        after=opened["after"],
    )


def _open_side(
    coordination_root: Path,
    repository: Path,
    side: Literal["before", "after"] | str,
    tree: str,
    unavailable: tuple[ReviewTreeSideState, str] | None,
) -> TreeKnowledge:
    wire_side: Literal["before", "after"] = "before" if side == "before" else "after"
    if unavailable is not None:
        state, detail = unavailable
        return TreeKnowledge(
            wire=ReviewKnowledgeSide(side=wire_side, state=state, tree=tree, detail=detail),
            database=None,
        )
    try:
        cache = KnowledgeIndexCache(default_cache_directory(coordination_root))
        with cache.for_git_tree(repository, tree) as index:
            state = index.state
            database = index.database_path
    except (MemoryTreeError, OSError, ValueError) as error:
        return TreeKnowledge(
            wire=ReviewKnowledgeSide(
                side=wire_side,
                state="unavailable-history",
                tree=tree,
                detail=f"the memory tree {tree} cannot be indexed: {error}",
            ),
            database=None,
        )
    return TreeKnowledge(
        wire=ReviewKnowledgeSide(
            side=wire_side,
            state="available",
            tree=tree,
            index_state=state.state,
            problems=state.problems,
        ),
        database=database,
    )


# -- reopening a recorded comparison --------------------------------------------------------------


def reopen_review_trees(
    coordination_root: Path,
    contract: WorktreeContract,
    number: int | None = None,
) -> ReviewTrees | ReviewRefusal | None:
    """Reopen a recorded comparison from its tree ids; ``None`` when the leaf recorded none.

    Each tree is asked of the repository the record names. A tree Git can no longer produce is
    ``unavailable-history`` on its side, named; today's tree is never read in its place.
    """

    leaf_id = contract.leaf_id or contract.task_name
    records = comparison_records(contract.task_root, leaf_id)
    if number is not None:
        chosen = next((record for record in records if record.number == number), None)
        if chosen is None:
            return ReviewRefusal(
                code="candidate_unresolved",
                detail=f"leaf {leaf_id} records no tree comparison {number}",
                next_action="name a comparison this leaf recorded, or open the live review",
                offending_input=str(number),
            )
    elif records:
        chosen = records[-1]
    else:
        return None
    return reopened_trees(
        coordination_root, chosen, comparison_directory(contract.task_root, leaf_id)
    )


def reopened_trees(
    coordination_root: Path, record: ReviewTreeComparisonRecord, directory: Path | None
) -> ReviewTrees:
    """Open one record's knowledge sides, marking each tree Git can no longer produce."""

    memory_repository = Path(record.memory_candidate.repository)
    states: dict[str, tuple[ReviewTreeSideState, str]] = {}
    before = _before_state(coordination_root, record)
    if before is not None:
        states["before"] = before
    after = _missing_tree(memory_repository, record.memory_candidate.tree)
    if after is not None:
        states["after"] = ("unavailable-history", after)
    path = None if directory is None else directory / f"{record.number}.json"
    opened = _open_sides(coordination_root, record, memory_repository, path, states=states)
    return replace(opened, code_sides=_code_sides(record))


def _code_sides(record: ReviewTreeComparisonRecord) -> tuple[ReviewCodeSide, ...]:
    """Each recorded code tree as Git can produce it now; a lost one is named, never replaced."""

    sides: list[ReviewCodeSide] = []
    for name, side in (("base", record.code_base), ("candidate", record.code_candidate)):
        missing = _missing_tree(Path(side.repository), side.tree)
        sides.append(
            ReviewCodeSide(
                side="base" if name == "base" else "candidate",
                state="available" if missing is None else "unavailable-history",
                tree=side.tree,
                detail=missing,
            )
        )
    return tuple(sides)


def _before_state(
    coordination_root: Path, record: ReviewTreeComparisonRecord
) -> tuple[ReviewTreeSideState, str] | None:
    repository = Path(record.memory_base.repository)
    converted = record.converted_base
    if converted is None:
        missing = _missing_tree(repository, record.memory_base.tree)
        return None if missing is None else ("unavailable-history", missing)
    if _missing_tree(repository, converted.tree) is None:
        return None
    try:
        derived = _converted_tree(
            coordination_root,
            repository,
            converted.commit,
            (Path(record.code_base.repository), converted.code_commit),
            converted.version,
        )
    except TreeSideUnreadable as error:
        return ("unavailable-history", error.detail)
    if derived != converted.tree:
        return (
            "unavailable-history",
            f"the converted base tree {converted.tree} is gone and re-deriving it from "
            f"{converted.commit} gives {derived}",
        )
    return None


def _missing_tree(repository: Path, tree: str) -> str | None:
    if _git(repository, "cat-file", "-t", tree) == "tree":
        return None
    return f"Git can no longer produce the tree {tree} in {repository}"


def tree_sides_refusal(trees: ReviewTrees) -> ReviewRefusal | None:
    """The refusal a comparison with an unreadable knowledge side earns, naming side and tree."""

    lost = [side for side in trees.sides() if side.state != "available"]
    if not lost:
        return None
    return ReviewRefusal(
        code="candidate_dataset_absent",
        detail="; ".join(
            f"the {side.side} knowledge side is {side.state}: {side.detail or side.tree}"
            for side in lost
        ),
        next_action=(
            "open the source review, which keeps both code sides; a knowledge side Git cannot "
            "produce is never replaced by today's tree"
        ),
        offending_input="; ".join(f"{side.side}:{side.state}:{side.tree}" for side in lost),
    )


def tree_limitations(trees: ReviewTrees) -> tuple[str, ...]:
    """The declared facts of a tree comparison: its record, and each side's state and index."""

    tokens = [f"review:trees:{trees.record.number}"]
    if trees.record.converted_base is not None:
        tokens.append(f"review:converted-base:{trees.record.converted_base.version}")
    for side in trees.sides():
        tokens.append(f"history:intent:{side.side}:{side.state}")
        if side.index_state is not None:
            tokens.append(f"knowledge-index:{side.side}:{side.index_state}")
    tokens += [
        f"history:code:{side.side}:{side.state}:{side.tree}"
        for side in trees.code_sides
        if side.state != "available"
    ]
    return tuple(tokens)


# -- the resolution the landed composition reads ----------------------------------------------------


def tree_resolution(
    repository_id: str,
    contract: WorktreeContract,
    trees: ReviewTrees,
    **extra: object,
) -> ReviewCandidateResolution:
    """The landed review's resolution over a tree comparison: two index files, two code trees.

    A knowledge side that is not available names no file at all (a path under the leaf's task root
    that is never created), so no read can reach a database in its place.
    """

    from agents_remember.application.review_candidate_resolution import (  # noqa: PLC0415 - cycle
        ReviewCandidateResolution,
    )

    record = trees.record
    return ReviewCandidateResolution(
        repository_id=repository_id,
        leaf_id=record.leaf_id,
        baseline_database=trees.before.database or _absent(contract, "before"),
        candidate_database=trees.after.database or _absent(contract, "after"),
        baseline_code_root=Path(record.code_base.repository),
        candidate_code_root=Path(record.code_candidate.repository),
        baseline_code_tree_id=record.code_base.commit or record.code_base.tree,
        candidate_code_tree_id=record.code_candidate.tree,
        contract=contract,
        trees=trees,
        **extra,  # type: ignore[arg-type]
    )


def _absent(contract: WorktreeContract, side: str) -> Path:
    return contract.task_root / ".review-knowledge-unavailable" / side


# -- small helpers --------------------------------------------------------------------------------


def _git(repository: Path, *args: str) -> str | None:
    result = run_git(repository, list(args), GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS))
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _require(repository: Path, revision: str, side: str) -> str:
    value = _git(repository, "rev-parse", "--verify", "--quiet", revision)
    if value is None:
        raise TreeSideUnreadable(side, f"{revision!r} names nothing in {repository}")
    return value


def _has_marker(repository: Path, tree: str) -> bool:
    return _git(repository, "cat-file", "-t", f"{tree}:{LAYOUT_MARKER_PATH}") == "blob"


def _pinned_version(repository: Path, tree: str) -> str | None:
    if not _has_marker(repository, tree):
        return None
    text = _git(repository, "cat-file", "blob", f"{tree}:{LAYOUT_MARKER_PATH}")
    if text is None:
        return None
    return str(parse_json(text)["conversion"])


def _unresolved(side: str, detail: str) -> ReviewRefusal:
    return ReviewRefusal(
        code="candidate_unresolved",
        detail=f"the {side} side of the tree comparison cannot be resolved: {detail}",
        next_action=(
            "repair the named side (a memory line whose commits carry Code-Commit trailers, a "
            "readable worktree), then reopen the review; no other tree is substituted"
        ),
        offending_input=side,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
