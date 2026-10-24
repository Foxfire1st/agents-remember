"""One leaf's explainer state: its binding, its generation, its marker and its seats (MIK-R186).

Four small durable facts live here, each with exactly one writer:

* **the binding** (``binding.json``) says which comparison the environment serves -- recorded, or
  the typed planned comparison of a leaf whose work has not started. Only the build and an Update
  write it, last, atomically (rules 4 and 6);
* **the generation** (``<leaf>.generation``) only grows, and it is raised under the leaf lock, so
  two Sends cannot make one seat (rule 5);
* **the retired marker** (``<leaf>/retired.json``) names the receipt a finished Retire wrote
  (rule 8);
* **the retire receipt** lies with the task's records, so removing the workspace removes no
  evidence of what was retired.

The seat's identity is derived, never stored: ``uuid5`` of the workspace path and the generation
(rule 5). A host that lost a create reply therefore reconciles the same seat, and a new generation
can never reuse an archived id.

Processes are found the way Retire must find them: by the two environment markers every seat process
carries -- its ``PASEO_AGENT_ID`` and the tool server's ``AR_EXPLAINER_WORKSPACE`` -- and by descent
from a recorded pid. A recorded tree is compared by pid *and* start time, so a recycled pid is never
mistaken for the seat's process.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import time
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, ConfigDict, Field
from pydantic import ValidationError as _ValidationError

from agents_remember.canvas.explainer_paths import (
    binding_path,
    explainer_task_root,
    guard_explainer_path,
    receipt_id_segment,
    receipt_path,
    retired_marker,
)
from agents_remember.canvas.paths import PathRefused, path_segment
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.base import KnowledgeModel
from agents_remember.models.knowledge.review_frame import FrameBinding

__all__ = [
    "AGENT_MARKER",
    "BINDING_SCHEMA",
    "ExplainerBinding",
    "ExplainerRefused",
    "ExplainerStateError",
    "PlannedIdentity",
    "ProcessRecord",
    "ProcessTree",
    "RetireReceipt",
    "agent_id",
    "binding_digest",
    "end_processes",
    "find_pending_receipt",
    "generation",
    "ledger_entry",
    "ledger_path",
    "mark_retired",
    "process_tree",
    "raise_generation",
    "read_binding",
    "read_inflight_create",
    "read_ledger",
    "read_receipt",
    "record_request",
    "request_message_id",
    "restore_generation",
    "retired",
    "same_binding",
    "write_binding",
    "write_inflight_create",
    "write_receipt",
]

BINDING_SCHEMA = "ar-explainer-binding/v1"
RECEIPT_SCHEMA = "ar-explainer-retire/v1"
REQUEST_LEDGER = "requests.json"
LEDGER_SCHEMA = "ar-explainer-requests/v1"
MAX_LEDGER_ENTRIES = 32
#: The workspace marker every seat process inherits, including a render child (rule 5).
WORKSPACE_MARKER = "AR_EXPLAINER_WORKSPACE"
AGENT_MARKER = "PASEO_AGENT_ID"


#: The leaf is building, updating or retiring: a writer that cannot wait says so by name.
BUSY = "explainer-busy"


class ExplainerRefused(ValueError):
    """A named refusal. The reason is a value the page or the tool shows; never a server error."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


class ExplainerStateError(ValueError):
    """A durable explainer fact could not be read or is not what it claims."""


class PlannedIdentity(KnowledgeModel):
    """The typed planned comparison a leaf before its go is bound to (rule 4).

    Its base equals its candidate on each side, at the master line's two tip commits. It is never
    stored as a comparison record and never pinned; only these facts are kept, and the reference
    ends in ``/planned`` so it is never read as a closed leaf's number-0 range.
    """

    code_tip: str = Field(min_length=1, max_length=128)
    memory_tip: str = Field(min_length=1, max_length=128)
    code_tree: str = Field(min_length=1, max_length=128)
    memory_tree: str = Field(min_length=1, max_length=128)


class ExplainerBinding(KnowledgeModel):
    """What one environment serves: the binding, its packet, and the snapshot that matches it."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_: Literal["ar-explainer-binding/v1"] = Field(default=BINDING_SCHEMA, alias="schema")
    binding: FrameBinding
    planned: PlannedIdentity | None = None
    snapshot: str = Field(min_length=1, max_length=64)
    prepared_at: str = Field(min_length=1, max_length=64)
    packet_digest: str = Field(min_length=1, max_length=128)
    #: The comparison record the packet was prepared from. A planned binding has none.
    record_digest: str | None = Field(default=None, max_length=128)


class ProcessRecord(KnowledgeModel):
    """One process Retire recorded: its pid and the start time that makes the pid its own."""

    pid: int = Field(ge=1)
    start_time: str = Field(min_length=1, max_length=64)
    marker: str = Field(min_length=1, max_length=64)
    memory_bytes: int = Field(default=0, ge=0)


class ProcessTree(KnowledgeModel):
    """Every process a seat started, recorded before anything is archived (rule 8)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    agent_id: str = Field(min_length=1, max_length=64)
    workspace: str = Field(min_length=1, max_length=4096)
    processes: tuple[ProcessRecord, ...] = ()
    measured_memory_bytes: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("measured_memory_bytes", "measuredMemoryBytes"),
        serialization_alias="measuredMemoryBytes",
    )


class RetireReceipt(KnowledgeModel):
    """One retire attempt: what it was retiring, what it did, and what it could not finish."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_: Literal["ar-explainer-retire/v1"] = Field(default=RECEIPT_SCHEMA, alias="schema")
    receipt_id: str = Field(min_length=1, max_length=64)
    leaf_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=64)
    generation: int = Field(ge=0)
    tree: ProcessTree
    state: Literal["pending", "finished", "failed"] = "pending"
    step: str = Field(default="recorded", min_length=1, max_length=64)
    detail: str = Field(default="", max_length=4096)
    ended_by_marker: int = Field(default=0, ge=0)
    ended_by_tree: int = Field(default=0, ge=0)
    remaining: tuple[int, ...] = ()
    held: tuple[str, ...] = ()
    measured_memory_bytes: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("measured_memory_bytes", "measuredMemoryBytes"),
        serialization_alias="measuredMemoryBytes",
    )
    recorded_at: str = Field(min_length=1, max_length=64)
    finished_at: str | None = Field(default=None, max_length=64)


def same_binding(left: FrameBinding, right: FrameBinding) -> bool:
    """Whether two bindings name one comparison, owner included (rule 4's identity).

    Number 0 already marks a closed leaf's committed range, so the owner is part of the identity and
    a planned binding is never taken as current against a landed leaf's number-0 comparison. The
    reference is *not* compared: it is a spelling of the same identity, and it differs between the
    planned form and the recorded one by design.
    """

    return (
        left.comparison_owner == right.comparison_owner
        and left.comparison_number == right.comparison_number
        and left.side_identity() == right.side_identity()
        and left.conversion_identity() == right.conversion_identity()
        and left.compared_memory_base_tree == right.compared_memory_base_tree
        and left.task_id == right.task_id
        and left.leaf_id == right.leaf_id
    )


def binding_digest(binding: ExplainerBinding) -> str:
    """The digest of one binding snapshot, so two reads can be compared without parsing prose."""

    payload = json.dumps(binding.model_dump(by_alias=True), sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def write_binding(workspace: Path, binding: ExplainerBinding) -> None:
    """Replace ``binding.json`` atomically -- the build's and Update's last act (rule 6)."""

    target = binding_path(workspace)
    guard_explainer_path(target, root=workspace.parent, label="explainer binding")
    atomic_write_text(target, json.dumps(binding.model_dump(by_alias=True), indent=2) + "\n")


def read_binding(workspace: Path) -> ExplainerBinding | None:
    """The environment's binding, or ``None`` when it has none. A corrupt one is an error."""

    target = binding_path(workspace)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise ExplainerStateError(f"the binding at {target} cannot be read: {error}") from error
    try:
        return ExplainerBinding.model_validate(payload)
    except _ValidationError as error:
        raise ExplainerStateError(f"the binding at {target} is not a binding: {error}") from error


def generation(generation_file: Path) -> int:
    """The seat generation this leaf has reached; ``0`` when no seat was ever made."""

    if not generation_file.is_file():
        return 0
    try:
        return int(generation_file.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError) as error:
        raise ExplainerStateError(
            f"the generation at {generation_file} is not a number: {error}"
        ) from error


def raise_generation(generation_file: Path) -> int:
    """Raise the counter by one and answer the new value. Caller holds the leaf lock."""

    guard_explainer_path(generation_file, root=generation_file.parent, label="explainer generation")
    generation_file.parent.mkdir(parents=True, exist_ok=True)
    guard_explainer_path(generation_file, root=generation_file.parent, label="explainer generation")
    raised = generation(generation_file) + 1
    atomic_write_text(generation_file, f"{raised}\n")
    return raised


def restore_generation(generation_file: Path, prior: int) -> None:
    """Restore ``generation_file`` to ``prior`` after a pre-seat failure."""

    guard_explainer_path(generation_file, root=generation_file.parent, label="explainer generation")
    if prior <= 0:
        if generation_file.exists() and not generation_file.is_symlink():
            generation_file.unlink()
        return
    generation_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(generation_file, f"{prior}\n")


def find_pending_receipt(receipts_root: Path, receipt_id: str) -> RetireReceipt | None:
    """Find ``receipt_id`` or an existing unfinished receipt in ``receipts_root``."""

    admitted = receipt_id_segment(receipt_id)
    existing = read_receipt(receipts_root, admitted)
    if existing is not None:
        return existing
    if not receipts_root.is_dir() or receipts_root.is_symlink():
        return None
    for path in sorted(receipts_root.glob("*.json")):
        if path.is_symlink():
            continue
        with suppress(ExplainerStateError, PathRefused):
            candidate = read_receipt(receipts_root, path.stem)
            if candidate is not None and candidate.state != "finished":
                return candidate.model_copy(update={"receipt_id": admitted})
    return None


def agent_id(workspace: Path, generation_number: int) -> str:
    """The seat's stable id: the workspace and the generation, never an attempt folder (rule 5)."""

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{workspace.as_posix()}#{generation_number}"))


def request_message_id(leaf_id: str, request_id: str) -> str:
    """Deterministic request-bound message UUID for ``(leaf, request_id)`` (MIK-R108)."""

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ar-explainer-message:{leaf_id}:{request_id}"))


def ledger_path(config: McpRuntimeConfig, repository_id: str, task: str, leaf_id: str) -> Path:
    root = explainer_task_root(config, repository_id, task)
    return root / f"{path_segment(leaf_id, name='leaf')}.{REQUEST_LEDGER}"


def _guard_ledger_paths(path: Path) -> None:
    guard_explainer_path(path, root=path.parent, label="explainer request ledger")
    shared = path.parent / REQUEST_LEDGER
    if shared.exists() or shared.is_symlink():
        guard_explainer_path(shared, root=path.parent, label="explainer request ledger")


def read_ledger(path: Path) -> dict[str, Any]:
    _guard_ledger_paths(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": LEDGER_SCHEMA, "requests": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("requests"), dict):
        return {"schema": LEDGER_SCHEMA, "requests": {}}
    return payload


def _write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    _guard_ledger_paths(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _guard_ledger_paths(path)
    atomic_write_text(path, json.dumps(ledger, indent=2, sort_keys=True) + "\n")


def ledger_entry(path: Path, leaf_id: str, request_id: str) -> dict[str, Any] | None:
    requests = read_ledger(path)["requests"]
    entry = requests.get(f"{leaf_id}:{request_id}")
    if not isinstance(entry, dict):
        legacy = requests.get(request_id)
        if isinstance(legacy, dict) and legacy.get("leaf") in {None, leaf_id}:
            entry = legacy
    if isinstance(entry, dict) and entry.get("leaf") in {None, leaf_id}:
        return entry
    return None


def record_request(path: Path, leaf_id: str, request_id: str, entry: Mapping[str, Any]) -> None:
    """Remember one Send by its leaf and request id, keeping the ledger bounded (MIK-R108)."""

    ledger = read_ledger(path)
    requests: dict[str, Any] = ledger["requests"]
    requests[f"{leaf_id}:{request_id}"] = {**dict(entry), "leaf": leaf_id, "requestId": request_id}
    while len(requests) > MAX_LEDGER_ENTRIES:
        requests.pop(next(iter(requests)))
    _write_ledger(path, ledger)


def read_inflight_create(path: Path) -> dict[str, Any] | None:
    inflight = read_ledger(path).get("inFlightCreate")
    return inflight if isinstance(inflight, dict) else None


def write_inflight_create(path: Path, inflight: dict[str, Any] | None) -> None:
    ledger = read_ledger(path)
    if inflight is None:
        ledger.pop("inFlightCreate", None)
    else:
        ledger["inFlightCreate"] = dict(inflight)
    _write_ledger(path, ledger)


def retired(workspace: Path) -> dict[str, object] | None:
    """The marker a finished Retire left, or ``None``."""

    marker = retired_marker(workspace)
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"receipt": None, "unreadable": True}
    return payload if isinstance(payload, dict) else {"receipt": None, "unreadable": True}


def mark_retired(workspace: Path, receipt_id: str, *, at: str) -> None:
    """Leave the one marker a retired environment keeps (rule 8 step 5)."""

    admitted_id = receipt_id_segment(receipt_id)
    target = retired_marker(workspace)
    guard_explainer_path(target, root=workspace.parent, label="retire marker")
    atomic_write_text(
        target,
        json.dumps({"schema": "ar-explainer-retired/v1", "receipt": admitted_id, "at": at}) + "\n",
    )


def clear_retired(workspace: Path) -> None:
    """A new first request clears the marker and builds anew (rule 8)."""

    marker = retired_marker(workspace)
    guard_explainer_path(marker, root=workspace.parent, label="retire marker")
    if marker.exists():
        marker.unlink()


def write_receipt(root: Path, receipt: RetireReceipt) -> Path:
    """Write or replace one retire receipt atomically."""

    target = receipt_path(root, receipt.receipt_id)
    guard_explainer_path(target, root=root.parent, label="retire receipt")
    root.mkdir(parents=True, exist_ok=True)
    guard_explainer_path(target, root=root.parent, label="retire receipt")
    atomic_write_text(
        target, json.dumps(receipt.model_dump(mode="json", by_alias=True), indent=2) + "\n"
    )
    return target


def read_receipt(root: Path, receipt_id: str) -> RetireReceipt | None:
    """Read one receipt, or ``None`` when it is absent. A corrupt one is an error."""

    target = receipt_path(root, receipt_id)
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if (
            isinstance(raw, dict)
            and "measuredMemoryBytes" in raw
            and "measured_memory_bytes" in raw
        ):
            raw = {key: value for key, value in raw.items() if key != "measured_memory_bytes"}
        return RetireReceipt.model_validate(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _ValidationError) as error:
        raise ExplainerStateError(
            f"the retire receipt at {target} is unreadable: {error}"
        ) from error


@dataclass(frozen=True)
class _Proc:
    pid: int
    ppid: int
    start_time: str
    environment: tuple[str, ...] = field(default=())
    rss_bytes: int = 0

    def carries(self, marker: str) -> bool:
        return any(item == marker for item in self.environment)


def process_tree(workspace: Path, agent: str) -> ProcessTree:
    """Every process of one seat, found by both markers and by descent from them (rule 8 step 1).

    ``/proc`` is the only source: a process's environment is what the tool server and every child
    inherit, and a pid's start time is what tells a live process from a recycled pid. A host without
    ``/proc`` refuses by name rather than reporting an empty tree as a clean one.
    """

    processes = _scan_proc()
    if processes is None:
        raise ExplainerRefused(
            "explainer-process-tree-unavailable",
            "this host exposes no /proc, so the seat's processes cannot be recorded",
        )
    by_marker = [
        proc
        for proc in processes.values()
        if proc.carries(f"{AGENT_MARKER}={agent}")
        or proc.carries(f"{WORKSPACE_MARKER}={workspace.as_posix()}")
    ]
    tree = _with_descendants(processes, by_marker)
    recorded = tuple(
        ProcessRecord(
            pid=proc.pid,
            start_time=proc.start_time,
            marker=("agent" if proc.carries(f"{AGENT_MARKER}={agent}") else "workspace"),
            memory_bytes=proc.rss_bytes,
        )
        for proc in sorted(tree, key=lambda item: item.pid)
    )
    measured = sum(item.memory_bytes for item in recorded)
    return ProcessTree(
        agent_id=agent,
        workspace=workspace.as_posix(),
        processes=recorded,
        measured_memory_bytes=measured,
    )


def _scan_proc() -> dict[int, _Proc] | None:
    root = Path("/proc")
    if not root.is_dir():
        return None
    found: dict[int, _Proc] = {}
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        parsed = _read_proc(entry, int(entry.name))
        if parsed is not None:
            found[parsed.pid] = parsed
    return found


def _read_rss_bytes(entry: Path) -> int:
    try:
        statm = (entry / "statm").read_text(encoding="utf-8", errors="replace").split()
        if len(statm) >= 2:
            page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
            return max(0, int(statm[1]) * int(page_size))
    except (OSError, ValueError):
        pass
    return 0


def _read_proc(entry: Path, pid: int) -> _Proc | None:
    try:
        stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # ``comm`` may hold spaces and parentheses; the fields after the last ')' are positional.
    tail = stat.rsplit(")", 1)[-1].split()
    if len(tail) < 20:
        return None
    try:
        ppid = int(tail[1])
        start_time = tail[19]
    except (IndexError, ValueError):
        return None
    environment: tuple[str, ...] = ()
    try:
        raw = (entry / "environ").read_bytes()
        environment = tuple(part.decode("utf-8", errors="replace") for part in raw.split(b"\0"))
    except OSError:
        environment = ()
    return _Proc(
        pid=pid,
        ppid=ppid,
        start_time=start_time,
        environment=environment,
        rss_bytes=_read_rss_bytes(entry),
    )


def _with_descendants(processes: dict[int, _Proc], seeds: list[_Proc]) -> list[_Proc]:
    """The seeds and every process descending from them, each once."""

    selected = {proc.pid: proc for proc in seeds}
    changed = True
    while changed:
        changed = False
        for proc in processes.values():
            if proc.pid in selected or proc.ppid not in selected:
                continue
            selected[proc.pid] = proc
            changed = True
    return list(selected.values())


def end_processes(
    tree: ProcessTree, *, workspace: Path, agent: str, timeout: float = 5.0
) -> tuple[int, int, tuple[int, ...]]:
    """End every process that carries either marker or is in the recorded tree, by both counts.

    A process is the seat's when its pid *and* its start time match a recorded one, or when its
    environment carries either marker. SIGTERM first, then SIGKILL after ``timeout`` seconds, and
    whatever still answers alive is returned so Retire can report it rather than claim success.
    """

    processes = _scan_proc()
    if processes is None:
        raise ExplainerRefused(
            "explainer-process-tree-unavailable",
            "this host exposes no /proc, so no process can be ended by name",
        )
    recorded = {(item.pid, item.start_time) for item in tree.processes}
    marker_seeds = [
        proc
        for proc in processes.values()
        if proc.carries(f"{AGENT_MARKER}={agent}")
        or proc.carries(f"{WORKSPACE_MARKER}={workspace.as_posix()}")
    ]
    by_marker = {(proc.pid, proc.start_time) for proc in _with_descendants(processes, marker_seeds)}
    tree_seeds = [proc for proc in processes.values() if (proc.pid, proc.start_time) in recorded]
    by_tree = {(proc.pid, proc.start_time) for proc in _with_descendants(processes, tree_seeds)}
    targets: set[tuple[int, str]] = by_marker | by_tree
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid, start_time in sorted(targets):
            if not _same_process_alive(pid, start_time):
                continue
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                continue
        _wait_until_gone(targets, timeout)
        targets = {
            (pid, start_time) for pid, start_time in targets if _same_process_alive(pid, start_time)
        }
    remaining = tuple(sorted(pid for pid, _start_time in targets))
    return len(by_marker), len(by_tree), remaining


def _same_process_alive(pid: int, start_time: str) -> bool:
    """Whether ``(pid, start_time)`` still runs as a non-zombie process (MIK-R186 rule 8).

    Re-reading ``/proc/<pid>/stat`` for both state and start time ensures a recycled pid is never
    signalled or reported as remaining.
    """

    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    tail = stat.rsplit(")", 1)[-1].split()
    if len(tail) < 20 or tail[0] == "Z":
        return False
    return tail[19] == start_time


def _alive(pid: int) -> bool:
    """Whether the process still runs. A zombie is ended: it holds no resources, only a pid."""

    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    tail = stat.rsplit(")", 1)[-1].split()
    return not (tail and tail[0] == "Z")


def _wait_until_gone(targets: set[tuple[int, str]], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    pending = set(targets)
    while pending and time.monotonic() < deadline:
        pending = {
            (pid, start_time) for pid, start_time in pending if _same_process_alive(pid, start_time)
        }
        if not pending:
            return
        time.sleep(0.05)
