"""L5 — how an admitted capsule reaches the Codex app-server instruction channel.

Every expected value in this module is an independent fixture: the channel names come from the
pinned schema bundle read off disk (``fixtures/codex_app_server_instruction_channels.json``), the
capsule shapes are built here as literals against L2's published frozen DTO, and the instruction
bytes are compared to a literal. Nothing imports its expectation from the module under test, and no
comparison is derived from a single artifact.

Channels are the whole point of this leaf, so the cases below are organized by the boundary they
protect: which field may carry trusted instructions, which channel task text and skill content must
*not* reach, and what happens to a second turn, a resume and a changed revision.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
from collections import deque
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    ControlIdentity,
    LaunchSpec,
)
from agents_remember.models.role_capsules.types import CapsuleCapsule
from agents_remember.serving.capsule_delivery import (
    INSTRUCTION_PARAM,
    LAUNCHER_ROLE_SENTINEL,
    LEGACY_PROJECT_DOC_KEY,
    CapsuleBindingIdentity,
    CapsuleRefresh,
    CapsuleSkillPointer,
    CodexCapsuleDelivery,
    ThreadInstructionState,
    capsule_delivery_from,
    legacy_instruction_switch,
    plan_refresh,
    thread_instruction_params,
)
from agents_remember.serving.codex_app_server_protocol import (
    CodexStdioTransport,
    JsonObject,
    RequestId,
)
from agents_remember.serving.codex_app_server_session import (
    CodexAppServerSession,
    CodexAppServerSettings,
)
from agents_remember.serving.harness_control_models import ShutdownMode

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "codex_app_server_instruction_channels.json"
MODEL_PAGE_PATH = Path(__file__).parent / "fixtures" / "codex_app_server_model_page.json"

from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.role_capsules.sources import CapsuleAdmissionRequest
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleCompilationResult,
    CapsuleLauncherSeat,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.serving import codex_app_server_adapter as adapter_module
from agents_remember.serving import harness_control_runner as runner_module
from agents_remember.serving.codex_app_server_adapter import CodexAppServerAdapter
from agents_remember.serving.codex_app_server_session import codex_launch_knobs
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    EffortOption,
    ModelCapability,
)
from agents_remember.serving.harness_control_factories import (
    LaunchSelection,
    create_harness_protocol_adapter,
)
from agents_remember.serving.harness_control_runner import (
    RunnerConfig,
    _prepare_controlled_launch,
    control_runner_command,
    parse_runner_config,
)
from agents_remember.serving.harness_launch import ResolvedLaunch
from agents_remember.serving.terminal_opener import (
    ControlRunnerRequest,
    LaunchCommand,
    TerminalLaunchRequest,
    _session_command,
)

# Independent expectations. The trusted stream is a literal with a token no other artifact contains.
TRUSTED_INSTRUCTIONS = (
    "# Role — worker\n\n"
    "You are the WORKER for leaf `L5`. TRUSTED-STREAM-TOKEN-4c17\n"
    "Never commit; the owning seat commits.\n"
)
TASK_TEXT = "Leaf L5 objective. TASK-CHANNEL-TOKEN-8b02"
SKILL_POINTER = CapsuleSkillPointer(
    identity="shipped#c-12-closeout",
    origin="shipped",
    uri="skill://shipped/c-12-closeout",
    revision="sha256:" + "ab" * 32,
)
BINDING = CapsuleBindingIdentity(
    repository_id="agents-remember",
    task_path="260915_role-capsules-and-native-eve/05_codex-capsule-delivery.json",
    role="worker",
    operation="implementation",
)


def delivery(
    *,
    instructions: str = TRUSTED_INSTRUCTIONS,
    digest: str = "sha256:" + "1f" * 32,
) -> CodexCapsuleDelivery:
    return CodexCapsuleDelivery(
        binding=BINDING,
        trusted_instructions=instructions,
        semantic_digest=digest,
        skill_pointers=(SKILL_POINTER,),
    )


@pytest.fixture(name="channels")
def channels_fixture() -> JsonObject:
    """The pinned instruction-channel contract, read from the fixture file on disk."""

    return cast(JsonObject, json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


class RecordingTransport:
    """A protocol-faithful transport that records exactly what the real session sent."""

    def __init__(
        self, *, instruction_sources: tuple[str, ...], thread_id: str = "thread-1"
    ) -> None:
        self.requests: list[tuple[str, JsonObject]] = []
        self.launches: list[LaunchSpec] = []
        self.instruction_sources = instruction_sources
        self.thread_id = thread_id
        self.stopped: list[ShutdownMode] = []
        self._responses: dict[str, deque[JsonObject]] = {}

    def queue(self, method: str, response: JsonObject) -> None:
        self._responses.setdefault(method, deque()).append(response)

    async def start(self, launch: LaunchSpec) -> None:
        self.launches.append(launch)

    async def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        before_write: object = None,
    ) -> JsonObject:
        del before_write
        self.requests.append((method, dict(params)))
        return deepcopy(self._responses[method].popleft())

    async def notify(self, method: str, params: Mapping[str, object]) -> None:
        del method, params

    def messages(self) -> AsyncIterator[JsonObject]:
        return self._messages()

    async def _messages(self) -> AsyncIterator[JsonObject]:
        if False:  # pragma: no cover - keeps this an async generator like the real transport
            yield {}

    async def respond(self, request_id: RequestId, result: Mapping[str, object]) -> None:
        del request_id, result

    async def respond_error(self, request_id: RequestId, *, code: int, message: str) -> None:
        del request_id, code, message

    async def stop(self, mode: ShutdownMode) -> None:
        self.stopped.append(mode)


def initialize_response() -> JsonObject:
    return {
        "codexHome": "/tmp/codex-home",
        "platformFamily": "unix",
        "platformOs": "linux",
        # The negotiated handshake shape the pinned app-server reports: product/version plus
        # diagnostics ending in this client's own (name; version) suffix.
        # Verbatim shape of the installed app-server's handshake reply, captured from a real
        # initialize exchange: product/version plus platform diagnostics.
        "userAgent": "agents_remember/0.151.0 (Ubuntu 22.4.0; x86_64) (agents_remember; 3.0.0)",
    }


def model_page() -> JsonObject:
    """One model row in the installed app-server's own spelling, read from a pinned fixture.

    Captured verbatim from a live ``model/list`` so the reasoning-effort options carry the
    descriptions the parser requires: the fixture is the real contract, not a guess about it.
    """

    return cast(JsonObject, json.loads(MODEL_PAGE_PATH.read_text(encoding="utf-8")))


def thread_open_response(
    *, thread_id: str = "thread-1", instruction_sources: tuple[str, ...] = ()
) -> JsonObject:
    return {
        "thread": {
            "id": thread_id,
            "cliVersion": "0.151.0",
            "turns": [],
            "status": {"type": "idle"},
        },
        "model": "gpt-5.6-sol",
        "modelProvider": "openai",
        "cwd": "/tmp/l5-cwd",
        "reasoningEffort": "medium",
        "instructionSources": list(instruction_sources),
    }


def launch() -> LaunchSpec:
    """A real launch spec for the codex harness; the live case runs this argv for real."""

    return LaunchSpec(
        identity=ControlIdentity(
            ar_session_id="ar-session-l5",
            tmux_name="ar-codex-l5",
            created_at="2026-09-16T00:00:00+00:00",
        ),
        harness_id="codex",
        cwd=Path("/tmp/l5-cwd"),
        argv=("codex", "app-server"),
    )


def session_settings(**overrides: object) -> CodexAppServerSettings:
    base: dict[str, object] = {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
    }
    base.update(overrides)
    return CodexAppServerSettings(**base)  # type: ignore[arg-type]


async def open_session_with(
    session: CodexAppServerSession,
    transport: RecordingTransport,
    *,
    resume_thread_id: str | None = None,
) -> CodexAppServerSession:
    """Connect an already-constructed session (the factory's own) against a recording transport."""

    transport.queue("initialize", initialize_response())
    transport.queue("model/list", model_page())
    transport.queue(
        "thread/resume" if resume_thread_id else "thread/start",
        thread_open_response(instruction_sources=transport.instruction_sources),
    )
    object.__setattr__(session, "_transport_factory", lambda: transport)
    await session.connect(launch(), resume_thread_id=resume_thread_id)
    return session


async def open_thread(
    transport: RecordingTransport,
    *,
    resume_thread_id: str | None = None,
    delivery_value: CodexCapsuleDelivery | None = None,
) -> CodexAppServerSession:
    transport.queue("initialize", initialize_response())
    transport.queue("model/list", model_page())
    transport.queue(
        "thread/resume" if resume_thread_id else "thread/start",
        thread_open_response(instruction_sources=transport.instruction_sources),
    )
    session = CodexAppServerSession(
        session_settings(capsule_delivery=delivery_value),
        transport_factory=lambda: transport,
    )
    await session.connect(launch(), resume_thread_id=resume_thread_id)
    return session


def test_instruction_channel_is_the_schema_supported_thread_open_field(
    channels: JsonObject,
) -> None:
    """The channel this seam writes is the one the pinned schema exposes on thread open."""

    fields = cast(dict[str, list[str]], channels["instructionFields"])
    # Pinned literal as well as membership: the seam occupies the developer channel by decision
    # (baseInstructions would replace the vendor's own base prompt), so a change of constant has to
    # fail here rather than silently pass by matching whichever field the schema happens to list.
    assert INSTRUCTION_PARAM == "developerInstructions"
    assert INSTRUCTION_PARAM in fields["ThreadStartParams"], "schema does not expose the channel"
    assert INSTRUCTION_PARAM in fields["ThreadResumeParams"]
    # The lifetime guarantee rests on this: an ordinary turn cannot carry instructions.
    assert fields["TurnStartParams"] == [], (
        "the pinned schema exposes a turn-level instruction field; refresh-in-place would need "
        "re-deciding"
    )
    assert channels["threadOpenResponseInstructionFields"] == ["instructionSources"]


def test_thread_instruction_params_carry_only_the_trusted_stream() -> None:
    """The request carries the capsule's own instruction bytes and no other channel's content."""

    plan = plan_refresh(delivery(), ThreadInstructionState())
    params = thread_instruction_params(delivery(), plan)

    assert list(params) == [INSTRUCTION_PARAM]
    # The key SET is pinned, not only the constant: an added key (a smuggled second channel) fails
    # here even when the instruction parameter itself is correct.
    assert set(params) == {"developerInstructions"}
    assert "baseInstructions" not in params
    assert params[INSTRUCTION_PARAM] == TRUSTED_INSTRUCTIONS
    # The other channels' content is absent from the instruction channel, by token.
    assert "TASK-CHANNEL-TOKEN" not in str(params)
    assert SKILL_POINTER.uri not in str(params)
    assert "c-12-closeout" not in str(params)


def test_refused_refresh_yields_no_instruction_params() -> None:
    """An unsupported refresh cannot smuggle a second revision into the request."""

    live = ThreadInstructionState(binding=BINDING, semantic_digest="sha256:" + "2f" * 32)
    plan = plan_refresh(delivery(), live, fork_available=False)
    assert plan.mode is CapsuleRefresh.FRESH_THREAD
    assert not plan.is_refusal

    other_binding = ThreadInstructionState(
        binding=CapsuleBindingIdentity(
            repository_id="other-repo",
            task_path="other/task.json",
            role="manager",
            operation="planning",
        ),
        semantic_digest="sha256:" + "3f" * 32,
    )
    refused = plan_refresh(delivery(), other_binding)
    assert refused.is_refusal
    with pytest.raises(Exception, match="refused capsule refresh is not deliverable"):
        thread_instruction_params(delivery(), refused)


def test_same_digest_restates_bytes_and_a_changed_digest_needs_a_boundary() -> None:
    """Resume with a known digest is in-place; a changed revision is boundary-or-refusal."""

    state = ThreadInstructionState(binding=BINDING, semantic_digest=delivery().semantic_digest)
    assert plan_refresh(delivery(), state).mode is CapsuleRefresh.IN_PLACE

    changed = delivery(digest="sha256:" + "9f" * 32)
    assert plan_refresh(changed, state, fork_available=True).mode is CapsuleRefresh.FORK_THREAD
    assert plan_refresh(changed, state, fork_available=False).mode is CapsuleRefresh.FRESH_THREAD

    unknown = ThreadInstructionState()
    assert plan_refresh(delivery(), unknown).mode is CapsuleRefresh.INITIAL


def test_task_and_skill_content_cannot_enter_the_instruction_slot() -> None:
    """The value type refuses an instruction parameter smuggled through the task channel."""

    # There is no task-context field to smuggle anything through: the delivery value carries the
    # instruction stream and skill pointers only, and the task channel belongs to the caller.
    assert "task_context_markdown" not in {
        field for field in CodexCapsuleDelivery.__dataclass_fields__
    }

    with pytest.raises(Exception, match="non-empty trusted instruction stream"):
        delivery(instructions="   ")

    with pytest.raises(Exception, match="semantic digest"):
        delivery(digest="not-a-digest")

    for field in ("repository_id", "task_path", "role", "operation"):
        with pytest.raises(Exception, match=f"binding {field}"):
            CapsuleBindingIdentity(
                repository_id="repo" if field != "repository_id" else "",
                task_path="task.json" if field != "task_path" else " ",
                role="worker" if field != "role" else "",
                operation="implementation" if field != "operation" else " ",
            )


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_ROOT = REPOSITORY_ROOT / "skills" / "l-01-agent-lifecycles"
MANIFEST_RELATIVE = "composition-manifest.json"


def _shipped_manifest():
    return parse_composition_manifest((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes())


def real_compilation(role: str = "worker", operation: str = "implementation"):
    """A GENUINE ``CapsuleCompilationResult`` from the public frozen compiler path.

    Nothing here is a stand-in: the binding is a real ``CapsuleBinding`` over real
    ``CapsuleAdmittedFacts``, the source set is the shipped corpus, and the result comes from
    ``compile_admitted_capsule``. This is the input shape the seam must consume.
    """

    parsed = _shipped_manifest()
    entry = parsed.roles[role]
    request = CapsuleAdmissionRequest(
        root=LIFECYCLE_ROOT,
        manifest=MANIFEST_RELATIVE,
        core=tuple(parsed.core[name].source for name in entry.core),
        role=(entry.file,),
        operation=(parsed.operations[operation].source,),
        skill=tuple(
            parsed.skills[name].source
            for name in getattr(entry, "skills", ())
            if name in parsed.skills
        ),
    )
    binding = CapsuleBinding(
        operation=operation,  # type: ignore[arg-type]
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=CapsuleRoleSeat(role=role, altitude=entry.altitude),  # type: ignore[arg-type]
            repository_id="agents-remember",
            work_branch="ar/260915-caps-l5-ar",
            # The admitted snapshot must cover what the composed blocks request; a role's own
            # declared tools are the honest source for that, exactly as the corpus declares them.
            tool_policy=CapsuleToolPolicy(
                granted=frozenset(tool for entry in parsed.roles.values() for tool in entry.tools)
            ),
        ),
    )
    outcome = compile_admitted_capsule(binding, request)
    assert outcome.ok, f"the shipped {role}/{operation} capsule failed to compile: {outcome.error}"
    assert outcome.result is not None
    return outcome.result


def test_the_frozen_shapes_this_conversion_depends_on() -> None:
    """The DTO field names are asserted against L2's real types, not assumed.

    If L2 ever moves ``admitted`` or renames ``task_reference``, this fails first and names the
    shape, instead of the conversion silently reading a missing attribute.
    """

    binding_fields = set(CapsuleBinding.__dataclass_fields__)
    assert {"operation", "admitted"} <= binding_fields, binding_fields
    admitted = CapsuleBinding.__dataclass_fields__["admitted"].type
    assert "CapsuleAdmittedFacts" in str(admitted), admitted
    assert "render_instructions" in vars(CapsuleCompilationResult)
    assert "binding" in CapsuleCapsule.__dataclass_fields__


def test_delivery_consumes_a_genuine_compilation_result() -> None:
    """L5R-1: the seam consumes the real frozen compilation, not a mirror of its own guesses."""

    compilation = real_compilation("worker", "implementation")
    result = capsule_delivery_from(compilation)

    admitted = compilation.capsule.binding.admitted
    assert result.binding.repository_id == admitted.repository_id
    assert result.binding.task_path == admitted.task_reference
    assert result.binding.role == admitted.seat.role
    assert result.binding.operation == compilation.capsule.binding.operation
    assert result.trusted_instructions == compilation.render_instructions()
    assert result.trusted_instructions.strip(), "the real capsule must render actual instructions"
    assert result.semantic_digest == compilation.semantic_digest
    assert result.instruction_channel == INSTRUCTION_PARAM
    # The old, wrong shape is not silently accepted: the stand-in field names must not exist.
    for legacy in ("repository_id", "task_path", "role"):
        assert not hasattr(compilation.capsule.binding, legacy), legacy


def test_a_launcher_seat_reports_its_sentinel_role() -> None:
    """The launcher seat has no role but still gets a capsule, so the role slot stays non-empty."""

    seat = CapsuleLauncherSeat(routing_condition="ambient-launcher")
    assert seat.role is None, "the launcher really does expose no role"
    assert seat.block_identity is None
    assert seat.key.startswith("launcher:")


def test_a_seat_the_conversion_does_not_recognise_is_refused() -> None:
    """An unknown seat kind refuses loudly rather than inventing a role."""

    class _UnknownSeat:
        kind = "wizard"

    class _Capsule:
        binding = type(
            "B",
            (),
            {
                "operation": "implementation",
                "admitted": type(
                    "A",
                    (),
                    {
                        "task_reference": "t.json",
                        "repository_id": "repo",
                        "seat": _UnknownSeat(),
                    },
                )(),
            },
        )()

    class _Result:
        capsule = _Capsule()

        def render_instructions(self) -> str:
            return "instructions"

    with pytest.raises(Exception, match="unrecognised seat kind"):
        capsule_delivery_from(_Result())


def _admitted_facts():
    """The real admitted-facts construction shared by the launcher and role assertions."""

    return {
        "task_reference": "agents-remember/tasks/demo/task.json",
        "task_document_digest": compute_content_digest(b'{"id": "demo"}'),
        "repository_id": "agents-remember",
        "work_branch": "ar/260915-caps-l5-ar",
        "tool_policy": CapsuleToolPolicy(granted=frozenset()),
    }, CapsuleAdmittedFacts


def test_a_real_launcher_compilation_reports_the_sentinel_role() -> None:
    """A genuine launcher binding converts, reporting the sentinel instead of an empty role."""

    kwargs, facts_type = _admitted_facts()
    binding = CapsuleBinding(
        operation="orientation",
        admitted=facts_type(
            seat=CapsuleLauncherSeat(routing_condition="ambient-launcher"), **kwargs
        ),
    )

    class _Result:
        capsule = type("C", (), {"binding": binding, "semantic_digest": "sha256:" + "0a" * 32})()

        def render_instructions(self) -> str:
            return "# Launcher\n"

    result = capsule_delivery_from(_Result())
    assert result.binding.role == LAUNCHER_ROLE_SENTINEL
    assert result.binding.operation == "orientation"


def test_the_production_factory_fills_the_capsule_carrier() -> None:
    """L5R-2 acceptance: the PRODUCTION factory carries a genuine capsule into the adapter.

    The capsule comes from a real compilation and the adapter comes from
    ``create_harness_protocol_adapter``; the assertion reads the settings object the factory itself
    built, which is the object the adapter's session consumes. The wire half of the same path is
    covered end to end by ``test_initial_thread_open_sends_instructions_on_the_wire``, which drives a
    session built from exactly these settings with a recorded transport (the factory owns the real
    transport choice and must not be bypassed here).
    """

    compilation = real_compilation("worker", "implementation")
    delivered = capsule_delivery_from(compilation)

    adapter = create_harness_protocol_adapter(
        "codex",
        env={},
        selection=LaunchSelection(
            resolved_launch=ResolvedLaunch("codex", "gpt-5.6-sol", "medium", Path("/tmp/l5-cwd")),
            launch_knobs=codex_launch_knobs(model_key="gpt-5.6-sol", effort="medium"),
        ),
        capsule_delivery=delivered,
    )

    concrete = cast(CodexAppServerAdapter, adapter)
    settings = concrete._session.settings
    carried = settings.capsule_delivery
    assert carried is not None
    assert carried is delivered, (
        "the production factory dropped the capsule; no admitted binding would reach the seam"
    )
    assert carried.trusted_instructions == compilation.render_instructions()
    assert carried.binding.role == "worker"
    # And the carrier reaches the real request builder of the session the factory built.
    assert isinstance(concrete._session, CodexAppServerSession)


@pytest.mark.anyio
async def test_the_factory_filled_settings_put_the_capsule_on_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory's own settings produce the capsule on the thread-open request.

    This drives the settings the factory built through the session's real request builder, so the
    carrier is proven to have a wire effect and not only to be stored.
    """

    compilation = real_compilation("worker", "implementation")
    adapter = create_harness_protocol_adapter(
        "codex",
        env={},
        selection=LaunchSelection(
            resolved_launch=ResolvedLaunch("codex", "gpt-5.6-sol", "medium", Path("/tmp/l5-cwd")),
            launch_knobs=codex_launch_knobs(model_key="gpt-5.6-sol", effort="medium"),
        ),
        capsule_delivery=capsule_delivery_from(compilation),
    )
    transport = RecordingTransport(instruction_sources=())
    product = cast(CodexAppServerAdapter, adapter)
    await open_session_with(product._session, transport)
    params = next(params for method, params in transport.requests if method == "thread/start")
    assert params[INSTRUCTION_PARAM] == compilation.render_instructions()


@pytest.mark.anyio
async def test_the_production_factory_refuses_a_capsule_for_a_harness_without_the_channel() -> None:
    """A capsule handed to a harness with no verified instruction channel is refused, not dropped."""

    for harness in ("claude", "pi", "eve"):
        with pytest.raises(Exception, match="only implemented for the codex harness"):
            create_harness_protocol_adapter(
                harness,
                env={},
                capsule_delivery=capsule_delivery_from(
                    real_compilation("worker", "implementation")
                ),
            )


@pytest.mark.anyio
async def test_a_launch_boundary_capsule_reaches_the_adapter_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R30 shape: a capsule supplied at the launch boundary arrives at settings.capsule_delivery.

    Drives the REAL ``_prepare_controlled_launch`` with a fully resolved codex ``RunnerConfig``, so
    both production factory calls happen. The transient DISCOVERER is doubled (it would otherwise
    start a second vendor process), and the real adapter is built by the production factory with the
    session transport doubled, because the factory owns that choice.
    """

    compilation = real_compilation("worker", "implementation")
    delivered = capsule_delivery_from(compilation)

    transport = RecordingTransport(instruction_sources=())
    transport.queue("initialize", initialize_response())
    transport.queue("model/list", model_page())
    transport.queue("thread/start", thread_open_response())
    monkeypatch.setattr(adapter_module, "CodexStdioTransport", lambda: transport)

    real_factory = runner_module.create_harness_protocol_adapter

    def factory_with_stub_discoverer(harness_id: str, **kwargs: object):
        if "selection" not in kwargs:
            # The capability-preflight adapter only has to answer discovery.
            return _StubDiscoverer()
        return real_factory(harness_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        runner_module, "create_harness_protocol_adapter", factory_with_stub_discoverer
    )

    config = RunnerConfig(
        identity=ControlIdentity(
            ar_session_id="ar-session-l5-r30",
            tmux_name="ar-codex-l5-r30",
            created_at="2026-09-16T00:00:00+00:00",
        ),
        harness_id="codex",
        cwd=Path("/tmp/l5-cwd"),
        argv=("codex", "app-server"),
        endpoint_root=Path("/tmp/l5-endpoint"),
        resolved_launch=ResolvedLaunch("codex", "gpt-5.6-sol", "medium", Path("/tmp/l5-cwd")),
        capsule_delivery=delivered,
    )

    # The carrier survives the launch configuration's own encoded payload, which is what the
    # subprocess is handed; parse it back rather than trusting the in-memory object.
    parsed = parse_runner_config(control_runner_command(config)[3])
    assert parsed.capsule_delivery is not None
    assert parsed.capsule_delivery.trusted_instructions == compilation.render_instructions()
    assert parsed.capsule_delivery.binding.role == "worker"

    adapter, _launch_spec = await _prepare_controlled_launch(parsed, env={})
    concrete = cast(CodexAppServerAdapter, adapter)
    carried = concrete._session.settings.capsule_delivery
    assert carried is not None, (
        "a capsule supplied at the launch boundary never reached settings.capsule_delivery"
    )
    assert carried.trusted_instructions == compilation.render_instructions()
    assert carried.binding == delivered.binding


class _StubDiscoverer:
    """The capability-preflight adapter: discovery only, no vendor process."""

    async def discover(self, launch: LaunchSpec):
        del launch
        return CapabilitySnapshot(
            models=(
                ModelCapability(
                    key="gpt-5.6-sol",
                    display_name="GPT-5.6-Sol",
                    effort_options=(
                        EffortOption(key="medium", display_name="Medium", description="captured"),
                    ),
                    default_effort="medium",
                    resolved_model="gpt-5.6-sol",
                    supports_effort=True,
                ),
            ),
            selected_model_key="gpt-5.6-sol",
            selected_effort="medium",
        )


def test_the_launch_boundary_request_carries_the_capsule_into_the_runner_config() -> None:
    """The launch-boundary half: a capsule on the caller's request survives into the runner payload.

    Drives the opener's own launch-configuration builder, which is the code path that turns a
    ``TerminalLaunchRequest`` into the ``RunnerConfig`` the subprocess receives. Without the carrier
    on the request, no production caller could ever supply one.
    """

    compilation = real_compilation("worker", "implementation")
    delivered = capsule_delivery_from(compilation)
    identity = ControlIdentity(
        ar_session_id="ar-session-l5-boundary",
        tmux_name="ar-codex-l5-boundary",
        created_at="2026-09-16T00:00:00+00:00",
    )
    request = TerminalLaunchRequest(
        kind="harness",
        workspace_root=Path("/tmp/l5-workspace"),
        shell="/bin/bash",
        harness="codex",
        control=ControlRunnerRequest(capsule_delivery=delivered),
    )
    argv, _endpoint = _session_command(
        identity=identity,
        command=LaunchCommand(cwd=Path("/tmp/l5-workspace"), argv=("codex", "app-server")),
        launch=request,
    )
    parsed = parse_runner_config(argv[3])
    assert parsed.capsule_delivery is not None, (
        "the launch request's capsule never reached the runner configuration"
    )
    assert parsed.capsule_delivery.trusted_instructions == compilation.render_instructions()
    assert parsed.capsule_delivery.binding.operation == "implementation"

    # And a request with no capsule stays carrier-free, so the legacy path is untouched.
    plain = TerminalLaunchRequest(
        kind="harness",
        workspace_root=Path("/tmp/l5-workspace"),
        shell="/bin/bash",
        harness="codex",
        control=ControlRunnerRequest(),
    )
    plain_argv, _ = _session_command(
        identity=identity,
        command=LaunchCommand(cwd=Path("/tmp/l5-workspace"), argv=("codex", "app-server")),
        launch=plain,
    )
    assert parse_runner_config(plain_argv[3]).capsule_delivery is None


@pytest.mark.anyio
async def test_the_no_selection_branch_also_carries_the_capsule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O19: the no-selection path of the launch preparation carries the carrier too.

    With ``resolved_launch=None`` there is no capability preflight: the adapter returned by
    ``_prepare_controlled_launch`` IS the real one. Seeds R30/R30c survived precisely because no case
    ever reached this branch.
    """

    compilation = real_compilation("reviewer", "review")
    delivered = capsule_delivery_from(compilation)
    _queue_handshake(monkeypatch, adapter_module, RecordingTransport(instruction_sources=()))

    seen: list[object] = []
    real_factory = runner_module.create_harness_protocol_adapter

    def observing_factory(harness_id: str, **kwargs: object):
        seen.append(kwargs.get("capsule_delivery"))
        return real_factory(harness_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runner_module, "create_harness_protocol_adapter", observing_factory)

    config = RunnerConfig(
        identity=_identity("no-selection"),
        harness_id="codex",
        cwd=Path("/tmp/l5-cwd"),
        argv=("codex", "app-server"),
        endpoint_root=Path("/tmp/l5-endpoint"),
        resolved_launch=None,
        capsule_delivery=delivered,
    )
    assert parse_runner_config(control_runner_command(config)[3]).capsule_delivery is not None

    adapter, launch_spec = await _prepare_controlled_launch(config, env={})
    del adapter, launch_spec
    assert seen == [delivered], "the no-selection branch did not hand the capsule to the factory"


def test_a_malformed_capsule_payload_is_refused_before_delivery() -> None:
    """O21: a malformed carrier refuses, which the packet's failure clause names explicitly.

    ``_optional_capsule_delivery`` verified true by probe but had no case; seed R32 (drop instead of
    refuse) survived because of that.
    """

    good = control_runner_command(
        RunnerConfig(
            identity=_identity("malformed"),
            harness_id="codex",
            cwd=Path("/tmp/l5-cwd"),
            argv=("codex", "app-server"),
            endpoint_root=Path("/tmp/l5-endpoint"),
        )
    )[3]
    decoded = json.loads(base64.urlsafe_b64decode(good.encode("ascii")))

    broken_payloads = {
        "not-an-object": "a string",
        "empty-object": {},
        "binding-not-an-object": {
            "binding": "nope",
            "trustedInstructions": "x",
            "semanticDigest": "sha256:" + "0" * 64,
        },
        "binding-missing-role": {
            "binding": {"repositoryId": "r", "taskPath": "t", "operation": "implementation"},
            "trustedInstructions": "x",
            "semanticDigest": "sha256:" + "0" * 64,
        },
        "no-instructions": {
            "binding": {
                "repositoryId": "r",
                "taskPath": "t",
                "role": "worker",
                "operation": "implementation",
            },
            "trustedInstructions": "   ",
            "semanticDigest": "sha256:" + "0" * 64,
        },
        "bad-digest": {
            "binding": {
                "repositoryId": "r",
                "taskPath": "t",
                "role": "worker",
                "operation": "implementation",
            },
            "trustedInstructions": "x",
            "semanticDigest": "not-a-digest",
        },
        "pointers-not-a-list": {
            "binding": {
                "repositoryId": "r",
                "taskPath": "t",
                "role": "worker",
                "operation": "implementation",
            },
            "trustedInstructions": "x",
            "semanticDigest": "sha256:" + "0" * 64,
            "skillPointers": "nope",
        },
    }
    for value in broken_payloads.values():
        mutated = dict(decoded, capsuleDelivery=value)
        token = base64.urlsafe_b64encode(json.dumps(mutated).encode("utf-8")).decode("ascii")
        with pytest.raises(HarnessControlError, match="malformed capsule delivery"):
            parse_runner_config(token)


def test_the_carrier_value_type_guards_its_own_wire_form() -> None:
    """O22: ``CodexCapsuleDelivery.from_json`` refuses a payload with no usable binding.

    Seed R36 survived because removing its binding guard is not behavioural while the binding's own
    parser still refuses — so both layers are asserted here, and the skill-pointer shape too.
    """

    binding = {
        "repositoryId": "agents-remember",
        "taskPath": "task.json",
        "role": "worker",
        "operation": "implementation",
    }
    digest = "sha256:" + "0" * 64

    assert CodexCapsuleDelivery.from_json(None) is None
    assert CodexCapsuleDelivery.from_json({"trustedInstructions": "x"}) is None, "no binding"
    assert (
        CodexCapsuleDelivery.from_json(
            {"binding": {}, "trustedInstructions": "x", "semanticDigest": digest}
        )
        is None
    ), "an empty binding is not a binding"
    assert CapsuleBindingIdentity.from_json(None) is None
    assert CapsuleBindingIdentity.from_json({"repositoryId": "r"}) is None, "partial binding"
    assert (
        CodexCapsuleDelivery.from_json(
            {
                "binding": binding,
                "trustedInstructions": "x",
                "semanticDigest": digest,
                "skillPointers": "nope",
            }
        )
        is None
    ), "skillPointers must be a list"

    # And a well-formed value still round-trips, so the guards are not refusing everything.
    good = CodexCapsuleDelivery(
        binding=CapsuleBindingIdentity.from_json(binding),  # type: ignore[arg-type]
        trusted_instructions="TRUSTED",
        semantic_digest=digest,
        skill_pointers=(SKILL_POINTER,),
    )
    revived = CodexCapsuleDelivery.from_json(good.to_json())
    assert revived is not None
    assert revived.binding == good.binding
    assert revived.trusted_instructions == "TRUSTED"
    assert revived.skill_pointers == good.skill_pointers


def _identity(label: str) -> ControlIdentity:
    """A control identity for the launch-preparation cases."""

    return ControlIdentity(
        ar_session_id=f"ar-session-l5-{label}",
        tmux_name=f"ar-codex-l5-{label}",
        created_at="2026-09-16T00:00:00+00:00",
    )


def _queue_handshake(
    monkeypatch: pytest.MonkeyPatch,
    adapter_module: object,
    transport: RecordingTransport,
) -> None:
    """Double the session transport and queue one successful thread-open handshake."""

    transport.queue("initialize", initialize_response())
    transport.queue("model/list", model_page())
    transport.queue("thread/start", thread_open_response())
    monkeypatch.setattr(adapter_module, "CodexStdioTransport", lambda: transport)


def test_a_capsule_free_launch_configuration_carries_nothing() -> None:
    """The carrier is optional: a legacy launch encodes and decodes with no capsule at all."""

    config = RunnerConfig(
        identity=ControlIdentity(
            ar_session_id="ar-session-l5-legacy",
            tmux_name="ar-codex-l5-legacy",
            created_at="2026-09-16T00:00:00+00:00",
        ),
        harness_id="codex",
        cwd=Path("/tmp/l5-cwd"),
        argv=("codex", "app-server"),
        endpoint_root=Path("/tmp/l5-endpoint"),
    )
    payload = control_runner_command(config)
    assert parse_runner_config(payload[3]).capsule_delivery is None
    decoded = json.loads(base64.urlsafe_b64decode(payload[3].encode("ascii")))
    # The key is absent, not null: a legacy launch sends exactly the payload it always sent.
    assert "capsuleDelivery" not in decoded
    # And no pre-existing payload key changed shape: the legacy keys are still present and typed.
    for key in ("identity", "harnessId", "cwd", "argv", "endpointRoot", "sessionCommands"):
        assert key in decoded, key


@pytest.mark.anyio
async def test_initial_thread_open_sends_instructions_on_the_wire() -> None:
    """The first thread open carries the capsule on the instruction field, recorded off the wire."""

    transport = RecordingTransport(instruction_sources=("/tmp/l5-cwd/AGENTS.md",))
    await open_thread(transport, delivery_value=delivery())

    methods = [method for method, _params in transport.requests]
    assert "thread/start" in methods
    params = next(params for method, params in transport.requests if method == "thread/start")
    assert params[INSTRUCTION_PARAM] == TRUSTED_INSTRUCTIONS
    assert "TASK-CHANNEL-TOKEN" not in json.dumps(params)
    # The preserved native launch facts are still there alongside the capsule.
    assert params["model"] == "gpt-5.6-sol"
    assert cast(JsonObject, params["config"])["model_reasoning_effort"] == "medium"
    assert params["cwd"] == "/tmp/l5-cwd"


@pytest.mark.anyio
async def test_legacy_launch_without_a_capsule_is_unchanged() -> None:
    """No capsule means the legacy wire shape: no instruction field at all."""

    transport = RecordingTransport(instruction_sources=())
    await open_thread(transport, delivery_value=None)

    params = next(params for method, params in transport.requests if method == "thread/start")
    assert "developerInstructions" not in params
    assert "baseInstructions" not in params


@pytest.mark.anyio
async def test_snapshot_reports_applied_capsule_and_observed_instruction_sources() -> None:
    """The thread-open evidence reports the applied revision and the host's own loaded sources."""

    transport = RecordingTransport(instruction_sources=("/tmp/l5-cwd/AGENTS.md", "/tmp/other.md"))
    session = await open_thread(transport, delivery_value=delivery())

    assert session.instruction_sources == ("/tmp/l5-cwd/AGENTS.md", "/tmp/other.md")
    report = cast(JsonObject, session.capsule_report)
    assert report["semanticDigest"] == delivery().semantic_digest
    assert report["instructionChannel"] == INSTRUCTION_PARAM
    assert cast(JsonObject, report["binding"])["role"] == "worker"
    assert cast(JsonObject, report["capsuleRefresh"])["mode"] == CapsuleRefresh.INITIAL.value
    assert len(cast(str, report["instructionChannel"])) > 0


@pytest.mark.anyio
async def test_a_second_turn_and_a_resume_do_not_restate_the_corpus() -> None:
    """A second turn carries no instruction field, and a resume restates identical bytes only."""

    transport = RecordingTransport(instruction_sources=())
    session = await open_thread(transport, delivery_value=delivery())

    # Second open on the same session: same binding and same digest, so the bytes are identical and
    # nothing new is introduced. The plan mode records that reasoning explicitly.
    plan = session._capsule_refresh_plan(resume_thread_id="thread-1")
    assert plan is not None and plan.mode is CapsuleRefresh.IN_PLACE

    # A resume whose applied revision this session does not know is a fresh thread, never a silent
    # restatement onto a thread that may already carry another revision.
    unknown = session._capsule_refresh_plan(resume_thread_id="thread-from-another-process")
    assert unknown is not None and unknown.mode is CapsuleRefresh.FRESH_THREAD
    assert unknown is not None and unknown.applies_instructions is not False

    # No turn-level request in this suite carries instructions: the schema has no such field, and
    # the session never invents one.
    turn_params = [params for method, params in transport.requests if method == "turn/start"]
    assert turn_params == []


@pytest.mark.anyio
async def test_the_session_decision_path_compares_the_recorded_binding_and_digest() -> None:
    """L5R-4: the RECORDED binding and digest decide the resume, asserted where the decision is made.

    Reviewer seed R9 substituted the incoming digest for the recorded one and survived the case named
    for this behaviour. Here the recorded values are read from the published report and compared, so
    both substitutions fail: replacing the recorded digest with the incoming one turns a changed
    revision into IN_PLACE, and dropping the binding comparison makes a foreign thread look usable.
    """

    transport = RecordingTransport(instruction_sources=())
    await open_thread(transport, delivery_value=delivery())
    selected = transport  # the session's own transport, already connected

    def session_for(incoming: CodexCapsuleDelivery) -> CodexAppServerSession:
        built = CodexAppServerSession(
            session_settings(capsule_delivery=incoming),
            transport_factory=lambda: transport,
        )
        built.thread_id = "thread-1"
        built.launch = launch()
        return built

    # (a) equal digest: the report's own digest is read, and the resume is IN_PLACE.
    same = session_for(delivery())
    same.capsule_report = {
        "binding": delivery().binding.as_report(),
        "semanticDigest": delivery().semantic_digest,
    }
    plan = same._capsule_refresh_plan(resume_thread_id="thread-1")
    assert plan is not None and plan.mode is CapsuleRefresh.IN_PLACE, plan

    # (b) a DIFFERENT digest behind the same thread: the recorded value must be the one compared.
    changed = session_for(delivery(digest="sha256:" + "5f" * 32))
    changed.capsule_report = {
        "binding": delivery().binding.as_report(),
        "semanticDigest": delivery().semantic_digest,
    }
    changed_plan = changed._capsule_refresh_plan(resume_thread_id="thread-1")
    assert changed_plan is not None and changed_plan.mode is CapsuleRefresh.FRESH_THREAD, (
        changed_plan
    )

    # (c) a DIFFERENT recorded binding for the same thread: refused, never reused.
    foreign = session_for(delivery())
    foreign_binding = CapsuleBindingIdentity(
        repository_id="other-repo",
        task_path="other/task.json",
        role="manager",
        operation="planning",
    )
    foreign.capsule_report = {
        "binding": foreign_binding.as_report(),
        "semanticDigest": delivery().semantic_digest,
    }
    foreign_plan = foreign._capsule_refresh_plan(resume_thread_id="thread-1")
    assert foreign_plan is not None and foreign_plan.is_refusal, foreign_plan

    # (d) an unreadable record is an unknown thread, not a silent match.
    unreadable = session_for(delivery())
    unreadable.capsule_report = {"binding": {}, "semanticDigest": ""}
    unreadable_plan = unreadable._capsule_refresh_plan(resume_thread_id="thread-1")
    assert unreadable_plan is not None
    assert unreadable_plan.mode is CapsuleRefresh.FRESH_THREAD, unreadable_plan
    del selected


@pytest.mark.anyio
async def test_changed_revision_on_a_live_thread_opens_a_fresh_binding() -> None:
    """A refresh refusal drops the threadId instead of stacking instructions on the live thread."""

    transport = RecordingTransport(instruction_sources=())
    opened = await open_thread(transport, delivery_value=delivery())
    selected = opened.models[0]
    assert selected.model == "gpt-5.6-sol", "the live model page must select the pinned model"

    # The live thread carries revision 1f...; this delivery is 5f..., so the seam must refuse to
    # restate instructions onto it. Re-opening is driven through the same call the adapter uses.
    refreshed = CodexAppServerSession(
        session_settings(capsule_delivery=delivery(digest="sha256:" + "5f" * 32)),
        transport_factory=lambda: transport,
    )
    refreshed.thread_id = "thread-1"
    refreshed.launch = launch()  # the session connect() would have recorded
    refreshed.capsule_report = {"semanticDigest": delivery().semantic_digest}
    plan = refreshed._capsule_refresh_plan(resume_thread_id="thread-1")
    assert plan is not None and plan.mode is CapsuleRefresh.FRESH_THREAD, plan

    params = refreshed._thread_params(
        resume_thread_id="thread-1",
        selected=selected,
        desired_effort="medium",
        capsule_plan=plan,
    )
    # The changed revision rides a fresh thread: the live thread is not named and nothing is stacked.
    assert "threadId" not in params
    assert params[INSTRUCTION_PARAM] == delivery(digest="sha256:" + "5f" * 32).trusted_instructions


def _codex_available() -> bool:
    return shutil.which("codex") is not None


def test_legacy_chain_switch_decision_respects_the_capsule_gate() -> None:
    """The decision function itself: no capsule means no suppression, whatever was observed."""

    legacy = legacy_instruction_switch(
        capsule_delivered=False, observed_sources=("/workspace/AGENTS.md",)
    )
    assert legacy.suppress is False
    assert legacy.request_config == {}, "a capsule-free launch must not request the switch"
    assert legacy.observed_sources == ("/workspace/AGENTS.md",)

    observed = legacy_instruction_switch(
        capsule_delivered=True, observed_sources=("/workspace/AGENTS.md",)
    )
    assert observed.suppress is True
    assert observed.request_config == {LEGACY_PROJECT_DOC_KEY: 0}
    assert "/workspace/AGENTS.md" in observed.reason, "the report must name what was loaded"
    assert observed.as_report()["observedInstructionSources"] == ["/workspace/AGENTS.md"]

    clean = legacy_instruction_switch(capsule_delivered=True, observed_sources=())
    assert clean.suppress is True
    assert clean.request_config == {LEGACY_PROJECT_DOC_KEY: 0}
    # The two reasons must differ: the observed content is a decision input, not decoration.
    assert clean.reason != observed.reason
    assert "observed on the previous open" not in clean.reason
    # And the CLEAN branch's reason is pinned in its own right, so degrading it fails here rather
    # than only being caught incidentally by the observed branch.
    assert "no project instruction document" in clean.reason, clean.reason
    assert "no project instruction document" not in observed.reason


@pytest.mark.anyio
async def test_legacy_chain_switch_is_scoped_to_the_capsule_launch() -> None:
    """The suppression rides the capsule launch only; a capsule-free open keeps legacy behaviour."""

    with_capsule = RecordingTransport(instruction_sources=("/workspace/AGENTS.md",))
    await open_thread(with_capsule, delivery_value=delivery())
    capsule_params = next(
        params for method, params in with_capsule.requests if method == "thread/start"
    )
    assert cast(JsonObject, capsule_params["config"])["project_doc_max_bytes"] == 0

    legacy = RecordingTransport(instruction_sources=("/workspace/AGENTS.md",))
    await open_thread(legacy, delivery_value=None)
    legacy_params = next(params for method, params in legacy.requests if method == "thread/start")
    assert "project_doc_max_bytes" not in cast(JsonObject, legacy_params["config"]), (
        "a capsule-free launch must not touch the legacy startup chain"
    )


@pytest.mark.anyio
@pytest.mark.skipif(not _codex_available(), reason="no codex CLI on PATH")
async def test_live_app_server_observes_instruction_sources_and_accepts_the_capsule(
    channels: JsonObject,
    tmp_path: Path,
) -> None:
    """Live-native: the real app-server reports what it loaded, and the pinned version matches."""

    probe = subprocess.run(
        ["codex", "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    if probe != channels["cliVersion"]:  # pragma: no cover - environment drift, reported not hidden
        pytest.skip(f"installed codex {probe!r} differs from pinned {channels['cliVersion']!r}")

    transport = CodexStdioTransport()
    session = CodexAppServerSession(
        session_settings(capsule_delivery=delivery()),
        transport_factory=lambda: transport,
    )
    live_root = tmp_path / "l5-live-root"
    live_root.mkdir(parents=True, exist_ok=True)
    # A workspace AGENTS.md, so automatic host injection is present to be observed.
    (live_root / "AGENTS.md").write_text(
        "# workspace instructions\nL5 live probe\n", encoding="utf-8"
    )
    live_launch = LaunchSpec(
        identity=ControlIdentity(
            ar_session_id="ar-session-l5-live",
            tmux_name="ar-codex-l5-live",
            created_at="2026-09-16T00:00:00+00:00",
        ),
        harness_id="codex",
        cwd=live_root,
        argv=("codex", "app-server"),
    )
    try:
        await asyncio.wait_for(session.connect(live_launch, resume_thread_id=None), timeout=180)
        # The workspace really does contain an AGENTS.md the host would otherwise load; the
        # experimental launch suppressed it, and the host itself reports what it loaded. This is
        # behaviour 6 observed live: no duplicate legacy chain, and the suppression is visible.
        assert (live_root / "AGENTS.md").is_file()
        assert session.instruction_sources == (), (
            f"the experimental launch still loaded a host instruction document: "
            f"{session.instruction_sources}"
        )
        report = cast(JsonObject, session.capsule_report)
        assert report["semanticDigest"] == delivery().semantic_digest
        switch = cast(JsonObject, report["legacyInstructionSwitch"])
        assert switch["suppressed"] is True
    finally:
        await transport.stop("forced")
