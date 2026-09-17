"""Managed citation source-index namespace, leases, and terminal fencing.

The namespace is keyed by the exact contract/code/memory triple.  Its control
directory is persistent: one lock plus one bounded state record per triple, rewritten
across lifecycles rather than leaking one marker per lifecycle.  Ordinary operations
take only their leaf lock plus a brief admission-root lock, so a terminal fence for one
leaf never serializes its neighbours.

CAPACITY IS A SHARED WORKSPACE RESOURCE AND EVERY LEAF HAS TO BE ABLE TO GET ONE.
Two rules keep that true, and they are the two halves of one decision:

* the resource is bounded by BYTES, not by a slot count.  ``MANAGED_NAMESPACE_LIMIT``
  is only a hard ceiling on how many namespaces one operation may consider; the figure
  that decides admission is :data:`MANAGED_NAMESPACE_BYTES_LIMIT`, because what a
  namespace costs is disk and the same source tree can cost 40 MB or 130 MB.
* ADMISSION RECLAIMS, AND ONLY FROM THE DEAD.  Before refusing, admission reclaims
  namespaces that are provably terminal -- their owning contract is gone, or its
  lifecycle fence says terminal, or its contract parses and says it is terminal, or
  its contract parses and neither stated worktree exists any more.  The reclaimed set
  is drawn only from those positive verdicts, and every other occupant -- including one
  whose control record or whose CONTRACT cannot be read -- is left exactly where it is.
  THE GUARANTEE IS FAIL-CLOSED, NOT OMNISCIENT: it is "nothing is evicted that is not
  proved dead", not "no live leaf can be evicted under any circumstances".  A live
  occupant can still be reclaimed if it presents a positive terminal verdict, which is
  the intended trade: a contract that PARSES and declares a terminal cleanup, or whose
  worktrees are both gone, is the leaf's own statement about itself.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, Protocol, TypedDict
from uuid import uuid4

from agents_remember.errors import CitationCacheError
from agents_remember.kernel.atomic_write import atomic_replace, atomic_write_text
from agents_remember.worktrees.worktree_contract import ContractError, load_contract

MANAGED_NAMESPACE_LIMIT = 4
"""Hard ceiling on namespaces an admission pass may examine, so one call cannot walk a tree."""

MANAGED_NAMESPACE_BYTES_LIMIT = 512 * 1024 * 1024
"""The figure admission actually decides on: total bytes the managed cache may hold.

Measured 2026-09-17 on this coordination root: one fully warm namespace for
``agents-remember`` is 122-130 MB, and four occupants held 375 MB. The slot ceiling of
four was therefore a byte ceiling of roughly half a gigabyte expressed as a count that
happened to fit one repository. Two repositories whose source trees differ by 3x would
have had the same four slots and a 1.5 GB cache; this is the honest unit.
"""

LOCK_TIMEOUT_SECONDS = 30.0
MANAGED_ROOT_RELATIVE = Path("temp/citation-source-index/managed")
ROOT_LOCK_NAME = "managed.lock"
CONTROL_DIR_NAME = ".control"
CONTROL_STATE_SCHEMA = 1
MAX_CONTROL_STATE_BYTES = 4096
TERMINAL_CLEANUP_STATES = frozenset({"completed", "abandoned"})


@dataclass(frozen=True)
class ManagedCacheAuthority:
    coordination_root: Path
    contract_path: Path
    code_root: Path
    memory_root: Path
    namespace_id: str
    lifecycle_id: str | None = None

    @property
    def managed_root(self) -> Path:
        return self.coordination_root / MANAGED_ROOT_RELATIVE

    @property
    def namespace(self) -> Path:
        return self.managed_root / self.namespace_id

    @property
    def control_dir(self) -> Path:
        return self.managed_root / CONTROL_DIR_NAME / self.namespace_id

    @property
    def control_lock(self) -> Path:
        return self.control_dir / "lease.lock"

    @property
    def control_state(self) -> Path:
        return self.control_dir / "state.json"

    def validate_roots(self, code_root: Path, memory_root: Path) -> None:
        code = code_root.resolve()
        memory = memory_root.resolve()
        if code != self.code_root or memory != self.memory_root:
            raise CitationCacheError(
                "managed citation cache authority belongs to different code/memory roots: "
                f"authority=({self.code_root}, {self.memory_root}), request=({code}, {memory})"
            )


@dataclass(frozen=True)
class CacheControlState:
    lifecycle_id: str
    phase: Literal["active", "terminal"]
    outcome: str = ""

    def to_json(self, authority: ManagedCacheAuthority) -> str:
        return (
            json.dumps(
                {
                    "schema": CONTROL_STATE_SCHEMA,
                    "namespace": authority.namespace_id,
                    "contract": authority.contract_path.as_posix(),
                    "codeRoot": authority.code_root.as_posix(),
                    "memoryRoot": authority.memory_root.as_posix(),
                    "lifecycleId": self.lifecycle_id,
                    "phase": self.phase,
                    "outcome": self.outcome,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )


class ContractCacheFacts(Protocol):
    @property
    def coordination_root(self) -> Path: ...

    @property
    def contract_path(self) -> Path: ...

    @property
    def code_worktree(self) -> Path: ...

    @property
    def memory_worktree(self) -> Path | None: ...

    @property
    def lifecycle_id(self) -> str: ...

    @property
    def cleanup(self) -> str: ...


def managed_cache_authority(
    *,
    coordination_root: Path,
    contract_path: Path,
    code_root: Path,
    memory_root: Path,
    lifecycle_id: str | None = None,
) -> ManagedCacheAuthority:
    authority = _resolved_authority(
        coordination_root=coordination_root,
        contract_path=contract_path,
        code_root=code_root,
        memory_root=memory_root,
        lifecycle_id=lifecycle_id,
    )
    for label, root in (("code", authority.code_root), ("memory", authority.memory_root)):
        if not root.is_dir():
            raise CitationCacheError(f"citation cache {label} root does not exist: {root}")
    return authority


def _resolved_authority(
    *,
    coordination_root: Path,
    contract_path: Path,
    code_root: Path,
    memory_root: Path,
    lifecycle_id: str | None,
) -> ManagedCacheAuthority:
    coordination = coordination_root.resolve()
    contract = contract_path.resolve()
    code = code_root.resolve()
    memory = memory_root.resolve()
    if not contract.is_relative_to(coordination):
        raise CitationCacheError(
            f"citation cache contract {contract} is outside coordination root {coordination}"
        )
    if code == memory:
        raise CitationCacheError("citation cache code and memory roots must be distinct")
    managed = (coordination / MANAGED_ROOT_RELATIVE).resolve()
    if _under(managed, code) or _under(managed, memory):
        raise CitationCacheError(
            f"managed citation cache {managed} must stay outside code root {code} and "
            f"memory root {memory}"
        )
    digest = hashlib.sha256(f"{contract}\0{code}\0{memory}".encode()).hexdigest()
    return ManagedCacheAuthority(
        coordination,
        contract,
        code,
        memory,
        digest,
        lifecycle_id=lifecycle_id,
    )


def contract_cache_authority(contract: ContractCacheFacts) -> ManagedCacheAuthority | None:
    if contract.memory_worktree is None:
        return None
    return _resolved_authority(
        coordination_root=contract.coordination_root,
        contract_path=contract.contract_path,
        code_root=contract.code_worktree,
        memory_root=contract.memory_worktree,
        lifecycle_id=contract.lifecycle_id,
    )


def open_shared_namespace(
    authority: ManagedCacheAuthority,
    *,
    create: bool,
) -> BinaryIO:
    """Open one persistent same-leaf lease and perform brief root-locked admission."""
    root = authority.managed_root
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Reclaim BEFORE taking this leaf's own exclusive lease. The reclamation pass takes each
    # occupant's control lock and then the root lock -- the same order this function uses --
    # so running it after the flock below would hold one leaf's lock while taking others'.
    if create:
        admit_managed_namespace(authority)
    handle = _control_handle(authority)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        state = _read_control_state(authority)
        transition = _acquisition_transition(authority, state)
        with _root_lock(root, exclusive=True):
            namespace = authority.namespace
            if not namespace.exists():
                if not create:
                    raise CitationCacheError(
                        f"managed citation cache namespace {authority.namespace_id} is not published"
                    )
                _refuse_when_nothing_can_be_admitted(root)
                namespace.mkdir(mode=0o700)
            elif not namespace.is_dir() or namespace.is_symlink():
                raise CitationCacheError(
                    f"managed citation cache namespace is not a directory: {namespace}"
                )
        if transition is not None:
            _write_control_state(authority, transition)
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        return handle
    except BaseException:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        raise


def _refuse_when_nothing_can_be_admitted(root: Path) -> None:
    """Refuse a new namespace out of room, and say what the ROOM is and who owns it.

    ``admit_managed_namespace`` has already run, so every occupant that could be proved dead
    is gone: what is left is live (or unprovable), and no leaf may take another master's live
    namespace. The refusal therefore states the measured resource and points at the OWNERS --
    it never instructs the blocked leaf to go and clean up somebody else's enclosure, which is
    the sentence the recorded four-occupant block printed and the one thing the blocked leaf
    cannot do.
    """
    occupants = _namespace_ids(root)
    footprint = _managed_bytes(root)
    over_count = len(occupants) >= MANAGED_NAMESPACE_LIMIT
    over_bytes = footprint > MANAGED_NAMESPACE_BYTES_LIMIT
    if not over_count and not over_bytes:
        return
    owners = [
        _occupant_description(root / CONTROL_DIR_NAME / namespace_id / "state.json")
        for namespace_id in occupants
    ]
    raise CitationCacheError(
        "managed citation cache has no reclaimable room for a new namespace: "
        f"{len(occupants)} namespace(s) holding {footprint} bytes "
        f"(count ceiling {MANAGED_NAMESPACE_LIMIT}, byte ceiling {MANAGED_NAMESPACE_BYTES_LIMIT}). "
        f"Every occupant is live or cannot be proved dead, and none was evicted. Owners: "
        f"{'; '.join(owners) or '<none>'}. This is a workspace-wide resource: it is released "
        "automatically when each of those leaves reaches its terminal cleanup, so retry after "
        "their closeouts land rather than deleting another leaf's namespace."
    )


def _occupant_description(state_path: Path) -> str:
    record = _control_record(state_path) if state_path.is_file() else None
    if record is None:
        return "<unreadable control record>"
    contract = record.get("contract")
    lifecycle = record.get("lifecycleId")
    return f"contract={contract} lifecycle={lifecycle} phase={record.get('phase')}"


def admit_managed_namespace(authority: ManagedCacheAuthority) -> NamespaceAdmission:
    """Reclaim what is provably dead and report the cache's byte footprint.

    Runs OUTSIDE the root lock and before ``open_shared_namespace``, because reclaiming has
    to take each occupant's own lease lock and that lock is taken BEFORE the root lock
    everywhere else in this module. A shorter critical section that also has a consistent
    order is worth the second pass over the root.

    Two reclamations, in this order:

    1. the caller's own namespace when its contract is already terminal. A leaf that
       reaches its terminal cleanup normally releases its namespace at that boundary; this
       catches the one whose owner could not (an interrupted cleanup, or a contract finished
       by a route that never held the guard). It is the caller's OWN namespace, so reclaiming
       it cannot take anything another leaf is using.
    2. other occupants that are provably terminal, when the cache is over its byte bound or
       at its count ceiling. Never on an unproved occupant: see :func:`_prove_terminal` for
       the exact verdicts that license eviction and the fail-closed rule for everything else.
    """
    root = authority.managed_root
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    result: NamespaceAdmission = {
        "bytesBefore": _managed_bytes(root),
        "bytesAfter": 0,
        "bytesLimit": MANAGED_NAMESPACE_BYTES_LIMIT,
        "namespacesBefore": _namespace_ids(root),
        "namespacesAfter": [],
        "reclaimed": [],
    }
    reclaimed: list[str] = []
    own = _own_terminal_authority(authority)
    if own is not None and authority.namespace.exists() and _reclaim_under_lease(own):
        reclaimed.append(authority.namespace_id)
    if _over_bound(root):
        for candidate in _eviction_candidates(root, keep=authority.namespace_id):
            if _reclaim_under_lease(candidate):
                reclaimed.append(candidate.namespace_id)
            if not _over_bound(root):
                break
    result["reclaimed"] = reclaimed
    result["bytesAfter"] = _managed_bytes(root)
    result["namespacesAfter"] = _namespace_ids(root)
    return result


def _managed_bytes(root: Path) -> int:
    """Bytes the occupant namespaces occupy -- the same population :func:`_occupant_paths` names.

    This used to walk the WHOLE managed root, which also summed ``.control/<id>/state.json`` and
    the lock files. Those are per-triple bookkeeping that is never pruned, so the figure drifted
    upward for reasons no namespace caused -- 131 control directories already sit in the live
    root -- and it contradicted this function's own docstring, which said control state is not
    counted. Count and bytes now come from ONE enumeration, so the two can never describe
    different populations, and a byte bound means what it says.
    """
    total = 0
    for occupant in _occupant_paths(root):
        for path in occupant.rglob("*"):
            if not path.is_file():
                continue
            try:
                total += path.stat().st_size
            except OSError:  # pragma: no cover - a file removed under the walk
                continue
    return total


def _over_bound(root: Path) -> bool:
    return len(_namespace_ids(root)) >= MANAGED_NAMESPACE_LIMIT or (
        _managed_bytes(root) > MANAGED_NAMESPACE_BYTES_LIMIT
    )


class NamespaceAdmission(TypedDict):
    """What one admission pass measured and reclaimed, as a typed record.

    A plain ``dict[str, object]`` return made every caller narrow by hand -- and one caller
    did not, which is what turned a stricter annotation on the reclamation report into a
    pyright `reportArgumentType` rather than a compile-time fact anyone could act on.
    """

    bytesBefore: int
    bytesAfter: int
    bytesLimit: int
    namespacesBefore: list[str]
    namespacesAfter: list[str]
    reclaimed: list[str]


@dataclass(frozen=True)
class _TerminalOccupant:
    """One published namespace with positive evidence that no live operation owns it."""

    namespace_id: str
    control_dir: Path
    evidence: str

    @property
    def namespace(self) -> Path:
        return self.control_dir.parent.parent / self.namespace_id


def _own_terminal_authority(authority: ManagedCacheAuthority) -> ManagedCacheAuthority | None:
    """The caller's own authority, but only when the contract it names proves terminal."""
    return authority if _prove_terminal(authority.control_state) is not None else None


def _eviction_candidates(root: Path, *, keep: str) -> list[_TerminalOccupant]:
    """EVERY provably-terminal occupant of ``root``, oldest evidence first.

    THE WHOLE ROOT IS EXAMINED. An earlier version truncated the enumeration at
    :data:`MANAGED_NAMESPACE_LIMIT`, which made the reclamation capacity-only under exactly the
    condition it was written for: a provably-terminal occupant whose id sorted beyond the first
    ``MANAGED_NAMESPACE_LIMIT`` names was never examined, so admission still refused at
    capacity with a dead leaf sitting in the root. Recorded as D45 of `260915-CAPS-L21` from
    observation 4 of the round-2 fix verification. What counts as proved-dead is unchanged;
    only WHICH occupants get examined widened.

    Truncation was never a real bound anyway: :func:`_namespace_ids` already performs the one
    ``iterdir`` over the root, so the slice saved no directory walk -- it only hid occupants.
    The per-candidate cost is one control-record read plus, for a terminal one, a lease probe,
    and the population is self-limiting: admission reclaims whenever the root is over its
    bound, and a leaf that finishes normally releases its namespace at its terminal boundary.
    Ordering stays by the control record's mtime, so a long-dead occupant is reclaimed before a
    freshly finished one.
    """
    occupants: list[_TerminalOccupant] = []
    for namespace_id in _namespace_ids(root):
        if namespace_id == keep:
            continue
        control_dir = root / CONTROL_DIR_NAME / namespace_id
        evidence = _prove_terminal(control_dir / "state.json")
        if evidence is None:
            continue
        occupants.append(_TerminalOccupant(namespace_id, control_dir, evidence))
    occupants.sort(key=lambda one: _mtime(one.control_dir / "state.json"))
    return occupants


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:  # pragma: no cover - a record removed under the walk
        return 0.0


def _prove_terminal(state_path: Path) -> str | None:
    """Why this namespace's owner cannot be running, or None when that cannot be proved.

    FOUR POSITIVE FACTS, and nothing inferred from silence:

    ``terminal-fence``
        the control record says ``phase: terminal``. The exact-leaf fence was published by a
        completed or abandoned lifecycle and every later acquisition is refused for it.
    ``contract-gone``
        the contract file the record names no longer exists. The record carries the contract
        path, so this is checkable without the contract: an operation addresses a leaf
        THROUGH its contract, and there is nothing left to address.
    ``contract-terminal``
        the contract still parses and its own ``cleanup`` cell is a terminal value.
    ``worktrees-gone``
        the contract still parses but neither of its worktrees exists. This is the recorded
        stuck shape: the enclosure locator was already removed while the control record kept
        ``phase: active``, so no operation can start and none can be in flight.

    An unreadable or foreign record returns None and the occupant is left where it is. That
    is the "never a live leaf" guarantee: eviction is drawn only from positive terminal
    evidence, so an occupant nothing can be proved about is never the one reclaimed.
    """
    if not state_path.is_file():
        return None
    record = _control_record(state_path)
    if record is None:
        return None
    if record.get("phase") == "terminal":
        return "terminal-fence"
    contract = record.get("contract")
    if not isinstance(contract, str) or not contract:
        return None
    if not Path(contract).exists():
        return "contract-gone"
    return _terminal_contract_evidence(Path(contract))


def _control_record(state_path: Path) -> dict[str, object] | None:
    """The raw control record, or None when it is absent, unreadable, or not an object."""
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _terminal_contract_evidence(contract_path: Path) -> str | None:
    """Whether the contract itself proves its leaf cannot still be running.

    A CONTRACT THAT CANNOT BE READ PROVES NOTHING AND EVICTS NOTHING. An earlier version of
    this function returned a ``contract-unreadable`` verdict here, treating bytes that fail to
    parse as equivalent to bytes that are absent -- which reclaimed a LIVE occupant whose
    contract happened to be corrupt, mid-write, or written under a schema this reader does not
    know. Failure to read is not evidence of a terminal lifecycle: it is the ABSENCE of
    evidence, and an occupant nothing can be proved about is left exactly where it is. Only a
    contract that parses AND says it is terminal, or whose stated worktrees are both gone,
    licenses eviction.
    """
    try:
        current = load_contract(contract_path)
    except (ContractError, OSError, UnicodeError, ValueError):
        return None
    if current.cleanup in TERMINAL_CLEANUP_STATES:
        return "contract-terminal"
    memory_gone = current.memory_worktree is None or not current.memory_worktree.exists()
    if memory_gone and not current.code_worktree.exists():
        return "worktrees-gone"
    return None


def _reclaim_under_lease(occupant: _TerminalOccupant | ManagedCacheAuthority) -> bool:
    """Remove one namespace while holding its own lease lock. False when the lease is held.

    The lock is the second, independent half of the liveness proof: a terminal record and a
    held lease cannot both be true, so a namespace whose lease answers is left alone whatever
    its record says. The removal itself is the same tombstone-then-delete sequence
    :func:`reclaim_managed_namespace` performs, under the same root lock.
    """
    control_lock = occupant.control_dir / "lease.lock"
    namespace = occupant.namespace
    control_lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with control_lock.open("a+b") as handle:
        if not _try_exclusive(handle):
            return False
        try:
            if not namespace.exists():
                return False
            tombstone = namespace.parent / (
                f".{namespace.name}.reclaim.{os.getpid()}.{uuid4().hex}"
            )
            with _root_lock(namespace.parent, exclusive=True):
                atomic_replace(namespace, tombstone)
            _remove_tree(tombstone)
            return True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def open_index_lock(
    authority: ManagedCacheAuthority | None,
    slot: Path,
    lock: Path,
    *,
    create: bool,
) -> BinaryIO:
    if authority is not None:
        return open_shared_namespace(authority, create=create)
    if create:
        slot.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = lock.open("a+b" if create else "r+b")
    fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
    return handle


def lock_exclusive(authority: ManagedCacheAuthority | None, handle: BinaryIO) -> None:
    if authority is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _validate_active_state(authority, _read_control_state(authority))
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


@dataclass
class TerminalNamespaceGuard:
    """One exact-leaf terminal reservation held through mutation and publication."""

    authority: ManagedCacheAuthority | None
    handle: BinaryIO | None
    previous_state: CacheControlState | None
    tombstone: Path | None = None
    completed: bool = False

    def preview(self) -> dict[str, object]:
        if self.authority is None:
            return {"removed": False, "reason": "no-external-memory-worktree"}
        return {
            "namespace": self.authority.namespace_id,
            "path": self.authority.namespace.as_posix(),
            "removed": False,
            **({"would_remove": True} if self.authority.namespace.exists() else {}),
            **({"reason": "already-absent"} if not self.authority.namespace.exists() else {}),
        }

    def complete(
        self,
        *,
        outcome: Literal["completed", "abandoned"],
        publish: Callable[[], None],
        rollback_publish: Callable[[], None],
    ) -> dict[str, object]:
        if self.completed:
            raise CitationCacheError("terminal citation cache guard was already completed")
        if self.authority is None:
            return self._complete_without_namespace(publish, rollback_publish)
        authority = self.authority
        result: dict[str, object] = {
            "namespace": authority.namespace_id,
            "path": authority.namespace.as_posix(),
            "removed": False,
        }
        self._quarantine_live_namespace(authority, result)
        try:
            retired = self._commit_terminal_boundary(
                authority,
                outcome=outcome,
                publish=publish,
                result=result,
            )
        except BaseException as error:
            self._rollback_precommit(authority, rollback_publish, error)
            raise
        # The terminal contract, persistent fence state, and non-live retired identity are
        # now one committed fact. Recursive cleanup is deliberately post-commit: a partial
        # rmtree can damage only retired garbage and can never be rolled back into service.
        self.completed = True
        self._cleanup_retired_namespace(retired, result)
        self._confirm_lease_released(authority, result)
        return result

    def _confirm_lease_released(
        self,
        authority: ManagedCacheAuthority,
        result: dict[str, object],
    ) -> None:
        """Confirm the reservation is gone, and name the attempt when it is not.

        ``_cleanup_retired_namespace`` normally leaves nothing behind, so this is a no-op on
        the ordinary path. It is NOT a second reclaim: this call still holds ``authority``'s
        own exclusive lease through :func:`terminal_namespace_guard`, so reclaiming here would
        take a lock this process already holds. What it does is make a FAILED unlink visible --
        a retired tree that could not be removed would otherwise keep occupying the cache with
        nothing in the payload saying so, which is the silent-full state this module grew the
        reclamation pass to end.
        """
        still_there = authority.namespace.exists() or any(
            path.name.startswith(f".{authority.namespace_id}.")
            for path in authority.managed_root.iterdir()
        )
        if still_there:
            result["lease_still_reserved"] = True
            result["lease_release_owner"] = "the next admission pass will reclaim this namespace"

    def _complete_without_namespace(
        self,
        publish: Callable[[], None],
        rollback_publish: Callable[[], None],
    ) -> dict[str, object]:
        try:
            publish()
        except BaseException as error:
            try:
                rollback_publish()
            except BaseException as rollback_error:
                raise CitationCacheError(
                    "terminal contract publication and rollback both failed: "
                    f"publish={error}; rollback={rollback_error}"
                ) from rollback_error
            raise
        self.completed = True
        return {"removed": False, "reason": "no-external-memory-worktree"}

    def _quarantine_live_namespace(
        self,
        authority: ManagedCacheAuthority,
        result: dict[str, object],
    ) -> None:
        if not authority.namespace.exists():
            result["reason"] = "already-absent"
            return
        self.tombstone = authority.managed_root / (
            f".{authority.namespace_id}.terminal.{os.getpid()}.{uuid4().hex}"
        )
        with _root_lock(authority.managed_root, exclusive=True):
            atomic_replace(authority.namespace, self.tombstone)

    def _commit_terminal_boundary(
        self,
        authority: ManagedCacheAuthority,
        *,
        outcome: Literal["completed", "abandoned"],
        publish: Callable[[], None],
        result: dict[str, object],
    ) -> Path | None:
        publish()
        _write_control_state(
            authority,
            CacheControlState(_required_lifecycle(authority), "terminal", outcome),
        )
        if self.tombstone is None:
            return None
        retired = authority.managed_root / (
            f".{authority.namespace_id}.retired.{os.getpid()}.{uuid4().hex}"
        )
        self._move_tombstone_to_retired(authority, retired, result)
        self.tombstone = None
        result["removed"] = True
        return retired

    def _move_tombstone_to_retired(
        self,
        authority: ManagedCacheAuthority,
        retired: Path,
        result: dict[str, object],
    ) -> None:
        assert self.tombstone is not None
        with _root_lock(authority.managed_root, exclusive=True):
            try:
                atomic_replace(self.tombstone, retired)
            except BaseException as error:
                if self.tombstone.exists() or not retired.exists():
                    raise
                result["retirement_commit"] = {
                    "durable": False,
                    "reason": str(error),
                }

    def _rollback_precommit(
        self,
        authority: ManagedCacheAuthority,
        rollback_publish: Callable[[], None],
        publication_error: BaseException,
    ) -> None:
        rollback_failures: list[BaseException] = []
        actions: tuple[Callable[[], None], ...] = (
            rollback_publish,
            lambda: _restore_control_state(authority, self.previous_state),
            self._restore_namespace,
        )
        for action in actions:
            try:
                action()
            except BaseException as rollback_error:
                rollback_failures.append(rollback_error)
        if rollback_failures:
            detail = "; ".join(str(failure) for failure in rollback_failures)
            raise CitationCacheError(
                "terminal pre-commit rollback failed after publication error "
                f"{publication_error}: {detail}"
            ) from rollback_failures[0]

    @staticmethod
    def _cleanup_retired_namespace(retired: Path | None, result: dict[str, object]) -> None:
        if retired is None:
            return
        try:
            _remove_tree(retired)
        except BaseException as error:
            result["retired_cleanup"] = {
                "path": retired.as_posix(),
                "removed": False,
                "reason": str(error),
            }
        else:
            result["retired_cleanup"] = {
                "path": retired.as_posix(),
                "removed": True,
            }

    def _restore_namespace(self) -> None:
        if self.authority is None or self.tombstone is None or not self.tombstone.exists():
            return
        with _root_lock(self.authority.managed_root, exclusive=True):
            if self.authority.namespace.exists():
                raise CitationCacheError(
                    "cannot restore terminal citation cache quarantine because the exact "
                    f"namespace was recreated: {self.authority.namespace}"
                )
            atomic_replace(self.tombstone, self.authority.namespace)
        self.tombstone = None


@contextmanager
def terminal_namespace_guard(
    contract: ContractCacheFacts,
    *,
    requested_contract_path: Path,
) -> Iterator[TerminalNamespaceGuard]:
    """Reserve one exact contract namespace before any terminal mutation."""
    requested = requested_contract_path.resolve()
    if requested != contract.contract_path.resolve():
        raise CitationCacheError(
            "terminal citation cache authority does not match the requested contract: "
            f"requested={requested}, contract={contract.contract_path.resolve()}"
        )
    authority = contract_cache_authority(contract)
    if authority is None:
        yield TerminalNamespaceGuard(None, None, None)
        return
    _required_lifecycle(authority)
    authority.control_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = _control_handle(authority)
    if not _exclusive_before_deadline(handle):
        handle.close()
        raise CitationCacheError(
            f"managed citation cache namespace {authority.namespace_id} has a live lease"
        )
    guard = TerminalNamespaceGuard(authority, handle, _read_control_state(authority))
    try:
        _validate_terminal_contract(authority, guard.previous_state)
        yield guard
    finally:
        if guard.tombstone is not None:
            guard._restore_namespace()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def reclaim_managed_namespace(
    authority: ManagedCacheAuthority,
    *,
    dry_run: bool,
) -> dict[str, object]:
    """Compatibility reclamation for non-contract direct callers.

    Lifecycle commands use :func:`terminal_namespace_guard`; this helper deliberately
    remains a one-shot exact deletion for direct callers that have no contract lifecycle.
    """
    handle = _control_handle(authority)
    if not _exclusive_before_deadline(handle):
        handle.close()
        return _lease_timeout(authority)
    try:
        if not authority.namespace.exists():
            return _absent_result(authority)
        if dry_run:
            return {**_base_result(authority), "would_remove": True}
        tombstone = authority.managed_root / (
            f".{authority.namespace_id}.reclaim.{os.getpid()}.{uuid4().hex}"
        )
        with _root_lock(authority.managed_root, exclusive=True):
            atomic_replace(authority.namespace, tombstone)
            _remove_tree(tombstone)
        return {**_base_result(authority), "removed": True}
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _acquisition_transition(
    authority: ManagedCacheAuthority,
    state: CacheControlState | None,
) -> CacheControlState | None:
    if authority.lifecycle_id is None:
        if state is not None:
            raise CitationCacheError(
                "unbound managed citation cache authority cannot cross a lifecycle fence"
            )
        return None
    if not authority.lifecycle_id:
        if state is not None:
            raise CitationCacheError(
                "legacy managed citation cache authority cannot cross a lifecycle fence"
            )
        _require_current_legacy_active_contract(authority)
        return None
    lifecycle = _required_lifecycle(authority)
    if state is None:
        _require_current_active_contract(authority)
        return CacheControlState(lifecycle, "active")
    if state.phase == "active":
        if state.lifecycle_id != lifecycle:
            raise CitationCacheError(
                "managed citation cache authority is stale for the active lifecycle: "
                f"authority={lifecycle}, active={state.lifecycle_id}"
            )
        return None
    if state.lifecycle_id == lifecycle:
        raise CitationCacheError(
            f"managed citation cache lifecycle {lifecycle} is terminal ({state.outcome})"
        )
    _require_current_active_contract(authority)
    return CacheControlState(lifecycle, "active")


def _validate_active_state(
    authority: ManagedCacheAuthority,
    state: CacheControlState | None,
) -> None:
    if authority.lifecycle_id is None and state is None:
        return
    if authority.lifecycle_id == "" and state is None:
        _require_current_legacy_active_contract(authority)
        return
    lifecycle = _required_lifecycle(authority)
    if state is None or state.phase != "active" or state.lifecycle_id != lifecycle:
        raise CitationCacheError(
            f"managed citation cache lifecycle {lifecycle} lost active authority"
        )


def _validate_terminal_contract(
    authority: ManagedCacheAuthority,
    state: CacheControlState | None,
) -> None:
    current = _current_contract(authority)
    lifecycle = _required_lifecycle(authority)
    if current.lifecycle_id != lifecycle:
        raise CitationCacheError(
            "terminal citation cache authority is stale: "
            f"authority={lifecycle}, current={current.lifecycle_id or '<empty>'}"
        )
    if current.cleanup in TERMINAL_CLEANUP_STATES:
        if state is None or state.phase != "terminal" or state.lifecycle_id != lifecycle:
            raise CitationCacheError(
                "terminal contract has no matching persistent citation cache fence state"
            )
        return
    if state is not None and state.phase == "active" and state.lifecycle_id != lifecycle:
        raise CitationCacheError(
            "terminal citation cache authority does not own the active lifecycle: "
            f"authority={lifecycle}, active={state.lifecycle_id}"
        )


def _require_current_active_contract(authority: ManagedCacheAuthority) -> None:
    current = _current_contract(authority)
    lifecycle = _required_lifecycle(authority)
    if current.lifecycle_id != lifecycle:
        raise CitationCacheError(
            "managed citation cache authority is stale for the current contract: "
            f"authority={lifecycle}, current={current.lifecycle_id or '<empty>'}"
        )
    if current.cleanup in TERMINAL_CLEANUP_STATES:
        raise CitationCacheError(
            f"managed citation cache lifecycle {lifecycle} is terminal ({current.cleanup})"
        )


def _require_current_legacy_active_contract(authority: ManagedCacheAuthority) -> None:
    current = _current_contract(authority)
    if current.lifecycle_id:
        raise CitationCacheError(
            "legacy managed citation cache authority is stale for a lifecycle-bound contract"
        )
    if current.cleanup in TERMINAL_CLEANUP_STATES:
        raise CitationCacheError(
            "legacy managed citation cache authority cannot open a terminal contract"
        )


def _current_contract(authority: ManagedCacheAuthority) -> ContractCacheFacts:
    current = load_contract(authority.contract_path)
    current_authority = contract_cache_authority(current)
    if current_authority is None or current_authority.namespace_id != authority.namespace_id:
        raise CitationCacheError(
            "current contract does not authorize this citation cache contract/root triple"
        )
    return current


def _required_lifecycle(authority: ManagedCacheAuthority) -> str:
    if not authority.lifecycle_id:
        raise CitationCacheError(
            "contract-scoped citation cache terminal authority requires a nonempty lifecycle id"
        )
    return authority.lifecycle_id


def _control_handle(authority: ManagedCacheAuthority) -> BinaryIO:
    authority.control_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return authority.control_lock.open("a+b")


def _read_control_state(authority: ManagedCacheAuthority) -> CacheControlState | None:
    path = authority.control_state
    if not path.exists():
        return None
    try:
        if path.stat().st_size > MAX_CONTROL_STATE_BYTES:
            raise ValueError("record exceeds its fixed size bound")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("record is not an object")
        expected = {
            "schema": CONTROL_STATE_SCHEMA,
            "namespace": authority.namespace_id,
            "contract": authority.contract_path.as_posix(),
            "codeRoot": authority.code_root.as_posix(),
            "memoryRoot": authority.memory_root.as_posix(),
        }
        for key, value in expected.items():
            if raw.get(key) != value:
                raise ValueError(f"{key} does not match authority")
        lifecycle = raw.get("lifecycleId")
        phase = raw.get("phase")
        outcome = raw.get("outcome", "")
        if not isinstance(lifecycle, str) or not lifecycle:
            raise ValueError("lifecycleId is empty")
        if phase not in {"active", "terminal"}:
            raise ValueError("phase is invalid")
        if not isinstance(outcome, str):
            raise ValueError("outcome is invalid")
        return CacheControlState(lifecycle, phase, outcome)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise CitationCacheError(
            f"managed citation cache control state is invalid at {path}: {error}"
        ) from error


def _write_control_state(
    authority: ManagedCacheAuthority,
    state: CacheControlState,
) -> None:
    payload = state.to_json(authority)
    if len(payload.encode()) > MAX_CONTROL_STATE_BYTES:
        raise CitationCacheError("managed citation cache control state exceeds its fixed bound")
    atomic_write_text(authority.control_state, payload)


def _restore_control_state(
    authority: ManagedCacheAuthority,
    state: CacheControlState | None,
) -> None:
    if state is None:
        authority.control_state.unlink(missing_ok=True)
    else:
        _write_control_state(authority, state)


def _exclusive_before_deadline(handle: BinaryIO) -> bool:
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        if _try_exclusive(handle):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _occupant_paths(root: Path) -> list[Path]:
    """Every directory the managed root counts as an occupant, in sorted order.

    ONE enumeration for the count, the byte total, and the eviction scan. A published namespace
    counts; so does the dot-prefixed ``.terminal.`` tombstone of an id, because the reservation
    is not released until that rename completes and a leaf that finished must not hold a slot
    for good. ``.retired.`` leftovers and the ``.control`` tree are deliberately NOT occupants:
    the first is post-commit garbage the same operation deletes, the second is per-triple
    bookkeeping that is never pruned.
    """
    occupants: dict[str, Path] = {}
    for path in root.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        if not path.name.startswith("."):
            occupants.setdefault(path.name, path)
            continue
        namespace, separator, _suffix = path.name[1:].partition(".terminal.")
        if (
            separator
            and len(namespace) == 64
            and all(character in "0123456789abcdef" for character in namespace)
        ):
            occupants.setdefault(namespace, path)
    return [occupants[key] for key in sorted(occupants)]


def _namespace_ids(root: Path) -> list[str]:
    """The occupant ids, derived from the one enumeration :func:`_occupant_paths` performs."""
    return sorted(
        path.name if not path.name.startswith(".") else path.name[1:].partition(".terminal.")[0]
        for path in _occupant_paths(root)
    )


@contextmanager
def _root_lock(root: Path, *, exclusive: bool) -> Iterator[BinaryIO]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = (root / ROOT_LOCK_NAME).open("a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield handle
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _try_exclusive(handle: BinaryIO) -> bool:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _base_result(authority: ManagedCacheAuthority) -> dict[str, object]:
    return {
        "namespace": authority.namespace_id,
        "path": authority.namespace.as_posix(),
        "removed": False,
    }


def _absent_result(authority: ManagedCacheAuthority) -> dict[str, object]:
    return {**_base_result(authority), "reason": "already-absent"}


def _lease_timeout(authority: ManagedCacheAuthority) -> dict[str, object]:
    return {**_base_result(authority), "reason": "live-lease-timeout"}


def _remove_tree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError as error:
        raise CitationCacheError(
            f"cannot reclaim managed citation cache {path}: {error}"
        ) from error


def _under(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    return resolved == root or root in resolved.parents
