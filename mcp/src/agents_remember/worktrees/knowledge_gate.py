"""The mandatory invariant gate at the worktree layer's routes (MIK-R09).

Every route that commits or lands memory asks the gate before it moves anything. The gate itself
ranks above this layer and is reached through
:class:`~agents_remember.worktrees.services.KnowledgeGatePort`; this module decides only whether it
applies, with a marker probe that reads no knowledge:

* **Applicability (rule 6).** The gate applies when K_C or K_B holds the layout marker
  ``knowledge/layout.json``, so a leaf that deletes the marker is still gated. Where neither side
  holds it, the memory is unconverted: the cutover lock (:mod:`.cutover_lock`, rule 6's second
  bullet) refuses it once the memory repository holds converted memory anywhere, naming the crossing
  sync; in a repository that holds none, the route behaves exactly as before this master and every
  function here returns ``None`` after the probe. A probe Git cannot answer refuses; an unreadable
  side is never taken for unconverted memory.
* **No bypass (rule 5).** A converted route with no bound gate is refused, never committed ungated.
  No function here takes a flag that skips it.

:func:`close_owner_history` is the closeout's own write (MIK-R07 rule 7): the memory commit that
publishes a leaf sets ``closed: true`` in its history file, creating the file with no rows when the
leaf has none.

**A direct landing's closing outlives the call (L09 review R1, finding 3; review R2).** A direct
landing closes the file before its journal generation exists, and the generation commits it later.
The closing is therefore kept in a receipt beside the series' reports, one per generation
(:func:`keep_direct_closing`), until that generation is decided (:func:`settle_direct_closing`): a
landed generation forgets it, a cancelled one -- or one never created -- restores the file's bytes,
but only while the file is still exactly what the closing wrote, so a later edit is never
overwritten. A request for another generation never replaces a kept receipt, and an unreadable
receipt refuses by name (:class:`ClosingReceiptError`); it is never passed over.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.models.knowledge_files.canonical import (
    CanonicalFormatError,
    canonical_text,
    parse_json,
)
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    LAYOUT_MARKER_PATH,
    history_path,
    owner_history_attempt,
)
from agents_remember.models.knowledge_files.history import HISTORY_SCHEMA
from agents_remember.worktrees.cutover_lock import cutover_lock_refusal
from agents_remember.worktrees.knowledge_validation import (
    LayoutProbeError,
    PairedCode,
    has_layout_marker,
    memory_commit_refusal,
)
from agents_remember.worktrees.modules.git import branch_commit, local_branch_ref
from agents_remember.worktrees.services import (
    DirectGateVerdict,
    KnowledgeGatePort,
    LandingGateRequest,
    LeafPublication,
    worktree_services,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "GATE_UNBOUND",
    "PREPARED_CLOSEOUT_UNCLOSABLE",
    "ClosingReceiptError",
    "HistoryClosing",
    "checkout_memory_converted",
    "close_owner_history",
    "closed_out_memory",
    "converted_memory",
    "direct_closing_receipt",
    "direct_gate_verdict",
    "forget_direct_closing",
    "keep_direct_closing",
    "landing_gate_refusal",
    "latest_owner_history",
    "leaf_cutover_refusal",
    "leaf_gate_refusal",
    "leaf_line",
    "leaf_memory_converted",
    "parent_memory_tip",
    "prepared_closeout_lock",
    "prepared_closeout_refusal",
    "require_readable_closings",
    "settle_direct_closing",
]

_HISTORY_DIRECTORY = f"{KNOWLEDGE_ROOT}/history"
GATE_UNBOUND = (
    "the mandatory invariant gate (MIK-R09) is not bound in this process; converted memory is "
    "never committed or landed ungated"
)


class GateProbeError(RuntimeError):
    """Git could not say whether a side of the route holds the layout marker."""


def converted_memory(repository: Path, *treeishes: str) -> bool:
    """Whether any named side holds the layout marker; a side Git cannot read raises."""

    try:
        return any(has_layout_marker(repository, treeish) for treeish in treeishes if treeish)
    except LayoutProbeError as error:
        raise GateProbeError(f"the mandatory invariant gate (MIK-R09) refuses: {error}") from error


def _port() -> KnowledgeGatePort | None:
    return worktree_services().knowledge_gate


def parent_memory_tip(contract: WorktreeContract) -> str | None:
    """The parent line's memory tip, the validator's base for a leaf (every record it made is new)."""

    if contract.memory_repo_path is None or not contract.memory_source_branch:
        return None
    return branch_commit(contract.memory_repo_path, contract.memory_source_branch)


def closed_out_memory(contract: WorktreeContract) -> tuple[str, ...]:
    """The memory commit of this leaf's completed closeout, when the contract records one.

    A leaf that continues after a closeout that was not integrated sits on that commit. The history
    file the closeout closed there is frozen, though the parent line -- the validator's base -- does
    not hold it yet, and the leaf's later rows go to its next attempt file (L37 ruling of
    2026-10-01T17:17:07, B). Only a recorded closeout freezes: a file closed by a hand commit is
    still the leaf's own and is read whatever its flag (L09 review R1, finding 1).
    """

    if contract.closeout_status == "completed" and contract.memory_content_commit:
        return (contract.memory_content_commit,)
    return ()


def leaf_line(contract: WorktreeContract) -> str:
    """How a refusal names a contract's memory line: its leaf (or task) and its memory worktree."""

    owner = contract.leaf_id or contract.task_name
    where = contract.memory_worktree or contract.memory_repo_path
    return f"{owner} ({where.as_posix()})" if where is not None else owner


def leaf_cutover_refusal(contract: WorktreeContract, operation: str) -> str | None:
    """The cutover lock over a contract whose memory is unconverted on every side (rule 6)."""

    if contract.memory_mode != "external":
        return None
    repository = contract.memory_repo_path or contract.memory_worktree
    return cutover_lock_refusal(repository, operation=operation, line=leaf_line(contract))


def leaf_memory_converted(contract: WorktreeContract) -> bool:
    """Whether a leaf's memory worktree, or its parent memory line's tip, holds the layout marker.

    The probe for the closeout's own writes (closing the history file, validating the exact tree):
    it reads one file's existence and one tree entry, and nothing else, for an unconverted leaf.
    """

    worktree = contract.memory_worktree
    if contract.kind != "leaf" or contract.memory_mode != "external" or worktree is None:
        return False
    if (worktree / LAYOUT_MARKER_PATH).is_file():
        return True
    repository = contract.memory_repo_path or worktree
    official = contract.memory_source_branch
    return bool(official) and converted_memory(repository, local_branch_ref(official))


def _leaf_gate_applies(contract: WorktreeContract, memory_tree: str) -> tuple[bool, str | None]:
    """Whether the gate applies to a leaf's candidate, or why its probe refuses."""

    repository = contract.memory_repo_path or contract.memory_worktree
    if contract.kind != "leaf" or contract.memory_mode != "external" or repository is None:
        return False, None
    official = contract.memory_source_branch
    try:
        official_ref = local_branch_ref(official) if official else ""
        return converted_memory(repository, memory_tree, official_ref), None
    except RuntimeError as error:  # an unreadable probe, or an invalid official branch cell
        return True, str(error)


PREPARED_CLOSEOUT_UNCLOSABLE = "prepared-closeout-knowledge-history-unclosable"


def prepared_closeout_refusal(contract: WorktreeContract) -> str | None:
    """Why the certified (prepared) closeout must refuse this leaf, or ``None`` (L09 ruling, gap 3).

    That path binds its memory commit to the exact curator-attested candidate, so it cannot write
    ``closed: true`` into the leaf's history file (MIK-R07 rule 7, MIK-R09 rule 3). On converted
    memory it therefore fails closed, naming why, until it can close the file; unconverted memory is
    untouched. A probe Git cannot answer refuses as well.
    """

    try:
        if not leaf_memory_converted(contract):
            return None
    except RuntimeError as error:
        return str(error)
    return (
        "the certified (prepared) closeout binds its memory commit to the curator-attested "
        "candidate, so it cannot set closed: true in the leaf's history file (MIK-R07 rule 7, "
        "MIK-R09 rule 3); on converted memory it refuses until it can -- close the leaf out through "
        "the worktree closeout commit, which closes the file and validates the exact tree"
    )


def prepared_closeout_lock(contract: WorktreeContract) -> str | None:
    """The cutover lock on the certified (prepared) closeout of unconverted memory (rule 6).

    ``None`` for converted memory (:func:`prepared_closeout_refusal` decides it, and names a probe
    Git cannot answer) and for a repository that holds no converted memory.
    """

    try:
        if leaf_memory_converted(contract):
            return None
    except RuntimeError:
        return None  # prepared_closeout_refusal refuses the unreadable probe by name
    return leaf_cutover_refusal(contract, "the certified (prepared) closeout")


def leaf_gate_refusal(
    contract: WorktreeContract, *, code_tree: str, memory_tree: str
) -> str | None:
    """The closeout validator's gate over a leaf's exact candidate; ``None`` when it passes."""

    applies, refusal = _leaf_gate_applies(contract, memory_tree)
    if refusal is not None:
        return refusal
    if not applies:
        return leaf_cutover_refusal(contract, "the closeout validator")
    port = _port()
    if port is None:
        return GATE_UNBOUND
    try:
        tip = parent_memory_tip(contract)
    except (RuntimeError, subprocess.SubprocessError) as error:  # failed or timed out: named
        return (
            "the mandatory invariant gate (MIK-R09) cannot read the parent memory line "
            f"({type(error).__name__}: {error})"
        )
    return port.leaf_refusal(
        contract, code_tree=code_tree, memory_tree=memory_tree, parent_memory_tip=tip
    )


def checkout_memory_converted(repository: Path) -> bool:
    """Whether a memory checkout's working tree or ``HEAD`` holds the layout marker.

    A direct landing's cheap probe before it captures anything: an unconverted checkout is read no
    further, so its landing is exactly what it was before this master.
    """

    if (repository / LAYOUT_MARKER_PATH).is_file():
        return True
    return converted_memory(repository, "HEAD")


def direct_gate_verdict(
    contract: WorktreeContract, *, code_commit: str, memory_tree: str
) -> DirectGateVerdict:
    """The gate at direct landing over its exact candidate (``applies`` is False when unconverted)."""

    repository = contract.memory_repo_path
    if repository is None:
        return DirectGateVerdict(False, None, None)
    try:
        if not converted_memory(repository, memory_tree, "HEAD"):
            return DirectGateVerdict(False, None, None)
    except GateProbeError as error:
        return DirectGateVerdict(True, None, str(error))
    port = _port()
    if port is None:
        return DirectGateVerdict(True, None, GATE_UNBOUND)
    return port.direct_verdict(contract, code_commit=code_commit, memory_tree=memory_tree)


def landing_gate_refusal(request: LandingGateRequest) -> str | None:
    """The gate over a landing (master, checkpoint or recorded); ``None`` when it passes.

    The validator runs through the route entry point every memory commit route uses
    (:func:`memory_commit_refusal`, MIK-R22 rule 8), over the landed memory commit against the
    request's bases and paired with its code commit; the gate port adds the leaf's closed history
    (record landing) and the master's net staleness (rule 4). Every refusal is named.
    """

    try:
        if not converted_memory(
            request.memory_repository, request.memory_commit, *request.memory_bases
        ):
            return cutover_lock_refusal(
                request.memory_repository,
                operation="the landing",
                line=f"the landed memory commit {request.memory_commit}",
            )
    except GateProbeError as error:
        return str(error)
    port = _port()
    if port is None:
        return GATE_UNBOUND
    # A leaf's landed commit is judged as its closeout judged it: only what an earlier recorded
    # closeout of the same leaf closed is frozen (L37 ruling B), which the request names.
    publication: bool | LeafPublication = bool(request.leaf_owner) and LeafPublication(
        request.memory_commit, tuple(request.memory_bases), frozen=request.frozen
    )
    refusals = [
        memory_commit_refusal(
            memory_repository=request.memory_repository,
            candidate_tree=request.memory_commit,
            bases=request.memory_bases,
            paired_code=PairedCode(repository=request.code_repository, commit=request.code_commit),
            leaf_publication=publication,
        ),
        port.landing_refusal(request),
    ]
    named = [refusal for refusal in refusals if refusal is not None]
    return "\n".join(named) if named else None


@dataclass(frozen=True)
class HistoryClosing:
    """One closing of a leaf's history file, and the bytes that were there (``None``: absent)."""

    path: Path
    previous: bytes | None

    def restore(self) -> None:
        """Undo the closing, when the route refuses before its commit."""

        if self.previous is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.write_bytes(self.previous)


def latest_owner_history(memory_root: Path, owner: str) -> Path:
    """The owner's latest history file in a memory checkout (its plain file when it has none).

    A leaf reopened after its closeout writes a later, attempt-qualified file (L37 ruling); the
    latest attempt is the one its closeout closes.
    """

    plain = memory_root / history_path(owner)
    attempts = {
        attempt: candidate
        for candidate in plain.parent.glob("*.json")
        if (attempt := owner_history_attempt(f"{_HISTORY_DIRECTORY}/{candidate.name}", owner))
    }
    return attempts[max(attempts)] if attempts else plain


def close_owner_history(memory_root: Path, owner: str) -> HistoryClosing:
    """Set ``closed: true`` in the owner's history file, creating it with no rows when absent.

    The rows are never rewritten: only the flag changes, and the file stays canonical. A file that
    does not parse is not touched here; the validator names it and refuses the commit. For a leaf
    reopened after its closeout, the file is its latest attempt; when that attempt is already closed
    (the reopened leaf wrote no row), nothing is written and its history stays the closed file.
    """

    path = latest_owner_history(memory_root, owner)
    previous = path.read_bytes() if path.is_file() else None
    if previous is None:
        document: object = {"schema": HISTORY_SCHEMA, "leaf": owner, "closed": True, "rows": []}
    else:
        try:
            document = parse_json(previous.decode("utf-8"))
        except (UnicodeDecodeError, CanonicalFormatError):
            return HistoryClosing(path, previous)
        if not isinstance(document, dict) or document.get("closed") is True:
            return HistoryClosing(path, previous)
        document = {**document, "closed": True}
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, canonical_text(document))
    return HistoryClosing(path, previous)


DIRECT_CLOSING_RECEIPTS = "direct-landing-history-closings"
DirectGenerationState = Literal["landed", "cancelled", "in-flight"]
_RECEIPT_SCHEMA = "direct-landing-history-closing/v1"


class ClosingReceiptError(RuntimeError):
    """A kept closing cannot be decided: its receipt is unreadable, or Git cannot say if it landed.

    Never passed over silently: the receipt holds the bytes a closed leaf history file is restored
    to, so the route refuses, naming the receipt and how to clear it (L09 review R2-5).
    """


def _receipts_directory(contract: WorktreeContract) -> Path:
    return contract.worktree_group / "reports" / DIRECT_CLOSING_RECEIPTS


def direct_closing_receipt(contract: WorktreeContract, fingerprint: str) -> Path:
    """Where the closing kept for generation ``fingerprint`` is recorded: one file per generation."""

    name = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
    return _receipts_directory(contract) / f"{name}.json"


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _closed_blob(repository: Path | None, target: Path) -> str | None:
    """The blob ID the memory line would record for ``target``, in the repository's own format.

    ``git hash-object`` hashes the file as ``git add`` would, with the repository's object format
    (SHA-1 or SHA-256) and its filters, so the landed-generation check compares like with like
    (L09 review R3-1). Git failing to answer raises :class:`ClosingReceiptError`.
    """

    if repository is None:
        return None
    try:
        result = run_git(
            repository,
            ["hash-object", "--", target.as_posix()],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (subprocess.SubprocessError, OSError) as error:
        result = None
        detail = f"{type(error).__name__}: {error}"
    else:
        detail = result.stderr.strip() or f"git exited {result.returncode}"
    if result is None or result.returncode != 0 or not result.stdout.strip():
        raise ClosingReceiptError(
            f"cannot record the closing of {target}: git hash-object failed ({detail}); "
            "nothing was admitted, retry once Git can read the memory checkout"
        )
    return result.stdout.strip()


def keep_direct_closing(
    contract: WorktreeContract, closing: HistoryClosing, fingerprint: str
) -> bool:
    """Keep ``closing`` for the direct-landing generation ``fingerprint`` until it is decided.

    Written before the journal creates the generation, so a call that ends in between leaves a
    receipt the next landing restores. Each generation has its own receipt, so a request for
    another generation never replaces it; an existing receipt of this generation is kept as it is
    (``False``: nothing was written).
    """

    path = direct_closing_receipt(contract, fingerprint)
    if path.exists():
        return False
    repository = contract.memory_repo_path
    previous = closing.previous
    document = {
        "schema": _RECEIPT_SCHEMA,
        "fingerprint": fingerprint,
        "repository": None if repository is None else str(repository),
        "path": closing.path.as_posix(),
        "previous": None if previous is None else base64.b64encode(previous).decode("ascii"),
        "closed": _sha256(closing.path.read_bytes()),
        "closedBlob": _closed_blob(repository, closing.path),
    }
    atomic_write_text(path, json.dumps(document, indent=2, sort_keys=True) + "\n")
    return True


def forget_direct_closing(contract: WorktreeContract, fingerprint: str) -> None:
    """Drop the receipt this call wrote for ``fingerprint``, once it was restored in process."""

    direct_closing_receipt(contract, fingerprint).unlink(missing_ok=True)


def settle_direct_closing(
    contract: WorktreeContract, *, current: str | None, state: DirectGenerationState
) -> list[Path]:
    """Decide every kept closing against the series' current generation; the files restored.

    ``current`` is the fingerprint of the series' current direct-landing generation (``None``: there
    is none) and ``state`` what became of it. Each receipt is decided on its own:

    * the current generation's is kept while it is ``in-flight``, forgotten once it ``landed``
      (its commit holds the closing), and restored once it is ``cancelled``;
    * any other generation's was never accepted by the journal (its call ended before the create),
      so it is restored -- unless the memory line's ``HEAD`` already holds the closed file, which
      means it landed.

    A restore happens only while the file is still exactly what the closing wrote; a later edit is
    never overwritten. An unreadable receipt raises :class:`ClosingReceiptError`.
    """

    restored: list[Path] = []
    for path, receipt in _receipts(contract):
        own = current is not None and receipt["fingerprint"] == current
        if own and state == "in-flight":
            continue
        landed = (own and state == "landed") or _landed(receipt)
        if not landed and _restore_receipt(receipt):
            restored.append(Path(receipt["path"]))
        path.unlink(missing_ok=True)
    return restored


def require_readable_closings(contract: WorktreeContract) -> None:
    """Raise :class:`ClosingReceiptError` when a kept closing's receipt cannot be read."""

    _receipts(contract)


def _receipts(contract: WorktreeContract) -> list[tuple[Path, dict[str, Any]]]:
    directory = _receipts_directory(contract)
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("*.json")):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ClosingReceiptError(
                _unreadable(path, f"{type(error).__name__}: {error}")
            ) from error
        if not _well_formed(receipt):
            raise ClosingReceiptError(_unreadable(path, "it is not a closing receipt"))
        found.append((path, receipt))
    return found


def _well_formed(receipt: object) -> bool:
    return (
        isinstance(receipt, dict)
        and receipt.get("schema") == _RECEIPT_SCHEMA
        and all(isinstance(receipt.get(key), str) for key in ("fingerprint", "path", "closed"))
    )


def _unreadable(path: Path, why: str) -> str:
    return (
        f"the direct-landing closing receipt {path} cannot be read ({why}). It records the bytes a "
        "leaf history file that a direct landing closed is restored to when that landing does not "
        "land, so nothing proceeds without it. To clear it: find the leaf history file under "
        "knowledge/history/ in the series memory checkout that is closed although no memory commit "
        "closed it, reopen it by hand (set closed: false) if its landing did not land, then delete "
        f"{path} and retry"
    )


def _landed(receipt: dict[str, Any]) -> bool:
    """Whether the memory line's ``HEAD`` holds the closed file (its generation landed)."""

    repository, blob = receipt.get("repository"), receipt.get("closedBlob")
    if not isinstance(repository, str) or not isinstance(blob, str):
        return False
    target = Path(receipt["path"])
    try:
        relative = target.relative_to(repository).as_posix()
        held = run_git(
            Path(repository),
            ["rev-parse", "--verify", "--quiet", f"HEAD:{relative}"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (ValueError, subprocess.SubprocessError, OSError) as error:
        raise ClosingReceiptError(
            f"cannot tell whether the closing of {target} landed ({type(error).__name__}: {error}); "
            "the kept closing is left in place, retry once Git can read the memory line"
        ) from error
    return held.returncode == 0 and held.stdout.strip() == blob


def _restore_receipt(receipt: dict[str, Any]) -> bool:
    target = Path(receipt["path"])
    current = target.read_bytes() if target.is_file() else None
    if current is None or _sha256(current) != receipt["closed"]:
        return False  # the file changed since the closing: it is never overwritten
    encoded = receipt.get("previous")
    HistoryClosing(target, None if encoded is None else base64.b64decode(str(encoded))).restore()
    return True
