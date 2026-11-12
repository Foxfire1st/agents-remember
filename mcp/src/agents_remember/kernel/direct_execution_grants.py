"""Direct-execution grant records: one per-leaf file outside task documents and settings.

This kernel module owns the store addressing, the record shape, and the
call-time read: every layer (``worktrees`` gates, ``application`` boundaries,
``mcp`` payloads) reads a grant through here, so no layer reaches up for it.
Giving and taking back a grant -- which prove the leaf unstarted -- stay in
``application.direct_execution_grants``.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.git_command import run_git
from agents_remember.models.task_document_ref import TaskDocumentRef

GRANT_SCHEMA = "ar-direct-execution-grant/v1"

GRANT_DIRECTORY_NAME = "direct-execution-grants"

DEVELOPER_GRANTER = "developer"
ARCHITECT_GRANTER = "architect"

#: Who can give a grant, as every refusal states it (no settings key is named).
GRANT_GIVERS = (
    "a direct-execution grant is given by the developer with the control on the task "
    "in the dashboard, or by an architect with the direct_execution_grant function"
)


class DirectGrantError(AgentsRememberError):
    """A direct-execution grant could not be read, given, or taken back."""

    def __init__(
        self,
        detail: str,
        *,
        status: str = "direct-grant-refused",
        facts: dict[str, object] | None = None,
    ) -> None:
        self.status = status
        self.detail = detail
        self.facts = facts or {}
        super().__init__(detail)


@dataclass(frozen=True)
class DirectExecutionGrant:
    """One leaf's direct-execution grant, as stored and as shown."""

    leaf: TaskDocumentRef
    granted_by: str
    granted_at: str
    granter_agent_id: str | None = None

    def describe(self) -> str:
        """Who gave the grant, for headers, answers, and refusals."""
        if self.granted_by == DEVELOPER_GRANTER:
            return f"granted by the developer at {self.granted_at}"
        if self.granter_agent_id:
            return f"granted by architect {self.granter_agent_id} at {self.granted_at}"
        return f"granted by an architect at {self.granted_at}"

    def model_dump(self) -> dict[str, Any]:
        return {
            "leaf": self.leaf.model_dump(mode="json"),
            "grantedBy": self.granted_by,
            "granterAgentId": self.granter_agent_id,
            "grantedAt": self.granted_at,
        }


def grant_store_dir(coordination_root: Path) -> Path:
    """The one directory holding a coordination root's grants."""
    return coordination_root / "controlplane" / GRANT_DIRECTORY_NAME


_GRANT_FILENAME_SAFE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _safe_filename_segment(value: str) -> str:
    """A filename segment that cannot escape the store or its task prefix."""
    return "".join(char if char in _GRANT_FILENAME_SAFE else "_" for char in value) or "_"


def _grant_digest(ref: TaskDocumentRef) -> str:
    return hashlib.sha256(f"{ref.repository}\0{ref.path}".encode()).hexdigest()


def _grant_master(ref: TaskDocumentRef) -> str:
    parts = Path(ref.path).parts
    return parts[0] if parts else "_"


def grant_path_for(coordination_root: Path, ref: TaskDocumentRef) -> Path:
    """The file holding one leaf's grant, addressed by repository and master.

    The task prefix bounds the per-task census to that task's grants (F09);
    the digest keeps the exact leaf address. Files written before addressing
    keep the bare digest name and are still read and migrated on write.
    """
    prefix = (
        f"{_safe_filename_segment(ref.repository)}--{_safe_filename_segment(_grant_master(ref))}"
    )
    return grant_store_dir(coordination_root) / f"{prefix}--{_grant_digest(ref)}.json"


def _legacy_grant_path_for(coordination_root: Path, ref: TaskDocumentRef) -> Path:
    """The pre-addressing file name, read and migrated but never written."""
    return grant_store_dir(coordination_root) / f"{_grant_digest(ref)}.json"


def grant_removal_paths(coordination_root: Path, ref: TaskDocumentRef) -> tuple[Path, ...]:
    """Every file that may hold one leaf's grant: addressed, then legacy."""
    path = grant_path_for(coordination_root, ref)
    legacy = _legacy_grant_path_for(coordination_root, ref)
    return (path,) if legacy == path else (path, legacy)


def grant_task_glob(repository: str, master: str) -> str:
    """The filename glob matching one task's addressed grants (plus legacy)."""
    return f"{_safe_filename_segment(repository)}--{_safe_filename_segment(master)}--*.json"


@contextlib.contextmanager
def grant_store_locked(coordination_root: Path) -> Iterator[None]:
    """Serialize grant mutations across processes and servers (F08).

    The lock covers the whole read-prove-write cycle of giving and taking
    back, so concurrent givers produce one changed grant and one unchanged
    response retaining its issuer and time. Reads never lock.
    """
    store = grant_store_dir(coordination_root)
    store.mkdir(parents=True, exist_ok=True)
    lock_path = store / ".lock"
    with open(lock_path, "a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _grant_candidate_paths(coordination_root: Path, ref: TaskDocumentRef) -> Iterator[Path]:
    """The addressed file first, then the pre-addressing name for migration reads."""
    path = grant_path_for(coordination_root, ref)
    yield path
    legacy = _legacy_grant_path_for(coordination_root, ref)
    if legacy != path:
        yield legacy


def read_grant(coordination_root: Path, ref: TaskDocumentRef) -> DirectExecutionGrant | None:
    """Read one leaf's grant at the moment of the call.

    ``None`` when the leaf has no grant. A file that cannot be read or parsed
    raises: a leaf is never taken as direct without a readable grant. Files
    written before task addressing keep their bare digest name and are read
    here; writes migrate them to the addressed name.
    """
    raw: bytes | None = None
    for path in _grant_candidate_paths(coordination_root, ref):
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise DirectGrantError(
                f"the direct-execution grant for {ref.repository}:{ref.path} cannot be read: {error}",
                status="direct-grant-unreadable",
            ) from error
        break
    if raw is None:
        return None
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise DirectGrantError(
            f"the direct-execution grant for {ref.repository}:{ref.path} does not parse: {error}",
            status="direct-grant-unreadable",
        ) from error
    return grant_from_document(ref, document)


def grant_from_document(ref: TaskDocumentRef, document: Any) -> DirectExecutionGrant:
    if not isinstance(document, dict):
        raise DirectGrantError(
            f"the direct-execution grant for {ref.repository}:{ref.path} is not an object",
            status="direct-grant-unreadable",
        )
    if document.get("schema") != GRANT_SCHEMA:
        raise DirectGrantError(
            f"the direct-execution grant for {ref.repository}:{ref.path} has an unknown schema",
            status="direct-grant-unreadable",
        )
    leaf = document.get("leaf")
    if (
        not isinstance(leaf, dict)
        or leaf.get("repository") != ref.repository
        or leaf.get("path") != ref.path
    ):
        raise DirectGrantError(
            f"the direct-execution grant does not name {ref.repository}:{ref.path}",
            status="direct-grant-unreadable",
        )
    granted_by = document.get("grantedBy")
    if granted_by not in (DEVELOPER_GRANTER, ARCHITECT_GRANTER):
        raise DirectGrantError(
            f"the direct-execution grant for {ref.repository}:{ref.path} names no giver",
            status="direct-grant-unreadable",
        )
    granted_at = document.get("grantedAt")
    granter_agent_id = document.get("granterAgentId")
    if not isinstance(granted_at, str) or not granted_at:
        raise DirectGrantError(
            f"the direct-execution grant for {ref.repository}:{ref.path} names no time",
            status="direct-grant-unreadable",
        )
    if granter_agent_id is not None and not isinstance(granter_agent_id, str):
        raise DirectGrantError(
            f"the direct-execution grant for {ref.repository}:{ref.path} names no giver",
            status="direct-grant-unreadable",
        )
    return DirectExecutionGrant(
        leaf=TaskDocumentRef(repository=ref.repository, path=ref.path),
        granted_by=granted_by,
        granted_at=granted_at,
        granter_agent_id=granter_agent_id,
    )


def grant_document(grant: DirectExecutionGrant) -> dict[str, Any]:
    """The stored JSON document for one grant."""
    return {
        "schema": GRANT_SCHEMA,
        "leaf": {"repository": grant.leaf.repository, "path": grant.leaf.path},
        "grantedBy": grant.granted_by,
        "granterAgentId": grant.granter_agent_id,
        "grantedAt": grant.granted_at,
    }


def write_grant_file(path: Path, grant: DirectExecutionGrant) -> None:
    """Atomically write one grant file; only the product's two routes call this."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(grant_document(grant), indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".grant-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise


def create_grant_file(path: Path, grant: DirectExecutionGrant) -> None:
    """Atomically create one grant file, refusing when one already exists (F08).

    The compare-and-swap for the product's two writers: exactly one
    concurrent giver wins the create; the loser sees ``FileExistsError`` and
    reports the stored grant unchanged instead of overwriting its issuer and
    time. Fixture and setup writes keep using :func:`write_grant_file`.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(grant_document(grant), indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".grant-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.link(tmp_name, path)
    finally:
        with suppress(OSError):
            os.unlink(tmp_name)


def remove_matching_grant(
    coordination_root: Path, ref: TaskDocumentRef, grant: DirectExecutionGrant
) -> None:
    """Remove exactly the given grant record, leaving any other grant alone.

    The post-write remediation for a give whose leaf started between its
    proof and its publication (F08): only the triple that this give wrote is
    removed, never a concurrent writer's grant.
    """

    try:
        stored = read_grant(coordination_root, ref)
    except DirectGrantError:
        return
    if (
        stored is None
        or stored.granted_by != grant.granted_by
        or stored.granted_at != grant.granted_at
        or stored.granter_agent_id != grant.granter_agent_id
    ):
        return
    for path in grant_removal_paths(coordination_root, ref):
        with suppress(OSError):
            path.unlink()


def utc_now_iso() -> str:
    """The grant time, as one UTC ISO string."""
    return datetime.now(UTC).isoformat()


def require_grant(coordination_root: Path, ref: TaskDocumentRef) -> DirectExecutionGrant:
    """Read one leaf's grant for a gate, refusing without one (fail closed)."""
    try:
        grant = read_grant(coordination_root, ref)
    except DirectGrantError as error:
        raise DirectGrantError(
            f"the direct-execution grant for {ref.repository}:{ref.path} cannot be read: "
            f"{error.detail}",
            status=error.status,
        ) from error
    if grant is None:
        raise DirectGrantError(
            f"{ref.repository}:{ref.path} has no direct-execution grant; {GRANT_GIVERS}",
            status="direct-grant-missing",
        )
    return grant


def iter_grant_files(
    store: Path, *, repository: str | None = None, master: str | None = None
) -> Iterator[Path]:
    """Candidate grant files in stable order: one task's addressed files first."""
    if not store.is_dir():
        return
    if repository is not None and master is not None:
        addressed = set(store.glob(grant_task_glob(repository, master)))
        paths = sorted(addressed)
        paths += [
            path
            for path in sorted(store.glob("*.json"))
            if len(path.stem) == 64 and path not in addressed
        ]
        yield from paths
    else:
        yield from sorted(store.glob("*.json"))


def iter_grant_leaves(
    store: Path, *, repository: str | None = None, master: str | None = None
) -> Iterator[tuple[str, str]]:
    """Every granted ``(repository, path)`` in stable order; skips unreadable files.

    Bounded to one task's addressed files when repository and master are
    given, with pre-addressing digest files as a shrinking legacy fallback.
    Callers still validate the parsed content: the filename only prefilters.
    """
    for path in iter_grant_files(store, repository=repository, master=master):
        try:
            document = json.loads(path.read_bytes().decode("utf-8"))
            leaf = document.get("leaf") if isinstance(document, dict) else None
            if not isinstance(leaf, dict):
                continue
            leaf_repo = leaf.get("repository")
            leaf_path = leaf.get("path")
            if not isinstance(leaf_repo, str) or not isinstance(leaf_path, str):
                continue
        except (OSError, ValueError):
            continue
        yield leaf_repo, leaf_path


def grants_for_task(
    coordination_root: Path, repository: str, task_name: str
) -> dict[str, DirectExecutionGrant]:
    """Every readable grant of one task folder, keyed by leaf path.

    Plan surfaces (rendering, projections) read what is shown from the one
    record. The census is bounded to the task: the addressed filename glob
    selects only this task's files, and each file is parsed once -- a broken
    grant file never breaks a task view, while gates still refuse it (F09/F13).
    Pre-addressing files keep their bare digest name; they are found by a
    second glob and migrate to the addressed name on the next write.
    """
    prefix = f"{task_name}/"
    found: dict[str, DirectExecutionGrant] = {}
    store = grant_store_dir(coordination_root)
    for path in iter_grant_files(store, repository=repository, master=task_name):
        try:
            document = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, ValueError):
            continue
        leaf = document.get("leaf") if isinstance(document, dict) else None
        if not isinstance(leaf, dict):
            continue
        leaf_repo = leaf.get("repository")
        leaf_path = leaf.get("path")
        if not isinstance(leaf_repo, str) or not isinstance(leaf_path, str):
            continue
        if leaf_repo != repository or not leaf_path.startswith(prefix):
            continue
        try:
            ref = TaskDocumentRef(repository=leaf_repo, path=leaf_path)
        except ValueError:
            # A structurally invalid canonical reference never breaks a task
            # view (F13); gate reads still refuse the selected invalid grant.
            continue
        try:
            grant = grant_from_document(ref, document)
        except DirectGrantError:
            continue
        found[leaf_path] = grant
    return found


_COMPLETED_DOC_STATUS = "Completed"

_ABANDONED_DOC_STATUS = "abandoned"


def doc_started_markers(document: Any) -> bool:
    """Whether a leaf document shows a start, whatever its status says.

    A non-planning status, execution registrations, a route review, or any
    non-pending step proves the leaf started -- even when the status was later
    marked ``Completed`` without the work landing (F05).
    """

    status = getattr(document, "status", None)
    if status is not None and status != "planning":
        return True
    if getattr(document, "executionRegistrations", None):
        return True
    if getattr(document, "routeReview", None) is not None:
        return True
    return any(
        (step.status, step.disposition) != ("pending", None)
        for step in (getattr(document, "steps", None) or [])
    )


def sibling_occupies_checkout(
    document: Any, *, checkout_dirty: bool | None, landed: bool | None = None
) -> bool:
    """Whether a granted sibling leaf still owns the master's checkout.

    An open started leaf occupies it. A ``Completed`` leaf with started
    markers occupies it when either checkout is dirty or no landing proof
    exists: document completion is progress, not proof of landing, so a
    second direct leaf must refuse and name the first (F05). A completed
    generation with the leaf's published pair releases the guard even with a
    ``Completed`` document, as does an explicitly abandoned leaf. With no
    record at all the document/dirt guard decides, as before.
    """

    if getattr(document, "kind", None) != "subTask":
        return False
    status = getattr(document, "status", None)
    if status == _ABANDONED_DOC_STATUS:
        return False
    if landed is True:
        return False
    markers = doc_started_markers(document)
    if status == _COMPLETED_DOC_STATUS:
        return bool(markers) and (checkout_dirty is True or landed is False)
    return markers


def _checkout_holding_branch(listing: str, work_branch: str) -> Path | None:
    """The worktree path holding the branch, or ``None`` when none does."""

    wanted = f"refs/heads/{work_branch}"
    path: str | None = None
    branch: str | None = None
    bare = False
    found: Path | None = None

    def flush() -> None:
        nonlocal found, path, branch, bare
        if found is None and path is not None and branch == wanted and not bare:
            found = Path(path)
        path, branch, bare = None, None, False

    for line in listing.splitlines():
        if not line.strip():
            flush()
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            path = value.strip()
        elif key == "branch":
            branch = value.strip()
        elif key == "bare":
            bare = True
    flush()
    return found


def code_checkout_dirty(code_repo_path: Path, work_branch: str) -> bool | None:
    """Whether the checkout holding the branch has uncommitted work.

    ``None`` when no checkout holds the branch or Git is unreadable -- then
    the caller falls back to the document-status guard instead of guessing.
    """

    try:
        listing = run_git(code_repo_path, ["worktree", "list", "--porcelain"])
    except Exception:
        return None
    if listing.returncode != 0:
        return None
    found = _checkout_holding_branch(listing.stdout, work_branch)
    if found is None:
        return None
    try:
        status = run_git(found, ["status", "--porcelain"])
    except Exception:
        return None
    if status.returncode != 0:
        return None
    return bool(status.stdout.strip())


def memory_checkout_dirty(memory_repo_path: Path | None, memory_work_branch: str) -> bool | None:
    """Whether the checkout holding the memory branch has uncommitted work.

    ``None`` when a memory checkout should exist but none holds the branch, or
    Git is unreadable -- then the caller falls back instead of guessing. A
    series with no memory repository and no memory branch has no memory side
    that could hold work, so it reads clean (``False``).
    """

    if memory_repo_path is None and not memory_work_branch:
        return False
    if memory_repo_path is None or not memory_work_branch:
        return None
    try:
        listing = run_git(memory_repo_path, ["worktree", "list", "--porcelain"])
    except Exception:
        return None
    if listing.returncode != 0:
        return None
    found = _checkout_holding_branch(listing.stdout, memory_work_branch)
    return _porcelain_dirty(found)


def _porcelain_dirty(checkout: Path | None) -> bool | None:
    """Whether one checkout has uncommitted work; ``None`` when unreadable."""

    if checkout is None:
        return None
    try:
        status = run_git(checkout, ["status", "--porcelain"])
    except Exception:
        return None
    if status.returncode != 0:
        return None
    return bool(status.stdout.strip())
