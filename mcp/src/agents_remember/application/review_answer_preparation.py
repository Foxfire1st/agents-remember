"""Name the exact inputs a recorded comparison's answers are a function of (MIK-R179 rule 3).

``serving`` ranks below ``application``, so the layer that can read a leaf's contract is the
composition root. This module binds that reading once and keeps it out of the app builder: the
reviewer's reuse cache is given, per read, the record, the two directories its generation lives in
and the object stores the recorded trees are read from -- never a working view and never a live
``HEAD``, which is why a closed leaf's answers can be served again unchanged.

A live leaf's whole reviews -- the task context's and each subject's -- and its subject catalogue
are kept too, on the witness of the live comparison they name (D570, D574;
:meth:`RecordedReviewAnswers.bind_live_witness`):
the capture proof that the working views still hold that comparison's trees, every input the
witness names, the refs the answers resolve, and every read they made, each re-observed before
any reuse.
"""

from __future__ import annotations

import mmap
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    recorded_leaf_contract,
)
from agents_remember.application.review_closure_proofs import (
    ClosureProofStore,
    ProvenClosure,
    layout_digest,
    witnesses_digest,
)
from agents_remember.application.review_comparison_generation import leaf_generation_root
from agents_remember.application.review_delivered_comparison import (
    delivered_notice,
    recorded_latest,
)
from agents_remember.application.review_tree_comparison import (
    comparison_directory,
    comparison_records,
    numbered_record,
    reopen_review_trees,
    review_ref,
)
from agents_remember.application.review_worklist_answers import stored_worklist_unbound
from agents_remember.kernel.git_command import GitRunnerOptions, run_git, run_git_into
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.read_cancellation import SharedFlights, raise_if_read_cancelled
from agents_remember.kernel.recorded_reads import (
    changed_observations,
    file_identity,
    has_failed_observation,
    observed_path_exists,
    recorded_reads,
    replay_reads,
)
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge_index.cache import default_cache_directory
from agents_remember.models.knowledge.review import (
    KnowledgeReviewResult,
    ReviewEntryListResult,
    ReviewSurfaceRequest,
)
from agents_remember.models.knowledge.review_intent_summary import ReviewIntentSummaryResult
from agents_remember.models.knowledge.review_trees import ReviewTrees, ReviewTreesResult
from agents_remember.serving.changeset_cache import (
    ContentRead,
    DirectoryRead,
    ListingRead,
    ObjectStoreRead,
    ReadInput,
    RepositoryRead,
)
from agents_remember.serving.changeset_generation import RepositoryLayout, repository_layout
from agents_remember.serving.review_answer_reuse import AnswerCodec, RecordedAnswerReuse
from agents_remember.serving.review_open_observation import answer_outcomes, note_answer
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "DETAIL_CAPACITY",
    "ComparisonRetention",
    "LiveWitness",
    "RecordedReviewAnswers",
    "recorded_answer_sources",
    "recorded_review_answers",
]


def recorded_answer_sources(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str
) -> tuple[ReadInput, ...] | None:
    """The inputs one recorded answer is a function of, or ``None`` when this leaf has no record."""

    contract = _recorded_contract(config, repository_id, master, leaf_id)
    if contract is None:
        return None
    return _sources_of(config, contract, repository_id, master, leaf_id)


def _recorded_contract(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str
) -> WorktreeContract | None:
    """The leaf's own enclosure contract, or ``None`` for a name that cannot address one."""

    for value in (repository_id, master, leaf_id):
        if not value or "/" in value or "\\" in value or value.startswith("."):
            return None
    contract = recorded_leaf_contract(config, repository_id, master, leaf_id)
    return contract if contract is not None and contract.kind == "leaf" else None


def _comparison_sources_of(
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    repository_id: str,
    master: str,
    leaf_id: str,
) -> tuple[ReadInput, ...]:
    """The record, the directories its generation lives in, and the stores it is read from.

    A successor record is another answer, so the names the comparison directory lists are an input
    -- its names, not the identities of the records under them: a record is an input only of the
    answers that read it (D570); a generation's listing is one too. The object census is an
    additional invalidator; direct recorded-closure checks below detect a pruned dependency even
    when an unrelated new object compensates for its count.
    """

    task_root = config.coordination_root / "tasks" / repository_id / master
    sources: list[ReadInput] = [
        ContentRead(contract.contract_path),
        ListingRead(comparison_directory(task_root, leaf_id)),
        DirectoryRead(leaf_generation_root(task_root, leaf_id)),
    ]
    for root in (contract.code_repo_path, contract.memory_repo_path):
        if root is not None and Path(root).is_dir():
            sources.append(ObjectStoreRead(Path(root)))
    return tuple(dict.fromkeys(sources))


def _sources_of(
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    repository_id: str,
    master: str,
    leaf_id: str,
) -> tuple[ReadInput, ...]:
    """A projected page additionally consumes its persisted worklist, including absence."""
    worklist = contract.contract_path.parent / "knowledge-worklist.json"
    return (
        *_comparison_sources_of(config, contract, repository_id, master, leaf_id),
        ContentRead(worklist) if worklist.exists() else worklist,
    )


def _additions_kept(sources: tuple[ReadInput, ...]) -> tuple[ReadInput, ...]:
    """``sources`` with each object store observed as a kept success needs it (D570).

    A kept success names its objects by id and proves their closures present again before each
    reuse, so an object added to its store -- another worktree's capture or commit, in the store
    every worktree of a repository shares -- changes nothing it read and keeps it; a removal, a
    repack or an alternate-store change still moves the store's stamp. The strict census
    (:class:`ObjectStoreRead`) stays with an answer that may be a refusal, which an added object
    can answer.
    """

    return tuple(
        dict.fromkeys(
            RepositoryRead(source.root) if isinstance(source, ObjectStoreRead) else source
            for source in sources
        )
    )


def _recorded_sides(
    task_root: Path, leaf_id: str, number: int | None = None
) -> tuple[tuple[Path, str], ...]:
    """The requested record's trees (all retained records when no number is given), deduplicated.

    A kept answer is only good while the objects it was computed from can still be read. Object
    counts cannot say that: one removed tree and one added blob leave the census the same size.
    """

    sides: list[tuple[Path, str]] = []
    # A numbered read opens only that record. Other comparison bodies are not inputs to its answer;
    # the directory's names remain an observed metadata input.
    if number is None:
        records = comparison_records(task_root, leaf_id)
    else:
        numbered = numbered_record(task_root, leaf_id, number)
        records = () if numbered is None else (numbered,)
    for record in records:
        for side in (
            record.code_base,
            record.code_candidate,
            record.memory_base,
            record.memory_candidate,
        ):
            sides.append((Path(side.repository), side.tree))
    return tuple(dict.fromkeys(sides))


@dataclass(frozen=True)
class _ClosureProof:
    layout: RepositoryLayout
    objects: tuple[str, ...]
    witnesses: tuple[str, ...]
    references: str


_CLOSURE_PROOFS: BoundedMemo[tuple[Path, tuple[str, ...]], _ClosureProof] = BoundedMemo(64)
# Each repository's most recently proven closure: a later comparison shares most of its objects,
# and a body consumed under a witness that still agrees need not be consumed again.
_LATEST_PROOF: BoundedMemo[Path, _ClosureProof] = BoundedMemo(16)
_PROVING: SharedFlights[tuple[Path, tuple[str, ...]], bool] = SharedFlights()


def _trees_still_readable(
    sides: tuple[tuple[Path, str], ...], proofs: ClosureProofStore | None = None
) -> bool:
    """Check the recorded closures, including descendant presence and object readability."""

    roots: dict[Path, list[str]] = {}
    for repository, tree in sides:
        roots.setdefault(repository, []).append(tree)
    for repository, trees in roots.items():
        if not _closure_still_readable(repository, tuple(sorted(set(trees))), proofs):
            return False
    return True


def _closure_still_readable(
    repository: Path, trees: tuple[str, ...], proofs: ClosureProofStore | None = None
) -> bool:
    """Reuse a consumed closure only while all its object-storage inputs still agree.

    One check per closure is in flight: a second asker of the same closure waits for the first's
    answer instead of traversing and consuming it again beside it. The check is the flight's: a
    cancelled asker never hands its cancellation to another (D569 mean 4).
    """
    readable, _owner = _PROVING.share(
        (repository, trees), lambda: _closure_check(repository, trees, proofs)
    )
    return readable


def _closure_check(
    repository: Path, trees: tuple[str, ...], proofs: ClosureProofStore | None
) -> bool:
    held, layout = _held_closure_holds(repository, trees, proofs)
    if held:
        return True
    if proofs is not None and _stored_closure_holds(repository, trees, proofs, layout):
        return True
    return _prove_closure(repository, trees, proofs, layout)


def _held_closure_holds(
    repository: Path, trees: tuple[str, ...], proofs: ClosureProofStore | None = None
) -> tuple[bool, RepositoryLayout | None]:
    """This process's proof, while every witness agrees; with its layout when that is current.

    A proof whose objects are all still witnessed holds. One whose layout and references agree
    but some of whose object witnesses moved is witnessed again (:func:`_rewitnessed_closure`):
    the closure's roots name the same objects, so only the bodies whose witness moved are read.
    """
    previous = _CLOSURE_PROOFS.get((repository, trees))
    if previous is None:
        return False, None
    proof = previous[0]
    try:
        if not proof.layout.current():
            return False, None
        if proof.layout.references(()) != proof.references:
            return False, proof.layout
        witnesses = proof.layout.objects_presence(proof.objects)
        if witnesses == proof.witnesses:
            return True, proof.layout
        return _rewitnessed_closure(repository, trees, proof, witnesses, proofs), proof.layout
    except OSError:
        return False, None  # The Git validation owns the refusal, never a cached success.


def _rewitnessed_closure(
    repository: Path,
    trees: tuple[str, ...],
    proof: _ClosureProof,
    witnesses: tuple[str, ...],
    proofs: ClosureProofStore | None,
) -> bool:
    """A held proof whose object witnesses moved, proved again for exactly the moved objects.

    Git rewrites an object's file times when it writes an object it already holds (a capture's
    ``git add`` or ``write-tree`` of unchanged content), which moves that object's witness without
    changing it. The closure's roots are object ids, so its object list is the same; each object
    whose witness moved -- freshened, packed, or lost -- has its body consumed again, and the proof
    is renewed only while the layout, the references and every new witness held through that read.
    A lost object fails the read, and the caller then traverses the closure again from its roots.
    """
    moved = [
        object_id
        for object_id, was, now in zip(proof.objects, proof.witnesses, witnesses, strict=True)
        if was != now
    ]
    if not _object_bodies_readable(repository, moved):
        return False
    layout = proof.layout
    if not (
        layout.objects_presence(proof.objects) == witnesses
        and layout.references(()) == proof.references
        and layout.current()
    ):
        return False
    renewed = _ClosureProof(layout, proof.objects, witnesses, proof.references)
    _CLOSURE_PROOFS.put((repository, trees), renewed)
    _LATEST_PROOF.put(repository, renewed)
    if proofs is not None:
        proofs.save(
            repository, trees, ProvenClosure(proof.objects, witnesses, proof.references, layout)
        )
    return True


def _prove_closure(
    repository: Path,
    trees: tuple[str, ...],
    proofs: ClosureProofStore | None,
    layout: RepositoryLayout | None,
) -> bool:
    """Git traverses the recorded closures and consumes every body."""
    try:
        layout = layout or repository_layout(repository)
        references = layout.references(())
        # A root can survive the loss of a subtree or blob. Traverse just these recorded
        # closures; Git marks missing descendants with '?', including compensated removals
        # which an object count cannot distinguish (L177-R1-05). Gitlinks are not traversed.
        result = run_git(
            repository,
            [
                "rev-list",
                "--objects",
                "--no-object-names",
                "--missing=print",
                *(f"{tree}^{{tree}}" for tree in trees),
            ],
        )
        if result.returncode or any(line.startswith("?") for line in result.stdout.splitlines()):
            return False
        objects = tuple(result.stdout.splitlines())
        before = layout.objects_presence(objects)
        unconsumed = _bodies_not_yet_consumed(repository, layout, references, objects, before)
        if unconsumed and not _object_bodies_readable(repository, unconsumed):
            return False
        after = layout.objects_presence(objects)
        if before == after and references == layout.references(()) and layout.current():
            proven = _ClosureProof(layout, objects, after, references)
            _CLOSURE_PROOFS.put((repository, trees), proven)
            _LATEST_PROOF.put(repository, proven)
            if proofs is not None:
                proofs.save(repository, trees, ProvenClosure(objects, after, references, layout))
    except OSError:
        return False
    return True


def _stored_closure_holds(
    repository: Path,
    trees: tuple[str, ...],
    proofs: ClosureProofStore,
    layout: RepositoryLayout | None,
) -> bool:
    """A closure an earlier process proved, taken only while every witness it held still agrees."""
    stored = proofs.load(repository, trees)
    if stored is None:
        return False
    try:
        fresh = layout or repository_layout(repository)
        if layout_digest(fresh) != stored.layout or fresh.references(()) != stored.references:
            return False
        witnesses = fresh.objects_presence(stored.objects)
        if witnesses_digest(witnesses) != stored.witnesses or not fresh.current():
            return False
    except OSError:
        return False  # The Git validation owns the refusal, never a stored success.
    taken = _ClosureProof(fresh, stored.objects, witnesses, stored.references)
    _CLOSURE_PROOFS.put((repository, trees), taken)
    _LATEST_PROOF.put(repository, taken)
    return True


def _bodies_not_yet_consumed(
    repository: Path,
    layout: RepositoryLayout,
    references: str,
    objects: tuple[str, ...],
    witnesses: tuple[str, ...],
) -> list[str]:
    """The objects whose bodies no agreeing proof of this repository has consumed yet.

    A body consumed while its object was held by one loose file or one pack set is the same body
    while that witness still agrees, under the same Git layout and replace references. Any other
    object -- new, moved, rewritten in place, or under a changed layout -- is consumed again.
    """
    latest = _LATEST_PROOF.get(repository)
    if latest is None:
        return list(objects)
    proof = latest[0]
    if proof.layout != layout or proof.references != references:
        return list(objects)
    consumed = dict(zip(proof.objects, proof.witnesses, strict=True))
    return [
        object_id
        for object_id, witness in zip(objects, witnesses, strict=True)
        if consumed.get(object_id) != witness
    ]


def _object_bodies_readable(repository: Path, objects: list[str]) -> bool:
    """Make Git consume each recorded body; readable headers alone do not prove this.

    Batch framing also detects an object lost between the traversal and this read. Bodies
    stay bytes: neither newline conversion nor a previously held blob can hide unreadability.
    Git writes the bodies into an unnamed file that is framed in place, so a closure's bytes
    never pass through this process; only each header and the newline after its body are read.
    """

    with tempfile.TemporaryFile() as output:
        result = run_git_into(
            repository,
            ["cat-file", "--batch"],
            output,
            GitRunnerOptions(input_text="\n".join(objects) + "\n"),
        )
        if result.returncode or result.stderr:
            return False
        if os.fstat(output.fileno()).st_size == 0:
            return not objects
        with mmap.mmap(output.fileno(), 0, access=mmap.ACCESS_READ) as written:
            return _batch_framed(written, objects)


def _batch_framed(written: mmap.mmap, objects: list[str]) -> bool:
    """Whether ``cat-file --batch`` wrote exactly one whole body per object, in order."""
    offset = 0
    for object_id in objects:
        end = written.find(b"\n", offset)
        if end < 0:
            return False
        header = written[offset:end].split()
        if len(header) != 3 or header[0] != object_id.encode("ascii") or not header[2].isdigit():
            return False
        end += 1 + int(header[2])
        if written[end : end + 1] != b"\n":
            return False
        offset = end + 1
    return offset == len(written)


def _reads_from_its_record(contract: WorktreeContract | None) -> bool:
    """Whether a leaf is answered from a durable record rather than from a live enclosure.

    A leaf whose enclosure still names a capture worktree is a live comparison: its answers depend
    on a working view this module never observes, so they are computed on every read. The entry
    catalogue has no ``history`` selector of its own, so this is how its recordedness is decided.
    """

    if contract is None:
        return False
    worktree = contract.code_worktree
    return worktree is None or not worktree.exists()


@dataclass(frozen=True)
class _AnswerPolicy[T]:
    """Eligibility and presentation of one kind of recorded answer."""

    must_be_closed: bool = True
    live_roots: bool = False
    """Observe the capture worktrees' existence, as the live comparison preparation selects by it."""
    keep: Callable[[T], bool] | None = None
    persistent: bool = False
    prepare: Callable[[T], T] | None = None
    decorate: Callable[[T, WorktreeContract], T] | None = None
    detail: bool = False
    """One invariant's detail (its cards or its review): kept apart from the page's opening answers."""
    successes: bool = False
    """``keep`` admits a success alone: an object added to a store it read keeps it (:func:`_additions_kept`)."""


@dataclass(frozen=True)
class _AnswerInputs[T]:
    """Current caller-owned inputs; persisted envelopes never choose these paths."""

    key: tuple[object, ...]
    sources: tuple[ReadInput, ...]
    reads: Mapping[str, str]
    codec: AnswerCodec[T] | None
    expected: Mapping[str, str] | None
    sides: tuple[tuple[Path, str], ...]


@dataclass(frozen=True)
class ComparisonRetention:
    observations: dict[str, str]
    sources: tuple[ReadInput, ...]
    binding: tuple[object, ...] | None


@dataclass(frozen=True)
class LiveWitness:
    """The live comparison a live leaf's working views hold now, as the comparison owner proved it.

    ``trees`` names the comparison (its number and four trees), ``sources`` are the inputs its
    witness observed, and ``current`` proves again that the working views still capture to those
    trees and that those inputs still read the same. Never loaded from disk.
    """

    contract: WorktreeContract
    trees: ReviewTrees
    sources: tuple[ReadInput, ...]
    current: Callable[[], bool]


#: The live comparison of a leaf (repository, master, leaf), or ``None`` when none is proven.
type LiveWitnessPort = Callable[[str, str, str], LiveWitness | None]


class RecordedReviewAnswers:
    """Serve a recorded comparison's answers from the bounded reuse of their exact inputs.

    One place decides, per read, whether the answer is a function of a record (kept while its
    inputs hold) or of a working view (computed every time), so the three routes cannot disagree
    about which reads are reusable. The keys are the exact questions: the whole intent request, the
    task context of an entry catalogue, and every field that selects a tree view.
    """

    def __init__(
        self,
        config: McpRuntimeConfig,
        reuse: RecordedAnswerReuse,
        proofs: ClosureProofStore | None = None,
        details: RecordedAnswerReuse | None = None,
    ) -> None:
        self._config = config
        self._reuse = reuse
        # Each invariant's detail -- its cards, its review -- is kept apart (D574): a leaf has as
        # many as it has changed invariants, and in one store they would push the page's opening
        # answers out of it.
        self._details = reuse if details is None else details
        self._proofs = proofs
        self._flights: SharedFlights[tuple[object, ...], tuple[object, dict[str, str], str]] = (
            SharedFlights()
        )
        self._live_witness: LiveWitnessPort | None = None

    def bind_live_witness(self, witness: LiveWitnessPort) -> None:
        """The live comparison owner the live whole reviews and catalogue are kept on (D570)."""

        self._live_witness = witness

    def intent[T](self, request: ReviewSurfaceRequest, produce: Callable[[], T]) -> T:
        """The review answer: reused for the leaf's record, or on its live comparison's witness.

        A live read is kept for a whole review -- the task context's or one subject's, with no
        page and no refreshed binding: the questions whose every read was named (D570, D574).
        A page of a collection, or a refresh against a binding, is computed each time.
        """

        if request.history != "recorded":
            live = ("live", "intent", request.model_dump_json())
            if not _whole_review(request):
                return self._shared(live, produce)
            return self._live(
                live[1:],
                (request.repository_id, request.master, request.leaf_id),
                produce,
                _reviewed_comparison,
                lambda: self._shared(live, produce),
                detail=request.selector is not None,
            )
        return self._answer(
            ("intent", request.model_dump_json()),
            (request.repository_id, request.master, request.leaf_id, None),
            produce,
            policy=_AnswerPolicy(must_be_closed=False, detail=request.selector is not None),
        )

    def entries[T](
        self,
        repository_id: str,
        master: str,
        leaf_id: str,
        number: int | None,
        produce: Callable[[], T],
    ) -> T:
        """The subject catalogue: reused only for a leaf answered from its record.

        ``number`` is the recorded comparison the catalogue was asked for (MIK-R89 v2 rule 10), or
        ``None`` for the leaf's own resolution; two numbers are two answers. A live leaf's own
        catalogue is kept on its live comparison's witness (D570).
        """

        key = ("entries", repository_id, master, leaf_id, number)

        def recorded() -> T:
            return self._answer(key, (repository_id, master, leaf_id, number), produce)

        if number is not None:
            return recorded()
        return self._live(
            key, (repository_id, master, leaf_id), produce, _listed_comparison, recorded
        )

    def trees[T](self, query: ReviewTreesQuery, produce: Callable[[], T]) -> T:
        """The tree view: reused only when the query names a recorded comparison.

        A card answer whose memory values could not be read on a readable after side, and an answer
        with a side whose index could not be built, names a failed read, so it is answered and
        never kept (INV-5M9WY3).
        """

        if not (query.recorded or query.number is not None):
            return self._shared(("live", *_tree_key(query)), produce)

        def static_answer(answer: T) -> T:
            if isinstance(answer, ReviewTreesResult) and not query.focused:
                answer = answer.model_copy(update={"latest": None, "delivered": None})
            return cast(T, answer)

        def decorate(value: T, contract: WorktreeContract) -> T:
            if query.focused or not isinstance(value, ReviewTreesResult) or value.state != "trees":
                return value
            return cast(
                T,
                value.model_copy(
                    update={
                        "latest": recorded_latest(contract),
                        "delivered": delivered_notice(contract, value.comparison),
                    }
                ),
            )

        return self._answer(
            ("trees", _tree_key(query)),
            (query.repository_id, query.master, query.leaf_id, query.number),
            produce,
            policy=_AnswerPolicy(
                # A numbered comparison is pinned to its record's trees, so an open leaf's answer is
                # kept too; the recorded-reads admission still refuses one that read a live view.
                must_be_closed=query.number is None,
                live_roots=not query.recorded,
                keep=_keepable_persisted_tree,
                successes=True,
                persistent=query.number is not None,
                prepare=static_answer,
                decorate=decorate,
                detail=bool(query.invariants) or query.entry is not None,
            ),
        )

    def comparison_readable(self, native: tuple[WorktreeContract, ReviewTrees]) -> bool:
        """Recheck native object closure, including a converted-before tree when present."""
        _contract, trees = native
        record = trees.record
        sides = [
            (Path(side.repository), side.tree)
            for side in (
                record.code_base,
                record.code_candidate,
                record.memory_base,
                record.memory_candidate,
            )
        ]
        if record.converted_base is not None:
            sides.append((trees.memory_repository, record.converted_base.tree))
        return _trees_still_readable(tuple(sides), self._proofs)

    def comparison_stamps(
        self, sources: tuple[ReadInput, ...]
    ) -> tuple[tuple[ReadInput, str | None], ...]:
        """Observe trusted current comparison inputs using the existing cache owner."""
        return tuple((source, self._reuse.cache.open_snapshot(source)) for source in sources)

    def comparison_sources(
        self,
        query: ReviewTreesQuery,
        contract: WorktreeContract,
        trees: ReviewTrees,
        observations: dict[str, str],
    ) -> tuple[tuple[ReadInput, ...], tuple[object, ...] | None]:
        """Current native roots and index identities; a disk envelope chooses none."""
        # This is a successful native comparison, whose exact object closures are
        # checked again by comparison_readable. Unrelated additions (including the
        # explainer's detached-worktree commits) do not change that answer. Keep the
        # strict absence census in _comparison_sources_of for refused answers.
        sources = tuple(
            RepositoryRead(source.root) if isinstance(source, ObjectStoreRead) else source
            for source in _comparison_sources_of(
                self._config, contract, query.repository_id, query.master, query.leaf_id
            )
        )
        # The comparison's own record is its input; the others' bytes are not (the directory's
        # names, among the sources above, name a successor).
        record = comparison_directory(contract.task_root, query.leaf_id) / (
            f"{trees.record.number}.json"
        )
        sources = (*sources, ContentRead(record))
        with recorded_reads() as reads:
            extra = _index_inputs(trees)
        replay_reads(reads)
        observations.update(reads)
        if extra is None:
            return sources, None
        index_sources, identities = extra
        return (*sources, *index_sources), (*identities, trees.record.model_dump_json())

    def comparison(
        self,
        query: ReviewTreesQuery,
        native: tuple[WorktreeContract, ReviewTrees],
        answer: ReviewTreesResult,
        inputs: ComparisonRetention,
        current: Callable[[], bool],
    ) -> ReviewTreesResult:
        """Retain a projection-independent comparison after native input admission.

        Live capture proofs remain producer-owned and process-local. A restart performs native
        resolution before restoring the typed component for those exact inputs.
        """
        _contract, trees = native
        observations, sources, binding = inputs.observations, inputs.sources, inputs.binding
        key = (
            "comparison",
            query.repository_id,
            query.master,
            query.leaf_id,
            query.number,
            query.recorded,
            trees.record.model_dump_json(),
            tuple(sorted(observations.items())),
        )
        expected = _expected_reads(sources, observations)
        codec = (
            None
            if binding is None or expected is None
            else AnswerCodec[ReviewTreesResult](ReviewTreesResult, binding=binding)
        )
        sides = tuple(
            (Path(side.repository), side.tree)
            for side in (
                trees.record.code_base,
                trees.record.code_candidate,
                trees.record.memory_base,
                trees.record.memory_candidate,
            )
        )
        return self._retain(
            _AnswerInputs(key, sources, observations, codec, expected, sides),
            lambda: answer,
            _AnswerPolicy(
                keep=lambda value: current() and _keepable_persisted_tree(value), successes=True
            ),
        )

    def summary(
        self,
        question: tuple[str, str, str, int | None],
        resolved: ReviewCandidateResolution,
        selection: tuple[tuple[ReadInput, ...], Mapping[str, str]],
        produce: Callable[[], ReviewIntentSummaryResult],
    ) -> ReviewIntentSummaryResult:
        """Keep the native resolver's exact non-live comparison, never select latest here."""
        sources, observations = selection
        trees, contract = resolved.trees, resolved.contract
        if trees is None or trees.live or contract is None:
            return self._shared(("live", "summary", *question), produce)
        record = trees.record
        sides = tuple(
            (Path(side.repository), side.tree)
            for side in (
                record.code_base,
                record.code_candidate,
                record.memory_base,
                record.memory_candidate,
            )
        )
        key = ("summary", *question, record.model_dump_json(), tuple(sorted(observations.items())))
        kept = cast(ReviewIntentSummaryResult | None, self._reuse.retained_answer(key))
        if kept is not None:
            note_answer(_answer_name(key), "read", 0.0)
            return kept
        if not _trees_still_readable(sides, self._proofs):
            return produce()
        with recorded_reads() as index_reads:
            extra = _index_inputs(trees)
        replay_reads(index_reads)
        reads = dict(observations)
        if extra is None or any(
            reads.get(path, identity) != identity for path, identity in index_reads.items()
        ):
            return produce()
        reads.update(index_reads)
        index_sources, identities = extra
        repository_id, master, leaf_id, _number = question
        sources = tuple(
            dict.fromkeys(
                (
                    *_sources_of(self._config, contract, repository_id, master, leaf_id),
                    *sources,
                    *index_sources,
                    *(ObjectStoreRead(path) for path, _tree in sides),
                    *(ContentRead(Path(path)) for path in reads if Path(path).is_absolute()),
                )
            )
        )
        expected = _expected_reads(sources, reads)
        if expected is None:
            return produce()
        codec: AnswerCodec[ReviewIntentSummaryResult] = AnswerCodec(
            ReviewIntentSummaryResult,
            binding=(*identities, tuple(sorted(reads.items()))),
        )

        decided: list[bool] = []  # read at most once per question, on its first keep decision

        def unbound() -> bool:
            if not decided:
                decided.append(stored_worklist_unbound(contract, trees))
            return decided[0]

        def keepable(value: ReviewIntentSummaryResult) -> bool:
            # An open count the stored worklist does not measure for these trees is a fact of
            # that worklist's bytes, which are among the summary's inputs (D570).
            return _keepable_summary(value) or (_unmeasured_open(value) and unbound())

        return self._retain(
            _AnswerInputs(key, sources, reads, codec, expected, sides),
            produce,
            _AnswerPolicy(keep=keepable, successes=True),
        )

    def _live[T](  # noqa: PLR0913 -- the question, its leaf and its three answer rules
        self,
        key: tuple[object, ...],
        context: tuple[str, str, str],
        produce: Callable[[], T],
        named: Callable[[T], int | None],
        otherwise: Callable[[], T],
        *,
        detail: bool = False,
    ) -> T:
        """A live task-context answer, kept on the witness of the comparison it names (D570).

        The comparison owner proves the live comparison first; with none proven the answer is
        ``otherwise``'s. The key carries that comparison's record, and the answer is kept only
        when it names the same comparison (``named``) and the witness still holds once it was
        made. Its inputs are the witness's, the leaf's own (:func:`_live_sources`), and every read
        it made; each is re-observed before any reuse, and a proven witness precedes each.
        """

        witness = None if self._live_witness is None else self._live_witness(*context)
        if witness is None:
            return otherwise()
        record = witness.trees.record
        key = ("live-kept", *key, record.model_dump_json())
        # Kept only when it names the witness's comparison, which proves its closures again.
        sources = _additions_kept(_live_sources(self._config, witness, *context))
        made: dict[str, str] = {}
        held = False

        def compute() -> T:
            nonlocal made, held
            with recorded_reads() as actual:
                value = produce()
            replay_reads(actual)
            made = dict(actual)
            held = not has_failed_observation(made) and witness.current()
            return value

        def keep(value: T) -> bool:
            # A read whose askers left keeps nothing (D569 mean 4).
            raise_if_read_cancelled()
            return held and named(value) == record.number

        store = self._details if detail else self._reuse
        return self._shared(
            key,
            lambda: store.answer(key, sources, compute, keep=keep, made=lambda: made),
        )

    def stale[T](
        self,
        question: tuple[object, ...],
        produce: Callable[[], T],
        keepable: Callable[[T], bool],
    ) -> T:
        """The explainer's staleness of one recorded comparison, kept on every read it made (D570).

        ``question`` is every field of the read that selects its answer. Its inputs are the reads
        the computation recorded -- the leaf's documents, the binding, the grades, the revisions'
        manifests and the admitted sources, listings included -- each re-observed identical before
        any reuse; the trees it reads are named by their object ids. ``keepable`` says which
        answers are such a function at all: one that read nothing it could name is computed again.
        """

        key = ("stale", *question)
        made: dict[str, str] = {}

        def compute() -> T:
            nonlocal made
            with recorded_reads() as actual:
                value = produce()
            replay_reads(actual)
            made = dict(actual)
            return value

        def keep(value: T) -> bool:
            # A read whose askers left keeps nothing (D569 mean 4).
            raise_if_read_cancelled()
            return bool(made) and not has_failed_observation(made) and keepable(value)

        return self._shared(
            key, lambda: self._reuse.answer(key, (), compute, keep=keep, made=lambda: made)
        )

    def _answer[T](
        self,
        key: tuple[object, ...],
        context: tuple[str, str, str, int | None],
        produce: Callable[[], T],
        *,
        policy: _AnswerPolicy[T] | None = None,
    ) -> T:
        """The answer to one recorded question: kept while its inputs hold, else computed."""

        policy = policy or _AnswerPolicy()
        repository_id, master, leaf_id, number = context
        with recorded_reads() as resolved_reads:
            contract = _recorded_contract(self._config, repository_id, master, leaf_id)
            if contract is not None and policy.live_roots:
                for root in (contract.code_worktree, contract.memory_worktree):
                    if root is not None:
                        observed_path_exists(root)
        replay_reads(resolved_reads)
        if contract is None or (policy.must_be_closed and not _reads_from_its_record(contract)):
            return self._shared(("live", *key, tuple(sorted(resolved_reads.items()))), produce)
        task_root = self._config.coordination_root / "tasks" / repository_id / master
        selection_key = tuple(sorted(resolved_reads.items()))
        kept = (
            cast(T | None, self._reuse.retained_answer((*key, selection_key)))
            if policy.persistent
            else None
        )
        if kept is not None:
            note_answer(_answer_name(key), "read", 0.0)
            return kept if policy.decorate is None else policy.decorate(kept, contract)
        sides = _recorded_sides(task_root, leaf_id, number)
        if not _trees_still_readable(sides, self._proofs):
            # A tree the answer was computed from is gone: the answer is not the same answer, and
            # the kept one must never stand in for the fresh read that reports the loss.
            return produce()
        sources = _sources_of(self._config, contract, repository_id, master, leaf_id)
        codec: AnswerCodec[T] | None = None
        if policy.persistent:
            index_sources, binding = _persistent_binding(
                self._config, contract, number, resolved_reads
            )
            sources = (*sources, *index_sources)
            codec = None if binding is None else AnswerCodec(ReviewTreesResult, binding=binding)
        # Only trusted current resolvers supply selection observations. Persisted
        # envelopes never supply paths to open or predicates to probe.
        expected_reads = None if codec is None else _expected_reads(sources, resolved_reads)
        if codec is not None:
            if expected_reads is None:
                codec = None
            else:
                key = (*key, selection_key)
        value = self._retain(
            _AnswerInputs(key, sources, resolved_reads, codec, expected_reads, sides),
            produce,
            policy,
        )
        return value if policy.decorate is None else policy.decorate(value, contract)

    def _shared[T](self, key: tuple[object, ...], produce: Callable[[], T]) -> T:
        """One computation per exact question in flight: a second asker waits for the first's.

        The waiter takes a copy of that answer and replays the reads it consumed, as the shared
        comparison preparation does, so its own recorder names the same inputs.

        The computation is the flight's, not its first asker's (D569 mean 4): it stops only when
        every asker's client is gone, and a waiter whose own client leaves stops waiting alone.
        """

        def owned() -> tuple[object, dict[str, str], str]:
            with recorded_reads() as consumed, answer_outcomes() as outcomes:
                answered = produce()
            # The outermost answer is noted last: an answer's own inner answers precede it.
            return answered, dict(consumed), outcomes[-1] if outcomes else "computed"

        started = time.monotonic()
        (value, reads, outcome), owner = self._flights.share(key, owned)
        replay_reads(reads)
        # An open's page tells an answer it waited for from one it found or made (D569).
        note_answer(_answer_name(key), outcome if owner else "joined", time.monotonic() - started)
        return cast(T, value if owner else deepcopy(value))

    def _retain[T](
        self, inputs: _AnswerInputs[T], produce: Callable[[], T], policy: _AnswerPolicy[T]
    ) -> T:
        """One actual-read admission rule for tree and summary answers, shared while in flight."""
        return self._shared(inputs.key, lambda: self._retain_once(inputs, produce, policy))

    def _retain_once[T](
        self, inputs: _AnswerInputs[T], produce: Callable[[], T], policy: _AnswerPolicy[T]
    ) -> T:
        key, sources, resolved_reads = inputs.key, inputs.sources, inputs.reads
        if policy.successes:
            sources = _additions_kept(sources)
        codec, expected_reads = inputs.codec, inputs.expected
        admitted = True
        made: dict[str, str] = {}

        def compute() -> T:
            nonlocal admitted, made
            with recorded_reads() as actual:
                value = produce()
            replay_reads(actual)
            # An answer is kept against the reads it actually made (D569): a read the selection
            # did not name -- the worklist child's task and requirement files -- goes with the
            # answer and is re-observed before every reuse, as a closure's witnesses are; a named
            # input that read otherwise moved during the computation, and nothing is kept.
            made = {
                path: identity
                for path, identity in actual.items()
                if expected_reads is not None and path not in expected_reads
            }
            admitted = codec is None or (
                not has_failed_observation(actual)
                and expected_reads is not None
                and all(
                    expected_reads.get(path) == identity
                    for path, identity in actual.items()
                    if path not in made
                )
            )
            return value if policy.prepare is None else policy.prepare(value)

        def keep_answer(value: T) -> bool:
            # A read whose askers left keeps nothing, in memory or on disk (D569 mean 4).
            raise_if_read_cancelled()
            return (
                admitted
                and _trees_still_readable(inputs.sides, self._proofs)
                and (codec is None or not changed_observations(resolved_reads))
                and (policy.keep is None or policy.keep(value))
            )

        # The reuse keeps and stores those reads with the answer and re-observes every one
        # before it is reused, in this process or after a restart.
        store = self._details if policy.detail else self._reuse
        return store.answer(key, sources, compute, keep=keep_answer, codec=codec, made=lambda: made)


def _keepable_tree_view(value: object) -> bool:
    """Whether a tree-view answer is a function of its recorded inputs alone.

    An answer is kept only after the reuse found every recorded tree readable, so a side it names
    ``unavailable-history`` names a read that failed -- an index that could not be built over a
    readable tree -- not a state of the record; a tree that is gone is never kept anyway. A card
    answer whose ``entry_memory`` is unavailable while its after side is available names a failed
    read too (MIK-R89 v2 rule 3, Failure and Recovery). Neither is kept, so the next read tries
    again; an unconverted (``legacy-unavailable``) side is the record's own state and is kept.
    """

    if not isinstance(value, ReviewTreesResult):
        return True
    sides = (*value.knowledge_sides, *value.code_sides)
    if any(side.state == "unavailable-history" for side in sides):
        return False
    if value.entry_memory is None or value.entry_memory.state == "available":
        return True
    return not any(
        side.side == "after" and side.state == "available" for side in value.knowledge_sides
    )


def _answer_name(key: tuple[object, ...]) -> str:
    """A short name of one answer's question, for an open's log: intent, overview, files, ..."""

    kind = str(key[0]) if key else "answer"
    if kind in ("live", "live-kept"):
        return "live " + _answer_name(key[1:])
    if kind == "trees" and len(key) > 1 and isinstance(key[1], tuple):
        fields = key[1]
        view, lane = fields[6], fields[8]
        return "files" if lane else "overview" if view == "overview" else "trees"
    return kind


def _tree_key(query: ReviewTreesQuery) -> tuple[object, ...]:
    """Every field of a tree-view query that selects its answer."""

    return (
        "trees",
        query.repository_id,
        query.master,
        query.leaf_id,
        query.number,
        query.recorded,
        query.view,
        query.invariants,
        query.lane,
        query.file,
        query.entry,
        query.entry_side,
    )


def _persistent_binding(
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    number: int | None,
    resolved_reads: dict[str, str],
) -> tuple[tuple[ReadInput, ...], tuple[object, ...] | None]:
    """Bind cold indexes and retain every extra trusted observation for warm validation."""
    with recorded_reads() as index_reads:
        extra = _persistent_inputs(config, contract, number)
    replay_reads(index_reads)
    conflicts = any(
        resolved_reads.get(path, identity) != identity for path, identity in index_reads.items()
    )
    resolved_reads.update(index_reads)
    if extra is None or conflicts:
        return (), None
    sources, identities = extra
    return sources, (*identities, tuple(sorted(resolved_reads.items())))


def _persistent_inputs(
    config: McpRuntimeConfig, contract: WorktreeContract, number: int | None
) -> tuple[tuple[ReadInput, ...], tuple[str, ...]] | None:
    """Current record/index bytes from their owner, never paths read from a disk answer."""
    trees = reopen_review_trees(config.coordination_root, contract, number)
    if not isinstance(trees, ReviewTrees):
        return None
    extra = _index_inputs(trees)
    if extra is None:
        return None
    sources, identities = extra
    # The reopened record's own bytes; the leaf's other records are no input of its answer.
    record = comparison_directory(contract.task_root, contract.leaf_id) / (
        f"{trees.record.number}.json"
    )
    return (ContentRead(record), *sources), identities


def _index_inputs(trees: ReviewTrees) -> tuple[tuple[ReadInput, ...], tuple[str, ...]] | None:
    """The logical and physical inputs of these already-resolved indexes."""
    databases = (trees.before.database, trees.after.database)
    if any(path is None for path in databases):
        return None
    try:
        paths = tuple(path for path in databases if path is not None)
        identities = tuple(dataset_identity(path).logical_digest for path in paths)
    except (KnowledgeStorageError, OSError, ValueError):
        return None
    physical = tuple(
        Path(f"{path}{suffix}") for path in paths for suffix in ("", "-wal", "-journal")
    )
    return tuple(ContentRead(path) for path in physical), identities


def _whole_review(request: ReviewSurfaceRequest) -> bool:
    """A whole review read -- the task context's or one subject's: no page, no refreshed binding.

    Both are the questions whose every read was named (D570; a subject's since D574), so a live
    leaf's answer to them is kept on its comparison's witness.
    """

    return (
        request.page_of is None
        and request.continuation is None
        and request.previous_binding_digest is None
    )


def _reviewed_comparison(value: object) -> int | None:
    """The comparison number a live review answer was resolved on, or ``None``."""

    if not isinstance(value, KnowledgeReviewResult) or value.payload is None:
        return None
    movement = value.payload.external_git_movement
    if movement is None or movement.comparison is None:
        return None
    return movement.comparison.number


def _listed_comparison(value: object) -> int | None:
    """The comparison number a live subject catalogue was listed on, or ``None``."""

    if not isinstance(value, ReviewEntryListResult) or value.state != "entries":
        return None
    return value.comparison


def _live_sources(
    config: McpRuntimeConfig, witness: LiveWitness, repository_id: str, master: str, leaf_id: str
) -> tuple[ReadInput, ...]:
    """Every input a live task-context answer reads beside its witnessed trees (D570).

    The read audit of the review and the catalogue names, beyond the task documents they record:
    the leaf's record inputs and persisted worklist (:func:`_sources_of`); the witness's own
    inputs; the refs they resolve in the code and memory repositories -- the comparison's pin,
    and the source and work branches whose heads the review reports; and the index cache's
    placement, which Git's work-tree discovery decides from each ancestor's ``.git``.
    """

    contract, record = witness.contract, witness.trees.record
    pin = review_ref(record.task_id, record.leaf_id, record.number)
    references = (
        (contract.code_repo_path, (contract.code_source_branch, contract.code_work_branch, pin)),
        (
            contract.memory_repo_path,
            (contract.memory_source_branch, contract.memory_work_branch, pin),
        ),
    )
    refs = tuple(
        RepositoryRead(
            Path(root), references=tuple(name for name in names if name), observe_objects=False
        )
        for root, names in references
        if root is not None and Path(root).is_dir()
    )
    cache = default_cache_directory(config.coordination_root)
    placement = tuple(directory / ".git" for directory in (cache, *cache.parents))
    return tuple(
        dict.fromkeys(
            (
                *_sources_of(config, contract, repository_id, master, leaf_id),
                *witness.sources,
                *refs,
                *placement,
            )
        )
    )


def _unmeasured_open(value: ReviewIntentSummaryResult) -> bool:
    """A counted summary whose stored worklist measured no open count."""

    return (
        value.state == "counted"
        and value.attribution is not None
        and value.attribution.state == "counted"
        and value.unexplained_open is None
    )


def _keepable_summary(value: ReviewIntentSummaryResult) -> bool:
    return (
        value.state == "counted"
        and value.attribution is not None
        and value.attribution.state == "counted"
        and value.unexplained_open is not None
    )


def _keepable_persisted_tree(value: object) -> bool:
    return (
        isinstance(value, ReviewTreesResult)
        and value.state == "trees"
        and _keepable_tree_view(value)
        and (value.entry_inventory is None or value.entry_inventory.state == "ready")
        and (value.overview is None or value.overview.state == "ready")
    )


def _expected_reads(
    sources: tuple[ReadInput, ...], resolved_reads: Mapping[str, str]
) -> dict[str, str] | None:
    """Byte and selection identities obtained from current caller-owned resolution."""
    if has_failed_observation(resolved_reads) or changed_observations(resolved_reads):
        return None
    expected = dict(resolved_reads)
    for source in sources:
        path = source.path if isinstance(source, ContentRead) else source
        if isinstance(path, Path):
            identity = file_identity(path)
            previous = expected.get(str(path))
            if previous is not None and previous != identity:
                return None
            expected[str(path)] = identity
    return expected


#: How many detail answers (an invariant's cards, its review) are held, in memory only: two per
#: changed invariant of a leaf of up to 200 of them; the least recently used is computed again.
DETAIL_CAPACITY = 400


def recorded_review_answers(config: McpRuntimeConfig) -> RecordedReviewAnswers:
    """One process's exact-source review-answer owner, used by dashboard and tool composition."""
    from agents_remember.serving.build_info import process_serving_build  # noqa: PLC0415

    source = process_serving_build().source_digest
    directory = config.coordination_root / "runtime" / "review-answers"
    return RecordedReviewAnswers(
        config,
        RecordedAnswerReuse(
            directory=None if source is None else directory, build_namespace=source
        ),
        None if source is None else ClosureProofStore(directory / "closures", source),
        RecordedAnswerReuse(capacity=DETAIL_CAPACITY),
    )
