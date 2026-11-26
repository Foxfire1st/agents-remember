"""Shared bounded process, output-log, and timeout ownership for commit checks (L29-R1).

Closeout landings and continued code-side sync commits both run repository-owned
pre-commit hooks under the same ``orchestration.landing.commitCheckTimeoutSeconds``
setting (default 900s), in their own process session, streaming stdout/stderr to a
reclaimed per-repository log file in the common Git directory. Past the bound the
process group is asked to stop (SIGTERM, 30s grace), then stopped (SIGKILL), and
any escaped descendants still holding the output pipe are stopped and verified dead
or named alive.
"""

from __future__ import annotations

import json
import os
import re
import select
import signal
import subprocess
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from agents_remember.errors import AgentsRememberError
from agents_remember.kernel._agentic_settings_core import (
    DEFAULT_COMMIT_CHECK_TIMEOUT_SECONDS,
)
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.worktrees.modules.landing_commit_check_pipes import _pipe_write_end_holders

#: After the bound, the process group is asked to stop before it is killed:
#: the gate restores its isolation stash on a trapped exit.
HOOK_TERM_GRACE_SECONDS = 30.0

#: Bound for asking an escaped pipe holder to die before killing it; the drain
#: grace already expired, so this is short patience, not a second grace.
HOOK_ESCAPED_TERM_SECONDS = 2.0

#: Rounds of scan-ask-stop-verify for escaped pipe holders before survivors
#: are named alive instead of being claimed stopped.
HOOK_ESCAPED_ROUNDS = 3

#: Bound verifying a killed holder is really dead before it counts as such;
#: a zombie holds no file end, so reaping lag never blocks the pipe, but the
#: return must not claim a live holder stopped.
HOOK_ESCAPED_KILL_VERIFY_SECONDS = 5.0

#: Output is copied to the log in chunks; no run holds more than one chunk in memory.
HOOK_OUTPUT_CHUNK_BYTES = 65536

#: The step parser reads only the log's tail, never the whole output.
HOOK_LOG_TAIL_BYTES = 65536

#: Retention: newest per-repo commit-check logs kept; older ones are reclaimed.
HOOK_LOG_KEEP_COUNT = 20

#: Bound for draining the pipe after the process group is stopped.
HOOK_DRAIN_GRACE_SECONDS = 10.0

#: Quantum for the output-copy wait and the group-death poll.
HOOK_COPY_QUANTUM_SECONDS = 0.05

_RESULT_FAIL_RE = re.compile(r"^.*result:\s+(?P<step>\S+)\s+FAIL\s*$")


class CommitCheckSettingsError(AgentsRememberError):
    """The repository's commit-check timeout setting could not be read or validated."""


@dataclass(frozen=True)
class CommitCheckProcessOutcome:
    """Exit status, timeout state, and escaped-holder facts from one bounded process run."""

    returncode: int
    timed_out: bool
    escaped_stopped: tuple[int, ...] = ()
    escaped_survivors: tuple[int, ...] = ()


@dataclass(frozen=True)
class CommitCheckRunResult:
    """One completed bounded command run with its log file and elapsed timing."""

    outcome: CommitCheckProcessOutcome
    log: Path
    timeout_seconds: int
    elapsed_seconds: float


@dataclass(frozen=True)
class BoundedCommandSpec:
    """Inputs for launching one bounded commit-check command."""

    repo: Path
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    header: str
    timeout_s: int | None = None
    log_path: Path | None = None


@dataclass(frozen=True)
class SyncCommitCheckStop:
    """Typed details of a continued sync stopped at the repository's commit check (L29-R1)."""

    side: str
    staged: tuple[str, ...]
    task_changed: Mapping[str, bool]
    timed_out: bool = False
    timeout_seconds: int | None = None
    log_path: Path | None = None
    escaped_survivors: tuple[int, ...] = ()
    changed_paths: tuple[str, ...] = ()


def _meta_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return run_git(repo, args, GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS))


def _require_common_git_dir(repo: Path) -> Path:
    result = _meta_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if result.returncode != 0:
        raise RuntimeError(
            "git rev-parse --git-common-dir failed: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return Path(result.stdout.strip())


def _merge_landing_block(merged: dict[str, object], path: Path, raw: object) -> None:
    """Merge one settings file's ``orchestration.landing`` block, failing closed."""

    if not isinstance(raw, dict):
        raise CommitCheckSettingsError(f"the settings file holds no object: {path}")
    orchestration = raw.get("orchestration")
    if orchestration is None:
        return
    if not isinstance(orchestration, dict):
        raise CommitCheckSettingsError(f"orchestration is no object: {path}")
    landing = orchestration.get("landing")
    if landing is None:
        return
    if not isinstance(landing, dict):
        raise CommitCheckSettingsError(f"orchestration.landing is no object: {path}")
    unknown = set(landing) - {"commitCheckTimeoutSeconds"}
    if unknown:
        raise CommitCheckSettingsError(
            f"unknown orchestration.landing key: {sorted(unknown)}: {path}"
        )
    merged.update(landing)


def _settings_candidates(repo: Path) -> list[Path]:
    candidates = (
        [Path(os.environ["XDG_CONFIG_HOME"]) / "agents-remember" / "settings.json"]
        if os.environ.get("XDG_CONFIG_HOME")
        else [Path.home() / ".config" / "agents-remember" / "settings.json"]
    )
    top = _meta_git(repo, ["rev-parse", "--show-toplevel"])
    if top.returncode == 0:
        top_path = Path(top.stdout.strip())
        candidates.append(top_path / "system" / "settings.json")
        # A linked worktree holds no repo-local settings of its own; resolve the
        # primary checkout through the common git dir, matching `.githooks/_gate.sh`.
        common = _meta_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
        if common.returncode == 0:
            main_root = Path(common.stdout.strip()).parent
            if main_root != top_path:
                candidates.append(main_root / "system" / "settings.json")
    return candidates


def resolve_commit_check_timeout_seconds(repo: Path) -> int:
    """Resolve ``orchestration.landing.commitCheckTimeoutSeconds`` (default 900s)."""

    merged: dict[str, object] = {}
    for path in _settings_candidates(repo):
        try:
            raw = json.loads(path.read_bytes().decode("utf-8"))
        except OSError:
            continue
        except ValueError as error:
            raise CommitCheckSettingsError(
                f"the settings file cannot be read: {path}: {error}"
            ) from error
        _merge_landing_block(merged, path, raw)
    if "commitCheckTimeoutSeconds" not in merged:
        return DEFAULT_COMMIT_CHECK_TIMEOUT_SECONDS
    value = merged["commitCheckTimeoutSeconds"]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CommitCheckSettingsError(
            "orchestration.landing.commitCheckTimeoutSeconds must be a positive whole number"
        )
    return value


def default_commit_check_log_path(repo: Path) -> Path:
    """Allocate a per-run commit-check log path in the repository's common Git directory."""

    common = _require_common_git_dir(repo)
    return common / f"agents-remember-commit-check-{uuid.uuid4().hex[:12]}.log"


def reclaim_commit_check_logs(repo: Path) -> None:
    """Keep the newest twenty per-repo commit-check logs; reclaim older ones."""

    try:
        common = _require_common_git_dir(repo)
        logs = sorted(
            common.glob("agents-remember-commit-check-*.log"),
            key=lambda path: (path.stat().st_mtime, path.name),
        )
    except (RuntimeError, OSError):
        return
    for stale in logs[: max(0, len(logs) - HOOK_LOG_KEEP_COUNT)]:
        with suppress(OSError):
            stale.unlink()


def read_commit_check_log_tail(log: Path) -> str:
    """Read only the bounded tail of a commit-check log, never the whole file."""

    try:
        size = log.stat().st_size
        with log.open("rb") as handle:
            if size > HOOK_LOG_TAIL_BYTES:
                handle.seek(size - HOOK_LOG_TAIL_BYTES)
            tail = handle.read()
    except OSError:
        return ""
    return tail.decode("utf-8", "surrogateescape")


def write_commit_check_log_footer(log: Path, started: float, outcome: str) -> float:
    """Append the elapsed-time footer to ``log`` and return elapsed seconds."""

    seconds = time.monotonic() - started
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"elapsed: {seconds:.3f}s {outcome}\n")
    return seconds


def last_failing_commit_check_step(output: str, *, hook_path: Path | None = None) -> str | None:
    """Extract the last ``result: <step> FAIL`` line, or the last non-empty diagnostic line."""

    step: str | None = None
    for line in output.splitlines():
        match = _RESULT_FAIL_RE.match(line.strip())
        if match:
            step = match.group("step")
    if step is not None:
        return step
    if hook_path is not None and "cannot exec" in output:
        try:
            first = hook_path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except OSError:
            first = ""
        if first.startswith("#!"):
            return f"the hook's interpreter cannot run: {first[2:].strip() or first}"
    for line in reversed(output.splitlines()):
        if line.strip():
            text = line.strip()
            return text if len(text) <= 200 else f"{text[:200]}…"
    return None


def spawn_commit_check_process(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str]
) -> subprocess.Popen[bytes]:
    """Spawn a commit-check command alone in its own session with combined output pipe."""

    return subprocess.Popen(
        list(argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=dict(env),
    )


def run_bounded_command(spec: BoundedCommandSpec) -> CommitCheckRunResult:
    """Run one command under the commit-check timeout, log, and process-group ownership."""

    bound = (
        spec.timeout_s
        if spec.timeout_s is not None
        else resolve_commit_check_timeout_seconds(spec.repo)
    )
    log = spec.log_path if spec.log_path is not None else default_commit_check_log_path(spec.repo)
    if spec.log_path is None:
        reclaim_commit_check_logs(spec.repo)
    started = time.monotonic()
    log.write_text(spec.header, encoding="utf-8")
    try:
        process = spawn_commit_check_process(spec.argv, cwd=spec.cwd, env=spec.env)
    except OSError:
        write_commit_check_log_footer(log, started, "unrunnable(spawn)")
        raise
    outcome = run_process_to_log(process, log, bound)
    elapsed = write_commit_check_log_footer(
        log, started, "timeout" if outcome.timed_out else f"exit={outcome.returncode}"
    )
    return CommitCheckRunResult(
        outcome=outcome,
        log=log,
        timeout_seconds=bound,
        elapsed_seconds=elapsed,
    )


def run_process_to_log(
    process: subprocess.Popen[bytes], log: Path, bound: int
) -> CommitCheckProcessOutcome:
    """Stream process output to ``log`` within ``bound``; stop group and escaped holders past it."""

    deadline = time.monotonic() + bound
    assert process.stdout is not None
    stopped: list[int] = []
    survivors: list[int] = []
    try:
        with log.open("ab") as handle:
            reached_eof = _copy_process_output(process, handle, deadline)
            timed_out = not reached_eof
            if timed_out:
                stopped, survivors = _handle_process_timeout(process, handle)
            else:
                # EOF with the process still computing must still end at the bound.
                try:
                    process.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _stop_process_tree(process)
            returncode = process.wait()
    finally:
        with suppress(OSError):
            process.stdout.close()
    return CommitCheckProcessOutcome(
        returncode=returncode,
        timed_out=timed_out,
        escaped_stopped=tuple(stopped),
        escaped_survivors=tuple(survivors),
    )


def _handle_process_timeout(
    process: subprocess.Popen[bytes], handle: BinaryIO
) -> tuple[list[int], list[int]]:
    _stop_process_tree(process)
    drained = _copy_process_output(process, handle, time.monotonic() + HOOK_DRAIN_GRACE_SECONDS)
    if drained:
        return [], []
    stopped, survivors = stop_escaped_pipe_holders(process)
    drained = _copy_process_output(process, handle, time.monotonic() + HOOK_DRAIN_GRACE_SECONDS)
    if stopped:
        pids = ", ".join(f"pid={pid}" for pid in stopped)
        handle.write(
            f"\n[escaped hook descendants holding the hook output pipe were stopped: {pids}]\n".encode()
        )
    for pid in survivors:
        handle.write(
            f"\n[escaped hook descendant holding the hook output pipe is STILL ALIVE: pid={pid}]\n".encode()
        )
    if not drained:
        handle.write(
            b"\n[a writer outside the stopped process group still held the hook output pipe; it was cut off]\n"
        )
    return stopped, survivors


def _copy_process_output(
    process: subprocess.Popen[bytes], handle: BinaryIO, deadline: float
) -> bool:
    """Copy the pipe to ``handle`` in bounded chunks until EOF or ``deadline``."""

    assert process.stdout is not None
    fd = process.stdout.fileno()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        ready, _, _ = select.select([fd], [], [], min(remaining, HOOK_COPY_QUANTUM_SECONDS))
        if not ready:
            continue
        try:
            chunk = os.read(fd, HOOK_OUTPUT_CHUNK_BYTES)
        except OSError:
            return True
        if not chunk:
            return True
        handle.write(chunk)


def _process_group_dead(pid: int) -> bool:
    """Whether no process survives in the owned process group."""

    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def _holder_alive(pid: int) -> bool:
    """Whether a pipe holder is live rather than a lingering zombie."""

    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, ValueError, IndexError):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            return True
        return True
    return state != "Z"


def _live_pipe_holders(pipe_inode: int) -> list[int]:
    return [pid for pid in _pipe_write_end_holders(pipe_inode) if _holder_alive(pid)]


def _ask_pipe_holders_to_close(pipe_inode: int) -> bool:
    for pid in _live_pipe_holders(pipe_inode):
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + HOOK_ESCAPED_TERM_SECONDS
    while time.monotonic() < deadline:
        if not _live_pipe_holders(pipe_inode):
            return True
        time.sleep(HOOK_COPY_QUANTUM_SECONDS)
    return not _live_pipe_holders(pipe_inode)


def _stop_pipe_holders(pipe_inode: int) -> None:
    holders = _live_pipe_holders(pipe_inode)
    for pid in holders:
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + HOOK_ESCAPED_KILL_VERIFY_SECONDS
    while time.monotonic() < deadline:
        # Closing the pipe can precede PID death; verify identity before returning.
        if not any(_holder_alive(pid) for pid in holders):
            break
        time.sleep(HOOK_COPY_QUANTUM_SECONDS)


def stop_escaped_pipe_holders(process: subprocess.Popen[bytes]) -> tuple[list[int], list[int]]:
    """Stop descendants that escaped the process group while still holding the output pipe."""

    assert process.stdout is not None
    try:
        pipe_inode = os.fstat(process.stdout.fileno()).st_ino
    except OSError:
        return [], []
    stopped: list[int] = []
    for _ in range(HOOK_ESCAPED_ROUNDS):
        holders = _live_pipe_holders(pipe_inode)
        if not holders:
            return stopped, []
        _ask_pipe_holders_to_close(pipe_inode)
        if not _live_pipe_holders(pipe_inode):
            return stopped, []
        kill_set = _live_pipe_holders(pipe_inode)
        _stop_pipe_holders(pipe_inode)
        for pid in dict.fromkeys([*holders, *kill_set]):
            if pid not in stopped and not _holder_alive(pid):
                stopped.append(pid)
    return stopped, _live_pipe_holders(pipe_inode)


def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop the owned process group: SIGTERM with 30s grace, then SIGKILL and reap."""

    with suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + HOOK_TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None and _process_group_dead(process.pid):
            break
        time.sleep(HOOK_COPY_QUANTUM_SECONDS)
    with suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def format_timeout_outcome(
    timeout_seconds: int,
    *,
    log_path: Path | None,
    escaped_survivors: Sequence[int] = (),
) -> str:
    """Describe one commit-check timeout consistently across closeout and continued sync."""

    if escaped_survivors:
        pids = ", ".join(f"pid={pid}" for pid in escaped_survivors)
        text = (
            f"the commit check reached the time limit ({timeout_seconds}s); "
            "its process group was stopped but escaped descendant(s) are still alive "
            f"({pids}); nothing was published"
        )
    else:
        text = (
            f"the commit check reached the time limit ({timeout_seconds}s); "
            "it was stopped with its children and nothing was published"
        )
    if log_path is not None:
        text += f"; hook log: {log_path.as_posix()}"
    return text


def extract_commit_check_log_detail(log: Path, *, limit: int = 800) -> str:
    """Bounded hook diagnostic lines from a commit-check log, without header or footer."""

    lines = [
        line
        for line in read_commit_check_log_tail(log).splitlines()
        if line.strip()
        and not line.startswith("sync commit check: ")
        and not line.startswith("elapsed: ")
    ]
    text = "\n".join(lines).strip()
    if not text:
        return "git commit failed"
    return text if len(text) <= limit else f"{text[:limit]}…"


def _format_sync_tree_block(stop: SyncCommitCheckStop) -> str:
    if stop.staged:
        lines = [
            f"- {path}: {'changed by the task' if stop.task_changed.get(path) else 'from the carried line'}"
            for path in stop.staged
        ]
        tree_block = "\nThe check judged the merged tree:\n" + "\n".join(lines)
    else:
        tree_block = "\nThe check judged the merged tree (no file differences staged against HEAD)."
    if stop.changed_paths:
        changed = "\n".join(f"- {path}" for path in stop.changed_paths)
        tree_block += (
            "\nThe commit check modified tracked file(s) (pinned index restored):\n" + changed
        )
    return tree_block


def format_sync_commit_check_summary(stop: SyncCommitCheckStop, detail: str, worktree: str) -> str:
    """Format the operator-facing ``sync-commit-check-failed`` summary (L29-R1)."""

    if stop.timed_out and stop.timeout_seconds is not None:
        reason = format_timeout_outcome(
            stop.timeout_seconds,
            log_path=stop.log_path,
            escaped_survivors=stop.escaped_survivors,
        )
        lead = f"The {stop.side} merge is complete in content but {reason}"
    else:
        log_suffix = (
            f"; hook log: {stop.log_path.as_posix()}"
            if stop.log_path is not None and stop.log_path.as_posix() not in detail
            else ""
        )
        lead = (
            f"The {stop.side} merge is complete in content but its commit did not pass the "
            f"repository's commit check: {detail}{log_suffix}"
        )
    tree_block = _format_sync_tree_block(stop)
    if stop.timed_out:
        recovery = (
            "\nRecovery: inspect the hook log, repair the cause or adjust "
            "orchestration.landing.commitCheckTimeoutSeconds, then continue with "
            "worktree_sync and resolution_action='continue'; or end the sync with "
            "resolution_action='cancel'."
        )
    else:
        recovery = (
            f"\nRecovery: repair the named files in {worktree}, stage them (git add), then "
            "continue with worktree_sync and resolution_action='continue'; or end the sync with "
            "resolution_action='cancel'."
        )
    return f"{lead}{tree_block}{recovery}"
