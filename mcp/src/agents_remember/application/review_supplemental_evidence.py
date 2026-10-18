"""Byte receipts for evidence admitted from outside the four trees (MIK-R102 rule 6).

An admitted task requirement, ruling, report or execution receipt is not part of the comparison, so
it cannot be cited as though it were. This module admits one source through the task that owns it
and binds the bytes actually examined:

* **the source is resolved, not asserted.** A caller gives a task root and a task-relative path; the
  path must resolve inside the task's own admitted roots (``requirements/`` and ``notes/``), must not
  be a symlink, and may not escape the task. The receipt's canonical source is the resolved relative
  path, so a caller cannot receipt one document's bytes under another document's name.
* **the bytes are the evidence.** The digest and size are of exactly what was read, up to a stated
  bound; a larger source is refused with the bound rather than silently truncated.
* **a change is a new input.** :func:`reread_supplemental` re-resolves the *same* canonical source;
  changed bytes answer a successor receipt, a lost source answers ``unavailable``, and the earlier
  receipt keeps the digest it captured. Nothing is rewritten in place.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from agents_remember.models.knowledge.review_evidence import SupplementalReceipt
from agents_remember.models.knowledge.review_evidence_read import SupplementalRead

__all__ = [
    "ADMITTED_ROOTS",
    "MAX_SUPPLEMENTAL_BYTES",
    "admit_supplemental",
    "capture_supplemental",
    "reread_supplemental",
]

ADMITTED_ROOTS: tuple[str, ...] = ("requirements", "notes")
MAX_SUPPLEMENTAL_BYTES = 1024 * 1024

_UNREADABLE = (
    "the source was not read: no bytes were examined, so this receipt establishes nothing and its "
    "digest is absent rather than a digest of a document that was never opened"
)


def admit_supplemental(  # noqa: PLR0911 - one refusal per way a source can fail admission
    task_root: Path, relative: str
) -> SupplementalRead:
    """Resolve one admitted task source to its exact bytes, or the refusal it earns (rule 6)."""

    if not relative or relative.startswith("/") or ".." in relative.split("/") or "\\" in relative:
        return SupplementalRead(
            state="refused",
            canonical_source=None,
            admitted_root=None,
            detail=(
                f"the requested source {relative!r} is not one task-relative path inside the task's "
                "own admitted roots; an absolute path, a '..' segment and a foreign root are refused"
            ),
            refusal_code="foreign-source",
        )
    base = Path(task_root).resolve()
    unresolved = base / relative
    if _through_a_link(base, unresolved):
        return SupplementalRead(
            state="refused",
            canonical_source=relative,
            admitted_root=relative.split("/", 1)[0],
            detail=(
                f"the requested source {relative!r} passes through a symlink; a receipt binds the "
                "task's own bytes and follows no link"
            ),
            refusal_code="foreign-source",
        )
    target = unresolved.resolve()
    if not target.is_relative_to(base):
        return SupplementalRead(
            state="refused",
            canonical_source=None,
            admitted_root=None,
            detail=(
                f"the requested source {relative!r} resolves outside the task root {base}; a "
                "supplemental receipt is only evidence of the task's own documents"
            ),
            refusal_code="foreign-source",
        )
    canonical = target.relative_to(base).as_posix()
    root = canonical.split("/", 1)[0]
    if root not in ADMITTED_ROOTS:
        return SupplementalRead(
            state="refused",
            canonical_source=None,
            admitted_root=root,
            detail=(
                f"the requested source {canonical!r} lies under {root!r}, which is not one of the "
                f"admitted roots ({', '.join(ADMITTED_ROOTS)}); the comparison's own trees are read "
                "through the bound read, not through a receipt"
            ),
            refusal_code="foreign-source",
        )
    # The bound is enforced by the read itself: one byte beyond the ceiling is read, so a source
    # that grows between any earlier measurement and this read is refused on the bytes examined.
    try:
        with target.open("rb") as stream:
            payload = stream.read(MAX_SUPPLEMENTAL_BYTES + 1)
    except OSError as failure:
        return SupplementalRead(
            state="unavailable",
            canonical_source=canonical,
            admitted_root=root,
            detail=f"{_UNREADABLE}: {canonical} could not be read ({failure})",
        )
    if len(payload) > MAX_SUPPLEMENTAL_BYTES:
        return SupplementalRead(
            state="refused",
            canonical_source=canonical,
            admitted_root=root,
            byte_size=len(payload),
            detail=(
                f"the requested source {canonical!r} is at least {len(payload)} byte(s), beyond the "
                f"admitted {MAX_SUPPLEMENTAL_BYTES}; it is refused rather than truncated, because a "
                "receipt of a prefix would bind bytes this read did not examine"
            ),
            refusal_code="read-beyond-limit",
        )
    return SupplementalRead(
        state="present",
        canonical_source=canonical,
        admitted_root=root,
        digest=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        detail=f"admitted {canonical} from the task's own {root}/ root, {len(payload)} byte(s)",
    )


def _through_a_link(base: Path, target: Path) -> bool:
    """Whether any component of ``target`` below ``base`` is a symlink."""

    probe = base
    try:
        relative = target.relative_to(base)
    except ValueError:
        return True
    for part in relative.parts:
        probe = probe / part
        if probe.is_symlink():
            return True
    return False


def capture_supplemental(  # noqa: PLR0913 - one source's own identity and receipt fields
    task_root: Path,
    relative: str,
    *,
    receipt_id: str,
    capture_identity: str,
    establishes: str,
    producer: str | None = None,
    authority: str | None = None,
    source_version: str | None = None,
    limitation: str | None = None,
) -> SupplementalReceipt:
    """Admit one task source and capture the bytes it holds as a supplemental receipt."""

    admitted = admit_supplemental(task_root, relative)
    if admitted.state != "present":
        return SupplementalReceipt(
            receipt_id=receipt_id,
            canonical_source=admitted.canonical_source or relative,
            producer=producer,
            authority=authority,
            read_state="unavailable",
            capture_identity=capture_identity,
            source_version=source_version,
            establishes=establishes,
            limitation=f"{_UNREADABLE}: {admitted.detail}",
        )
    return SupplementalReceipt(
        receipt_id=receipt_id,
        canonical_source=str(admitted.canonical_source),
        producer=producer,
        authority=authority,
        digest=admitted.digest,
        byte_size=admitted.byte_size,
        read_state="present",
        capture_identity=capture_identity,
        source_version=source_version,
        establishes=establishes,
        limitation=limitation,
    )


def reread_supplemental(  # noqa: PLR0913 - the previous receipt and this read's own fields
    previous: SupplementalReceipt,
    *,
    task_root: Path,
    receipt_id: str,
    capture_identity: str,
    source_version: str | None = None,
    producer: str | None = None,
    authority: str | None = None,
) -> SupplementalReceipt:
    """The successor receipt one later read of the *same* admitted source earns (rule 6).

    The path is the earlier receipt's canonical source, resolved again through the task: a caller
    cannot pass another path and inherit this source's identity. Changed bytes are ``changed`` with
    their own digest; a source that did not move is still ``present``; a lost source is
    ``unavailable``. A version or authority the new bytes do not carry is dropped rather than copied
    forward, so a different document never wears the earlier one's provenance.
    """

    admitted = admit_supplemental(task_root, previous.canonical_source)
    update: dict[str, object] = {
        "receipt_id": receipt_id,
        "capture_identity": capture_identity,
        "source_version": source_version,
        "producer": producer if producer is not None else previous.producer,
        "authority": authority if authority is not None else previous.authority,
    }
    if admitted.state != "present":
        update.update(
            {
                "digest": None,
                "byte_size": 0,
                "read_state": "unavailable",
                "limitation": (
                    f"{_UNREADABLE}: re-reading {previous.canonical_source} failed "
                    f"({admitted.detail}); the earlier receipt {previous.receipt_id} keeps the "
                    "digest it captured"
                ),
            }
        )
        return previous.model_copy(update=update)
    changed = admitted.digest != previous.digest
    update.update(
        {
            "digest": admitted.digest,
            "byte_size": admitted.byte_size,
            "read_state": "changed" if changed else "present",
            "limitation": (
                (
                    f"the source moved since receipt {previous.receipt_id}: these bytes are a new "
                    "input and the earlier digest stays with the earlier receipt"
                )
                if changed
                else None
            ),
        }
    )
    return previous.model_copy(update=update)
