"""The cited evidence bytes' named destination, and the read-back that proves they survived.

``KS-R15@v1`` §6.6 fixes one destination for an assessment's cited evidence bytes:

.. code-block:: text

    <task_root>/notes/reports/evidence/<assessment_id>/<filename>

and gives three measured reasons for it: it is the shipped durable precedent for exactly this
property (the curator-coherence authority's own route onto the coordination task root), it is outside
the worktree group that terminal cleanup removes, and the terminal enclosure archive **cannot** hold
it -- that archive's scanner admits a fixed content set and refuses an unclassifiable canonical-root
file with ``terminal-archive-unowned-artifact``.

Three rules are implemented here rather than asserted in a docstring:

* **A digest without a path cannot be read back.** Each published byte is recorded on the assessment
  record as three facts -- its task-root-relative path, its digest and its size -- so
  :func:`read_back_published_bytes` can open it later without guessing. The recorded destination is
  resolved through the *shipped* resolver
  :func:`~agents_remember.worktrees.integration.closeout.curator_coherence.resolve_curator_evidence_ref`,
  so the bytes a reader opens are the bytes that resolver already confines to a namespace root.
* **The publisher and the reader agree by construction.** :func:`publish_assessment_evidence_bytes`
  returns one :class:`PublishedEvidenceByte` per citation, each carrying its own citation *and* its
  own measurement. A caller never has to zip two parallel sequences and hope the orders match: the
  correspondence between "what the curator cited" and "what the substrate kept" is a field on the
  value.
* **Survival is proven by reading back, never asserted.** The publication function writes each byte,
  opens it again by its recorded path and compares the digest before it returns. A failed read-back is
  a **blocked** terminal state carrying the exact destination, the expected digest and the observed
  state -- it is never repaired by writing a second copy, never re-homed into the terminal archive,
  and never reported as published (``KS-R15@v1`` §6.7, §7.4).

The published byte is a **copy of the cited bytes** at the task-root destination, and that is
deliberate rather than incidental: a citation may name a `code:` or `memory:` file inside a worktree
that terminal cleanup removes, so the only way §6.6's "the evidence bytes survive cleanup" can hold
for such a citation is for the surviving tree to hold the bytes. The record therefore carries both
facts -- the citation the curator wrote and the destination the substrate kept -- and the read-back
verifies the destination. What ties the two together is that the published bytes are the cited bytes
by content: :func:`publish_assessment_evidence_bytes` computes the recorded digest *from the source
it read* before writing the copy, so a digest mismatch is a blocked item rather than a silently
different file.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentEvidenceByte,
    AssessmentEvidenceReference,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

from .curator_coherence import resolve_curator_evidence_ref

EVIDENCE_DIRECTORY = Path("notes") / "reports" / "evidence"


class AssessmentEvidenceBlockedError(ValueError):
    """One cited evidence byte could not be published, or did not read back as written.

    This is the ``blocked`` terminal state ``KS-R15@v1`` §6.7 names, and its fields are deliberately
    the three facts that clause requires a blocked item to carry: the exact destination, the expected
    digest and the observed state.
    """

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        expected: dict[str, object],
        observed: dict[str, object],
    ) -> None:
        self.status = status
        self.detail = detail
        self.expected = dict(expected)
        self.observed = dict(observed)
        super().__init__(f"{status}: {detail}")

    def response_fields(self) -> dict[str, object]:
        """Return the typed refusal payload a wire response carries."""

        return {
            "status": self.status,
            "detail": self.detail,
            "expected": self.expected,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class PublishedEvidenceByte:
    """One citation paired with the byte the substrate published for it.

    The citation travels on the value rather than beside it, so a caller that keeps these in a list
    cannot lose the correspondence between what was cited and what was kept -- the defect a pair of
    parallel sequences invites.
    """

    reference: AssessmentEvidenceReference
    byte: AssessmentEvidenceByte


@dataclass(frozen=True)
class AssessmentEvidencePublication:
    """What one assessment's evidence-byte publication actually did, after the read-back."""

    assessmentId: str
    directory: Path
    taskRelativeDirectory: str
    published: tuple[PublishedEvidenceByte, ...]

    @property
    def bytes(self) -> tuple[AssessmentEvidenceByte, ...]:
        """Return the recorded per-byte facts, in citation order."""

        return tuple(item.byte for item in self.published)


def evidence_directory_for(contract: WorktreeContract, assessment_id: str) -> Path:
    """Return the one task-root publication directory an assessment's bytes live in.

    ``assessment_id`` is refused rather than normalized when it cannot form a single path component.
    A value carrying a separator or a parent reference would let a caller address a directory outside
    the named destination, and quietly rewriting it would mean the recorded path and the requested
    identity disagree -- so the refusal is the only shape that keeps "recorded" and "written" equal.
    """

    cleaned = assessment_id.strip()
    if (
        not cleaned
        or cleaned != assessment_id
        or "/" in cleaned
        or "\\" in cleaned
        or cleaned.startswith(".")
    ):
        raise AssessmentEvidenceBlockedError(
            "review-assessment-evidence-destination-invalid",
            f"assessment identity {assessment_id!r} cannot name one evidence directory",
            expected={"directory": EVIDENCE_DIRECTORY.as_posix()},
            observed={"assessmentId": assessment_id},
        )
    return contract.task_root / EVIDENCE_DIRECTORY / cleaned


def publish_assessment_evidence_bytes(
    contract: WorktreeContract,
    assessment_id: str,
    references: Sequence[AssessmentEvidenceReference],
) -> AssessmentEvidencePublication:
    """Publish each cited evidence byte to the 6.6 destination, then read every one back.

    The bytes published are the bytes the citation resolves to, which is what keeps this from being a
    second copy: there is one file, addressed by the citation the curator wrote, and the recorded path
    is that same file's task-root-relative spelling under the per-assessment directory. A citation
    that resolves outside the task root cannot be recorded by a task-root-relative path at all, and is
    refused rather than silently recorded under a path that would not open later.
    """

    directory = evidence_directory_for(contract, assessment_id)
    task_relative_directory = _task_relative(contract, directory, assessment_id)
    published: list[PublishedEvidenceByte] = []
    for reference in references:
        source = resolve_curator_evidence_ref(contract, reference.spelling)
        payload = _read(source)
        digest = hashlib.sha256(payload).hexdigest()
        destination = directory / source.name
        relative = _task_relative(contract, destination, assessment_id)
        try:
            atomic_write_bytes(destination, payload)
        except OSError as error:
            raise _blocked_write(assessment_id, destination, digest, error) from error
        published.append(
            PublishedEvidenceByte(
                reference=reference,
                byte=AssessmentEvidenceByte(path=relative, sha256=digest, size=len(payload)),
            )
        )
    publication = AssessmentEvidencePublication(
        assessmentId=assessment_id,
        directory=directory,
        taskRelativeDirectory=task_relative_directory,
        published=tuple(published),
    )
    read_back_published_bytes(contract, publication)
    return publication


def read_back_published_bytes(
    contract: WorktreeContract,
    publication: AssessmentEvidencePublication,
) -> tuple[AssessmentEvidenceByte, ...]:
    """Open every recorded byte by its recorded path and verify it against its recorded digest.

    This is ``KS-R15@v1`` §7.3's read-back, and it is the same function a post-cleanup reader calls:
    the record's own digest is not the read-back for the bytes, and a byte only counts as survived
    once its recorded path could be opened and its contents hashed to the recorded value. The three
    recorded facts are each checked, because they fail differently -- an absent path, a digest that
    moved, and a length that disagrees are three distinct observations and the blocked item reports
    which one it saw.

    It deliberately does **not** re-resolve the original citation. The published byte is a copy at
    this destination by design (a citation may name a worktree file that cleanup removes), so asking
    whether the citation and the destination are the same file would ask the wrong question; what
    makes the copy the *cited* bytes is that its recorded digest was computed from the bytes actually
    read from the citation before the copy was written.
    """

    for item in publication.published:
        resolved = _resolve_recorded(contract, item.byte)
        if not resolved.is_file():
            raise AssessmentEvidenceBlockedError(
                "review-assessment-evidence-read-back-absent",
                f"the recorded evidence byte could not be opened after publication: "
                f"{item.byte.path}",
                expected=_expected(item.byte),
                observed={"path": resolved.as_posix(), "state": "absent-or-unreadable"},
            )
        observed = _read(resolved)
        digest = hashlib.sha256(observed).hexdigest()
        if digest != item.byte.sha256 or len(observed) != item.byte.size:
            raise AssessmentEvidenceBlockedError(
                "review-assessment-evidence-read-back-mismatch",
                f"the recorded evidence byte does not match its recorded digest: {item.byte.path}",
                expected=_expected(item.byte),
                observed={
                    "path": resolved.as_posix(),
                    "sha256": digest,
                    "size": len(observed),
                    "state": "present-but-different",
                },
            )
    return publication.bytes


def _expected(byte: AssessmentEvidenceByte) -> dict[str, object]:
    return {"path": byte.path, "sha256": byte.sha256, "size": byte.size}


def _resolve_recorded(contract: WorktreeContract, byte: AssessmentEvidenceByte) -> Path:
    root = contract.task_root.resolve()
    resolved = (root / byte.path).resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise AssessmentEvidenceBlockedError(
            "review-assessment-evidence-path-escapes-task-root",
            f"a recorded evidence path does not resolve inside the task root: {byte.path}",
            expected={"taskRoot": root.as_posix()},
            observed={"path": resolved.as_posix()},
        )
    return resolved


def _task_relative(contract: WorktreeContract, path: Path, assessment_id: str) -> str:
    resolved = path.resolve(strict=False)
    root = contract.task_root.resolve()
    if not resolved.is_relative_to(root):
        raise AssessmentEvidenceBlockedError(
            "review-assessment-evidence-outside-task-root",
            f"assessment {assessment_id} would publish evidence outside the task root",
            expected={"taskRoot": root.as_posix()},
            observed={"path": resolved.as_posix()},
        )
    return resolved.relative_to(root).as_posix()


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise AssessmentEvidenceBlockedError(
            "review-assessment-evidence-unreadable",
            f"a cited evidence byte could not be read: {path}",
            expected={"path": path.as_posix()},
            observed={"error": type(error).__name__, "state": "unreadable"},
        ) from error


def _blocked_write(
    assessment_id: str, destination: Path, digest: str, error: OSError
) -> AssessmentEvidenceBlockedError:
    return AssessmentEvidenceBlockedError(
        "review-assessment-evidence-unwritable",
        f"assessment {assessment_id} could not publish its cited bytes to the named destination",
        expected={"path": destination.as_posix(), "sha256": digest},
        observed={"error": type(error).__name__, "state": "unwritable"},
    )


__all__ = [
    "EVIDENCE_DIRECTORY",
    "AssessmentEvidenceBlockedError",
    "AssessmentEvidencePublication",
    "PublishedEvidenceByte",
    "evidence_directory_for",
    "publish_assessment_evidence_bytes",
    "read_back_published_bytes",
]
