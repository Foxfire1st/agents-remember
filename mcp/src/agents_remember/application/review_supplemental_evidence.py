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
import json
from pathlib import Path

from agents_remember.models.knowledge.review_evidence import SupplementalReceipt
from agents_remember.models.knowledge.review_evidence_read import SupplementalRead

__all__ = [
    "ADMITTED_ROOTS",
    "LEAF_DOCUMENT_ROOT",
    "MAX_SUPPLEMENTAL_BYTES",
    "admit_supplemental",
    "capture_supplemental",
    "packet_version_stale",
    "reread_supplemental",
]

ADMITTED_ROOTS: tuple[str, ...] = ("requirements", "notes")
#: The leaf's own task document is admitted by its exact names at the task root -- never the root
#: itself, and never another leaf's document (MIK-R102 v2 item 2).
LEAF_DOCUMENT_ROOT = "task"
MAX_SUPPLEMENTAL_BYTES = 1024 * 1024

_UNREADABLE = (
    "the source was not read: no bytes were examined, so this receipt establishes nothing and its "
    "digest is absent rather than a digest of a document that was never opened"
)


def admit_supplemental(  # noqa: PLR0911 - one refusal per way a source can fail admission
    task_root: Path, relative: str, *, leaf_document: str | None = None
) -> SupplementalRead:
    """Resolve one admitted task source to its exact bytes, or the refusal it earns (rule 6).

    The admitted roots are ``requirements/``, ``notes/`` and -- named file by file -- the leaf's own
    task document at the task root. ``leaf_document`` is the leaf's document stem (for example
    ``183_the-review-pages-explainer``): its ``.json`` and ``.md`` are admitted, the task root is
    not, and another leaf's document stays refused.
    """

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
    first = canonical.split("/", 1)[0]
    root = _admitted_root(canonical, leaf_document)
    if root is None:
        return SupplementalRead(
            state="refused",
            canonical_source=None,
            admitted_root=first,
            detail=(
                f"the requested source {canonical!r} lies under {first!r}, which is not one of the "
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
    leaf_document: str | None = None,
) -> SupplementalReceipt:
    """Admit one task source and capture the bytes it holds as a supplemental receipt."""

    admitted = admit_supplemental(task_root, relative, leaf_document=leaf_document)
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
    leaf_document: str | None = None,
) -> SupplementalReceipt:
    """The successor receipt one later read of the *same* admitted source earns (rule 6).

    The path is the earlier receipt's canonical source, resolved again through the task: a caller
    cannot pass another path and inherit this source's identity. Changed bytes are ``changed`` with
    their own digest; a source that did not move is still ``present``; a lost source is
    ``unavailable``. A version or authority the new bytes do not carry is dropped rather than copied
    forward, so a different document never wears the earlier one's provenance.
    """

    admitted = admit_supplemental(
        task_root,
        previous.canonical_source,
        leaf_document=_reread_leaf_document(task_root, previous, leaf_document),
    )
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


def _admitted_root(canonical: str, leaf_document: str | None) -> str | None:
    """The admitted root for ``canonical``, or ``None`` when the path is not admitted."""

    if "/" not in canonical:
        return LEAF_DOCUMENT_ROOT if _is_leaf_document(canonical, leaf_document) else None
    first = canonical.split("/", 1)[0]
    return first if first in ADMITTED_ROOTS else None


def _is_leaf_document(canonical: str, leaf_document: str | None) -> bool:
    """Whether this task-root file is the leaf's own document, by its exact two names."""

    if not leaf_document:
        return False
    stem = leaf_document.removesuffix(".json").removesuffix(".md")
    if not stem or "/" in stem or stem == "DEVELOPER-DIRECTION":
        return False
    return canonical in {f"{stem}.json", f"{stem}.md"}


def _reread_leaf_document(
    task_root: Path, previous: SupplementalReceipt, leaf_document: str | None
) -> str | None:
    """The leaf-document stem to admit on reread of a root-level task document (MIK-R102 v2)."""

    if leaf_document is not None:
        return leaf_document
    source = previous.canonical_source
    if "/" in source or not source.endswith((".json", ".md")):
        return None
    if previous.read_state not in {"present", "changed"}:
        return None
    stem = source.rsplit(".", 1)[0]
    if previous.producer and previous.producer.startswith("leaf "):
        expected_leaf = previous.producer.removeprefix("leaf ").strip()
        if not _stem_matches_leaf(task_root, stem, expected_leaf):
            return None
    return stem


def _stem_matches_leaf(task_root: Path, stem: str, expected_leaf: str) -> bool:
    """Whether ``<stem>.json`` at ``task_root`` is absent or does not name a foreign leaf."""

    doc_path = Path(task_root) / f"{stem}.json"
    if not doc_path.is_file():
        return True
    try:
        payload = json.loads(doc_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    doc_id = payload.get("id")
    return not isinstance(doc_id, str) or not doc_id or doc_id == expected_leaf


def packet_version_stale(task_root: Path, canonical_source: str, digest: str | None) -> str | None:
    """Why a packet receipt is stale, when the manifest registers a later version (MIK-R102 v2).

    A receipt binds the bytes it examined, and a packet's new version is a new file: the old file's
    bytes do not change, so a re-read alone can never notice. The manifest is what says the stable ID
    has moved on, and the answer names both versions so a reader can see what happened.
    """

    if not canonical_source.startswith("requirements/") or digest is None:
        return None
    manifest_path = Path(task_root) / "requirements" / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    stem = canonical_source.rsplit("/", 1)[-1]
    stable_id, _separator, version = stem.partition("-v")
    if not _separator:
        return None
    version = version.split("-", 1)[0]
    registered = _manifest_version(manifest, stable_id)
    if registered is None or registered == version:
        return None
    return (
        f"the manifest registers {stable_id}@v{registered} where this receipt examined "
        f"{stable_id}@v{version}; the packet moved on and the claim resting on it is stale"
    )


def _manifest_version(manifest: object, stable_id: str) -> str | None:
    """The version the manifest registers for one stable packet ID, when it names it."""

    packets = manifest.get("packets") if isinstance(manifest, dict) else None
    if not isinstance(packets, list):
        return None
    for entry in packets:
        if not isinstance(entry, dict) or entry.get("id") != stable_id:
            continue
        version = entry.get("version")
        if isinstance(version, str):
            return version.lstrip("v")
    return None
