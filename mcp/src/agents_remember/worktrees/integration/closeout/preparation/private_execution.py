"""Execute one selected private code or memory-content command suffix."""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.errors import CertificationContractError
from agents_remember.kernel.git_command import (
    admit_private_git_preparation,
    inspect_git_preparation,
    preparation_command,
    run_git,
    run_git_preparation,
)
from agents_remember.kernel.git_preparation import (
    GitPreparationCommandError,
    PreparationAction,
    PrivateGitPreparationBinding,
    PrivateGitPreparationCapability,
)
from agents_remember.models.lifecycles.preparation import CloseoutPreparationIntent
from agents_remember.models.lifecycles.preparation_state import (
    PreparationCommand,
    PreparationCommandTerminal,
    SelectedPreparation,
)
from agents_remember.worktrees.integration.closeout.certification.observation import refuse
from agents_remember.worktrees.integration.closeout.preparation_selection import (
    begin_preparation_command,
    observe_preparation_command,
)
from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.modules.landing_commit_check import (
    commit_check_timeout_seconds,
    unrunnable_hook_reason,
    worktree_blob_map,
)
from agents_remember.worktrees.modules.models import PATH_SAMPLE_LIMIT

from .policy import observe_git_preparation_policy
from .selected import ReobservePreparation, SelectedCloseoutPreparation


def _selected_leg(selected: SelectedCloseoutPreparation) -> SelectedPreparation:
    state = selected.handoff.record.preparation
    leg = (
        None
        if state is None
        else next((item for item in state.legs if item.leg == selected.intent.leg), None)
    )
    if leg is None or leg.intent != selected.reference:
        refuse("private-preparation-selection-moved", selected.reference, leg)
    return leg


def private_git_binding(intent: CloseoutPreparationIntent) -> PrivateGitPreparationBinding:
    """Convert one exact write-enabled intent through the single preparation binding owner."""
    if not intent.writeEnabled or intent.privateRoot is None or intent.normalizedMessage is None:
        refuse("private-preparation-write-disabled", "write-enabled private intent", intent.leg)
    return PrivateGitPreparationBinding(
        logical_root=Path(intent.logicalRoot),
        logical_ref=intent.logicalRef,
        expected_logical_commit=intent.expectedOldCommit,
        common_directory=Path(intent.repositoryIdentity),
        private_root=Path(intent.privateRoot),
        parent_commit=intent.parentCommit,
        admitted_tree=intent.admittedTree,
        message=intent.normalizedMessage,
        hook_policy=intent.hookPolicy,
        operation_key=intent.operationKey,
        generation=intent.generation,
        intent_digest=intent.intentDigest,
    )


def _capability(
    selected: SelectedCloseoutPreparation, reobserve: ReobservePreparation
) -> PrivateGitPreparationCapability:
    binding = private_git_binding(selected.intent)

    def authorize(actual: PrivateGitPreparationBinding) -> None:
        current = reobserve(selected)
        if actual != private_git_binding(current.intent):
            refuse("private-preparation-binding-moved", binding.intent_digest, actual.intent_digest)
        if actual.private_root.exists():
            policy = observe_git_preparation_policy(actual.private_root)
            if policy.git_config_sha256 != current.intent.gitConfigSha256:
                refuse(
                    "private-preparation-config-mismatch",
                    current.intent.gitConfigSha256,
                    policy.git_config_sha256,
                )
            # Before materialization, repository-relative hooks may not exist yet.
            # The actual commit and every output read must prove the full policy.
            leg = _selected_leg(current)
            if any(item.kind == "commit" for item in leg.commands) or leg.output is not None:
                policy.require_intent(current.intent)

    return admit_private_git_preparation(binding, authorize=authorize)


def _terminal_record(
    selected: SelectedCloseoutPreparation,
    command: PreparationCommand,
    terminal: PreparationCommandTerminal,
) -> SelectedCloseoutPreparation:
    handoff = selected.handoff
    current = handoff.store.read()
    if current is None or (current.operationKey, current.generation) != (
        handoff.record.operationKey,
        handoff.record.generation,
    ):
        refuse("private-preparation-terminal-owner-moved", handoff.record.operationKey, current)
    updated = replace(selected, handoff=replace(handoff, record=current))
    leg = _selected_leg(updated)
    assert current.preparation is not None
    if current.preparation.legs[-1] != leg:
        refuse("private-preparation-terminal-leg-moved", selected.intent.leg, leg.leg)
    record = observe_preparation_command(
        handoff.contract, handoff.store, current, command, terminal
    )
    return replace(selected, handoff=replace(handoff, record=record))


#: The hook's own failure line: ``result: <step> FAIL`` (same rule as the ordinary runner).
_RESULT_FAIL_RE = re.compile(r"^.*result:\s+(?P<step>\S+)\s+FAIL\s*$")


def _refuse_unrunnable_preparation_hook(private_root: Path, hook_log: Path) -> None:
    """Refuse a prepared commit where a hook file exists that Git would not run (MIK-R152 rule 6).

    Porcelain ``git commit`` silently ignores a present nonexecutable hook; the
    landing refuses before anything commits, with the reason the ordinary runner
    would give. The refusal carries the hook log pointer: the log records the
    reason and the elapsed time like every other hook outcome (rules 4, 9).
    """

    reason = unrunnable_hook_reason(private_root)
    if reason is not None:
        started = time.monotonic()
        hook_log.write_text(
            f"prepared commit hook refused before running: {reason}\n"
            f"elapsed: {time.monotonic() - started:.3f}s\n",
            encoding="utf-8",
        )
        pointer = f"log={hook_log.as_posix()} elapsed=0.000s unrunnable={reason}"
        refuse(
            "private-preparation-hook-unrunnable",
            "a runnable pre-commit hook or none",
            f"{reason}; {pointer}",
        )


def _preparation_hook_log_path(logical_root: Path) -> Path:
    """Where one prepared commit's whole hook output lives (MIK-R152 rules 4, 9)."""
    common = require_git(logical_root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    return Path(common) / f"agents-remember-preparation-{uuid.uuid4().hex[:12]}.log"


def _hook_failing_step(output: str) -> str | None:
    """The last ``result: <step> FAIL`` line the hook output names, if any."""
    step: str | None = None
    for line in output.splitlines():
        match = _RESULT_FAIL_RE.match(line.strip())
        if match:
            step = match.group("step")
    return step


def _preparation_timeout_pointer(hook_log: Path, hook_seconds: float) -> str:
    """The log pointer shape every prepared-commit timeout carries."""

    return f"log={hook_log.as_posix()} elapsed={hook_seconds:.3f}s timeout"


class _PreparedLegHookTimeout(RuntimeError):
    """A prepared leg's commit hook ran past the bound: partial log written, nothing published.

    Raised from the original :class:`subprocess.TimeoutExpired` so the cause chain
    keeps it; the pointer names the partial-output log the terminal record carries.
    """

    def __init__(self, hook_pointer: str) -> None:
        self.hook_pointer = hook_pointer
        super().__init__(
            "the repository's commit check did not end within the bound; it was "
            f"stopped with its children and nothing was published; {hook_pointer}; "
            "repair the cause and run the closeout again"
        )


def _commit_prepared_leg(
    capability: PrivateGitPreparationCapability,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run one prepared leg's commit through the repository hook with its log.

    The exact commit sequence the closeout uses for every prepared leg (code and
    memory): a hook file Git would not run refuses with its log before anything
    commits; the commit runs bounded; the whole hook output with the elapsed time
    is persisted with the pointer the terminal record carries; a hook-changed
    admitted tree refuses with the log written. Answers the commit result and the
    hook pointer. A timeout writes the partial-output log and raises
    :class:`_PreparedLegHookTimeout` from the original expiry; nothing was
    published on any refusal path. (MIK-R152 rules 3, 4, 6, 9.)
    """

    bound = commit_check_timeout_seconds(capability.binding.logical_root)
    hook_log = _preparation_hook_log_path(capability.binding.logical_root)
    _refuse_unrunnable_preparation_hook(capability.binding.private_root, hook_log)
    before = _snapshot_private_tree(capability.binding.private_root)
    hook_started = time.monotonic()
    try:
        result = run_git_preparation(capability, "commit", commit_timeout_s=float(bound))
    except subprocess.TimeoutExpired as error:
        hook_seconds = time.monotonic() - hook_started
        _write_preparation_timeout_log(hook_log, error, hook_seconds)
        raise _PreparedLegHookTimeout(
            _preparation_timeout_pointer(hook_log, hook_seconds)
        ) from error
    except GitPreparationCommandError as error:
        hook_pointer = _write_preparation_hook_log(
            hook_log, error.result, time.monotonic() - hook_started
        )
        refuse(
            "private-preparation-hook-changed-tree",
            capability.binding.admitted_tree,
            f"{error}; nothing was published; {hook_pointer}",
        )
    hook_seconds = time.monotonic() - hook_started
    hook_pointer = _write_preparation_hook_log(hook_log, result, hook_seconds)
    _refuse_hook_changed_tree(
        capability.binding.private_root, capability.binding.admitted_tree, before, hook_pointer
    )
    return result, hook_pointer


def _refuse_failed_preparation_command(step: str, hook_pointer: str) -> None:
    """Refuse a prepared commit whose hook failed, carrying the log pointer (MIK-R152 rule 9).

    The refusal names the failing step and the whole-output log; the step alone
    cannot recover what the hook said.
    """
    refuse(
        "private-preparation-command-failed",
        "named-output recovery without rerun",
        f"{step}; {hook_pointer}",
    )


def _write_preparation_timeout_log(
    hook_log: Path, error: subprocess.TimeoutExpired, hook_seconds: float
) -> str:
    """Persist a prepared commit's partial hook output past the bound (MIK-R152 rules 4, 9)."

    Answers the same log pointer shape as a finished run: whole output captured
    so far, elapsed time, and the timeout outcome. The bound stops the commit
    with its children; nothing was published.
    """

    def _text(payload: object) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, bytes):
            return payload.decode("utf-8", "surrogateescape")
        return ""

    hook_log.write_bytes(
        b"prepared commit hook output (exit=timeout):\n"
        + _text(error.stdout).encode("utf-8", "surrogateescape")
        + _text(error.stderr).encode("utf-8", "surrogateescape")
        + f"\nelapsed: {hook_seconds:.3f}s timeout\n".encode()
    )
    return _preparation_timeout_pointer(hook_log, hook_seconds)


def _write_preparation_hook_log(
    hook_log: Path, result: subprocess.CompletedProcess[str], hook_seconds: float
) -> str:
    """Persist a prepared commit's whole hook output and elapsed time (MIK-R152 rules 4, 9)."

    Answers the log pointer the terminal record carries: the hashes alone cannot
    recover what the hook said.
    """

    stdout = result.stdout if isinstance(result.stdout, str) else ""
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    failing = _hook_failing_step(stdout + "\n" + stderr)
    hook_log.write_bytes(
        f"prepared commit hook output (exit={result.returncode}):\n".encode()
        + stdout.encode("utf-8", "surrogateescape")
        + stderr.encode("utf-8", "surrogateescape")
        + f"\nelapsed: {hook_seconds:.3f}s".encode()
        + (f" failing-step: {failing}".encode() if failing else b"")
        + b"\n"
    )
    pointer = f"log={hook_log.as_posix()} elapsed={hook_seconds:.3f}s"
    return pointer if failing is None else f"{pointer} failing-step={failing}"


def _snapshot_private_tree(private_root: Path) -> tuple[str, list[str], str, dict[str, str]]:
    """The private index tree, tracked status, raw diff and worktree bytes a commit must not change.

    Status labels cannot see a hook rewrite that keeps them, and neither can
    the raw diff (git reports zeros for dirty worktree files instead of
    hashing them); the worktree bytes hashed directly can (MIK-R152 rule 3).
    """
    tree = require_git(private_root, ["write-tree"])
    status = sorted(
        line
        for line in require_git(private_root, ["status", "--porcelain=v1"]).splitlines()
        if line and not line.startswith("??")
    )
    # Settle the stat cache first (see landing_commit_check._diff_head_raw): a
    # fresh index reports zero blob identities until git hashes the worktree
    # once, so two consecutive diffs would otherwise disagree with no byte
    # changed. Best effort; the byte comparison below decides.
    run_git(private_root, ["update-index", "--refresh", "-q"])
    raw = require_git(private_root, ["diff", "HEAD", "--raw", "--no-abbrev", "--no-renames"])
    blobs = worktree_blob_map(private_root)
    return tree, status, raw, blobs


def _refuse_hook_changed_tree(
    private_root: Path,
    admitted_tree: str,
    before: tuple[str, list[str], str, dict[str, str]],
    hook_pointer: str,
) -> None:
    """Refuse a commit whose tree is not the one the hook judged (MIK-R152 rule 3)."""
    after_tree, _after_status, after_raw, after_blobs = _snapshot_private_tree(private_root)
    if after_tree == admitted_tree and after_blobs == before[3]:
        if not after_raw.strip():
            # The commit landed the admitted tree: index and worktree equal the
            # new HEAD, so the raw diff is empty by the commit's own effect.
            return
        if set(after_raw.splitlines()) == set(before[2].splitlines()):
            # No commit happened (the hook failed and Git aborted it): the
            # staged and worktree state is exactly what the hook judged.
            return
    if after_tree != admitted_tree:
        changed = ["the index"]
    else:
        changed = sorted(
            set(_raw_paths(before[2])) ^ set(_raw_paths(after_raw))
            | {
                path
                for path in set(before[3]) | set(after_blobs)
                if before[3].get(path) != after_blobs.get(path)
            }
        )[:PATH_SAMPLE_LIMIT]
    refuse(
        "private-preparation-hook-changed-tree",
        admitted_tree,
        f"the hook's run changed {', '.join(changed)}; nothing was published; {hook_pointer}",
    )


def _raw_paths(raw: str) -> list[str]:
    """The paths one raw diff names, in sorted order."""
    paths = set()
    for line in raw.splitlines():
        _meta, _, path = line.partition("\t")
        if path:
            paths.add(path)
    return sorted(paths)


def _run_once(
    selected: SelectedCloseoutPreparation,
    action: PreparationAction,
    reobserve: ReobservePreparation,
) -> SelectedCloseoutPreparation:
    selected = reobserve(selected)
    leg = _selected_leg(selected)
    assert selected.handoff.record.preparation is not None
    if selected.handoff.record.preparation.legs[-1] != leg:
        refuse("private-preparation-command-leg-moved", selected.intent.leg, leg.leg)
    capability = _capability(selected, reobserve)
    plan = preparation_command(capability.binding, action)
    handoff = selected.handoff
    owner = handoff.record
    if (
        owner.workerPid is None
        or owner.workerLease is None
        or owner.workerProcessFingerprint is None
    ):
        refuse(
            "private-preparation-worker-missing", "actual live worker identity", owner.operationKey
        )
    command = PreparationCommand(
        kind=action,
        cwd=plan.cwd.as_posix(),
        argv=plan.argv,
        workerPid=owner.workerPid,
        workerLease=owner.workerLease,
        workerProcessFingerprint=owner.workerProcessFingerprint,
        startedAt=datetime.now(UTC).isoformat(),
    )
    record = begin_preparation_command(handoff.contract, handoff.store, owner, command)
    selected = replace(selected, handoff=replace(handoff, record=record))
    # Rebinding reopens the selected command before Git; the earlier inspection
    # cannot itself authorize a start after a concurrent cancellation.
    hook_pointer = ""
    try:
        if action == "commit":
            result, hook_pointer = _commit_prepared_leg(_capability(selected, reobserve))
        else:
            result = run_git_preparation(_capability(selected, reobserve), action)
    except BaseException as error:
        detail = f"Original command did not return a proved terminal: {type(error).__name__}"
        if action == "commit" and isinstance(error, _PreparedLegHookTimeout):
            detail = str(error)
        elif action == "commit" and isinstance(error, CertificationContractError):
            detail += "; " + "; ".join(str(item["observed"]) for item in error.findings)
        _terminal_record(
            selected,
            command,
            PreparationCommandTerminal(
                outcome="unknown",
                observedAt=datetime.now(UTC).isoformat(),
                detail=detail,
            ),
        )
        raise
    selected = _terminal_record(
        selected,
        command,
        PreparationCommandTerminal(
            outcome="succeeded" if result.returncode == 0 else "failed",
            observedAt=datetime.now(UTC).isoformat(),
            returnCode=result.returncode,
            stdoutSha256=hashlib.sha256(
                result.stdout.encode("utf-8", "surrogateescape")
            ).hexdigest(),
            stderrSha256=hashlib.sha256(
                result.stderr.encode("utf-8", "surrogateescape")
            ).hexdigest(),
            detail=hook_pointer[:8192],
        ),
    )
    if result.returncode != 0:
        step = hook_pointer.split("failing-step=")[1] if "failing-step=" in hook_pointer else action
        _refuse_failed_preparation_command(step, hook_pointer)
    return reobserve(selected)


def observe_private_output(
    selected: SelectedCloseoutPreparation, *, reobserve: ReobservePreparation
) -> bytes:
    """Prove only the selected original private output; existing legs belong to their owners."""
    selected = reobserve(selected)
    _selected_leg(selected)
    observed = inspect_git_preparation(_capability(selected, reobserve))
    if observed.state != "committed" or observed.raw_commit is None:
        refuse(
            "private-preparation-output-unfinished", "one exact committed output", observed.state
        )
    binding = private_git_binding(selected.intent)
    observe_git_preparation_policy(binding.private_root).require_intent(selected.intent)
    reobserve(selected)
    return observed.raw_commit


def prepare_private_output(
    selected: SelectedCloseoutPreparation, *, reobserve: ReobservePreparation
) -> tuple[SelectedCloseoutPreparation, bytes]:
    """Complete only an unstarted command suffix; uncertain original starts never repeat."""
    selected = reobserve(selected)
    binding = private_git_binding(selected.intent)
    if binding.private_root.parent.resolve() != binding.private_root.parent:
        refuse(
            "private-preparation-namespace-unsafe",
            "canonical selected namespace",
            binding.private_root,
        )
    # The owning workflow selected this exact intent before any namespace creation.
    leg = _selected_leg(selected)
    binding.private_root.parent.mkdir(parents=True, exist_ok=True)
    if leg.output is None:
        for action in ("create", "materialize", "commit"):
            prior = next(
                (item for item in _selected_leg(selected).commands if item.kind == action), None
            )
            if prior is not None:
                if prior.terminal is not None and prior.terminal.outcome == "succeeded":
                    continue
                if action == "commit" and prior.terminal is not None:
                    # A failed/unknown original commit may have produced its real
                    # object. Inspect that exact output; never repeat Git.
                    break
                refuse(
                    "private-preparation-command-unresolved",
                    "original named-output recovery",
                    action,
                )
            selected = _run_once(selected, action, reobserve)
    raw = observe_private_output(selected, reobserve=reobserve)
    return reobserve(selected), raw
