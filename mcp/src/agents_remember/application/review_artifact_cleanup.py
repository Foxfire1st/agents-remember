"""The archive hook for review artifacts: nothing a review pinned or copied outlives its task (D17).

MIK-R25 rule 5. When a task is archived, this deletes, in both the code and the memory repository:

* every ``refs/ar/review/<task-directory>/…`` ref -- the pins of the task's tree comparisons, named
  by the task's directory name and nothing else;
* the task's legacy ``refs/ar/retained-code/<leaf>/…`` refs -- the landed reviewer's code pins, one
  per comparison generation of a leaf of this task;

and, under the task's notes, every legacy knowledge dataset copy -- a comparison generation's
retained snapshot, or any other SQLite file with the knowledge schema, identified by its content
rather than its name (MIK-R26 rule 6). Git-tracked files are skipped: they leave with their branch.

A generation's pin and snapshots are released through their own deletion owners
(:mod:`agents_remember.application.review_comparison_reclamation`), which write the
unavailable-history record first, so a later reopen of the archived generation says the history was
deleted and why. Whatever those owners did not cover is deleted directly. Every deletion, and every
failure, is recorded in the task's archive report (``notes/reports/review-artifact-cleanup.json``),
which the finalizer also returns under ``taskArchive.reviewArtifacts``. The receipts are kept per
attempt and written before anything is deleted
(:mod:`agents_remember.application.review_artifact_receipts`); a retry lists what an earlier attempt
already dealt with as ``alreadyAbsent``. Nothing else creates review copies, so nothing else needs
cleaning.

**Where every target's identity comes from (R3-R5 rulings).**

* **Review refs** are named by the task's directory name (the series contract's ``task_root``) and
  looked up by it. Nothing read from a file under the task folder enters that namespace.
* **The trust line for legacy cleanup.** The ICR-era retained-code pins and a generation manifest's
  own-leaf check need the task's short id (``260921-ICR``), which only the task folder's
  system-written control documents carry. They -- the root series contract (which finalize already
  trusts for the repository paths), ``task.json`` and the leaf enclosure contracts -- are accepted as
  the identity source for these two legacy targets only: ``task.json``'s ``id`` counts when it is the
  directory name, or when a leaf contract of this task (physically inside it, naming it as parent)
  carries ``<id>-L<n>`` (:func:`_confirmed_task_id`). Any other file content -- a comparison record,
  a manifest -- is data checked against that identity, never a source of targets.
* **Physical confinement.** Every path the hook deletes, releases or writes lies inside the resolved
  task root with no symlink on the way (:class:`_Confinement`); what fails is held and reported.
* **A check-then-use window** remains between :class:`_Confinement` and the owners' own writes; it
  needs a concurrent writer inside the just-archived task, which could delete those files directly
  anyway, so it grants nothing (accepted, R5 ruling).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

import apsw

from agents_remember.application.review_artifact_receipts import (
    CLEANUP_REPORT_NAME,
    DELETION_KEYS,
    ReceiptLedger,
)
from agents_remember.application.review_comparison_generation import (
    COMPARISON_DELETIONS_DIRECTORY,
    COMPARISON_GENERATIONS_DIRECTORY,
    COMPARISON_MANIFEST_NAME,
    ComparisonGenerationManifest,
    generation_directory,
    read_manifest,
)
from agents_remember.application.review_comparison_reclamation import (
    discard_comparison_snapshots,
    release_comparison_code_object,
)
from agents_remember.errors import CodeObjectRetentionError, ComparisonReclamationError
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.review_trees import REVIEW_REF_NAMESPACE
from agents_remember.tasks.task_paths import slugify
from agents_remember.worktrees.modules.code_object_retention import RETAINED_CODE_REF_NAMESPACE
from agents_remember.worktrees.services import ReviewArtifactCleanupRequest
from agents_remember.worktrees.worktree_contract import ContractError, load_contract

__all__ = [
    "CLEANUP_REPORT_NAME",
    "ReviewArtifactCleanup",
    "cleanup_review_artifacts",
]

_REASON: Final = "the task was archived (MIK-R25 rule 5, D17)"
_SQLITE_HEADER: Final = b"SQLite format 3\x00"
# The tables every generation of the knowledge store has had: a file holding both is a dataset.
_KNOWLEDGE_TABLES: Final = frozenset({"invariant", "invariant_revision"})


@dataclass
class _Report:
    dry_run: bool
    task_id: str
    review_refs: list[dict[str, str]] = field(default_factory=list)
    retained_code_refs: list[dict[str, str]] = field(default_factory=list)
    released_generations: list[dict[str, str]] = field(default_factory=list)
    dataset_copies: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    held_refs: set[str] = field(default_factory=set)
    confinement: _Confinement | None = None
    held_directories: list[Path] = field(default_factory=list)

    def hold(self, directory: Path, ref: str | None, detail: str) -> None:
        """Leave one generation's pin and snapshots alone, and report why."""

        self.held_directories.append(directory)
        if ref is not None:
            self.held_refs.add(ref)
        self.failures.append(
            {
                "target": directory.as_posix(),
                "detail": f"held, not deleted: {detail}",
                **({"ref": ref} if ref is not None else {}),
            }
        )

    def document(self) -> dict[str, Any]:
        verb = "would-delete" if self.dry_run else ("partial" if self.failures else "deleted")
        return {
            "schema": "ar-review-artifact-cleanup/v1",
            "state": verb,
            "taskId": self.task_id,
            "reason": _REASON,
            "reviewRefs": self.review_refs,
            "retainedCodeRefs": self.retained_code_refs,
            "releasedGenerations": self.released_generations,
            "datasetCopies": self.dataset_copies,
            "failures": self.failures,
            "alreadyAbsent": [],
        }


@dataclass(frozen=True)
class ReviewArtifactCleanup:
    """The composition-bound :class:`ReviewArtifactCleanupPort` of the worktree layer."""

    def cleanup(self, request: ReviewArtifactCleanupRequest) -> dict[str, Any]:
        return cleanup_review_artifacts(request)


def cleanup_review_artifacts(request: ReviewArtifactCleanupRequest) -> dict[str, Any]:
    """Delete (or, on a dry run, list) every review artifact of one archived task, and record it.

    It never raises for an artifact or a repository it cannot reach: that is a ``failures`` entry,
    because the task has already been archived when this runs. A real run first lists what it is
    about to delete and writes that into this attempt's receipt; it deletes nothing when that
    receipt cannot be written.
    """

    confined = _Confinement(request.task_root)
    ledger = ReceiptLedger.open(
        durable_reports_root(confined.task_root),
        confined.problem,
        lambda name: _still_there(request, confined, name),
    )
    if request.dry_run:
        document = _sweep(request, confined).document()
        document["alreadyAbsent"] = ledger.already_absent(document)
        ledger.preview(document)
        return document
    planned = _sweep(replace(request, dry_run=True), confined).document()
    if any(planned[key] for key in DELETION_KEYS):
        refused = ledger.begin(planned)
        if refused is not None:
            return _nothing_deleted(planned, ledger, refused)
    document = _sweep(request, confined).document()
    document["alreadyAbsent"] = ledger.already_absent(document)
    ledger.finish(document)
    return document


def _sweep(request: ReviewArtifactCleanupRequest, confined: _Confinement) -> _Report:
    """One pass over the task's review artifacts: list them on a dry run, delete them otherwise."""

    # The legacy identity (retained-code pins, manifest own-leaf check) follows the trust line; the
    # review-ref namespace is the directory name, whatever any file says.
    task_id, identity_problem = _confirmed_task_id(request, confined)
    report = _Report(dry_run=request.dry_run, task_id=task_id)
    if identity_problem is not None and _pins_under_unconfirmed_id(request, confined):
        report.failures.append({"target": "task.json", "detail": identity_problem})
    _release_generations(request, report, confined)
    for repository in dict.fromkeys((request.code_repository, request.memory_repository)):
        if repository is None:
            continue
        if not repository.is_dir():
            report.failures.append(
                {"target": repository.as_posix(), "detail": "the repository does not exist"}
            )
            continue
        _delete_refs(
            repository, f"{REVIEW_REF_NAMESPACE}/{request.task_name}/", report.review_refs, report
        )
        for ref in _retained_code_refs(repository, task_id):
            if ref not in report.held_refs:
                _delete_ref(repository, ref, report.retained_code_refs, report)
    report.confinement = confined
    _delete_dataset_copies(confined, report)
    return report


def _nothing_deleted(
    planned: dict[str, Any], ledger: ReceiptLedger, refused: str
) -> dict[str, Any]:
    """The outcome of an attempt that could not write its receipt, and so deleted nothing."""

    document = _Report(dry_run=False, task_id=planned["taskId"]).document()
    document["state"] = "partial"
    document["reportPath"] = None
    document["failures"] = [
        *planned["failures"],
        {
            "target": ledger.path.as_posix(),
            "detail": "nothing was deleted, because this attempt's receipt cannot be written: "
            f"{refused}",
        },
    ]
    document["alreadyAbsent"] = ledger.already_absent(document)
    return document


def _still_there(request: ReviewArtifactCleanupRequest, confined: _Confinement, name: str) -> bool:
    """Whether a named artifact of this task still exists; yes for a name that is not one."""

    if name.startswith("refs/"):
        return any(
            repository is not None and repository.is_dir() and _ref_exists(repository, name)
            for repository in (request.code_repository, request.memory_repository)
        )
    path = Path(name)
    if not path.is_absolute() or confined.problem(path) is not None:
        return True
    return path.exists() or path.is_symlink()


# -- physical confinement --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Confinement:
    """Every path the hook touches lies physically inside the archived task (R4 ruling).

    ``task_root`` is resolved once. A path passes when every component between the task root and it
    is a real directory (``lstat``: no symlink on the way, however it resolves) and when the path
    itself resolves inside the resolved root. A symlink anywhere below the task root therefore
    confines nothing: the hook holds what lies behind it and reports it.
    """

    task_root: Path

    @property
    def root(self) -> Path:
        return self.task_root.resolve()

    def problem(self, path: Path) -> str | None:
        """Why ``path`` is not physically inside the task, or ``None`` when it is."""

        try:
            relative = path.relative_to(self.task_root)
        except ValueError:
            return f"{path} is not under the task root {self.task_root}"
        current = self.task_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return f"{current} is a symlink; nothing behind it is the task's own"
        try:
            resolved = path.resolve()
        except OSError as error:
            return f"{path} cannot be resolved: {error}"
        if not resolved.is_relative_to(self.root):
            return f"{path} resolves to {resolved}, outside the task {self.root}"
        return None


def _confirmed_task_id(
    request: ReviewArtifactCleanupRequest, confined: _Confinement
) -> tuple[str, str | None]:
    """The legacy identity (retained-code pins, manifest own-leaf check), and why not, if it is not.

    This is the trust line of the module docstring; it never names the review-ref namespace.

    The contract's own identity is the task directory name (``request.task_name``). A ``task.json``
    ``id`` is taken only when it is that name, or when a leaf enclosure contract of this task (one
    naming it as parent, physically inside the task) has a leaf id ``<id>-L<n>`` -- the id every pin
    of that leaf was named under. Otherwise the directory name is used and the id is reported, as a
    failure while a legacy pin exists under it (:func:`_pins_under_unconfirmed_id`).
    """

    named = _task_document_id(confined) or request.task_name
    if named == request.task_name:
        return named, None
    own = re.compile(rf"{re.escape(named)}-L\d+[A-Za-z]?", re.IGNORECASE)
    for path in sorted((request.task_root / "enclosures").glob("*/series-contract.md")):
        if confined.problem(path) is not None:
            continue
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            continue
        if contract.parent_task_name == request.task_name and own.fullmatch(contract.leaf_id):
            return named, None
    return request.task_name, (
        f"task.json names the task id {named!r}, which no leaf contract of task "
        f"{request.task_name!r} carries; the legacy pins and manifests of {named!r} are not "
        "touched"
    )


def _pins_under_unconfirmed_id(
    request: ReviewArtifactCleanupRequest, confined: _Confinement
) -> bool:
    """Whether a legacy pin exists that only the unconfirmed ``task.json`` id would select.

    An id that no leaf contract confirms is a failure because pins named under it are left
    untouched. A task that never had a leaf enclosure has no such pin: nothing is left untouched,
    so there is nothing to report and nothing a repeated attempt could ever clear.
    """

    named = _task_document_id(confined)
    return named is not None and any(
        _retained_code_refs(repository, named)
        for repository in (request.code_repository, request.memory_repository)
        if repository is not None and repository.is_dir()
    )


def _task_document_id(confined: _Confinement) -> str | None:
    """``task.json``'s ``id``, read only when the file is physically the task's own."""

    path = confined.task_root / "task.json"
    if confined.problem(path) is not None:
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    identifier = loaded.get("id") if isinstance(loaded, dict) else None
    return identifier if isinstance(identifier, str) and identifier else None


# -- generations: released through their own deletion owners -----------------------------------------


def _release_generations(
    request: ReviewArtifactCleanupRequest, report: _Report, confined: _Confinement
) -> None:
    root = durable_reports_root(request.task_root) / COMPARISON_GENERATIONS_DIRECTORY
    for manifest_path in sorted(root.glob(f"*/*/{COMPARISON_MANIFEST_NAME}")):
        outside = confined.problem(manifest_path)
        if outside is not None:
            report.hold(manifest_path.parent, _named_pin(manifest_path), outside)
            continue
        try:
            manifest = read_manifest(manifest_path)
        except KnowledgeStorageError as error:
            report.hold(manifest_path.parent, None, str(error))
            continue
        entry = {"leaf": manifest.leaf_id, "generation": manifest.generation_id}
        foreign = _foreign_generation(request, report.task_id, manifest, manifest_path) or (
            _outside_generation(confined, manifest, manifest_path)
        )
        if foreign is not None:
            retained = manifest.source.retained
            report.hold(manifest_path.parent, None if retained is None else retained.ref, foreign)
            continue
        if request.dry_run:
            report.released_generations.append({**entry, "state": "would-release"})
            continue
        refused = _release_one(request.task_root, manifest, report)
        if refused is not None:
            retained = manifest.source.retained
            report.hold(manifest_path.parent, None if retained is None else retained.ref, refused)
            continue
        report.released_generations.append({**entry, "state": "released"})


def _foreign_generation(
    request: ReviewArtifactCleanupRequest,
    task_id: str,
    manifest: ComparisonGenerationManifest,
    manifest_path: Path,
) -> str | None:
    """Why a manifest names something outside this task, or ``None`` when all of it is the task's.

    Every deletion target must come from the archived task's own identity, never from what a file
    under its folder says. So a generation is released only when its leaf and its pin's leaf are
    this task's own (the F1 rule), its code repository is the task's, and the owner's paths --
    the generation directory and each snapshot -- stay inside the directory the manifest was found in.
    """

    own = _own_leaf(task_id)
    retained = manifest.source.retained
    directory = manifest_path.parent.resolve()
    if not own.fullmatch(slugify(manifest.leaf_id)):
        return f"the manifest's leaf {manifest.leaf_id} is not a leaf of task {task_id}"
    if retained is not None and not own.fullmatch(_ref_leaf(retained.ref)):
        return f"the manifest's pin {retained.ref} is not a pin of task {task_id}"
    repository = request.code_repository
    if repository is None or not _same_path(Path(manifest.source.code_repository_root), repository):
        return (
            f"the manifest names the code repository {manifest.source.code_repository_root}, "
            f"not this task's {repository}"
        )
    owned = generation_directory(request.task_root, manifest.leaf_id, manifest.generation_id)
    if owned.resolve() != directory:
        return f"the manifest's generation directory is {owned}, not where it was found"
    for binding in manifest.knowledge:
        artifact = binding.artifact
        if artifact is not None and not (
            directory / artifact.relative_path
        ).resolve().is_relative_to(directory):
            return f"the snapshot path {artifact.relative_path} leaves the generation directory"
    return None


def _named_pin(manifest_path: Path) -> str | None:
    """The pin a held generation's manifest names (read only), so that pin is held as well."""

    try:
        retained = read_manifest(manifest_path).source.retained
    except (KnowledgeStorageError, OSError):
        return None
    return None if retained is None else retained.ref


def _outside_generation(
    confined: _Confinement, manifest: ComparisonGenerationManifest, manifest_path: Path
) -> str | None:
    """Why an owner would touch a path outside the task: its records or one of its snapshots."""

    directory = manifest_path.parent
    targets = [directory / COMPARISON_DELETIONS_DIRECTORY]
    targets += [
        directory / binding.artifact.relative_path
        for binding in manifest.knowledge
        if binding.artifact is not None
    ]
    return next((problem for target in targets if (problem := confined.problem(target))), None)


def _own_leaf(task_id: str) -> re.Pattern[str]:
    """The F1 rule: this task's slug, or one of its leaves ``<slug>-l<n>``, and nothing longer."""

    return re.compile(rf"{re.escape(slugify(task_id))}(-l\d+[a-z]?)?")


def _ref_leaf(ref: str) -> str:
    return (
        ref[len(RETAINED_CODE_REF_NAMESPACE) + 1 :].split("/", 1)[0]
        if ref.startswith(f"{RETAINED_CODE_REF_NAMESPACE}/")
        else ""
    )


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _release_one(
    task_root: Path, manifest: ComparisonGenerationManifest, report: _Report
) -> str | None:
    """Release one generation's pin, then its snapshots, through their owners; why not, or ``None``.

    A refused (or unreachable) pin release stops here: neither that pin nor that generation's
    snapshots are then deleted by anything else in this run (they are held and reported).
    """

    leaf, generation = manifest.leaf_id, manifest.generation_id
    retained = manifest.source.retained
    repository = Path(manifest.source.code_repository_root)
    try:
        if retained is not None:
            if not repository.is_dir():
                return f"the generation's code repository {repository} does not exist"
            if _ref_exists(repository, retained.ref):
                release_comparison_code_object(task_root, leaf, generation, reason=_REASON)
                report.retained_code_refs.append(
                    {
                        "repository": repository.as_posix(),
                        "ref": retained.ref,
                        "via": "release_comparison_code_object",
                    }
                )
        for deletion in discard_comparison_snapshots(task_root, leaf, generation, reason=_REASON):
            report.dataset_copies.append(
                {
                    "path": deletion.cleanup_scope,
                    "sha256": deletion.deleted_digest,
                    "via": "discard_comparison_snapshots",
                }
            )
    except (
        CodeObjectRetentionError,
        ComparisonReclamationError,
        KnowledgeStorageError,
        OSError,
    ) as error:
        return str(error)
    return None


# -- refs --------------------------------------------------------------------------------------------


def _retained_code_refs(repository: Path, task_id: str) -> list[str]:
    """The legacy code pins of this task's leaves: ``refs/ar/retained-code/<task leaf>/…``."""

    own = _own_leaf(task_id)
    return [
        ref
        for ref in _refs(repository, f"{RETAINED_CODE_REF_NAMESPACE}/")
        if own.fullmatch(_ref_leaf(ref))
    ]


def _delete_refs(
    repository: Path, prefix: str, into: list[dict[str, str]], report: _Report
) -> None:
    for ref in _refs(repository, prefix):
        _delete_ref(repository, ref, into, report)


def _delete_ref(repository: Path, ref: str, into: list[dict[str, str]], report: _Report) -> None:
    try:
        target = run_git(repository, ["rev-parse", "--verify", "--quiet", ref]).stdout.strip()
        entry = {"repository": repository.as_posix(), "ref": ref, "target": target}
        if report.dry_run:
            into.append(entry)
            return
        deleted = run_git(repository, ["update-ref", "-d", ref])
    except OSError as error:
        report.failures.append({"target": ref, "detail": str(error)})
        return
    if deleted.returncode != 0:
        report.failures.append({"target": ref, "detail": deleted.stderr.strip()})
        return
    into.append(entry)


def _refs(repository: Path, prefix: str) -> list[str]:
    try:
        listed = run_git(repository, ["for-each-ref", "--format=%(refname)", prefix])
    except OSError:
        return []
    return listed.stdout.split() if listed.returncode == 0 else []


def _ref_exists(repository: Path, ref: str) -> bool:
    return run_git(repository, ["rev-parse", "--verify", "--quiet", ref]).returncode == 0


# -- dataset copies ------------------------------------------------------------------------------


def _delete_dataset_copies(confined: _Confinement, report: _Report) -> None:
    notes = _scan_root(confined, report)
    if notes is None:
        return
    # A symlink is never followed or deleted (the confinement refuses it): its target is not the
    # task's own file.
    for path in sorted(candidate for candidate in notes.rglob("*") if candidate.is_file()):
        if not _is_leftover_copy(path, report):
            continue
        entry: dict[str, Any] = {"path": path.as_posix(), "bytes": path.stat().st_size}
        if not report.dry_run:
            try:
                path.unlink()
            except OSError as error:
                report.failures.append({"target": path.as_posix(), "detail": str(error)})
                continue
        report.dataset_copies.append({**entry, "via": "content"})


def _scan_root(confined: _Confinement, report: _Report) -> Path | None:
    """The task's own ``notes`` to scan, or ``None``; a symlinked or escaping root is reported."""

    notes = confined.task_root / "notes"
    if not notes.exists() and not notes.is_symlink():
        return None
    outside = confined.problem(notes)
    if outside is not None:
        report.failures.append({"target": notes.as_posix(), "detail": f"not scanned: {outside}"})
        return None
    return notes


def _is_leftover_copy(path: Path, report: _Report) -> bool:
    """A knowledge dataset nothing tracks, outside every generation this run held back."""

    if any(path.is_relative_to(held) for held in report.held_directories):
        return False
    if report.confinement is not None and report.confinement.problem(path) is not None:
        return False
    return _is_knowledge_dataset(path) and not _tracked(path)


def _is_knowledge_dataset(path: Path) -> bool:
    """Whether a file is SQLite holding the knowledge store's tables, by content, not by name."""

    try:
        with path.open("rb") as handle:
            if handle.read(len(_SQLITE_HEADER)) != _SQLITE_HEADER:
                return False
        connection = open_read_only_database(path)
    except (OSError, apsw.Error, KnowledgeStorageError):
        return False
    try:
        names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    except apsw.Error:
        return False
    finally:
        connection.close()
    return names >= _KNOWLEDGE_TABLES


def _tracked(path: Path) -> bool:
    """Whether a Git working tree tracks the file (then it leaves with its branch, not here)."""

    listed = run_git(path.parent, ["ls-files", "--error-unmatch", "--", path.name])
    return listed.returncode == 0
