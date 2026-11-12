"""Build the explainer seat and send its bound review request.

MIK-R186 owns the bytes (:mod:`agents_remember.canvas.explainer_workspace`) and the records
(:mod:`agents_remember.canvas.explainer_state`); this module owns the *order* they happen in, which
is where the packet's promises live:

* one environment per leaf, built only from a Send, under the leaf's one lock (rules 1 and 2);
* the seat's id derived from the workspace and a generation counter that only grows, so a retired or
  archived seat is never revived (rule 5);
* the first message sent *after* the lock is released, so the held store's lock never nests under
  the leaf lock;
* a build after master retirement or sprint archive is refused as ``task-archived`` (rule 1).

Existing-seat updates, inspection, retirement and cancellation live in
:mod:`agents_remember.canvas.explainer_maintenance`.

The host, the comparison owner and the packet preparation are handed in, so the whole order is
exercised without a Paseo host, a Git comparison or a real evidence packet.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypedDict, Unpack

from agents_remember.canvas.explainer_comparison_inputs import (
    BoundComparison,
    LeafFacts,
    LeafScope,
    PreparedPacket,
)
from agents_remember.canvas.explainer_paths import (
    LOCK_RESOURCE,
    binding_path,
    cleanup_workspace_tree,
    compose_request_message,
    generation_path,
    guard_explainer_path,
    leaf_lock_resource,
    leaf_workspace,
    lock_path,
    repository_path,
)
from agents_remember.canvas.explainer_reopen import archived_refusal, archived_reopen
from agents_remember.canvas.explainer_staleness import (
    BlockFingerprint,
)
from agents_remember.canvas.explainer_state import (
    BUSY,
    ExplainerBinding,
    ExplainerRefused,
    RetireReceipt,
    agent_id,
    end_processes,
    generation,
    ledger_entry,
    ledger_path,
    mint_seat_attempt,
    process_tree,
    raise_generation,
    read_binding,
    read_inflight_create,
    record_request,
    request_message_id,
    restore_generation,
    seat_attempt,
    write_inflight_create,
)
from agents_remember.canvas.explainer_workspace import (
    EnvironmentBuild,
    EnvironmentInputs,
    EnvironmentRun,
    build_environment,
)
from agents_remember.kernel.file_lock import LockAcquisitionTimeout, exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_frame import FrameBinding

#: What an empty Send asks for. The dashboard's `explainer/model.ts` states the same ask in
#: `END_TO_END_LINE` for the form's line, so the words the developer reads and the seat's first turn
#: cannot drift apart (MIK-R211 v2 rule 5, the developer's F1).
END_TO_END_REQUEST = (
    "Explain this change set end to end: what changed, which invariants it touches and how each one "
    "still holds, and where the evidence for each claim is."
)

__all__ = [
    "END_TO_END_REQUEST",
    "BoundComparison",
    "ExplainerDeps",
    "ExplainerHost",
    "ExplainerState",
    "HostReply",
    "LeafFacts",
    "LeafScope",
    "PreparedPacket",
    "SeatOutcome",
    "build_seat",
    "cancel_build",
    "request_message_id",
]

#: The reasons this module answers with, each naming the state the caller is in.
IN_TURN = "explainer-in-turn"
RETIRED = "explainer-retired"
MISSING = "explainer-missing"
ARCHIVED = "task-archived"
SEAT_LIVE = "live"
SEAT_CLOSED = "closed"
SEAT_ARCHIVED = "archived"
SEAT_MISSING = "missing"
#: A seat in one of these answers its chat; the next Send is a later request, not a new seat.
RESUMABLE = (SEAT_LIVE, SEAT_CLOSED)
#: The lock names its resource in the kernel's diagnostics; one name for build, Update and Retire.
# LOCK_RESOURCE is imported from the paths module: the seat's own grade writers hold the same file.
MAX_LEDGER_ENTRIES = 32


@dataclass(frozen=True)
class SeatOutcome:
    """One Send's answer: the environment, the seat, and what it did."""

    leaf: LeafScope
    workspace: Path
    generation: int
    agent_id: str
    binding: FrameBinding | None
    seat: str
    built: bool
    current: bool
    duplicate: bool
    message_sent: bool
    steps: tuple[str, ...] = ()
    code: dict[str, Any] | None = None
    memory: dict[str, Any] | None = None
    code_count: int = 0
    memory_count: int = 0
    delivery: str = "delivered"
    message_id: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "leaf": {
                "repository": self.leaf.repository_id,
                "task": self.leaf.task,
                "leaf": self.leaf.leaf_id,
            },
            "workspace": str(self.workspace),
            "generation": self.generation,
            "agentId": self.agent_id,
            "binding": None if self.binding is None else self.binding.model_dump(mode="json"),
            "seat": self.seat,
            "built": self.built,
            "current": self.current,
            "duplicate": self.duplicate,
            "messageSent": self.message_sent,
            "delivery": self.delivery,
            "messageId": self.message_id,
            "steps": list(self.steps),
            "code": self.code,
            "memory": self.memory,
        }


@dataclass(frozen=True)
class ExplainerState:
    """What the review page reads: the environment, its binding and the seat (rule 5)."""

    leaf: LeafScope
    workspace: Path
    present: bool
    generation: int
    agent_id: str
    binding: ExplainerBinding | None
    seat: str
    retired: dict[str, Any] | None
    receipt: RetireReceipt | None
    workspace_id: str = ""
    building: dict[str, Any] | None = None

    def as_record(self) -> dict[str, Any]:
        ws_id = self.workspace_id or (
            str(self.workspace) if self.present or self.generation else ""
        )
        label = (
            None
            if self.binding is None
            else (
                "planned"
                if self.binding.binding.comparison_owner == "planned"
                else f"#{self.binding.binding.comparison_number}"
            )
        )
        return {
            "leaf": {
                "repository": self.leaf.repository_id,
                "task": self.leaf.task,
                "leaf": self.leaf.leaf_id,
            },
            "workspace": str(self.workspace),
            "workspaceId": ws_id,
            "present": self.present,
            "generation": self.generation,
            "agentId": self.agent_id,
            "binding": None if self.binding is None else self.binding.model_dump(mode="json"),
            "label": label,
            "seat": self.seat,
            "retired": self.retired,
            "receipt": None if self.receipt is None else self.receipt.model_dump(mode="json"),
            "building": self.building,
        }


class HostReply(Protocol):
    """One host answer, read by the field names it states.

    A reply is a mapping of the host's own facts. This service reads one field of one answer --
    a send's delivery ``status`` -- and takes a create or a resume as done when it returns.
    """

    def get(self, key: str, /) -> object: ...


class ExplainerHost(Protocol):
    """What this module asks of the host: a seat with no turn, a message, an archive, its state.

    Every method that answers more than an acknowledgement answers a mapping of the host's own
    named facts. This service never reads past the seat it made (``agentId``) and one send's
    delivery ``status``: a create and a resume are taken as done when the host returns, and a send
    is held only when its reply says so.
    """

    def create_seat(
        self,
        *,
        agent_id: str,
        workspace: Path,
        title: str,
        capsule: str,
        labels: Mapping[str, str],
    ) -> HostReply | None: ...

    def send(
        self, agent_id: str, text: str, *, message_id: str | None = None
    ) -> HostReply | None: ...

    def resume(self, agent_id: str) -> HostReply | None: ...

    def cancel(self, agent_id: str, *, request_id: str) -> HostReply | None: ...

    def archive(self, agent_id: str) -> None: ...

    def seat_state(self, agent_id: str) -> str: ...

    def in_turn(self, agent_id: str) -> bool: ...


@dataclass(frozen=True)
class ExplainerDeps:
    """Everything one build, Update or Retire is composed with."""

    config: McpRuntimeConfig
    host: ExplainerHost
    #: The leaf's stage and whether its master is archived (rule 1).
    facts: Callable[[LeafScope], LeafFacts]
    #: The comparison the environment binds: recorded, or planned for a ``planning`` leaf (rule 4).
    comparison: Callable[[LeafScope, LeafFacts], BoundComparison]
    #: The MIK-R102 packet for that comparison, through MIK-R185 v2's repaired path.
    prepare: Callable[[LeafScope, LeafFacts, BoundComparison], PreparedPacket]
    #: The capsule the seat is started with (MIK-R105 v2 rule 12).
    capsule: Callable[[LeafScope], str]
    #: Re-observe the bound comparison inputs without composing another review view.
    recheck: Callable[[LeafScope, LeafFacts, BoundComparison], bool]
    #: The block counter an Update's answer uses (rule 6 step 3), injected by the composition root
    #: so this module never imports `serving`, which imports it.
    counter: Callable[[Any, Any], int | None] | None = None
    now: Callable[[], str] | None = None
    #: How long a build or Retire waits for the leaf lock; Update never waits.
    lock_timeout: float | None = 30.0
    present_references: Callable[[LeafScope, FrameBinding], Collection[str] | None] | None = None
    grade_fingerprints: (
        Callable[[LeafScope, FrameBinding], Mapping[str, BlockFingerprint]] | None
    ) = None

    def stamp(self) -> str:
        if self.now is None:
            return datetime.now(UTC).isoformat()
        return self.now()


_IN_FLIGHT_LOCK = threading.Lock()
_IN_FLIGHT_SENDS: dict[tuple[str, str], threading.Event] = {}
_BUILD_STATES: dict[tuple[str, str, str], dict[str, Any]] = {}
# One event per leaf's live build. A step reports it is still going, and a cancel or a stall sets
# it: the builder raises at its next checkpoint instead of running to the end of a snapshot it can
# no longer serve (MIK-R211 v2 rules 4 and 5).
_BUILD_CANCELS: dict[tuple[str, str, str], threading.Event] = {}
_BUILD_WATCH_LOCK = threading.Lock()
_BUILD_WATCHDOG: threading.Thread | None = None
# A build that stops reporting is failed with its step and reason rather than left "building"
# forever. The window sits well past the longest legitimate silent stretch: every snapshot reports
# by chunk now, so a real build touches its record every few seconds.
BUILD_STALL_SECONDS = 300.0
BUILD_WATCH_POLL_SECONDS = 5.0


def _ledger_path(config: McpRuntimeConfig, scope: LeafScope) -> Path:
    return ledger_path(config, scope.repository_id, scope.task, scope.leaf_id)


_ledger_entry = ledger_entry
_record_request = record_request
_read_inflight_create = read_inflight_create
_write_inflight_create = write_inflight_create


def _workspace(config: McpRuntimeConfig, scope: LeafScope) -> Path:
    return leaf_workspace(config, scope.repository_id, scope.task, scope.leaf_id)


def _seat(config: McpRuntimeConfig, scope: LeafScope, number: int) -> str:
    """The seat this generation made: its id carries the attempt that minted it (rule 5)."""

    workspace = _workspace(config, scope)
    return agent_id(workspace, number, seat_attempt(workspace))


def _checked_facts(deps: ExplainerDeps, scope: LeafScope) -> LeafFacts:
    # R103 v2: an archive reclaimed this explainer's revisions and counter; its receipt says why
    # no reopen follows, and the build refuses with that before any counter could start again.
    archived = archived_reopen(_workspace(deps.config, scope))
    if archived is not None:
        raise ExplainerRefused(ARCHIVED, archived)
    facts = deps.facts(scope)
    if facts.archived:
        raise ExplainerRefused(ARCHIVED, archived_refusal(scope.leaf_id, facts.archive_receipt))
    return facts


@contextmanager
def _lock(deps: ExplainerDeps, scope: LeafScope, *, wait: bool) -> Iterator[None]:
    """The leaf's one lock: a build and Retire wait, an Update refuses at once (rules 6, 8)."""

    lock_file = lock_path(deps.config, scope.repository_id, scope.task, scope.leaf_id)
    guard_explainer_path(lock_file, root=deps.config.coordination_root, label="explainer lock")
    resource = leaf_lock_resource(lock_file)
    deadline = (
        None
        if not wait
        else (None if deps.lock_timeout is None else time.monotonic() + deps.lock_timeout)
    )
    if not wait:
        deadline = 0.0
    try:
        with exclusive_file_lock(resource, LOCK_RESOURCE, deadline=deadline):
            yield
    except LockAcquisitionTimeout as error:
        raise ExplainerRefused(BUSY, str(error)) from error


#: How many ids one create may name before it reports the host's refusal: a spent id, and two more.
_SEAT_ATTEMPTS = 3


def _attempt_for(request_id: str | None) -> str:
    """The attempt a create names, derived from the request that asked for it.

    Derived, not minted: a retry of the same request must reconcile the seat that request may
    already have made (a lost reply is not a new attempt), while a different request -- the
    developer pressing Send again -- names a seat the host has never seen. A create the host refuses
    because it already holds that id moves to the next attempt suffix below.
    """

    seed = request_id or uuid.uuid4().hex
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _create_seat(  # noqa: PLR0913 - one seat: its leaf, generation, workspace, request and attempt
    deps: ExplainerDeps,
    scope: LeafScope,
    number: int,
    workspace: Path,
    *,
    request_id: str | None = None,
    attempt: str | None = None,
) -> str:
    # Every create attempt gets its own id: the host never forgets a seat id it has seen (an
    # archived one included), so reusing one turns a retry into the same refusal forever
    # (MIK-R211 v2 rule 6, and the L183 outcome). The attempt is recorded before the create, so a
    # later read, resume or retire derives the id the seat really has.
    base = attempt or _attempt_for(request_id)
    labels: dict[str, str] = {
        "ar.capability": "review-explainer",
        "ar.repository": scope.repository_id,
        "ar.task": scope.task,
        "ar.explainer-generation": str(number),
    }
    if request_id:
        labels["ar.request"] = request_id
    held: ExplainerRefused | None = None
    for tries in range(_SEAT_ATTEMPTS):
        # The host never releases a seat id it has seen -- an archived seat's included -- so a
        # refused id is followed by the next attempt suffix, which names an id it has never seen
        # (MIK-R211 v2 rule 6, the L183 outcome).
        name = base if tries == 0 else f"{base}.{tries + 1}"
        seat = agent_id(workspace, number, mint_seat_attempt(workspace, name))
        labels["ar.explainer-attempt"] = name
        try:
            deps.host.create_seat(
                agent_id=seat,
                workspace=workspace,
                title=f"Explainer · {scope.leaf_id}",
                capsule=deps.capsule(scope),
                labels=labels,
            )
        except ExplainerRefused as refused:
            if refused.reason != "explainer-seat-held":
                raise
            held = refused
            continue
        return seat
    assert held is not None
    raise held


def _build_environment(
    deps: ExplainerDeps,
    scope: LeafScope,
    bound: BoundComparison,
    prepared: PreparedPacket,
    progress: Callable[[str, Mapping[str, Any]], None] | None,
) -> EnvironmentBuild:
    inputs = EnvironmentInputs(
        binding=bound.binding,
        packet=prepared.packet,
        capsule=deps.capsule(scope),
        code_repository=repository_path(deps.config, scope.repository_id),
        memory_repository=repository_path(deps.config, scope.repository_id, memory=True),
        planned=bound.planned,
        record_digest=bound.record_digest,
    )
    return build_environment(
        EnvironmentRun(
            ref=scope.environment(deps.config),
            inputs=inputs,
            now=deps.stamp(),
            progress=progress,
        )
    )


_PRE_CREATE_REFUSALS = frozenset(
    {
        "explainer-seat-label-refused",
        "explainer-repository-unknown",
        "explainer-seat-config-unapplied",
        "explainer-binding-invalid",
        "explainer-launch-incomplete",
    }
)


def _is_lost_create_reply(deps: ExplainerDeps, seat: str, error: BaseException) -> bool:
    if isinstance(error, ExplainerRefused) and error.reason in _PRE_CREATE_REFUSALS:
        with suppress(Exception):
            deps.host.archive(seat)
        return False
    try:
        state = deps.host.seat_state(seat)
    except Exception:
        return True
    if state in (SEAT_LIVE, SEAT_CLOSED):
        return True
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    return isinstance(error, ExplainerRefused) and any(
        token in error.reason for token in ("timeout", "unreachable", "unknown")
    )


def _reclaim_old_seat(deps: ExplainerDeps, workspace: Path, old_seat: str) -> None:
    """Archive an old seat and end every surviving process before minting a new generation."""

    tree = process_tree(workspace, old_seat)
    try:
        deps.host.archive(old_seat)
    except ExplainerRefused:
        raise
    except Exception as error:
        raise ExplainerRefused("explainer-archive-failed", str(error)) from error
    _by_m, _by_t, remaining = end_processes(tree, workspace=workspace, agent=old_seat)
    if remaining:
        raise ExplainerRefused(
            "explainer-processes-remain",
            f"process(es) {list(remaining)} of seat {old_seat} still answer alive; "
            "no new generation was made",
        )


def _send_to_host(deps: ExplainerDeps, seat: str, text: str, message_id: str) -> str:
    result = deps.host.send(seat, text, message_id=message_id)
    if isinstance(result, Mapping) and result.get("status") == "held":
        return "held"
    return "delivered"


_STEP_PERCENTS: dict[str, int] = {
    "workspace": 10,
    "binding": 20,
    "checkout-code": 45,
    "checkout-memory": 65,
    "packet": 75,
    "instructions": 85,
    "bound": 90,
    "seat": 95,
    "message": 100,
}


def _progress_tracker(
    scope: LeafScope,
    request_id: str,
    sink: Callable[[str, Mapping[str, Any]], None] | None,
) -> tuple[Callable[[str, Mapping[str, Any]], None], dict[str, Any]]:
    key = (scope.repository_id, scope.task, scope.leaf_id)
    started = time.monotonic()
    state: dict[str, Any] = {
        "state": "building",
        "requestId": request_id,
        "step": "workspace",
        "percent": 0,
        "steps": [],
        "failedStep": None,
        "startedAt": started,
        "updatedAt": started,
    }
    _BUILD_STATES[key] = state
    cancel = threading.Event()
    _BUILD_CANCELS[key] = cancel

    def report(name: str, facts: Mapping[str, Any]) -> None:
        if cancel.is_set():
            # The developer's Cancel, or the watchdog ending an attempt that stopped reporting. The
            # checkpoint is here because each real build stage comes through it, so a
            # build stops within one chunk instead of running a snapshot nobody will read.
            raise ExplainerRefused(
                "build-cancelled",
                f"the build stopped at {state['step']} ({state['percent']}%): "
                "the developer ended it, or it stopped reporting",
            )
        pct = _STEP_PERCENTS.get(name, state["percent"])
        state["step"] = name
        state["percent"] = pct
        state["updatedAt"] = time.monotonic()
        state["steps"].append({"name": name, "percent": pct, **dict(facts)})
        if sink is not None:
            sink(name, facts)

    return report, state


def _end_live_build(
    key: tuple[str, str, str], *, reason: str, failed_step: str | None = None
) -> dict[str, Any] | None:
    """End one live build's record and stop its thread at the next checkpoint.

    Pops nothing: ``state_of`` keeps answering the ended record until the leaf builds again, so the
    page that pressed Cancel keeps reading what happened rather than falling back to the last good
    revision (MIK-R211 v2 rule 4).
    """

    state = _BUILD_STATES.get(key)
    if state is None or state.get("state") != "building":
        return None
    state["state"] = "failed"
    state["failedStep"] = failed_step or state.get("step") or "workspace"
    state["error"] = reason
    state["endedAt"] = time.monotonic()
    cancel = _BUILD_CANCELS.get(key)
    if cancel is not None:
        cancel.set()
    return dict(state)


def cancel_build(
    deps: ExplainerDeps,  # noqa: ARG001 - the route hands every explainer call the same deps
    scope: LeafScope,
    *,
    request_id: str | None = None,
) -> dict[str, Any] | None:
    """End this leaf's live build, the developer's own hand (MIK-R105 v1 rule 9, MIK-R211 v2 rule 5).

    Answers ``None`` when no build is live for the leaf, or when a named request id belongs to a
    different attempt, so a stale page cannot end a build it is not looking at.
    """

    key = (scope.repository_id, scope.task, scope.leaf_id)
    state = _BUILD_STATES.get(key)
    if state is None or state.get("state") != "building":
        return None
    if request_id and state.get("requestId") not in (None, request_id):
        return None
    return _end_live_build(
        key,
        reason=(
            "build-cancelled: the developer ended this attempt; the workspace is kept for the "
            "next one"
        ),
    )


def _fail_stalled_builds(
    *, now: float | None = None, stall_seconds: float = BUILD_STALL_SECONDS
) -> list[dict[str, Any]]:
    """Fail every build that has not reported for longer than the stall window.

    A build that died (its thread ended on a BaseException the request never saw) or wedged in one
    blocking call would otherwise answer "building" forever with no reason on the page. This turns
    that silence into one named failure per attempt and stops the builder if it ever resumes.
    """

    moment = time.monotonic() if now is None else now
    ended: list[dict[str, Any]] = []
    for key, state in list(_BUILD_STATES.items()):
        if state.get("state") != "building":
            continue
        last = state.get("updatedAt")
        if not isinstance(last, (int, float)):
            continue
        quiet = moment - float(last)
        if quiet < stall_seconds:
            continue
        record = _end_live_build(
            key,
            reason=(
                f"build-stalled: no progress for {int(quiet)} s at {state.get('step')} "
                f"({state.get('percent')}%); the attempt stopped reporting, so it is ended here. "
                "Press Send to try again."
            ),
        )
        if record is not None:
            ended.append(record)
    return ended


_LOGGER = logging.getLogger(__name__)


def _watch_builds() -> None:
    while True:
        time.sleep(BUILD_WATCH_POLL_SECONDS)
        try:
            for record in _fail_stalled_builds():
                _LOGGER.warning(
                    "explainer build %s ended: %s",
                    record.get("requestId"),
                    record.get("error"),
                )
        except Exception:  # a watcher never takes the process down with it
            _LOGGER.exception("the explainer build watchdog could not read the build records")


def _ensure_build_watchdog() -> None:
    """Start the one watchdog thread the first time a build runs in this process."""

    global _BUILD_WATCHDOG  # noqa: PLW0603 - one watchdog per process, guarded by the lock below
    with _BUILD_WATCH_LOCK:
        if _BUILD_WATCHDOG is not None and _BUILD_WATCHDOG.is_alive():
            return
        _BUILD_WATCHDOG = threading.Thread(
            target=_watch_builds, name="explainer-build-watchdog", daemon=True
        )
        _BUILD_WATCHDOG.start()


@dataclass(frozen=True)
class _SendContext:
    deps: ExplainerDeps
    scope: LeafScope
    request_id: str
    msg_id: str
    ledger: Path
    workspace: Path
    inflight_key: tuple[str, str]
    comparison_number: int | None
    reporter_sink: Callable[[str, Mapping[str, Any]], None]


def _acquire_inflight_send(inflight_key: tuple[str, str], timeout: float) -> threading.Event:
    own_event = threading.Event()
    with _IN_FLIGHT_LOCK:
        wait_event = _IN_FLIGHT_SENDS.get(inflight_key)
        if wait_event is None:
            _IN_FLIGHT_SENDS[inflight_key] = own_event
    if wait_event is not None:
        wait_event.wait(timeout=timeout)
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT_SENDS[inflight_key] = own_event
    return own_event


def _deliver_prepared_turn(
    ctx: _SendContext,
    composed: str,
    prepared: tuple[
        int,
        str,
        ExplainerBinding | None,
        EnvironmentBuild | None,
        tuple[str, ...],
        bool,
        bool,
    ],
) -> SeatOutcome:
    deps, scope = ctx.deps, ctx.scope
    number, seat, existing, build, steps, current, closed = prepared
    try:
        if closed:
            deps.host.resume(seat)
        delivery = _send_to_host(deps, seat, composed, ctx.msg_id)
        ctx.reporter_sink("message", {"messageId": ctx.msg_id, "delivery": delivery})
    except BaseException as error:
        with _lock(deps, scope, wait=True):
            _record_request(
                ctx.ledger,
                scope.leaf_id,
                ctx.request_id,
                {
                    "generation": number,
                    "agentId": seat,
                    "messageId": ctx.msg_id,
                    "at": deps.stamp(),
                    "built": build is not None,
                    "attached": current,
                    "delivery": "failed",
                    "error": str(error),
                },
            )
        raise
    with _lock(deps, scope, wait=True):
        _record_request(
            ctx.ledger,
            scope.leaf_id,
            ctx.request_id,
            {
                "generation": number,
                "agentId": seat,
                "messageId": ctx.msg_id,
                "at": deps.stamp(),
                "built": build is not None,
                "attached": current,
                "delivery": delivery,
            },
        )
    return SeatOutcome(
        leaf=scope,
        workspace=ctx.workspace,
        generation=number,
        agent_id=seat,
        binding=None if existing is None else existing.binding,
        seat=deps.host.seat_state(seat),
        built=build is not None,
        current=current,
        duplicate=False,
        message_sent=delivery == "delivered",
        steps=(*steps, "seat", "message") if steps else (),
        code=None if build is None or build.code is None else build.code.as_record(),
        memory=None if build is None or build.memory is None else build.memory.as_record(),
        code_count=0 if build is None or build.code is None else build.code.files,
        memory_count=0 if build is None or build.memory is None else build.memory.files,
        delivery=delivery,
        message_id=ctx.msg_id,
    )


class _SendOptions(TypedDict, total=False):
    """The request fields a Send may carry beside its message, and the sink it reports through.

    The routes compose a request's picks and stale references into the message before they call
    the builder, and this module reads the same three shapes the composer does so a second caller
    cannot pass a value the message composer would not have taken.
    """

    history: str
    picks: Sequence[Mapping[str, object] | str]
    stale_part_ids: Sequence[str]
    stale_grades: Sequence[str]
    progress_callback: Callable[[str, Mapping[str, object]], None]
    comparison_number: int


def build_seat(
    deps: ExplainerDeps,
    scope: LeafScope,
    *,
    request_id: str,
    message: str = "",
    progress: Callable[[str, Mapping[str, Any]], None] | None = None,
    **options: Unpack[_SendOptions],
) -> SeatOutcome:
    """Build the leaf's environment and make its seat, then send the first message."""

    history = options.get("history")
    if history is not None and history not in {"live", "recorded", "planned"}:
        raise ExplainerRefused("request-invalid", f"unknown history mode {history!r}")
    composed = compose_request_message(
        message,
        picks=options.get("picks"),
        stale_part_ids=options.get("stale_part_ids"),
        stale_grades=options.get("stale_grades"),
    )
    if not composed.strip():
        # An empty request is not nothing to send: the form says "leave it empty for the end-to-end
        # review", so an empty Send is that review (MIK-R211 v2 rule 5). The words live in one place
        # so the seat's first turn and the form's line cannot drift apart.
        composed = END_TO_END_REQUEST
    _checked_facts(deps, scope)
    ledger = _ledger_path(deps.config, scope)
    workspace = _workspace(deps.config, scope)
    msg_id = request_message_id(scope.leaf_id, request_id)
    inflight_key = (str(workspace), request_id)
    own_event = _acquire_inflight_send(inflight_key, deps.lock_timeout or 30.0)
    effective_sink = progress or options.get("progress_callback")
    reporter_sink, build_state = _progress_tracker(scope, request_id, effective_sink)
    build_key = (scope.repository_id, scope.task, scope.leaf_id)
    _ensure_build_watchdog()
    ctx = _SendContext(
        deps=deps,
        scope=scope,
        request_id=request_id,
        msg_id=msg_id,
        ledger=ledger,
        workspace=workspace,
        inflight_key=inflight_key,
        comparison_number=options.get("comparison_number"),
        reporter_sink=reporter_sink,
    )
    try:
        prepared_turn = _prepare_seat_under_lock(ctx)
        if isinstance(prepared_turn, SeatOutcome):
            _BUILD_STATES.pop(build_key, None)
            return prepared_turn
        outcome = _deliver_prepared_turn(ctx, composed, prepared_turn)
        _BUILD_STATES.pop(build_key, None)
        return outcome
    except BaseException as error:
        build_state["state"] = "failed"
        build_state["failedStep"] = build_state.get("step") or "workspace"
        build_state["error"] = str(error)
        raise
    finally:
        with _IN_FLIGHT_LOCK:
            if _IN_FLIGHT_SENDS.get(inflight_key) is own_event:
                _IN_FLIGHT_SENDS.pop(inflight_key, None)
        own_event.set()
        # The attempt is over however it ended, so its cancel event no longer names anything. The
        # record stays for the page to read (rule 4) until the leaf builds again.
        _BUILD_CANCELS.pop(build_key, None)


def _prepare_seat_under_lock(
    ctx: _SendContext,
) -> (
    SeatOutcome
    | tuple[
        int,
        str,
        ExplainerBinding | None,
        EnvironmentBuild | None,
        tuple[str, ...],
        bool,
        bool,
    ]
):
    deps, scope = ctx.deps, ctx.scope
    gen_file = generation_path(deps.config, scope.repository_id, scope.task, scope.leaf_id)
    with _lock(deps, scope, wait=True):
        facts = _checked_facts(deps, scope)
        existing = read_binding(ctx.workspace) if ctx.workspace.is_dir() else None
        duplicate = _ledger_entry(ctx.ledger, scope.leaf_id, ctx.request_id)
        if duplicate is not None and duplicate.get("beforeCreate") is True:
            # This request failed without a host call; retry still owes the whole build.
            duplicate = None
        if duplicate is not None and duplicate.get("delivery") in {
            None,
            "delivered",
            "held",
        }:
            number = int(duplicate.get("generation") or generation(gen_file))
            seat = str(duplicate.get("agentId") or _seat(deps.config, scope, number))
            return SeatOutcome(
                leaf=scope,
                workspace=ctx.workspace,
                generation=number,
                agent_id=seat,
                binding=None if existing is None else existing.binding,
                seat=deps.host.seat_state(seat),
                built=False,
                current=True,
                duplicate=True,
                message_sent=False,
                delivery=str(duplicate.get("delivery") or "delivered"),
                message_id=str(duplicate.get("messageId") or ctx.msg_id),
            )
        if duplicate is not None and duplicate.get("delivery") in {"failed", "pending"}:
            number = int(duplicate.get("generation") or generation(gen_file))
            seat = str(duplicate.get("agentId") or _seat(deps.config, scope, number))
            state = deps.host.seat_state(seat)
            closed = state == SEAT_CLOSED
            return number, seat, existing, None, (), True, closed
        inflight = _read_inflight_create(ctx.ledger)
        if existing is not None and inflight is None:
            number, seat, current, closed = _attach_or_replace_existing(
                deps, scope, ctx.workspace, gen_file, ctx.request_id
            )
            build = None
            steps: tuple[str, ...] = ()
        else:
            number, seat, existing, build, steps = _initial_or_reconciled_build(
                ctx, facts, gen_file, inflight
            )
            current = False
            closed = False
        ctx.reporter_sink("seat", {"agentId": seat, "generation": number})
        _record_request(
            ctx.ledger,
            scope.leaf_id,
            ctx.request_id,
            {
                "generation": number,
                "agentId": seat,
                "messageId": ctx.msg_id,
                "at": deps.stamp(),
                "built": build is not None,
                "attached": current,
                "delivery": "pending",
            },
        )
        return number, seat, existing, build, steps, current, closed


def _attach_or_replace_existing(
    deps: ExplainerDeps,
    scope: LeafScope,
    workspace: Path,
    gen_file: Path,
    request_id: str,
) -> tuple[int, str, bool, bool]:
    number = generation(gen_file)
    seat = _seat(deps.config, scope, number)
    state = deps.host.seat_state(seat)
    if state in RESUMABLE:
        return number, seat, True, state == SEAT_CLOSED
    _reclaim_old_seat(deps, workspace, seat)
    prior_gen = number
    number = raise_generation(gen_file)
    try:
        try:
            seat = _create_seat(deps, scope, number, workspace, request_id=request_id)
        except ExplainerRefused as held:
            if held.reason != "explainer-seat-held":
                raise
            # The host already holds the id this generation's last attempt named, and it never
            # releases one; the next attempt under the same generation names a fresh id
            # (MIK-R211 v2 rule 6).
            seat = _create_seat(deps, scope, number, workspace, request_id=request_id)
    except BaseException:
        restore_generation(gen_file, prior_gen)
        raise
    return number, seat, False, False


def _record_precreate_failure(
    ctx: _SendContext, gen_file: Path, stage: str, error: BaseException
) -> None:
    """Persist only a completed failure that never reached host creation."""
    key = (ctx.scope.repository_id, ctx.scope.task, ctx.scope.leaf_id)
    progress = _BUILD_STATES.get(key, {})
    failed_step = progress.get("step", "build") if stage == "build" else stage
    progress["beforeCreate"] = True
    _record_request(
        ctx.ledger,
        ctx.scope.leaf_id,
        ctx.request_id,
        {
            "state": "failed",
            "outcome": "failed",
            "beforeCreate": True,
            "generation": generation(gen_file),
            "at": ctx.deps.stamp(),
            "step": failed_step,
            "failedStep": failed_step,
            "reason": error.reason if isinstance(error, ExplainerRefused) else type(error).__name__,
            "error": str(error),
            "percent": progress.get("percent", 0),
        },
    )


def _rollback_uncreated_workspace(
    workspace: Path, gen_file: Path, prior_gen: int, inflight: dict[str, Any] | None
) -> None:
    if inflight is None:
        restore_generation(gen_file, prior_gen)
        cleanup_workspace_tree(workspace)


def _initial_or_reconciled_build(
    ctx: _SendContext,
    facts: LeafFacts,
    gen_file: Path,
    inflight: dict[str, Any] | None,
) -> tuple[int, str, ExplainerBinding, EnvironmentBuild, tuple[str, ...]]:
    deps, scope, workspace = ctx.deps, ctx.scope, ctx.workspace
    stage = "comparison"
    try:
        bound = deps.comparison(scope, facts)
        if (
            ctx.comparison_number is not None
            and bound.binding.comparison_owner != "planned"
            and bound.binding.comparison_number != ctx.comparison_number
        ):
            raise ExplainerRefused(
                "comparison-moved",
                f"requested comparison #{ctx.comparison_number}, but current comparison is "
                f"#{bound.binding.comparison_number}",
            )
        stage = "packet"
        prepared = deps.prepare(scope, facts, bound)
        prior_gen = generation(gen_file)
        if inflight is not None and isinstance(inflight.get("generation"), int):
            number = int(inflight["generation"])
            if generation(gen_file) != number:
                restore_generation(gen_file, number)
        else:
            number = raise_generation(gen_file)
        attempt = _attempt_for(ctx.request_id)
        candidate_seat = agent_id(workspace, number, attempt)
        try:
            stage = "build"
            build = _build_environment(deps, scope, bound, prepared, ctx.reporter_sink)
            # E03: the environment's packet and binding are written together, and a comparison that
            # moved while they were being written wins: the build refuses instead of publishing a
            # binding the leaf has left. The recheck runs after the write, so a move between the first
            # read and the write is caught before a seat is created.
            stage = "recheck"
            if not deps.recheck(scope, facts, bound):
                raise ExplainerRefused(
                    "binding-moved",
                    "the leaf's comparison moved while its environment was being built; retry to bind "
                    "the comparison it holds now",
                )
        except BaseException:
            _rollback_uncreated_workspace(workspace, gen_file, prior_gen, inflight)
            raise
    except BaseException as error:
        if inflight is None:
            _record_precreate_failure(ctx, gen_file, stage, error)
        raise
    _write_inflight_create(
        ctx.ledger,
        {
            "generation": number,
            "agentId": candidate_seat,
            "requestId": ctx.request_id,
            "at": deps.stamp(),
        },
    )
    try:
        seat = _create_seat(
            deps,
            scope,
            number,
            build.workspace,
            request_id=ctx.request_id,
            attempt=attempt,
        )
    except BaseException as error:
        if not _is_lost_create_reply(deps, candidate_seat, error):
            _write_inflight_create(ctx.ledger, None)
            restore_generation(gen_file, prior_gen)
        # Keep the completed inputs available to diagnose the failed create. Without a binding,
        # the next Send rebuilds them; Git reuses the same two worktrees on the next attempt.
        with suppress(OSError):
            binding_path(build.workspace).unlink(missing_ok=True)
        raise
    _write_inflight_create(ctx.ledger, None)
    return number, seat, build.binding, build, build.steps
