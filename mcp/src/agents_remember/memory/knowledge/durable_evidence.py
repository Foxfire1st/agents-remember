"""Publishing durable evidence, and the post-cleanup read-back that is the only proof it survived.

The evidence a projection relies on is the binding record itself plus -- where a reported observation
is not reproducible from records alone -- a durable evidence artifact. ``KS-R18@v1`` §3.3 fixes both
where that artifact goes and what proves it is still there:

* **The destination is ``<task_root>/notes/reports/``**, following the shipped curator-coherence
  route onto the coordination task root (``worktrees/integration/closeout/curator_coherence.py``).
  That path is **outside** the enclosure root and outside ``<worktree_group>/`` by construction. The
  enclosure's own ``reports/`` directory is **pre-closeout** evidence only -- it is removed at
  cleanup for a leaf contract -- and the terminal enclosure archive's content set is fixed, so this
  module neither writes into either of them nor widens anything.

* **A retention claim is proven by a post-cleanup read-back**, not by code inspection. Publication
  computes the artifact's digest and records it *with* the destination and the file name; the
  read-back reads the destination back and compares digests. The source review that the clause cites
  declines to certify retention in its own words, which is exactly why the measurement is mandatory
  rather than reassuring.

* **A failed or mismatching read-back is a blocked terminal state**, carrying the exact destination,
  the expected digest and the observed state -- never reported as published. That is why
  :class:`EvidenceReadBack` has a ``state`` rather than a boolean: "the bytes are not there" and "the
  bytes are different" are different facts, and neither is "published".

Nothing here decides *what* the evidence says. This module moves bytes to a named destination, records
the reference a later reader needs, and reports what it found when it read them back -- a publication
mechanism rather than a claim about the artifact's content.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = [
    "DURABLE_EVIDENCE_REFUSED_DESTINATIONS",
    "DurableEvidencePublication",
    "EvidenceReadBack",
    "enclosure_reports_removed",
    "publish_durable_evidence",
    "read_back_evidence",
]

# The two destinations a retention claim may **not** rest on, named here so a caller cannot reach
# them by accident and so a case can assert that neither is ever produced. An enclosure-local report
# is removed at cleanup for a leaf contract, and the terminal enclosure archive's content set is
# fixed to the enclosure manifest, the adoption receipt and the operation journals.
DURABLE_EVIDENCE_REFUSED_DESTINATIONS: tuple[str, ...] = ("enclosure_reports", "terminal_archive")

# Where a durable artifact may live, relative to the task root. This is the shipped curator-coherence
# route's own subdirectory, so one place owns "leaf evidence parked where it survives".
_TASK_RELATIVE_REPORTS = ("notes", "reports")


@dataclass(frozen=True)
class DurableEvidencePublication:
    """One published artifact: the exact destination, the digest, and how it was published.

    ``destination`` is absolute and is what a later reader is given -- the record carries it
    explicitly so nobody has to guess it. ``sha256`` is computed from the bytes that were written,
    so a read-back compares against what was published rather than against what the artifact was
    meant to say.
    """

    destination: Path
    file_name: str
    sha256: str
    byte_count: int
    replaced_existing: bool

    def reference(self) -> str:
        """Return the publication reference a record carries, as the exact destination path."""

        return str(self.destination)

    def digest(self) -> str:
        """Return the published digest in the canonical ``sha256:<hex>`` form."""

        return f"sha256:{self.sha256}"


@dataclass(frozen=True)
class EvidenceReadBack:
    """What reading one named durable destination back actually found.

    ``state`` is the fact, and ``published`` is deliberately not a state this can claim on its own:
    a publication is proven by a read-back that *matched*, and the two are separate events because
    the enclosure is cleaned between them.
    """

    destination: Path
    expected_sha256: str
    observed_sha256: str | None
    byte_count: int | None
    state: Literal["matched", "missing", "mismatched", "unreadable"]

    def matched(self) -> bool:
        """Return whether the read-back proved the published bytes are still there."""

        return self.state == "matched"

    def blocked_reason(self) -> str:
        """Return the exact blocked-terminal-state reason one failed read-back carries.

        The clause requires the failure to carry the destination, the expected digest and the
        observed state, so that is what this renders -- never "published".
        """

        if self.matched():
            return ""
        observed = self.observed_sha256 if self.observed_sha256 is not None else "<not readable>"
        return (
            f"the durable destination {self.destination} did not read back as published: "
            f"expected sha256 {self.expected_sha256}, observed {observed} (state {self.state}). "
            "A retention claim that rests on code inspection alone does not satisfy the clause, and "
            "this artifact is not reported as published."
        )


def publish_durable_evidence(
    task_root: Path,
    file_name: str,
    content: str,
    *,
    replaced_existing: bool = False,
) -> DurableEvidencePublication:
    """Publish one artifact to ``<task_root>/notes/reports/<file_name>`` and return its reference.

    The destination is built from the task root and never from the worktree group or the enclosure, so
    a caller cannot ask for a destination that cleanup removes: the shape of this function makes the
    unacceptable answer unrepresentable rather than merely discouraged. The file name is a single
    segment -- a name carrying a separator or a traversal is refused, because a "file name" that can
    leave the directory is not a name.
    """

    _require_one_file_name(file_name)
    directory = task_root.joinpath(*_TASK_RELATIVE_REPORTS)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / file_name
    existed = destination.exists()
    encoded = content.encode("utf-8")
    destination.write_bytes(encoded)
    return DurableEvidencePublication(
        destination=destination,
        file_name=file_name,
        sha256=hashlib.sha256(encoded).hexdigest(),
        byte_count=len(encoded),
        replaced_existing=existed or replaced_existing,
    )


def read_back_evidence(publication: DurableEvidencePublication) -> EvidenceReadBack:
    """Read one published destination back and compare digests against what was published.

    This is the acceptance measurement the clause requires. It reads the destination the record
    names -- not the enclosure, not the worktree, and not a regenerated copy -- and reports the fact
    it found, so a caller that runs it after cleanup is measuring retention rather than asserting it.
    """

    destination = publication.destination
    try:
        content = destination.read_bytes()
    except FileNotFoundError:
        return EvidenceReadBack(
            destination=destination,
            expected_sha256=publication.sha256,
            observed_sha256=None,
            byte_count=None,
            state="missing",
        )
    except OSError:
        return EvidenceReadBack(
            destination=destination,
            expected_sha256=publication.sha256,
            observed_sha256=None,
            byte_count=None,
            state="unreadable",
        )
    observed = hashlib.sha256(content).hexdigest()
    return EvidenceReadBack(
        destination=destination,
        expected_sha256=publication.sha256,
        observed_sha256=observed,
        byte_count=len(content),
        state="matched" if observed == publication.sha256 else "mismatched",
    )


def enclosure_reports_removed(enclosure_reports: Path) -> bool:
    """Return whether one enclosure's own ``reports/`` directory is gone.

    The clause's measurement is "publish, clean the enclosure up, read the destination back". This is
    the middle step, and it is measured rather than assumed for the same reason the read-back is: a
    retention claim that rests on reading ``worktrees/modules/cleanup.py`` is precisely the claim the
    source review declined to certify. Its next action is not to store evidence here -- it is to store
    it at the durable destination.
    """

    return not enclosure_reports.exists()


def _require_one_file_name(file_name: str) -> None:
    """Refuse a file name that is not exactly one path segment inside the reports directory."""

    if not file_name:
        raise ValueError("a durable evidence file name must be nonempty")
    if file_name in {".", ".."} or "/" in file_name or "\\" in file_name or "\x00" in file_name:
        raise ValueError(
            f"a durable evidence file name must be one path segment: {file_name!r}. A name that "
            "can leave the reports directory is not a name, and the destination this clause fixes "
            "is exactly <task_root>/notes/reports/."
        )
