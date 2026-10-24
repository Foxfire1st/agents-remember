"""What an archive does to every explainer environment of one task (MIK-R186 rule 9).

Master retirement and sprint archive end the explainer seats of the task's leaves and then reclaim
their folders, in this order:

* **retire** every environment -- record the seat's process tree, archive the seat, end its
  processes by the tree and by both markers, and remove the workspace down to its ``retired.json``
  marker, all under the leaf's own lock (rule 8's steps, shared with the developer's Retire);
* **remove** what the cleanup removes afterwards: the leaf's workspace, its generation counter, its
  grades file and its retire receipts, inside the explainer's own confinement. The leaf lock file
  is kept, because a lock is never removed (rule 2).

Two promises shape the code. A dry run lists and retires nothing, so a preview can say what the
archive would end without touching a seat, a process or a file. And a retire left ``pending`` keeps
its leaf folder, its counter and its receipt: a repeat of the same retirement request finishes it by
the agent id, the generation and the tree the receipt recorded.

A path below the task's explainer folder that carries a symlink is held and reported, never
followed: nothing behind it is removed. That is ``_Confinement``'s lstat rule, applied to the one
folder that lies outside every task (rule 2's named departure).
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_grades import grades_path
from agents_remember.canvas.explainer_paths import (
    GENERATION_SUFFIX,
    LOCK_RESOURCE,
    RETIRED_NAME,
    explainer_roots,
    guard,
    guard_explainer_path,
    leaf_lock_resource,
    retire_receipts_root,
)
from agents_remember.canvas.explainer_state import (
    ExplainerStateError,
    RetireReceipt,
    agent_id,
    generation,
    read_receipt,
    retired,
)
from agents_remember.canvas.explainer_workspace import (
    RetireOutcome,
    RetirePlace,
    RetireStart,
    resume_retire,
    retire_workspace,
)
from agents_remember.canvas.paths import PathRefused
from agents_remember.kernel.file_lock import exclusive_file_lock

__all__ = [
    "ARCHIVE_CALL_SECONDS",
    "ArchiveRun",
    "LeafEnvironment",
    "environments",
    "guard_problem",
    "host_seat_archive",
    "remove_environment",
    "retire_environments",
    "seat_ids",
]

#: How long the hook's own host call may take. The archive is already committed when it runs.
ARCHIVE_CALL_SECONDS = 2.0


def _minted_receipt() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class ArchiveRun:
    """One archive's explainer pass: the task it closes, when, and whether it is a dry run."""

    task_root: Path
    now: str
    dry_run: bool = False
    archive_session: Callable[[str], None] | None = None
    receipt_id: Callable[[], str] = _minted_receipt


@dataclass(frozen=True)
class LeafEnvironment:
    """One leaf's environment as it lies on disk: its folder, its counter and its receipts."""

    root: Path
    leaf: str
    generation: int
    pending: tuple[RetireReceipt, ...] = ()
    #: A receipt or a counter that cannot be read: the leaf is held rather than guessed at.
    blocked: bool = False

    @property
    def workspace(self) -> Path:
        return self.root / self.leaf

    @property
    def counter(self) -> Path:
        return self.root / f"{self.leaf}{GENERATION_SUFFIX}"

    @property
    def agent(self) -> str:
        return agent_id(self.workspace, self.generation)

    def record(self) -> dict[str, Any]:
        """What a preview or a receipt says about this environment."""

        return {
            "leaf": self.leaf,
            "agentId": self.agent,
            "generation": self.generation,
            "workspace": self.workspace.as_posix(),
        }

    @property
    def retired(self) -> bool:
        """Whether a finished Retire left its marker and nothing else to end."""

        return not self.pending and retired(self.workspace) is not None


def _environment(
    root: Path, task_root: Path | None, leaf: str
) -> tuple[LeafEnvironment, list[dict[str, str]]]:
    """Read one leaf's environment, and whatever about it had to be held."""

    problems: list[dict[str, str]] = []
    number = 0
    blocked = False
    counter = root / f"{leaf}{GENERATION_SUFFIX}"
    if counter.exists() or counter.is_symlink():
        try:
            number = generation(counter)
        except ExplainerStateError as error:
            problems.append(_held(counter, str(error)))
            blocked = True
    pending: tuple[RetireReceipt, ...] = ()
    if task_root is not None:
        pending, receipt_problems = _pending_receipts(task_root, leaf)
        problems.extend(receipt_problems)
        blocked = blocked or bool(receipt_problems)
    return (
        LeafEnvironment(root=root, leaf=leaf, generation=number, pending=pending, blocked=blocked),
        problems,
    )


def _pending_receipts(
    task_root: Path, leaf: str
) -> tuple[tuple[RetireReceipt, ...], list[dict[str, str]]]:
    """Every retire receipt of one leaf that did not finish, and the ones that cannot be read."""

    folder = retire_receipts_root(task_root, leaf)
    if folder.is_symlink() or not folder.is_dir():
        return (), []
    pending: list[RetireReceipt] = []
    problems: list[dict[str, str]] = []
    for path in sorted(folder.glob("*.json")):
        if path.is_symlink():
            problems.append(_held(path, f"{path} is a symlink; it is held, not followed"))
            continue
        try:
            receipt = read_receipt(folder, path.stem)
        except ExplainerStateError as error:
            problems.append(_held(path, str(error)))
            continue
        if receipt is not None and receipt.state != "finished":
            pending.append(receipt)
    return tuple(pending), problems


def environments(
    root: Path, task_root: Path | None = None
) -> tuple[list[LeafEnvironment], list[dict[str, str]]]:
    """Every environment of one task's explainer folder, and every path held on the way.

    A leaf is named by its workspace folder, by its generation counter, or by both. A symlinked
    entry of the task's explainer folder is held and reported; nothing behind it is read.
    """

    if not root.exists() and not root.is_symlink():
        return [], []
    if root.is_symlink():
        return [], [_held(root, f"{root} is a symlink; it is held, not followed")]
    if not root.is_dir():
        return [], [_held(root, f"{root} is not a directory")]
    leaves: set[str] = set()
    problems: list[dict[str, str]] = []
    for child in sorted(root.iterdir()):
        if child.is_symlink():
            problems.append(_held(child, f"{child} is a symlink; it is held, not followed"))
            continue
        if child.is_dir():
            leaves.add(child.name)
        elif child.name.endswith(GENERATION_SUFFIX):
            leaves.add(child.name[: -len(GENERATION_SUFFIX)])
    found: list[LeafEnvironment] = []
    for leaf in sorted(leaves):
        environment, held = _environment(root, task_root, leaf)
        found.append(environment)
        problems.extend(held)
    return found, problems


def seat_ids(coordination_root: Path) -> set[str]:
    """Every explainer seat the coordination root holds, whether or not it is live.

    The warmup asks this so a host restart never resumes an explainer: its chat loads when it is
    shown or written to, and never before (rule 5).
    """

    found: set[str] = set()
    for root in explainer_roots(coordination_root):
        for environment in environments(root)[0]:
            found.add(environment.agent)
    return found


def retire_environments(root: Path, run: ArchiveRun) -> dict[str, Any]:
    """Retire every environment of one task (rule 9), or list the ones a dry run would retire."""

    found, problems = environments(root, run.task_root)
    entries = [_retire_one(environment, run) for environment in found]
    if not run.dry_run:
        for entry in entries:
            if entry.get("state") not in {"retired", "already-retired", "would-retire"}:
                problems.append(
                    {
                        "target": str(entry.get("workspace") or entry.get("leaf") or root),
                        "detail": str(entry.get("detail") or "explainer retirement did not finish"),
                    }
                )
    return {"environments": entries, "failures": problems}


def _retire_one(environment: LeafEnvironment, run: ArchiveRun) -> dict[str, Any]:
    """Retire one environment under its own leaf lock, or say what a dry run would do."""

    entry = environment.record()
    if run.dry_run:
        live = bool(environment.pending) or (not environment.retired and _has_body(environment))
        return {**entry, "state": "would-retire" if live else "already-retired"}
    if environment.blocked:
        return {**entry, "state": "held", "detail": "the leaf's own state cannot be read"}
    try:
        guard_explainer_path(
            environment.workspace, root=environment.root, label="explainer workspace"
        )
    except PathRefused as refused:
        return {**entry, "state": "held", "detail": str(refused)}
    with exclusive_file_lock(leaf_lock_resource(environment.workspace), LOCK_RESOURCE):
        current, problems = _environment(environment.root, run.task_root, environment.leaf)
        if problems:
            return {**entry, "state": "held", "detail": problems[0]["detail"]}
        outcome = _end_seat(current, run)
    if outcome is None:
        return {**entry, "state": "already-retired"}
    # A step that did not finish is pending, never failed: a Retire finishes the same receipt
    # on its next call, and only a terminal receipt retires the environment (rule 8).
    state = "retired" if outcome.receipt.state == "finished" else "pending"
    return {
        **entry,
        "agentId": outcome.receipt.agent_id,
        "generation": outcome.receipt.generation,
        "state": state,
        "detail": outcome.detail,
    }


def _end_seat(environment: LeafEnvironment, run: ArchiveRun) -> RetireOutcome | None:
    """End one leaf's seat: finish a pending retire, or retire the live environment."""

    place = RetirePlace(
        workspace=environment.workspace,
        receipts_root=retire_receipts_root(run.task_root, environment.leaf),
        now=run.now,
        archive_session=run.archive_session,
    )
    if environment.pending:
        return resume_retire(place, environment.pending[-1])
    if not environment.workspace.is_dir():
        return None
    return retire_workspace(
        RetireStart(
            place=place,
            leaf_id=environment.leaf,
            generation=environment.generation,
            receipt_id=run.receipt_id(),
        )
    )


def _has_body(environment: LeafEnvironment) -> bool:
    """Whether anything of the environment is left beyond a finished retire's marker."""

    if not environment.workspace.is_dir():
        return False
    return any(child.name != RETIRED_NAME for child in environment.workspace.iterdir())


def remove_environment(
    environment: LeafEnvironment,
    *,
    task_root: Path,
    admit: Callable[[Path], str | None],
    dry_run: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Remove one leaf's workspace, counter, grades and receipts; keep a pending retire's own.

    ``admit`` refuses a path whose confinement fails (the explainer's own folder, or the task's for
    the grades and receipts it holds). A refusal is reported and that path is left alone.
    """

    if environment.pending or environment.blocked:
        kept = [
            _artifact(path, environment, "kept", "a retire receipt is pending or unreadable")
            for path in (environment.workspace, environment.counter)
        ]
        return kept, []
    entries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for path, kind in _removals(environment, task_root):
        problem = admit(path)
        if problem is not None:
            failures.append(_held(path, problem))
            continue
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_dir():
            link = _symlink_within(path)
            if link is not None:
                failures.append(_held(path, f"{link} is a symlink; the leaf is held, not followed"))
                continue
        entries.append(_artifact(path, environment, "would-delete" if dry_run else "deleted", kind))
        if dry_run:
            continue
        error = _remove(path)
        if error is not None:
            failures.append(_held(path, error))
    return entries, failures


def _removals(environment: LeafEnvironment, task_root: Path) -> list[tuple[Path, str]]:
    """What the cleanup removes for one leaf, workspace first and receipts last."""

    return [
        (environment.workspace, "workspace"),
        (environment.counter, "generation"),
        (grades_path(task_root, environment.leaf).parent, "grades"),
        (retire_receipts_root(task_root, environment.leaf), "retire-receipts"),
    ]


def _remove(path: Path) -> str | None:
    """Remove one admitted path: a file, or a directory tree with no symlink in it."""

    try:
        if path.is_dir() and not path.is_symlink():
            for root_dir, dirs, files in os.walk(path, followlinks=False):
                base = Path(root_dir)
                with suppress(OSError):
                    base.chmod(0o700)
                for name in dirs:
                    entry = base / name
                    if not entry.is_symlink():
                        with suppress(OSError):
                            entry.chmod(0o700)
                for name in files:
                    entry = base / name
                    if not entry.is_symlink():
                        with suppress(OSError):
                            entry.chmod(0o600)
            shutil.rmtree(path)
        else:
            if path.exists() and not path.is_symlink():
                with suppress(OSError):
                    path.chmod(0o600)
            path.unlink(missing_ok=True)
    except OSError as error:
        return str(error)
    return None


def _symlink_within(path: Path) -> Path | None:
    """The first symlink under ``path``, if it holds one."""

    for root_dir, dirs, files in os.walk(path, followlinks=False):
        base = Path(root_dir)
        for name in (*dirs, *files):
            candidate = base / name
            if candidate.is_symlink():
                return candidate
    return None


def _artifact(path: Path, environment: LeafEnvironment, state: str, kind: str) -> dict[str, Any]:
    return {"path": path.as_posix(), "leaf": environment.leaf, "kind": kind, "state": state}


def _held(path: Path, detail: str) -> dict[str, str]:
    return {"target": path.as_posix(), "detail": f"held, not deleted: {detail}"}


def guard_problem(root: Path, target: Path, *, label: str = "explainer path") -> str | None:
    """Why ``target`` fails the explainer folder's confinement, or ``None`` when it is admitted."""

    try:
        guard(root, target, label=label)
    except PathRefused as refused:
        return str(refused)
    return None


def host_seat_archive(config: Any) -> Callable[[str], None] | None:
    """The seat-archive callable an archive uses, or ``None`` when no host is configured.

    The archive is already committed when it runs, so a host that is missing or unreachable must
    leave the retire ``pending`` with its receipt (rule 8) rather than stop the retirement. A host
    that answers for no such agent archives nothing and reports no fault: the seat is gone already.
    """

    if config is None or getattr(config, "paseo_runtime", None) is None:
        return None

    def archive(seat: str) -> None:
        # Imported here: the archive reads the host only when a configured host exists, and every
        # other path through this module (the warmup's list, a cleanup) must not load the bridge.
        from agents_remember.cli.paseo_bridge import bridge_call  # noqa: PLC0415

        reply = bridge_call(
            config, "agent-archive", {"agentId": seat}, timeout_seconds=ARCHIVE_CALL_SECONDS
        )
        if reply.get("found") is False or reply.get("archived") is True:
            return
        raise RuntimeError(f"the host did not archive agent {seat}: {reply.get('reason') or reply}")

    return archive
