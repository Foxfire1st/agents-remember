"""The review route's transient worklist children; no cache or durable job state.

MIK-R42: the supported worklist/read-set collections have no total byte ceiling. The protocol
therefore adds no arbitrary result cap. Stdlib communicate drains both bounded OS pipes while
materializing the one result, as the existing HTTP model does.

**Admission (ruling 1 of the second round).** One reader never meets a "busy" refusal while the
reviewer is merely working:

* Requests for the same computation (the digest of the request, which names the contract, the four
  trees and the build) in flight at once share one child. The computation belongs to its *flight*,
  not to the request that started it: a requester that disconnects leaves, and the child is stopped
  only when no requester is left.
* At most :data:`MAX_ACTIVE_CHILDREN` children run. A different computation waits its turn, first in
  first out, for as long as its own deadline allows.
* At most :data:`MAX_WAITING_COMPUTATIONS` computations run or wait at once; requests that share a
  computation do not count. A request that would start one beyond that, or whose deadline ends
  while it still waits for a child, is refused as
  :class:`WorklistOverloaded` with its own code and an action that is true: the reviewer is
  computing other worklists, retry.

**Lifetime (ruling 3).** The child must not outlive the dashboard. It has its own session and
process group, so the parent stops the whole group; and the child itself arms
:func:`arm_child_lifetime` before its work: a parent-death signal (Linux ``prctl``; elsewhere a
parent watcher thread) and a deadline of its own, either of which kills the child's whole group,
so its Git children go with it even when the dashboard was killed without a chance to clean up.
"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import agents_remember
from agents_remember.kernel.recorded_reads import valid_observation

MAX_ACTIVE_CHILDREN: Final = 2
MAX_WAITING_COMPUTATIONS: Final = 8
DEADLINE_SECONDS: Final = 60
# The child's own deadline is a little past the parent's, so that in ordinary operation the
# parent's explicit answer wins and the child's is only the net under a dashboard that is gone.
CHILD_DEADLINE_SECONDS: Final = DEADLINE_SECONDS + 5
CHILD_MODULE: Final = "agents_remember.application.reviewer_worklist_child"
OPERATION: Final = "reviewer-worklist/v1"
BUILD_FIELDS: Final = ("version", "sourceDigest", "pythonExecutable", "packageRoot", "commit")
PARENT_PID_ENV: Final = "AR_WORKLIST_PARENT_PID"
EXIT_BUILD_MISMATCH: Final = 75
_PR_SET_PDEATHSIG: Final = 1
_POLL_SECONDS: Final = 0.05
_REAP_SECONDS: Final = 5.0
# A slot that opens with less of the deadline left than this would only start a computation to end
# it: the request is refused as overloaded instead, which is what it is.
_MINIMUM_RUN_SECONDS: Final = 1.0
_CANCELLATION: ContextVar[threading.Event | None] = ContextVar(
    "worklist_cancellation", default=None
)
_ARRIVAL: ContextVar[float | None] = ContextVar("worklist_arrival", default=None)
_RESTART_ACTION: Final = (
    "restart the dashboard so it serves the build that is installed now, then reopen the review"
)


class WorklistProcessError(RuntimeError):
    """No complete, identified child answer exists; never substitute an empty worklist."""

    code = "candidate_unresolved"
    next_action = "correct the named worklist process failure, then reopen the review"


class WorklistOverloaded(WorklistProcessError):
    """The reviewer was busy with other worklists for the whole time this request could wait."""

    code = "reviewer_busy"
    next_action = "the reviewer is computing other worklists; retry"


class WorklistBuildMismatch(WorklistProcessError):
    """The child is not the build the dashboard booted with: the installed package moved."""

    next_action = _RESTART_ACTION


@contextmanager
def worklist_request(cancellation: threading.Event) -> Iterator[None]:
    """Bind the actual HTTP request's disconnect/cancellation and arrival to its worker thread.

    The request's deadline runs from this moment, so the work before the child starts (capture,
    memo key, knowledge diff) consumes it and the child gets what remains.
    """

    token = _CANCELLATION.set(cancellation)
    arrival = _ARRIVAL.set(time.monotonic())
    try:
        yield
    finally:
        _ARRIVAL.reset(arrival)
        _CANCELLATION.reset(token)


def build_identity(stamp: Mapping[str, Any]) -> dict[str, Any]:
    """Stable runnable identity, excluding the boot time of each distinct process."""

    result = {field: stamp[field] for field in BUILD_FIELDS if field in stamp}
    if not all(result.get(field) for field in BUILD_FIELDS[:4]):
        raise WorklistProcessError("reviewer worklist source identity is unavailable")
    return result


def request_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _environment(source: Mapping[str, Any]) -> dict[str, str]:
    root = Path(agents_remember.__file__).resolve().parent
    if (
        source["packageRoot"] != root.as_posix()
        or source["pythonExecutable"] != Path(sys.executable).resolve().as_posix()
    ):
        raise WorklistProcessError(
            "reviewer worklist selected source or interpreter disagrees with its parent"
        )
    if not (root / "application/reviewer_worklist_child.py").is_file():
        raise WorklistBuildMismatch(
            "reviewer worklist child is unavailable in the selected package; the installed "
            "package changed under this running dashboard"
        )
    # One exact import root, also for source-only launches; never guess an installed alternative.
    return {**os.environ, "PYTHONPATH": root.parent.as_posix(), PARENT_PID_ENV: str(os.getpid())}


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Stop the entire native POSIX group; SIGTERM lets subprocess.run reap its Git child."""

    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=0.5)
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _invalid_number(number: str) -> None:
    raise ValueError(f"invalid JSON number: {number}")


def _finite_float(number: str) -> float:
    value = float(number)
    if not math.isfinite(value):
        _invalid_number(number)
    return value


def _reply(data: bytes, request: Mapping[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(data, parse_constant=_invalid_number, parse_float=_finite_float)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise WorklistProcessError(
            "reviewer worklist child returned malformed or truncated JSON"
        ) from error
    if not isinstance(value, dict) or set(value) != {
        "operation",
        "request",
        "source",
        "module",
        "pid",
        "document",
        "reads",
        "computation",
        "error",
    }:
        raise WorklistProcessError("reviewer worklist child returned an invalid protocol")
    expected_module = str(
        Path(request["source"]["packageRoot"]) / "application/reviewer_worklist_child.py"
    )
    if value["operation"] != OPERATION or value["request"] != request["request"]:
        raise WorklistProcessError(
            "reviewer worklist child build, module or input identity disagrees"
        )
    if value["source"] != request["source"] or value["module"] != expected_module:
        raise WorklistBuildMismatch(
            "reviewer worklist child ran a different build or module than this dashboard booted "
            "with; the installed package changed under this running dashboard"
        )
    if not isinstance(value["pid"], int) or value["pid"] == os.getpid():
        raise WorklistProcessError(
            "reviewer worklist computation did not identify a distinct process"
        )
    times = value["computation"]
    if (
        not isinstance(times, list)
        or len(times) != 2
        or not all(type(item) is float and math.isfinite(item) for item in times)
        or not 0 <= times[0] <= times[1] <= time.monotonic()
    ):
        raise WorklistProcessError(
            "reviewer worklist child returned invalid computation observations"
        )
    reads = value["reads"]
    if not isinstance(reads, dict) or any(
        not isinstance(path, str)
        or not isinstance(identity, str)
        or not valid_observation(path, identity)
        for path, identity in reads.items()
    ):
        raise WorklistProcessError("reviewer worklist child returned invalid actual-read evidence")
    _validate_document(value["document"], reads, value["error"])
    return value


def _validate_document(document: Any, reads: dict[str, str], error: Any) -> None:
    if error is not None and (not isinstance(error, str) or not error or document is not None):
        raise WorklistProcessError("reviewer worklist child returned an invalid computation error")
    if document is not None and (
        not isinstance(document, dict)
        or document.get("schema") != "knowledge-worklist/v1"
        or document.get("state") not in ("complete", "incomplete")
        or not isinstance(document.get("pairing"), dict)
        or any(
            not isinstance(document.get(field), list)
            or any(not isinstance(item, dict) for item in document[field])
            for field in ("items", "incomplete")
        )
    ):
        raise WorklistProcessError("reviewer worklist child returned an invalid worklist")
    if document is not None:
        if document.get("state") == "complete" and not reads:
            raise WorklistProcessError(
                "reviewer worklist child omitted required actual-read evidence"
            )
        for field in ("changes",):
            if field in document and (
                not isinstance(document[field], list)
                or any(not isinstance(item, dict) for item in document[field])
            ):
                raise WorklistProcessError(
                    "reviewer worklist child returned invalid change records"
                )


@dataclass
class _Flight:
    """One computation in progress, shared by every request that asked for the same digest."""

    digest: str
    created: float
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    waiters: int = 0
    running: bool = False
    shared: bool = False
    result: dict[str, Any] | None = None
    error: WorklistProcessError | None = None


class ReviewerWorklistProcesses:
    """The route's bounded active processes and their flights, to admit, share, stop and reap."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._active: set[subprocess.Popen[bytes]] = set()
        self._flights: dict[str, _Flight] = {}
        self._queue: deque[_Flight] = deque()
        self._waiting = 0
        self._stopped = threading.Event()

    def shutdown(self) -> None:
        with self._changed:
            self._stopped.set()
            active = tuple(self._active)
            self._changed.notify_all()
        for process in active:
            _stop(process)

    def compute(self, payload: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        started = _ARRIVAL.get() or time.monotonic()
        request = {"operation": OPERATION, "source": source, "payload": payload}
        request["request"] = request_digest(request)
        environment = _environment(source)
        flight = self._join(request, environment, started)
        try:
            value = self._await(flight, started)
        finally:
            if self._leave(flight):
                # The last requester left an unfinished flight: it is stopped, and this request
                # does not end before the child and its Git children are reaped.
                flight.done.wait(_REAP_SECONDS)
        # Requests that shared a computation each own their copy of its one validated answer.
        return copy.deepcopy(value) if flight.shared else value

    def _join(
        self, request: dict[str, Any], environment: dict[str, str], started: float
    ) -> _Flight:
        digest = request["request"]
        with self._changed:
            if self._stopped.is_set():
                raise WorklistProcessError("reviewer worklist dashboard is shutting down")
            flight = self._flights.get(digest)
            if flight is not None and flight.cancel.is_set():
                flight = None  # its last requester left: it only awaits its reaping, never a joiner
            if flight is None:
                # The waiting bound counts computations, not requests that share one.
                if len(self._flights) >= MAX_WAITING_COMPUTATIONS:
                    raise WorklistOverloaded(
                        f"reviewer worklist is overloaded: {len(self._flights)} computations are "
                        "already running or waiting"
                    )
                flight = self._flights[digest] = _Flight(digest, started)
                try:
                    threading.Thread(
                        target=self._fly,
                        args=(flight, request, environment),
                        name=f"reviewer-worklist-{digest[:8]}",
                        daemon=True,
                    ).start()
                except RuntimeError as error:
                    # This publisher still holds the registry lock; no waiter can join a flight
                    # whose thread never started, and no child exists to reap.
                    if self._flights.get(digest) is flight:
                        del self._flights[digest]
                    flight.error = WorklistProcessError(
                        f"reviewer worklist thread could not start: {error}"
                    )
                    flight.done.set()
                    self._changed.notify_all()
                    raise flight.error from error
            flight.shared = flight.shared or flight.waiters > 0
            flight.waiters += 1
            self._waiting += 1
            return flight

    def _leave(self, flight: _Flight) -> bool:
        """Leave the flight; ``True`` when this was its last requester and it is unfinished."""

        with self._changed:
            flight.waiters -= 1
            self._waiting -= 1
            abandoned = flight.waiters == 0 and not flight.done.is_set()
            if abandoned:
                flight.cancel.set()  # nobody is left to want the answer
                self._changed.notify_all()
            return abandoned

    def _await(self, flight: _Flight, started: float) -> dict[str, Any]:
        cancellation = _CANCELLATION.get()
        while not flight.done.wait(_POLL_SECONDS):
            if self._stopped.is_set() or (cancellation is not None and cancellation.is_set()):
                raise WorklistProcessError("reviewer worklist computation was cancelled")
            if time.monotonic() - started >= DEADLINE_SECONDS:
                raise _deadline(waiting=not flight.running)
        if flight.error is not None:
            raise type(flight.error)(*flight.error.args)
        assert flight.result is not None
        return flight.result

    def _fly(self, flight: _Flight, request: dict[str, Any], environment: dict[str, str]) -> None:
        """Admit, run and reap one child for every request that shares the flight."""

        process = None
        try:
            process = self._admit(flight, environment)
            value = _reply(self._collect(process, json.dumps(request).encode(), flight), request)
            if value["pid"] != process.pid:
                raise WorklistProcessError("reviewer worklist child process identity disagrees")
            flight.result = value
        except WorklistProcessError as error:
            flight.error = error
        except (OSError, subprocess.SubprocessError) as error:
            flight.error = WorklistProcessError(
                f"reviewer worklist process failed: {type(error).__name__}: {error}"
            )
        except Exception as error:  # a waiter must always be answered, never left to its deadline
            flight.error = WorklistProcessError(
                f"reviewer worklist computation failed: {type(error).__name__}: {error}"
            )
        finally:
            if process is not None:
                _stop(process)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
            with self._changed:
                if process is not None:
                    self._active.discard(process)
                if self._flights.get(flight.digest) is flight:
                    del self._flights[flight.digest]
                flight.done.set()
                self._changed.notify_all()

    def _admit(self, flight: _Flight, environment: dict[str, str]) -> subprocess.Popen[bytes]:
        """Wait first in, first out for one of the child slots, then start the child."""

        with self._changed:
            self._queue.append(flight)
            try:
                while True:
                    if self._stopped.is_set():
                        raise WorklistProcessError("reviewer worklist dashboard is shutting down")
                    if flight.cancel.is_set():
                        raise WorklistProcessError("reviewer worklist computation was cancelled")
                    remaining = DEADLINE_SECONDS - (time.monotonic() - flight.created)
                    if remaining <= min(_MINIMUM_RUN_SECONDS, DEADLINE_SECONDS / 4):
                        raise _deadline(waiting=True)  # too late to start a computation
                    if len(self._active) < MAX_ACTIVE_CHILDREN and self._queue[0] is flight:
                        process = self._launch(environment)
                        flight.running = True
                        return process
                    self._changed.wait(min(remaining, _POLL_SECONDS))
            finally:
                self._queue.remove(flight)
                self._changed.notify_all()

    def _launch(self, environment: dict[str, str]) -> subprocess.Popen[bytes]:
        try:
            process = subprocess.Popen(
                [sys.executable, "-P", "-m", CHILD_MODULE],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                start_new_session=True,
            )
        except OSError as error:
            raise WorklistProcessError(f"reviewer worklist launch failed: {error}") from error
        self._active.add(process)
        return process

    def _collect(self, process: subprocess.Popen[bytes], data: bytes, flight: _Flight) -> bytes:
        first = True
        while True:
            if self._stopped.is_set() or flight.cancel.is_set():
                raise WorklistProcessError("reviewer worklist computation was cancelled")
            remaining = DEADLINE_SECONDS - (time.monotonic() - flight.created)
            if remaining <= 0:
                raise _deadline(waiting=False)
            try:
                output, errors = process.communicate(
                    data if first else None, timeout=min(_POLL_SECONDS * 2, remaining)
                )
            except subprocess.TimeoutExpired:
                first = False
                continue
            if process.returncode == EXIT_BUILD_MISMATCH:
                raise WorklistBuildMismatch(
                    "reviewer worklist child imported a different build than this dashboard "
                    "booted with; the installed package changed under this running dashboard"
                )
            if process.returncode:
                raise WorklistProcessError(
                    f"reviewer worklist child exited {process.returncode}: "
                    f"{errors.decode(errors='replace')[:1000]}"
                )
            return output


def _deadline(*, waiting: bool) -> WorklistProcessError:
    """The deadline's answer: overload when the request never got a child, else a failure."""

    if waiting:
        return WorklistOverloaded(
            f"reviewer worklist could not start within its {DEADLINE_SECONDS} second deadline: "
            "too little of the request's lifetime remained to start its child"
        )
    return WorklistProcessError(
        f"reviewer worklist exceeded its {DEADLINE_SECONDS} second deadline"
    )


def _kill_group(*_signal: object) -> None:
    """End this child and, with it, every Git process it started (its own process group)."""

    if os.getpgrp() == os.getpid():  # the launcher gave this child its own session and group
        with suppress(ProcessLookupError):
            os.killpg(os.getpgrp(), signal.SIGKILL)
    os._exit(1)  # run by hand from a shell: the shell's group is not this child's to kill


def _watch_parent(parent: int) -> None:
    while os.getppid() == parent:
        time.sleep(1)
    _kill_group()


def arm_child_lifetime() -> None:
    """The child's first act: it must not outlive the dashboard that asked for it.

    * ``SIGTERM`` (from the parent's stop, or the kernel's parent-death signal) and ``SIGALRM`` (the
      child's own deadline) end the child's whole process group, Git children included.
    * Linux asks the kernel for the parent-death signal (``prctl(PR_SET_PDEATHSIG)``). Other
      platforms have none, so a watcher thread polls the parent instead.
    * A parent that already died before this call is noticed by comparing with the pid it recorded.
    """

    signal.signal(signal.SIGTERM, _kill_group)
    signal.signal(signal.SIGALRM, _kill_group)
    signal.setitimer(signal.ITIMER_REAL, CHILD_DEADLINE_SECONDS)
    parent = int(os.environ.get(PARENT_PID_ENV) or 0)
    if sys.platform.startswith("linux"):
        if ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0):
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
    elif parent:
        threading.Thread(target=_watch_parent, args=(parent,), daemon=True).start()
    if parent and os.getppid() != parent:
        _kill_group()
