"""What reopening one durable comparison generation reports, channel by channel.

:mod:`agents_remember.application.review_comparison_generation` owns the record; this module owns the
*read-back*. :func:`reopen_comparison_generation` resolves a leaf's generation from the task artifact
plane alone -- no enclosure contract, no worktree, no live process state -- and then measures every
channel the record binds against the world it names:

* the two code objects, asked of the repository the record names, plus the custody the repository
  shows now rather than the custody that was recorded at freeze time;
* each retained knowledge snapshot, re-read as a dataset and compared against the exact logical
  identity that was frozen, with a digest read-back beside it;
* each cited owner-produced artifact, re-read and compared against the digest it was cited for.

Each channel answers with its own state and never with one verdict for the generation. The states are
deliberately distinct, because a consumer acts on them differently:

* ``available`` -- the exact recorded content resolves;
* ``not-recorded`` / ``not-selected`` -- a typed absence the record states, which is not a failure and
  does not make the generation unavailable;
* ``missing`` -- expected content is not there and nothing records a deletion;
* ``corrupt`` -- the bytes are there and are not the content the record binds;
* ``unavailable-history`` -- the record's own deletion record says this was deleted deliberately.

The generation-level state is separate again: ``available``, ``unavailable``, ``absent`` (nothing was
published), ``ambiguous`` (more than one record claims the index that was asked for, which is stated
rather than resolved by directory order) and ``manifest-unreadable`` (a record is there and cannot be
read, so nothing about it is claimed).

Nothing here writes, re-measures a comparison, or re-derives an identity an owner produced. It reads
bytes and object ids and reports what it found -- including, when a channel is gone, that it cannot
say *why* unless a deletion record says so.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import apsw

from agents_remember.application.review_comparison_generation import (
    COMPARISON_MANIFEST_NAME,
    TYPED_ABSENCE_STATES,
    ComparisonArtifactReference,
    ComparisonGenerationManifest,
    ComparisonGenerationRef,
    ComparisonHistoryDeletion,
    ComparisonKnowledgeBinding,
    ComparisonSourceBinding,
    KnowledgeSide,
    generation_directories,
    generation_directory,
    leaf_generation_root,
    read_generation_refs,
    read_history_deletion,
    read_manifest,
    task_root_for_review,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.worktrees.modules.code_object_retention import (
    CodeObjectCustody,
    CodeObjectObservation,
    CustodyNames,
    code_object_observation,
    object_readable,
    retained_object_readable,
)

__all__ = [
    "ComparisonEvidenceChannel",
    "ComparisonKnowledgeChannel",
    "ComparisonReopen",
    "ComparisonSourceChannel",
    "reopen_comparison_generation",
]


@dataclass(frozen=True)
class _Addressed:
    """The task context a reopen was asked for, as one value rather than three loose strings."""

    repository_id: str
    master: str
    leaf_id: str


# -- what a reopen reports ----------------------------------------------------------------------


@dataclass(frozen=True)
class ComparisonSourceChannel:
    """Whether the exact two code objects of one generation still resolve, and their custody now.

    ``custody_observed`` is measured at reopen and is ``None`` when the repository itself could not
    be read, which is a different fact from a measured ``retained``: a reader that filled the
    unreadable case with the recorded value would be reporting a measurement it never took.
    """

    state: Literal["available", "missing", "corrupt", "unavailable-history"]
    baseline_code_tree_id: str
    candidate_code_tree_id: str
    custody_recorded: CodeObjectCustody
    # What the repository shows *now*, in three values rather than two: ``absent`` says the object
    # does not resolve at all, which is neither of the recorded custody facts and must never be
    # reported as ``retained`` (a pin holding bytes that are gone).
    custody_observed: CodeObjectObservation | None
    # Whether the recorded pin resolves to the commit it recorded, measured now. This is the fact a
    # reader acts on; the release record below is history and is reported after it.
    pin_present: bool
    # Whether an explicit release of this generation's pin is on record. It is separate from both
    # facts above because it is a third one: a release may have discarded nothing (the objects still
    # resolve), and a comparison frozen again after a release has a live pin *and* a release on
    # record -- the reader is told which is current rather than being left to guess.
    release_recorded: bool
    detail: str


@dataclass(frozen=True)
class ComparisonKnowledgeChannel:
    """Whether one knowledge half of one generation still holds the dataset that was frozen."""

    side: KnowledgeSide
    state: Literal[
        "available",
        "not-recorded",
        "not-selected",
        "missing",
        "corrupt",
        "unavailable-history",
    ]
    identity: SnapshotIdentity | None
    path: Path | None
    detail: str


@dataclass(frozen=True)
class ComparisonEvidenceChannel:
    """Whether one cited owner-produced artifact still reads back as the bytes it was cited for."""

    owner: str
    relative_path: str
    state: Literal["available", "missing", "corrupt", "unavailable-history"]
    detail: str


@dataclass(frozen=True)
class ComparisonReopen:
    """The whole answer to "reopen this comparison": one state per channel and never one verdict.

    ``available`` means every channel that was expected to resolve did; a channel recording a typed
    absence is *not* an unavailability, because nothing was ever claimed to be there. ``unavailable``
    means at least one expected channel did not resolve, and ``unavailable_channels()`` names which,
    so a consumer can keep the channels that did resolve instead of discarding the generation.
    """

    state: Literal["available", "unavailable", "absent", "ambiguous", "manifest-unreadable"]
    repository_id: str
    master: str
    leaf_id: str
    generation: ComparisonGenerationRef | None = None
    manifest: ComparisonGenerationManifest | None = None
    source: ComparisonSourceChannel | None = None
    knowledge: tuple[ComparisonKnowledgeChannel, ...] = ()
    evidence: tuple[ComparisonEvidenceChannel, ...] = ()
    refusal: ReviewRefusal | None = None

    def available(self) -> bool:
        """Whether this reopen resolved every channel that was expected to resolve."""

        return self.state == "available"

    def unavailable_channels(self) -> tuple[str, ...]:
        """Return the exact channels that did not resolve, in the order they are reported."""

        failed = [
            f"{channel.side}:{channel.state}"
            for channel in self.knowledge
            if channel.state in {"missing", "corrupt", "unavailable-history"}
        ]
        failed.extend(
            f"evidence:{channel.relative_path}:{channel.state}"
            for channel in self.evidence
            if channel.state != "available"
        )
        if self.source is not None and self.source.state != "available":
            failed.insert(0, f"source:{self.source.state}")
        return tuple(failed)


def reopen_comparison_generation(
    config: McpRuntimeConfig,
    repository_id: str,
    master: str,
    leaf_id: str,
    *,
    generation_id: str | None = None,
) -> ComparisonReopen:
    """Reopen one leaf's recorded comparison generation from the task artifact plane alone.

    Nothing about the leaf's live enclosure is consulted: the generation is addressed by the task
    root its manifest records, so a leaf whose worktree, branches and process are all gone resolves
    exactly as it did when it was frozen. A named generation that is not there is *absent*; an
    unnamed one resolves to the highest index the leaf recorded, and a tie between two directories
    claiming one index is refused rather than resolved by directory order.
    """

    addressed = _Addressed(repository_id=repository_id, master=master, leaf_id=leaf_id)
    task_root = task_root_for_review(config, repository_id, master)
    if generation_id is not None:
        return _reopen_named(task_root, addressed, generation_id)
    return _reopen_latest(task_root, addressed)


def _reopen_named(task_root: Path, addressed: _Addressed, generation_id: str) -> ComparisonReopen:
    """Reopen one exact generation, reporting an absent or unreadable record by name."""

    directory = generation_directory(task_root, addressed.leaf_id, generation_id)
    if not directory.is_dir():
        return _absent(
            addressed,
            f"the leaf records no comparison generation {generation_id} under {directory}",
            "freeze the comparison before its worktree and its process are gone; a generation that "
            "was never published cannot be reopened",
            offending_input=generation_id,
        )
    return _read_and_measure(task_root, addressed, directory, generation_id)


def _reopen_latest(task_root: Path, addressed: _Addressed) -> ComparisonReopen:
    """Reopen the highest generation one leaf recorded, refusing an ambiguous or empty record."""

    refs = read_generation_refs(task_root, addressed.leaf_id)
    if not refs:
        unreadable = generation_directories(task_root, addressed.leaf_id)
        if len(unreadable) == 1:
            # One directory: report the record's own reason rather than a summary of it, because
            # "which field is wrong" is the only thing an operator can act on.
            return _read_and_measure(task_root, addressed, unreadable[0], unreadable[0].name)
        if unreadable:
            return _unreadable(
                addressed,
                f"the leaf has {len(unreadable)} published generation(s) "
                f"({', '.join(directory.name for directory in unreadable)}) and not one of their "
                "records could be read, so no generation is claimed",
            )
        return _absent(
            addressed,
            f"the leaf records no readable comparison generation under "
            f"{leaf_generation_root(task_root, addressed.leaf_id)}",
            "freeze the comparison before its worktree and its process are gone; a generation that "
            "was never published cannot be reopened",
            offending_input=addressed.leaf_id,
        )
    highest = refs[-1]
    tied = [
        ref
        for ref in refs
        if ref.generation_index == highest.generation_index
        and ref.binding_digest != highest.binding_digest
    ]
    if tied:
        return _ambiguous(
            addressed,
            f"two generations of this leaf claim index {highest.generation_index} with different "
            f"bindings ({', '.join(ref.generation_id for ref in tied)} and {highest.generation_id})",
        )
    return _read_and_measure(task_root, addressed, highest.directory, highest.generation_id)


def _read_and_measure(
    task_root: Path,
    addressed: _Addressed,
    directory: Path,
    generation_id: str,
) -> ComparisonReopen:
    """Read one generation's record and measure every channel it binds."""

    try:
        manifest = read_manifest(directory / COMPARISON_MANIFEST_NAME)
    except KnowledgeStorageError as error:
        return _unreadable(
            addressed,
            f"the comparison generation {generation_id} is present but its record cannot be read, "
            f"so nothing about it is claimed ({error})",
        )
    source = _source_channel(manifest, task_root)
    knowledge = tuple(_knowledge_channel(manifest, task_root, side) for side in _SIDES)
    evidence = tuple(_evidence_channel(task_root, reference) for reference in manifest.evidence)
    state: Literal["available", "unavailable"] = (
        "available" if _all_resolved(source, knowledge, evidence) else "unavailable"
    )
    return ComparisonReopen(
        state=state,
        repository_id=addressed.repository_id,
        master=addressed.master,
        leaf_id=addressed.leaf_id,
        generation=ComparisonGenerationRef(
            generation_id=manifest.generation_id,
            generation_index=manifest.generation_index,
            binding_digest=manifest.binding_digest,
            manifest_digest=manifest.manifest_digest(),
            directory=directory,
        ),
        manifest=manifest,
        source=source,
        knowledge=knowledge,
        evidence=evidence,
    )


_SIDES: tuple[KnowledgeSide, ...] = ("before", "after")


def _all_resolved(
    source: ComparisonSourceChannel,
    knowledge: tuple[ComparisonKnowledgeChannel, ...],
    evidence: tuple[ComparisonEvidenceChannel, ...],
) -> bool:
    """Whether every channel that was expected to resolve did resolve."""

    if source.state != "available":
        return False
    if any(channel.state not in TYPED_ABSENCE_STATES | {"available"} for channel in knowledge):
        return False
    return all(channel.state == "available" for channel in evidence)


def _source_channel(
    manifest: ComparisonGenerationManifest, task_root: Path
) -> ComparisonSourceChannel:
    """Measure both bound code objects, the recorded pin, and the custody the repository shows now."""

    binding = manifest.source
    repository = Path(binding.code_repository_root)
    deleted = _deletion_or_raise(task_root, manifest, "code-object")
    readable = repository.is_dir()
    baseline_readable = readable and object_readable(repository, binding.baseline_code_tree_id)
    candidate_readable = readable and object_readable(repository, binding.candidate_code_tree_id)
    names = CustodyNames(
        durable_refs=binding.custody_refs, recorded_commits=binding.custody_commits
    )
    custody_now = (
        code_object_observation(repository, binding.candidate_code_tree_id, names)
        if readable
        else None
    )
    pin_present = bool(
        binding.retained is not None
        and readable
        and retained_object_readable(repository, binding.retained)
    )
    state, detail = _source_state(
        binding=binding,
        deleted=deleted,
        baseline_readable=baseline_readable,
        candidate_readable=candidate_readable,
        repository=repository,
    )
    return ComparisonSourceChannel(
        state=state,
        baseline_code_tree_id=binding.baseline_code_tree_id,
        candidate_code_tree_id=binding.candidate_code_tree_id,
        custody_recorded=binding.custody,
        custody_observed=custody_now,
        pin_present=pin_present,
        release_recorded=deleted is not None,
        detail=_current_first(detail, pin_present, binding, deleted),
    )


def _current_first(
    detail: str,
    pin_present: bool,
    binding: ComparisonSourceBinding,
    deleted: ComparisonHistoryDeletion | None,
) -> str:
    """State the pin's measurement *now*, then the release history behind it.

    The order matters because the two can disagree without either being false: a comparison frozen
    again after its pin was released has a live pin and a release on record, and a detail that led
    with the release would read as though the content were gone while the reader is looking straight
    at it. The measurement leads; the record follows as the reason the pin is where it is.
    """

    if binding.retained is None:
        return detail
    state = "present" if pin_present else "absent"
    measured = f"the recorded pin {binding.retained.ref} is {state} in the repository now"
    if deleted is None:
        return f"{measured}; {detail}"
    return f"{measured}. Earlier, it was released explicitly: {deleted.reason}"


def _source_state(
    *,
    binding: ComparisonSourceBinding,
    deleted: ComparisonHistoryDeletion | None,
    baseline_readable: bool,
    candidate_readable: bool,
    repository: Path,
) -> tuple[Literal["available", "missing", "unavailable-history"], str]:
    """The source channel's state and its own words, from the facts the caller measured.

    The order is the point. A released pin is *not* by itself an unavailable source: the release may
    have been a no-op that stopped duplicating history a branch already holds. So readability is
    measured first, and the deletion record decides the state only for objects that really are gone.
    """

    if not repository.is_dir():
        return ("missing", f"the recorded code repository {repository} is not present")
    if baseline_readable and candidate_readable:
        return ("available", _resolved_source_detail(binding, deleted))
    gone = [
        name
        for name, present in (
            (f"baseline {binding.baseline_code_tree_id}", baseline_readable),
            (f"candidate {binding.candidate_code_tree_id}", candidate_readable),
        )
        if not present
    ]
    if deleted is not None:
        return (
            "unavailable-history",
            f"the recorded objects {', '.join(gone)} no longer resolve in {repository}, and the "
            f"code pin {deleted.deletion_owner} released is recorded as the reason: {deleted.reason}",
        )
    return ("missing", f"the recorded objects {', '.join(gone)} no longer resolve in {repository}")


def _resolved_source_detail(
    binding: ComparisonSourceBinding, deleted: ComparisonHistoryDeletion | None
) -> str:
    """Which of the shapes this generation's source side resolved under, and what happened to its pin."""

    released = (
        ""
        if deleted is None
        else (
            f" The pin recorded at freeze time was later released explicitly ({deleted.reason}), "
            "and both objects still resolve, so the release discarded no content"
        )
    )
    if binding.retained is None:
        return (
            "both bound objects resolve, and the record shows committed history already held the "
            f"tree when it was frozen, so no code pin was needed{released}"
        )
    return (
        f"both bound objects resolve under the explicit pin {binding.retained.ref}, which the "
        f"record shows was holding tree {binding.retained.tree} on base "
        f"{binding.retained.base_commit}{released}"
    )


def _knowledge_channel(
    manifest: ComparisonGenerationManifest, task_root: Path, side: KnowledgeSide
) -> ComparisonKnowledgeChannel:
    """Measure one knowledge half: its retained bytes, or the typed state it recorded instead."""

    binding = manifest.knowledge_side(side)
    if binding.state != "retained":
        # A non-retained side carries no identity and no artifact, and states its reason instead;
        # the record's own validator is what makes those three fields one fact.
        return ComparisonKnowledgeChannel(
            side=side,
            state=binding.state,
            identity=None,
            path=None,
            detail=binding.reason or f"the {side} side carries no snapshot",
        )
    artifact = binding.artifact
    assert artifact is not None  # a retained side always names its artifact; the validator says so
    directory = generation_directory(task_root, manifest.leaf_id, manifest.generation_id)
    path = directory / artifact.relative_path
    deleted = _deletion_or_raise(task_root, manifest, f"knowledge-{side}")
    if deleted is not None:
        return ComparisonKnowledgeChannel(
            side=side,
            state="unavailable-history",
            identity=None,
            path=path,
            detail=(
                f"the retained {side} snapshot was deleted explicitly by {deleted.deletion_owner} "
                f"within {deleted.cleanup_scope}: {deleted.reason}"
            ),
        )
    return _measure_snapshot(side, path, binding)


def _measure_snapshot(
    side: KnowledgeSide, path: Path, binding: ComparisonKnowledgeBinding
) -> ComparisonKnowledgeChannel:
    """Read one retained snapshot back and compare it against the dataset it was frozen as."""

    artifact = binding.artifact
    assert artifact is not None and binding.identity is not None
    if not path.is_file():
        return ComparisonKnowledgeChannel(
            side=side,
            state="missing",
            identity=None,
            path=path,
            detail=(
                f"the {side} snapshot recorded at {path} is not present, and no deletion of it was "
                "recorded; a missing expected dataset is unavailable, not absent history"
            ),
        )
    observed = _observed_snapshot(path)
    if observed is None:
        return ComparisonKnowledgeChannel(
            side=side,
            state="corrupt",
            identity=None,
            path=path,
            detail=(
                f"the {side} snapshot at {path} is not readable as a dataset of this code, so it is "
                "not the dataset this generation froze"
            ),
        )
    return _compare_snapshot(side, path, observed, binding)


def _compare_snapshot(
    side: KnowledgeSide,
    path: Path,
    observed: SnapshotIdentity,
    binding: ComparisonKnowledgeBinding,
) -> ComparisonKnowledgeChannel:
    """The channel for a readable snapshot, or the corrupt state naming what disagrees."""

    recorded = binding.identity
    assert recorded is not None
    if observed != recorded:
        return ComparisonKnowledgeChannel(
            side=side,
            state="corrupt",
            identity=observed,
            path=path,
            detail=(
                f"the {side} snapshot at {path} holds {observed.logical_digest}, while this "
                f"generation froze {recorded.logical_digest}; the bytes are not the dataset that "
                "was retained"
            ),
        )
    return ComparisonKnowledgeChannel(
        side=side,
        state="available",
        identity=observed,
        path=path,
        detail=(
            f"the {side} snapshot reads back as the dataset this generation froze "
            f"({observed.logical_digest})"
        ),
    )


def _observed_snapshot(path: Path) -> SnapshotIdentity | None:
    """One snapshot file's own logical identity, or ``None`` when it is not a dataset."""

    try:
        return dataset_identity(path)
    except (KnowledgeStorageError, apsw.Error, OSError):
        return None


def _evidence_channel(
    task_root: Path, reference: ComparisonArtifactReference
) -> ComparisonEvidenceChannel:
    """Read one cited owner-produced artifact back and compare it against its recorded digest."""

    path = _confined_reference(task_root, reference.relative_path)
    if path is None:
        return ComparisonEvidenceChannel(
            owner=reference.owner,
            relative_path=reference.relative_path,
            state="corrupt",
            detail=(
                f"the citation {reference.relative_path} is not inside the task artifact root "
                f"{task_root}, so it is not a reference this record may resolve"
            ),
        )
    try:
        payload = path.read_bytes()
    except OSError:
        return ComparisonEvidenceChannel(
            owner=reference.owner,
            relative_path=reference.relative_path,
            state="missing",
            detail=f"the cited artifact {path} is not present",
        )
    observed = _digest_bytes(payload)
    if observed != reference.sha256:
        return ComparisonEvidenceChannel(
            owner=reference.owner,
            relative_path=reference.relative_path,
            state="corrupt",
            detail=(
                f"the cited artifact {path} holds sha256 {observed}, while this generation cited "
                f"{reference.sha256}"
            ),
        )
    return ComparisonEvidenceChannel(
        owner=reference.owner,
        relative_path=reference.relative_path,
        state="available",
        detail=f"the cited artifact {path} reads back as the bytes this generation cited",
    )


def _confined_reference(task_root: Path, relative_path: str) -> Path | None:
    """Resolve one task-relative reference, or ``None`` when it escapes the task root."""

    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return Path(task_root) / candidate


def _deletion_or_raise(
    task_root: Path, manifest: ComparisonGenerationManifest, target: str
) -> ComparisonHistoryDeletion | None:
    """Read one target's deletion record, refusing a record that cannot be read."""

    return read_history_deletion(task_root, manifest.leaf_id, manifest.generation_id, target)


def _ambiguous(addressed: _Addressed, detail: str) -> ComparisonReopen:
    """One ambiguous-generation answer: more than one record claims the index that was asked for.

    A distinct state rather than an absence, because the two are different facts and a consumer acts
    on them differently: nothing is here, versus several things are and directory order must not pick
    between them. The remedy is the same one the packet's own revision rule uses -- name the exact
    identity -- so the refusal asks for the generation id rather than choosing.
    """

    return ComparisonReopen(
        state="ambiguous",
        repository_id=addressed.repository_id,
        master=addressed.master,
        leaf_id=addressed.leaf_id,
        refusal=_refusal(
            detail,
            "name the exact generation id to reopen; an ambiguous index is not resolved by "
            "directory order",
            offending_input="generation-index",
        ),
    )


def _unreadable(addressed: _Addressed, detail: str) -> ComparisonReopen:
    """One unreadable-record answer: the generation is there, and nothing about it is asserted."""

    return ComparisonReopen(
        state="manifest-unreadable",
        repository_id=addressed.repository_id,
        master=addressed.master,
        leaf_id=addressed.leaf_id,
        refusal=_refusal(
            detail,
            "restore the generation's manifest.json from the published artifact, or freeze the "
            "comparison again; the reopen never reconstructs a record it could not read",
            offending_input=COMPARISON_MANIFEST_NAME,
        ),
    )


def _absent(
    addressed: _Addressed,
    detail: str,
    next_action: str,
    *,
    offending_input: str,
) -> ComparisonReopen:
    """One absent-generation answer carrying the refusal that names what to do instead."""

    return ComparisonReopen(
        state="absent",
        repository_id=addressed.repository_id,
        master=addressed.master,
        leaf_id=addressed.leaf_id,
        refusal=_refusal(detail, next_action, offending_input=offending_input),
    )


def _refusal(detail: str, next_action: str, *, offending_input: str) -> ReviewRefusal:
    """One typed review refusal, in the surface's own vocabulary."""

    return ReviewRefusal(
        code="comparison_refused",
        detail=detail,
        next_action=next_action,
        offending_input=offending_input,
    )


def _digest_bytes(payload: bytes) -> str:
    """Return the lowercase hex sha256 of exact bytes, for read-backs against a recorded digest."""

    return hashlib.sha256(payload).hexdigest()
