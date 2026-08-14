"""Managed citation source-index namespace, leases, and terminal fencing.

The namespace is keyed by the exact contract/code/memory triple.  Its control
directory is persistent: one lock plus one bounded state record per triple, rewritten
across lifecycles rather than leaking one marker per lifecycle.  Ordinary operations
take only their leaf lock plus a brief admission-root lock, so a terminal fence for one
leaf never serializes its neighbours.
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
from typing import BinaryIO, Literal, Protocol
from uuid import uuid4

from agents_remember.errors import CitationCacheError
from agents_remember.kernel.atomic_write import atomic_replace, atomic_write_text
from agents_remember.worktrees.worktree_contract import load_contract

MANAGED_NAMESPACE_LIMIT = 4
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
                occupants = _namespace_ids(root)
                if len(occupants) >= MANAGED_NAMESPACE_LIMIT:
                    raise CitationCacheError(
                        "managed citation cache capacity is full; active namespaces are "
                        f"{occupants}. Complete worktree cleanup or abandon for an inactive leaf "
                        "before admitting another namespace; no active leaf was evicted"
                    )
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
        return result

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


def _namespace_ids(root: Path) -> list[str]:
    occupants: set[str] = set()
    for path in root.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        if not path.name.startswith("."):
            occupants.add(path.name)
            continue
        namespace, separator, _suffix = path.name[1:].partition(".terminal.")
        if (
            separator
            and len(namespace) == 64
            and all(character in "0123456789abcdef" for character in namespace)
        ):
            occupants.add(namespace)
    return sorted(occupants)


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
