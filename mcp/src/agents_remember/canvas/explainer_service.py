"""The explainer's seat: build at the first request, Update, Retire, and the states between.

MIK-R186 owns the bytes (:mod:`agents_remember.canvas.explainer_workspace`) and the records
(:mod:`agents_remember.canvas.explainer_state`); this module owns the *order* they happen in, which
is where the packet's promises live:

* one environment per leaf, built only from a Send, under the leaf's one lock (rules 1 and 2);
* the seat's id derived from the workspace and a generation counter that only grows, so a retired or
  archived seat is never revived (rule 5);
* the first message sent *after* the lock is released, so the held store's lock never nests under
  the leaf lock;
* Update refuses when the seat is in a turn or the lock is held, while Retire waits for the lock
  (rules 6 and 8);
* a build after master retirement or sprint archive is refused as ``task-archived`` (rule 1).

The host, the comparison owner and the packet preparation are handed in, so the whole order is
exercised without a Paseo host, a Git comparison or a real evidence packet.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypedDict, Unpack

from agents_remember.application.review_frame_store import frame_scope, revision_manifest
from agents_remember.canvas.explainer_grades import Grades, GradeStore, grades_path
from agents_remember.canvas.explainer_paths import (
    EXPLAINER_FRAME,
    LOCK_RESOURCE,
    binding_path,
    cleanup_workspace_tree,
    compose_request_message,
    generation_path,
    guard_explainer_path,
    leaf_lock_resource,
    leaf_workspace,
    lock_path,
    read_workspace_id,
    receipt_id_segment,
    reference_path_of,
    repository_path,
    retire_receipts_root,
)
from agents_remember.canvas.explainer_staleness import (
    BlockFingerprint,
    grade_staleness,
    stale_grade_references,
)
from agents_remember.canvas.explainer_staleness_read import (
    GradeInputs,
    gone_grade_references,
    screen_target,
    staleness_read,
)
from agents_remember.canvas.explainer_state import (
    BUSY,
    ExplainerBinding,
    ExplainerRefused,
    ExplainerStateError,
    PlannedIdentity,
    RetireReceipt,
    agent_id,
    end_processes,
    find_pending_receipt,
    generation,
    ledger_entry,
    ledger_path,
    process_tree,
    raise_generation,
    read_binding,
    read_inflight_create,
    read_receipt,
    record_request,
    request_message_id,
    restore_generation,
    retired,
    write_inflight_create,
)
from agents_remember.canvas.explainer_workspace import (
    EnvironmentBuild,
    EnvironmentInputs,
    EnvironmentRef,
    EnvironmentRun,
    EnvironmentUpdate,
    RetireOutcome,
    RetirePlace,
    RetireRequest,
    build_environment,
    resume_retire,
    retire_environment,
    update_environment,
)
from agents_remember.canvas.paths import PathRefused, task_root
from agents_remember.kernel.file_lock import LockAcquisitionTimeout, exclusive_file_lock
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.tasks.planned_comparison import retained_revision_numbers

__all__ = [
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
    "request_message_id",
    "retire_seat",
    "seat_state",
    "state_of",
    "update_seat",
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
class LeafScope:
    """Which leaf: its repository, its task folder and its leaf id, never a path."""

    repository_id: str
    task: str
    leaf_id: str

    def environment(self, config: McpRuntimeConfig) -> EnvironmentRef:
        return EnvironmentRef(
            config=config,
            repository_id=self.repository_id,
            task=self.task,
            leaf_id=self.leaf_id,
        )


@dataclass(frozen=True)
class LeafFacts:
    """What the task document and the master's records say about the leaf (rules 1, 4)."""

    #: The leaf document's stage, such as ``planning`` or ``inProgress``.
    status: str
    #: Whether the master is retired or archived, so a build is refused as ``task-archived``.
    archived: bool = False
    #: ``intentReview.agreedRevision.sha256`` when the leaf has one (MIK-R189 rule 9).
    agreed_revision: str | None = None

    @property
    def planned(self) -> bool:
        """Whether the leaf has no work yet, so its binding is the planned comparison."""

        return self.status == "planning"


@dataclass(frozen=True)
class BoundComparison:
    """The comparison an environment binds, with the identity the answers carry (rule 4)."""

    binding: FrameBinding
    planned: PlannedIdentity | None = None
    record_digest: str | None = None
    #: The record the binding was read from, when the owner answered one: the Update answer counts
    #: the blocks between it and the binding it replaces (rule 6). A planned binding has none.
    record: Any = None

    def identity(self) -> str:
        """The label every answer names: ``#7``, or ``planned``, never ``#0`` for planned."""

        if self.binding.comparison_owner == "planned":
            return "planned"
        return f"#{self.binding.comparison_number}"


@dataclass(frozen=True)
class PreparedPacket:
    """One admitted packet for the bound comparison, and the digest of the bytes written."""

    packet: Any
    digest: str


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


def _ledger_path(config: McpRuntimeConfig, scope: LeafScope) -> Path:
    return ledger_path(config, scope.repository_id, scope.task, scope.leaf_id)


_ledger_entry = ledger_entry
_record_request = record_request
_read_inflight_create = read_inflight_create
_write_inflight_create = write_inflight_create


def _workspace(config: McpRuntimeConfig, scope: LeafScope) -> Path:
    return leaf_workspace(config, scope.repository_id, scope.task, scope.leaf_id)


def _seat(config: McpRuntimeConfig, scope: LeafScope, number: int) -> str:
    return agent_id(_workspace(config, scope), number)


def _checked_facts(deps: ExplainerDeps, scope: LeafScope) -> LeafFacts:
    facts = deps.facts(scope)
    if facts.archived:
        raise ExplainerRefused(
            ARCHIVED,
            f"the master of leaf {scope.leaf_id} is retired or archived, so no environment is built",
        )
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


def _create_seat(
    deps: ExplainerDeps,
    scope: LeafScope,
    number: int,
    workspace: Path,
    *,
    request_id: str | None = None,
) -> str:
    seat = agent_id(workspace, number)
    labels = {
        "ar.capability": "review-explainer",
        "ar.repository": scope.repository_id,
        "ar.task": scope.task,
        "ar.explainer-generation": str(number),
    }
    if request_id:
        labels["ar.request"] = request_id
    deps.host.create_seat(
        agent_id=seat,
        workspace=workspace,
        title=f"Explainer · {scope.leaf_id}",
        capsule=deps.capsule(scope),
        labels=labels,
    )
    return seat


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
        "explainer-host-extra-arguments",
        "explainer-host-map-unread",
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
    "snapshot-code": 45,
    "snapshot-memory": 65,
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
    state: dict[str, Any] = {
        "state": "building",
        "requestId": request_id,
        "step": "workspace",
        "percent": 0,
        "steps": [],
        "failedStep": None,
    }
    _BUILD_STATES[key] = state

    def report(name: str, facts: Mapping[str, Any]) -> None:
        pct = _STEP_PERCENTS.get(name, state["percent"])
        done = facts.get("done")
        total = facts.get("total")
        if (
            isinstance(done, int)
            and isinstance(total, int)
            and total > 0
            and name == "snapshot-code"
        ):
            pct = 20 + int(25 * done / total)
        state["step"] = name
        state["percent"] = pct
        state["steps"].append({"name": name, "percent": pct, **dict(facts)})
        if sink is not None:
            sink(name, facts)

    return report, state


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
        raise ExplainerRefused("request-invalid", "a Send carries a message or a reference")
    _checked_facts(deps, scope)
    ledger = _ledger_path(deps.config, scope)
    workspace = _workspace(deps.config, scope)
    msg_id = request_message_id(scope.leaf_id, request_id)
    inflight_key = (str(workspace), request_id)
    own_event = _acquire_inflight_send(inflight_key, deps.lock_timeout or 30.0)
    effective_sink = progress or options.get("progress_callback")
    reporter_sink, build_state = _progress_tracker(scope, request_id, effective_sink)
    build_key = (scope.repository_id, scope.task, scope.leaf_id)
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
        if duplicate is not None and duplicate.get("delivery") in {None, "delivered", "held"}:
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
        seat = _create_seat(deps, scope, number, workspace, request_id=request_id)
    except BaseException:
        restore_generation(gen_file, prior_gen)
        raise
    return number, seat, False, False


def _initial_or_reconciled_build(
    ctx: _SendContext,
    facts: LeafFacts,
    gen_file: Path,
    inflight: dict[str, Any] | None,
) -> tuple[int, str, ExplainerBinding, EnvironmentBuild, tuple[str, ...]]:
    deps, scope, workspace = ctx.deps, ctx.scope, ctx.workspace
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
    prepared = deps.prepare(scope, facts, bound)
    prior_gen = generation(gen_file)
    if inflight is not None and isinstance(inflight.get("generation"), int):
        number = int(inflight["generation"])
        if generation(gen_file) != number:
            restore_generation(gen_file, number)
    else:
        number = raise_generation(gen_file)
    candidate_seat = agent_id(workspace, number)
    try:
        build = _build_environment(deps, scope, bound, prepared, ctx.reporter_sink)
        # E03: the environment's packet and binding are written together, and a comparison that
        # moved while they were being written wins: the build refuses instead of publishing a
        # binding the leaf has left. The recheck runs after the write, so a move between the first
        # read and the write is caught before a seat is created.
        rechecked = deps.comparison(scope, facts)
        if not bound.binding.same_comparison(rechecked.binding):
            raise ExplainerRefused(
                "binding-moved",
                "the leaf's comparison moved while its environment was being built; retry to bind "
                "the comparison it holds now",
            )
    except BaseException:
        if inflight is None:
            restore_generation(gen_file, prior_gen)
            cleanup_workspace_tree(workspace)
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
        seat = _create_seat(deps, scope, number, build.workspace, request_id=ctx.request_id)
    except BaseException as error:
        if _is_lost_create_reply(deps, candidate_seat, error):
            with suppress(OSError):
                binding_path(build.workspace).unlink(missing_ok=True)
        else:
            _write_inflight_create(ctx.ledger, None)
            restore_generation(gen_file, prior_gen)
            cleanup_workspace_tree(build.workspace)
        raise
    _write_inflight_create(ctx.ledger, None)
    return number, seat, build.binding, build, build.steps


def _latest_manifest(deps: ExplainerDeps, scope: LeafScope):
    """The highest retained revision's manifest of the leaf's explainer frame, or ``None``.

    The store's own retained list is the bound (descending), so revision 32 and beyond are read
    exactly like the first one; no display ceiling decides which revision the service sees.
    """

    root = _task_folder(deps, scope)
    try:
        frame = frame_scope(root, scope.leaf_id, EXPLAINER_FRAME)
    except ValueError:
        return None
    for number in retained_revision_numbers(frame.frame_dir):
        manifest = revision_manifest(frame.frame_dir, number)
        if manifest is not None:
            return manifest
    return None


def _tree_paths_at(repository: Path, tree: str) -> set[str] | None:
    if not repository.is_dir():
        return None
    result = run_git(repository, ["ls-tree", "-r", "--name-only", tree])
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _default_present_references(
    deps: ExplainerDeps, scope: LeafScope, grades: Grades, target_binding: FrameBinding | None
) -> set[str]:
    all_refs = {item.reference for item in grades.grades}
    if target_binding is None or not all_refs:
        return all_refs
    if deps.present_references is not None:
        supplied = deps.present_references(scope, target_binding)
        if supplied is not None:
            return set(supplied)
    with suppress(PathRefused, KeyError):
        code_repo = repository_path(deps.config, scope.repository_id)
        base_paths = _tree_paths_at(code_repo, target_binding.code_base_tree)
        cand_paths = _tree_paths_at(code_repo, target_binding.code_candidate_tree)
        if base_paths is not None and cand_paths is not None:
            known_paths = base_paths | cand_paths
            present: set[str] = set()
            for ref in all_refs:
                ref_path = reference_path_of(ref)
                if ref_path is None or ref_path in known_paths:
                    present.add(ref)
            return present
    return all_refs


def _reconcile_grades_and_counts(
    deps: ExplainerDeps,
    scope: LeafScope,
    target_binding: FrameBinding | None,
    *,
    prune: bool = True,
) -> tuple[int | None, int | None, int, str | None]:
    """Prune absent grade references and compute ``(stale_parts, stale_grades, removed, error)``."""

    task_root_path = _task_folder(deps, scope)
    store = GradeStore(grades_path(task_root_path, scope.leaf_id), scope.leaf_id)
    try:
        stored = store.load()
    except (ExplainerRefused, ValueError, OSError) as error:
        return None, None, 0, str(error)
    fingerprints: Mapping[str, BlockFingerprint] = (
        deps.grade_fingerprints(scope, target_binding)
        if deps.grade_fingerprints is not None and target_binding is not None
        else {}
    )
    removed = 0
    if prune and stored.grades:
        initial_states = grade_staleness(stored, fingerprints)
        present = _default_present_references(deps, scope, stored, target_binding)
        gone = gone_grade_references(initial_states, present)
        if gone:
            stored, removed = store.prune(gone)
    states = grade_staleness(stored, fingerprints)
    stale_grades_count = len(stale_grade_references(states))
    manifest = _latest_manifest(deps, scope)
    if manifest is None or target_binding is None:
        return 0, stale_grades_count, removed, None
    try:
        read = staleness_read(
            task_root=task_root_path,
            leaf_id=scope.leaf_id,
            manifest=manifest,
            target=screen_target(
                task_root=task_root_path,
                environment=target_binding,
                supplemental=manifest.supplemental,
            ),
            grades=GradeInputs(grades=stored, fingerprints=fingerprints),
        )
    except (ExplainerRefused, ValueError) as error:
        return None, stale_grades_count, removed, str(error)
    return len(read.stale_parts), len(read.stale_grades), removed, None


def _count_blocks_changed(
    deps: ExplainerDeps, previous: FrameBinding, bound: BoundComparison
) -> int | None:
    if deps.counter is None:
        return None
    if bound.record is not None:
        return deps.counter(previous, bound.record)
    with suppress(Exception):
        return deps.counter(previous, bound.binding)
    return None


def update_seat(deps: ExplainerDeps, scope: LeafScope) -> EnvironmentUpdate:
    """Rebind one environment to the leaf's current comparison, or refuse by name (rule 6)."""

    workspace = _workspace(deps.config, scope)
    if retired(workspace) is not None:
        raise ExplainerRefused(RETIRED, f"the explainer of leaf {scope.leaf_id} is retired")
    gen_file = generation_path(deps.config, scope.repository_id, scope.task, scope.leaf_id)
    with _lock(deps, scope, wait=False):
        if retired(workspace) is not None:
            raise ExplainerRefused(RETIRED, f"the explainer of leaf {scope.leaf_id} is retired")
        number = generation(gen_file)
        seat = _seat(deps.config, scope, number)
        if deps.host.in_turn(seat):
            raise ExplainerRefused(
                IN_TURN, f"the explainer of leaf {scope.leaf_id} is in a turn; nothing is queued"
            )
        facts = deps.facts(scope)
        previous = read_binding(workspace)
        if previous is None:
            raise ExplainerRefused(MISSING, f"leaf {scope.leaf_id} has no environment to update")
        bound = deps.comparison(scope, facts)
        prepared = deps.prepare(scope, facts, bound)
        updated = update_environment(
            EnvironmentRun(
                ref=scope.environment(deps.config),
                inputs=EnvironmentInputs(
                    binding=bound.binding,
                    packet=prepared.packet,
                    capsule=deps.capsule(scope),
                    code_repository=repository_path(deps.config, scope.repository_id),
                    memory_repository=repository_path(
                        deps.config, scope.repository_id, memory=True
                    ),
                    planned=bound.planned,
                    record_digest=bound.record_digest,
                ),
                now=deps.stamp(),
                previous=previous,
                in_turn=lambda: deps.host.in_turn(seat),
            )
        )
        blocks = _count_blocks_changed(deps, previous.binding, bound)
        stale_parts, stale_grades, removed_grades, unavailable = _reconcile_grades_and_counts(
            deps, scope, bound.binding, prune=True
        )
        return replace(
            updated,
            blocks_changed=blocks,
            stale_parts=stale_parts,
            stale_grades=stale_grades,
            removed_grades=removed_grades,
            counts_unavailable=unavailable,
        )


def retire_seat(
    deps: ExplainerDeps,
    scope: LeafScope,
    *,
    receipt_id: str,
) -> RetireOutcome:
    """Retire one environment, waiting for the lock, and leave a finished receipt (rule 8)."""

    admitted_id = receipt_id_segment(receipt_id)
    workspace = _workspace(deps.config, scope)
    task_root_path = _task_folder(deps, scope)
    receipts = retire_receipts_root(task_root_path, scope.leaf_id)
    with _lock(deps, scope, wait=True):
        existing_binding = None
        if workspace.is_dir():
            with suppress(ExplainerStateError):
                existing_binding = read_binding(workspace)
        target_binding = None if existing_binding is None else existing_binding.binding
        if target_binding is None:
            with suppress(Exception):
                target_binding = deps.comparison(scope, deps.facts(scope)).binding
        _sp, _sg, removed_grades, _unavail = _reconcile_grades_and_counts(
            deps, scope, target_binding, prune=True
        )
        place = RetirePlace(
            workspace=workspace,
            receipts_root=receipts,
            now=deps.stamp(),
            archive_session=deps.host.archive,
        )
        pending = find_pending_receipt(receipts, admitted_id)
        if pending is not None and pending.state != "finished":
            return replace(resume_retire(place, pending), removed_grades=removed_grades)
        marker = retired(workspace)
        if marker is not None:
            marker_receipt = marker.get("receipt")
            existing = (
                read_receipt(receipts, marker_receipt)
                if isinstance(marker_receipt, str) and marker_receipt
                else None
            )
            if existing is not None and existing.state == "finished":
                return RetireOutcome(
                    receipt=existing,
                    workspace_removed=True,
                    detail="the environment was already retired",
                    removed_grades=removed_grades,
                )
            if existing is not None:
                return replace(resume_retire(place, existing), removed_grades=removed_grades)
            raise ExplainerRefused(
                RETIRED,
                f"the explainer of leaf {scope.leaf_id} is retired and its receipt is "
                f"{marker.get('receipt')!r}",
            )
        number = generation(
            generation_path(deps.config, scope.repository_id, scope.task, scope.leaf_id)
        )
        outcome = retire_environment(
            RetireRequest(
                ref=scope.environment(deps.config),
                receipts_root=receipts,
                receipt_id=admitted_id,
                generation=number,
                now=deps.stamp(),
                archive_session=deps.host.archive,
            )
        )
        return replace(outcome, removed_grades=removed_grades)


def _task_folder(deps: ExplainerDeps, scope: LeafScope) -> Path:
    """The leaf's task folder, from where its other reports lie (never from a caller's path)."""

    return task_root(deps.config, scope.repository_id, scope.task)


def seat_state(deps: ExplainerDeps, scope: LeafScope) -> ExplainerState:
    """The page's read: no lock, no turn, no build. It names what is there and nothing more."""

    workspace = _workspace(deps.config, scope)
    number = generation(
        generation_path(deps.config, scope.repository_id, scope.task, scope.leaf_id)
    )
    seat = agent_id(workspace, number) if number else ""
    binding = read_binding(workspace) if workspace.is_dir() else None
    marker = retired(workspace) if workspace.is_dir() else None
    receipts = retire_receipts_root(_task_folder(deps, scope), scope.leaf_id)
    marker_receipt = None if marker is None else marker.get("receipt")
    receipt = (
        read_receipt(receipts, marker_receipt)
        if isinstance(marker_receipt, str) and marker_receipt
        else None
    )
    if marker is not None:
        state = "retired"
    elif binding is None:
        state = SEAT_MISSING
    else:
        state = deps.host.seat_state(seat)
    building = _BUILD_STATES.get((scope.repository_id, scope.task, scope.leaf_id))
    return ExplainerState(
        leaf=scope,
        workspace=workspace,
        present=binding is not None,
        generation=number,
        agent_id=seat,
        binding=binding,
        seat=state,
        retired=marker,
        receipt=receipt,
        workspace_id=read_workspace_id(workspace),
        building=None if building is None else dict(building),
    )


@dataclass(frozen=True)
class CancelOutcome:
    """One cancel's answer: the exact seat and request it named, and what the host says after.

    MIK-R105 v1 rule 9: cancellation names whether a turn stopped, publication was prevented, or
    a request remains uncertain; it keeps the last ready revision and changes nothing else.
    ``publication_prevented`` is the route's own before/after read of the frame store, filled in
    by the caller that owns it; this outcome carries the host's facts.
    """

    leaf: LeafScope
    agent_id: str
    request_id: str
    seat: str
    turn_stopped: bool
    request_uncertain: bool
    permission_closed: bool
    publication_prevented: bool = True

    def as_record(self) -> dict[str, Any]:
        return {
            "leaf": {
                "repository": self.leaf.repository_id,
                "task": self.leaf.task,
                "leaf": self.leaf.leaf_id,
            },
            "agentId": self.agent_id,
            "requestId": self.request_id,
            "seat": self.seat,
            "turnStopped": self.turn_stopped,
            "requestUncertain": self.request_uncertain,
            "publicationPrevented": self.publication_prevented,
            "permissionClosed": self.permission_closed,
        }


def cancel_seat(deps: ExplainerDeps, scope: LeafScope, *, request_id: str) -> CancelOutcome:
    """Cancel the leaf's seat at the exact seat and request (MIK-R105 v1 rule 9).

    The seat is the environment's recorded agent and nothing else; the request identity is the
    leaf's own build ledger row. A request the ledger does not name is reported uncertain rather
    than claimed cancelled. The host's named cancel ends the seat's turn and never archives or
    replaces it, and a permission decision that was pending is closed. Nothing writes to the
    frame store here, so the last ready revision is kept; the route measures publication itself.
    """

    state = seat_state(deps, scope)
    ledger = _ledger_path(deps.config, scope)
    entry = _ledger_entry(ledger, scope.leaf_id, request_id)
    request_uncertain = entry is None or entry.get("agentId") != state.agent_id
    if state.seat in {SEAT_ARCHIVED, SEAT_MISSING} and not state.agent_id:
        return CancelOutcome(
            leaf=scope,
            agent_id=state.agent_id,
            request_id=request_id,
            seat=state.seat,
            turn_stopped=False,
            request_uncertain=True,
            permission_closed=True,
        )
    answer = deps.host.cancel(state.agent_id, request_id=request_id) or {}
    return CancelOutcome(
        leaf=scope,
        agent_id=state.agent_id,
        request_id=request_id,
        seat=state.seat,
        turn_stopped=bool(answer.get("turnStopped")) if isinstance(answer, Mapping) else False,
        request_uncertain=request_uncertain
        or (not isinstance(answer, Mapping) or bool(answer.get("requestUncertain"))),
        permission_closed=(isinstance(answer, Mapping) and bool(answer.get("permissionClosed"))),
    )


state_of = seat_state
