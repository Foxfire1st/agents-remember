"""260915-CAPS-L15 — the compiled capsule reaches a started session's own first prompt.

The master built and individually proved every link of the capsule chain and left the top severed:
the compiler (L2), the admission/MCP surface (L4), the Codex instruction seam (L5) and the eve
carrier (L7) were each green, and **no production launch point supplied a capsule to a session**.
Every green test hand-supplied the intermediate value, which is exactly why no leaf's suite could
see the gap (``notes/leaf-runbook.md``, standing rule added 2026-09-16).

This module closes it with the shape that rule requires — a production path from producer to
consumer with the artifact read from the **consumer's own side**:

* :func:`test_a_task_attached_seat_reads_its_compiled_capsule_out_of_its_own_first_prompt` starts a
  leaf seat through the internal spawn primitive (the launch point ``dispatch_agent`` calls), with a
  real task document and a real enclosure contract;
* :func:`test_a_free_agent_reads_its_compiled_capsule_out_of_its_own_first_prompt` starts a
  ``bootstrap`` seat with **no task document** through the dashboard's production
  ``POST /api/terminal/{session}`` route.

In both, the runner argv the launch point actually built is parsed back out of the base64 token the
child process would be handed, the **real** runner preparation and the **real** adapter factory run,
and the capsule is read from the ``thread/start`` request the session sent to the vendor boundary.
The expected side is the compiler's own result for the same seat, never a hand-built literal.

The remaining cases pin the parts that must stay true around that delivery: the mode gate, the
byte-identical legacy payload, the single-capsule payload bound (D12), the enumeration of every
production ``TerminalLaunchRequest`` site, D13's registered-boundary repair, D20's fail-open, and the
free agent's named-absence admission.
"""

from __future__ import annotations

import ast
import base64
import json
import os
import shutil
import subprocess
import tempfile
from collections import deque
from collections.abc import AsyncIterator, Iterator, Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from agents_remember.application.role_capsules.launch import (
    FREE_AGENT_TASK_REFERENCE_PREFIX,
    FreeAgentSeatAdmission,
    compile_launch_capsule,
    free_agent_seat_admission,
)
from agents_remember.application.skill_resources import (
    CapsuleCompileRequest,
    compile_task_capsule,
)
from agents_remember.application.skill_resources.operation import (
    CapsuleOperationRequest,
    role_capsule_compile_tool,
)
from agents_remember.application.task_projection import parse_task_reference
from agents_remember.application.terminal_tools import (
    SpawnOverrides,
    SpawnSeat,
    spawn_agent_session_tool,
)
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.errors import HarnessControlError, TaskProjectionSourceError
from agents_remember.kernel.agentic_settings import agentic_settings_path
from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.kernel.eve_runtime_readiness import eve_runtime_readiness
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.registration.capsule_serving import register_capsule_and_skill_tools
from agents_remember.models.conversations.control_wire import ControlIdentity, LaunchSpec
from agents_remember.models.role_capsules.types import CapsuleDigest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer import reset_ambient
from agents_remember.serving import codex_app_server_adapter as adapter_module
from agents_remember.serving.app import create_app
from agents_remember.serving.codex_app_server_adapter import CodexAppServerAdapter
from agents_remember.serving.codex_mcp_readiness import CodexMcpToolReadiness
from agents_remember.serving.conversation.library.open_service import (
    LIBRARY_REOPEN_LEGACY_REASON,
)
from agents_remember.serving.eve_runtime_launch import (
    launch_spec_binding,
    stage_runtime_root,
    verify_capsule_binding,
)
from agents_remember.serving.harness_control_models import ShutdownMode
from agents_remember.serving.harness_control_runner import (
    RunnerConfig,
    _prepare_controlled_launch,
    control_runner_command,
    parse_runner_config,
)
from agents_remember.serving.launch_capsule import (
    CAPSULE_CARRIER_HARNESSES,
    LEGACY_SESSION_ROLES,
    LaunchCapsuleMode,
    LaunchCapsuleRequest,
    legacy_launch_capsule,
    resolve_launch_capsule,
)
from agents_remember.serving.projector import ProjectionCadence
from agents_remember.serving.terminal import (
    TerminalHost,
    TerminalSessionBinding,
    TerminalSessionSpec,
)
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.worktrees.worktree_contract import load_contract
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP
from test_spawn_agent_session import _write_leaf_task
from test_worktree_support import write_current_task_lineage

LEAF_REF = TaskDocumentRef(repository="repo", path="master/leaf-1.json")
MASTER = "master"
REPO = "repo"
LEAF_ID = "leaf-1"
MODEL_PAGE_FIXTURE = Path(__file__).parent / "fixtures" / "codex_app_server_model_page.json"
SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "agents_remember"


# --------------------------------------------------------------------------------------------
# A recording stand-in for the vendor process boundary.
# --------------------------------------------------------------------------------------------


class RecordingTransport:
    """A protocol-faithful transport that records exactly what the real session sent.

    The session under test is the production one — built by the production adapter factory — and
    only the vendor process at the other end of its stdio is doubled, which is what makes the
    recorded ``thread/start`` request the *consumer's* view of the capsule rather than a seam read.
    """

    def __init__(self, *, thread_id: str = "thread-l15") -> None:
        self.requests: list[tuple[str, dict[str, object]]] = []
        self.launches: list[LaunchSpec] = []
        self.thread_id = thread_id
        self.stopped: list[ShutdownMode] = []
        self._responses: dict[str, deque[dict[str, object]]] = {}

    def queue(self, method: str, response: dict[str, object]) -> None:
        self._responses.setdefault(method, deque()).append(response)

    async def start(self, launch: LaunchSpec) -> None:
        self.launches.append(launch)

    async def request(
        self, method: str, params: Mapping[str, object], *, before_write: object = None
    ) -> dict[str, object]:
        del before_write
        self.requests.append((method, dict(params)))
        if method in {"thread/start", "thread/resume"}:
            return self._thread_open_reply(method, params)
        return deepcopy(self._responses[method].popleft())

    def _thread_open_reply(self, method: str, params: Mapping[str, object]) -> dict[str, object]:
        """The vendor's reply, echoing exactly what the session asked to open.

        The real app-server echoes the model and effort it accepted; a fixed reply would make this
        case assert its own fixture instead of the launch's own selection.
        """

        config = params.get("config")
        effort = config.get("model_reasoning_effort") if isinstance(config, Mapping) else None
        thread_id = params.get("threadId") if method == "thread/resume" else self.thread_id
        return {
            "thread": {
                "id": thread_id or self.thread_id,
                "cliVersion": "0.151.0",
                "turns": [],
                "status": {"type": "idle"},
            },
            "model": params.get("model") or "gpt-5.6-sol",
            "modelProvider": "openai",
            "cwd": params.get("cwd"),
            "reasoningEffort": effort,
            "instructionSources": [],
        }

    async def notify(self, method: str, params: Mapping[str, object]) -> None:
        del method, params

    def messages(self) -> AsyncIterator[dict[str, object]]:
        return self._messages()

    async def _messages(self) -> AsyncIterator[dict[str, object]]:
        if False:  # pragma: no cover - keeps this an async generator, like the real transport
            yield {}

    async def respond(self, request_id: object, result: Mapping[str, object]) -> None:
        del request_id, result

    async def respond_error(self, request_id: object, *, code: int, message: str) -> None:
        del request_id, code, message

    async def stop(self, mode: ShutdownMode) -> None:
        self.stopped.append(mode)

    def params_of(self, method: str) -> dict[str, object]:
        """The one recorded request for ``method``; a missing or repeated one is a failure."""

        matches = [params for name, params in self.requests if name == method]
        assert len(matches) == 1, f"expected exactly one {method!r} request, recorded {matches!r}"
        return matches[0]


def _queued_transport() -> RecordingTransport:
    """A transport that answers the app-server's own open sequence, then records."""

    transport = RecordingTransport()
    transport.queue(
        "initialize",
        {
            "codexHome": "/tmp/codex-home",
            "platformFamily": "unix",
            "platformOs": "linux",
            "userAgent": (
                "agents_remember/0.151.0 (Ubuntu 22.4.0; x86_64) (agents_remember; 3.0.0)"
            ),
        },
    )
    transport.queue(
        "model/list",
        cast(dict[str, object], json.loads(MODEL_PAGE_FIXTURE.read_text(encoding="utf-8"))),
    )
    return transport


async def _first_prompt_from(
    token: str,
    *,
    spawn_env: Mapping[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    """Run the real runner + adapter over the exact token a launch point produced.

    ``token`` is the encoded launch configuration the child process would receive; parsing it back
    is what proves the delivery survived the launch boundary rather than only existing in memory.
    """

    config = parse_runner_config(token)
    transport = _queued_transport()

    # The role's MCP-readiness gate is a separate, separately-tested startup gate; it is stubbed to
    # a fixed admitted value so this case observes the FIRST PROMPT and nothing else.
    async def admitted(*_args: object, **_kwargs: object) -> CodexMcpToolReadiness:
        return CodexMcpToolReadiness(
            server_name="agents-remember",
            runtime_status="ready",
            tool_name="ping",
            tool_count=1,
        )

    monkeypatch.setattr(adapter_module, "wait_for_codex_mcp_tool", admitted)

    adapter, launch_spec = await _prepare_controlled_launch(config, env=dict(spawn_env))
    concrete = cast(CodexAppServerAdapter, adapter)
    # The adapter factory owns the transport choice (default argument bound at definition time), so
    # the vendor boundary is doubled on the session the factory produced rather than by patching the
    # class name the default already captured.
    object.__setattr__(concrete._session, "_transport_factory", lambda: transport)
    await concrete.start(launch_spec)
    await transport.stop("forced")
    return transport.params_of("thread/start")


# --------------------------------------------------------------------------------------------
# A fake terminal host: the tmux process boundary, doubled.
# --------------------------------------------------------------------------------------------


class _FakeHost:
    """The tmux boundary, doubled for the OPEN path only.

    The opener resolves a command, ensures the session and upserts a row; attaching a PTY client to
    an already-created session is a different path with its own doubles, so the remaining host
    surface refuses loudly rather than pretending.
    """

    def __init__(self) -> None:
        self.ensured: list[TerminalSessionSpec] = []
        self.known: set[str] = set()

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.known

    def shutdown(self) -> None:
        return None

    def ensure(self, sid: str, spec: TerminalSessionSpec) -> TerminalSessionBinding:
        self.ensured.append(spec)
        tmux_name = spec.tmux_name_for(sid)
        self.known.add(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=spec.cwd,
            command=spec.command,
            lifecycle_id=spec.lifecycle_id,
            suspend_unsafe=spec.suspend_unsafe,
        )

    def last_spec(self) -> TerminalSessionSpec:
        assert self.ensured, "no session was ever ensured by the opener"
        return self.ensured[-1]


@pytest.fixture(name="world")
def world_fixture() -> Iterator[tuple[Path, McpRuntimeConfig, _FakeHost, Path]]:
    """A real coordination world under a SHORT root, removed afterwards.

    Short on purpose: the opener mints a Unix-domain control socket under the coordination root and
    the 103-byte path limit is a real production constraint, so a deep pytest temporary directory
    would fail the launch for a reason that has nothing to do with this leaf.
    """

    root = Path(tempfile.mkdtemp(prefix="l15-"))
    try:
        yield (root, *_scratch_world(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _scratch_world(tmp_path: Path) -> tuple[McpRuntimeConfig, _FakeHost, Path]:
    """A real coordination world: a task, its enclosure contract, a worktree and its memory root.

    The coordination surface the compiler resolves through is the real one, so the world has to be
    complete: the enclosure lookup goes through the project's own memory resolution, which refuses
    when the repository's external memory root is absent.
    """

    memory_root = tmp_path / "memory-repos" / f"ar-{REPO}"
    (memory_root / "system").mkdir(parents=True, exist_ok=True)
    (memory_root / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory_root / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
    _write_leaf_task(tmp_path)
    repo = write_current_task_lineage(tmp_path, repo_name=REPO, master_name=MASTER, leaf_id=LEAF_ID)
    _materialize_admitted_worktree(tmp_path, repo)
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs" / "mcp",
        repositories={REPO: RepositoryScope(repo_id=REPO, path=repo)},
    )
    return config, _FakeHost(), repo


def _materialize_admitted_worktree(coordination_root: Path, repo: Path) -> Path:
    """Create the code worktree the enclosure contract declares, on its admitted branch.

    ``write_current_task_lineage`` writes a complete contract and the repository it points at; the
    checkout itself is what a real leaf has and a fixture does not. An eve carrier binds a runtime
    to that worktree and proves its branch, so the fixture world has to have one.
    """

    contract = load_contract(
        coordination_root / "tasks" / REPO / MASTER / "enclosures" / LEAF_ID / "series-contract.md"
    )
    worktree = Path(contract.code_worktree)
    worktree.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", str(worktree), contract.code_work_branch],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return worktree


def _write_settings(root: Path, *, harness: str, with_selection: bool = True) -> None:
    """Point the worker/bootstrap roles at one harness — the settings-owned launch selection.

    ``harness="eve"`` also teaches the registry an ``eve`` row with a program on PATH, which is the
    documented operator surface for making a harness launchable (``orchestration.harnesses``); the
    shipped eve row deliberately names no program because the adapter starts its runtime itself.
    """

    role: dict[str, object] = {"harness": harness}
    if with_selection:
        role.update({"model": "gpt-5.6-sol", "effort": "medium"})
    payload: dict[str, object] = {"roles": {"worker": dict(role), "bootstrap": dict(role)}}
    if harness == "eve":
        payload["harnesses"] = {"eve": {"command": EVE_STUB_PROGRAM, "argv": [EVE_STUB_PROGRAM]}}
    path = agentic_settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"orchestration": payload}), encoding="utf-8")


def _write_codex_settings(root: Path) -> None:
    """Point the worker role at the codex harness — the settings-owned launch selection."""

    _write_settings(root, harness="codex")


EVE_STUB_PROGRAM = "ar-l15-eve-fixture"


def _eve_stub_bin(root: Path) -> Path:
    """A PATH program the taught eve row can resolve, so the launch is detected as launchable."""

    directory = root / "bin"
    directory.mkdir(parents=True, exist_ok=True)
    program = directory / EVE_STUB_PROGRAM
    program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    program.chmod(0o755)
    return directory


def _version_reporting_node(root: Path, version: str = "v24.19.0") -> Path:
    """A Node >= 24 interpreter, declared the way an operator declares one (``AR_EVE_NODE``).

    The eve row's readiness probe resolves the AR-owned application and an interpreter new enough
    for the pinned release; this case is about the **workspace** the launch runs in, so the
    interpreter is declared rather than searched — the same environment precondition
    ``which=_installed`` is for the codex cases. The live run in the fix-round evidence uses the
    real interpreter.
    """

    path = root / "node-fixture"
    path.write_text(f'#!/bin/sh\necho "{version}"\n', encoding="utf-8")
    path.chmod(0o755)
    return path


def _declare_eve_launchable(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make an eve launch detectable here: the taught registry row, its program and its node."""

    monkeypatch.setenv("AR_EVE_NODE", str(_version_reporting_node(root)))
    monkeypatch.setenv("PATH", f"{_eve_stub_bin(root)}{os.pathsep}{os.environ['PATH']}")
    assert eve_runtime_readiness().ready, (
        "the AR-owned eve application must be resolvable for this case to exercise the chain"
    )


ADMITTED_WORKTREE_CONTRACT = None  # resolved per world by _admitted_worktree()


def _installed(name: str) -> str:
    return f"/usr/bin/{name}"


def _admitted_worktree(root: Path) -> Path:
    """The code worktree the scratch world's enclosure contract admits."""

    contract = load_contract(
        root / "tasks" / REPO / MASTER / "enclosures" / LEAF_ID / "series-contract.md"
    )
    return Path(contract.code_worktree)


def _tool_payload(result: object) -> dict[str, object]:
    """The JSON body of a FastMCP ``call_tool`` result, whichever shape the SDK returns it in."""

    if isinstance(result, dict):
        return cast(dict[str, object], result)
    if isinstance(result, tuple):
        _content, structured = result
        if isinstance(structured, dict):
            return cast(dict[str, object], structured)
        result = _content
    blocks = result if isinstance(result, list) else [result]
    for block in blocks:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, str):
            text = block
        if text:
            return cast(dict[str, object], json.loads(text))
    raise AssertionError(f"no JSON body in the tool result: {result!r}")


# --------------------------------------------------------------------------------------------
# The acceptance: a started session's own first prompt.
# --------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_task_attached_seat_reads_its_compiled_capsule_out_of_its_own_first_prompt(
    world: tuple[Path, McpRuntimeConfig, _FakeHost, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production path: spawn primitive -> opener argv -> runner -> the vendor's thread/start."""

    tmp_path, config, host, repo = world
    _write_codex_settings(tmp_path)
    reset_ambient()
    payload = spawn_agent_session_tool(
        config,
        seat=SpawnSeat(
            kind="harness",
            task_document_ref=LEAF_REF,
            label="L15 acceptance",
            env={"AR_SPAWN_ROLE": "worker"},
        ),
        overrides=SpawnOverrides(
            session_id="l15-task", host=cast(TerminalHost, host), which=_installed
        ),
    )
    assert payload["ok"] is True, payload
    assert payload["instructionMode"]["mode"] == "capsule", payload["instructionMode"]

    spec = host.last_spec()
    token = spec.command[-1]
    prompt = await _first_prompt_from(token, spawn_env=spec.env or {}, monkeypatch=monkeypatch)

    # The expected side is the compiler's own result for the same admitted seat, read through the
    # same application entry point the MCP operation uses — never a hand-built literal.
    expected = compile_task_capsule(
        config,
        CapsuleCompileRequest(
            enclosure=EnclosureSelector(task_name=MASTER, leaf_id=LEAF_ID),
            task_path=LEAF_REF.path,
            operation="orientation",
            role="worker",
            code_repository_root=repo,
        ),
    )
    assert expected.ok, expected.explanation()
    assert expected.result is not None
    assert prompt["developerInstructions"] == expected.result.render_instructions()
    assert payload["instructionMode"]["semanticDigest"] == expected.result.capsule.semantic_digest


@pytest.mark.anyio
async def test_a_free_agent_reads_its_compiled_capsule_out_of_its_own_first_prompt(
    world: tuple[Path, McpRuntimeConfig, _FakeHost, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production path: the dashboard's open route for a role with NO task document."""

    tmp_path, config, host, _repo = world
    _write_codex_settings(tmp_path)
    reset_ambient()
    catalog_path = tmp_path / "logs" / "dashboard" / "terminal-sessions.json"
    collaborators = replace(
        serving_collaborators(config),
        terminal_host=cast(TerminalHost, host),
        terminal_catalog=TerminalCatalog(catalog_path),
    )
    app = create_app(config, cadence=ProjectionCadence(interval=100), collaborators=collaborators)
    with TestClient(app) as client:
        response = client.post(
            "/api/terminal/l15-free-agent",
            json={"kind": "harness", "harness": "codex", "role": "bootstrap", "label": "L15 free"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["instructionMode"]["mode"] == "capsule", body["instructionMode"]
    assert body["taskDocumentRef"] is None, "a free agent has no task document by construction"

    spec = host.last_spec()
    prompt = await _first_prompt_from(
        spec.command[-1], spawn_env=spec.env or {}, monkeypatch=monkeypatch
    )

    admission = free_agent_seat_admission(
        config, role="bootstrap", workspace_root=config.workspace_root
    )
    assert admission.is_taskless
    launched = compile_launch_capsule(
        config,
        LaunchCapsuleRequest(
            role="bootstrap", workspace_root=config.workspace_root, harness="codex"
        ),
    )
    assert launched.codex_delivery is not None
    assert prompt["developerInstructions"] == launched.codex_delivery.trusted_instructions
    assert body["instructionMode"]["semanticDigest"] == launched.codex_delivery.semantic_digest, (
        "the route published a capsule the session did not receive"
    )


def test_an_uncapsulable_role_refuses_by_name_before_any_host_effect(
    world: tuple[Path, McpRuntimeConfig, _FakeHost, Path],
) -> None:
    """The negative case at the dashboard route: a role with no capsule refuses, nothing starts.

    The dashboard's open route is where a role arrives unvalidated by a task binding, so it is
    where an un-compilable role can actually be presented — and the refusal must stop the launch
    before the host is touched.
    """

    tmp_path, config, host, _repo = world
    _write_codex_settings(tmp_path)
    reset_ambient()
    collaborators = replace(
        serving_collaborators(config),
        terminal_host=cast(TerminalHost, host),
        terminal_catalog=TerminalCatalog(
            tmp_path / "logs" / "dashboard" / "terminal-sessions.json"
        ),
    )
    app = create_app(config, cadence=ProjectionCadence(interval=100), collaborators=collaborators)
    with TestClient(app) as client:
        response = client.post(
            "/api/terminal/l15-refused",
            json={"kind": "harness", "harness": "codex", "role": "not-a-role"},
        )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["status"] == "capsule-unavailable"
    assert "not-a-role" in body["detail"]
    assert host.ensured == [], "a refused capsule must not reach the host"


# --------------------------------------------------------------------------------------------
# The mode gate, the legacy payload, and the size bound.
# --------------------------------------------------------------------------------------------


def test_the_mode_gate_names_capsule_legacy_and_refused_for_every_seat_class() -> None:
    """One decision point: which seats are legacy, which refuse, and which must compile."""

    workspace = Path("/tmp/l15-mode-gate")
    legacy = {
        None: "no role",
        "chat": "not an agent seat",
        "terminal": "not an agent seat",
    }
    for role, expected in legacy.items():
        answer = resolve_launch_capsule(
            None, LaunchCapsuleRequest(role=role, workspace_root=workspace, harness="codex")
        )
        assert answer.mode is LaunchCapsuleMode.LEGACY, role
        assert expected in answer.reason, (role, answer.reason)

    # A harness with no verified channel is legacy BY DECLARATION, naming the harness.
    for harness in ("claude", "pi", "custom-harness", None):
        answer = resolve_launch_capsule(
            None, LaunchCapsuleRequest(role="worker", workspace_root=workspace, harness=harness)
        )
        assert answer.mode is LaunchCapsuleMode.LEGACY, harness
        assert (harness or "no harness") in answer.reason, answer.reason

    # A role the frozen vocabulary does not admit is a refusal, never a legacy launch.
    unknown = resolve_launch_capsule(
        None, LaunchCapsuleRequest(role="bogus", workspace_root=workspace, harness="codex")
    )
    assert unknown.mode is LaunchCapsuleMode.REFUSED
    assert unknown.refusal_status == "role-not-capsule-addressable"
    assert "bogus" in unknown.refusal_detail

    # A role-configured seat on a channel-bearing harness with no compiler behind it refuses: a
    # process with no resolver cannot silently run a seat blind.
    unavailable = resolve_launch_capsule(
        None, LaunchCapsuleRequest(role="worker", workspace_root=workspace, harness="eve")
    )
    assert unavailable.mode is LaunchCapsuleMode.REFUSED
    assert unavailable.refusal_status == "capsule-resolver-unavailable"

    assert frozenset({"codex", "eve"}) == CAPSULE_CARRIER_HARNESSES
    assert frozenset({"chat", "terminal"}) == LEGACY_SESSION_ROLES


def test_the_legacy_launch_payload_is_byte_identical_to_the_pre_capsule_payload() -> None:
    """R05's invariant, measured: no capsule selected means the same payload, byte for byte.

    The expected side is built here from the payload's own documented keys — the shape the launch
    sent before this leaf existed — so a delivery that quietly changed a legacy launch's bytes
    fails here rather than passing by comparing two runs of the same new code.
    """

    identity = ControlIdentity(
        ar_session_id="ar-session-l15-legacy",
        tmux_name="ar-codex-l15-legacy",
        created_at="2026-09-17T00:00:00+00:00",
    )
    before = {
        "identity": identity.to_json(),
        "harnessId": "codex",
        "cwd": "/tmp/l15-cwd",
        "argv": ["codex", "app-server"],
        "endpointRoot": "/tmp/l15-endpoint",
        "sessionCommands": [],
        "resolvedLaunch": None,
        "resumeThreadId": None,
    }
    expected = base64.urlsafe_b64encode(
        json.dumps(before, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")

    plain = control_runner_command(
        RunnerConfig(
            identity=identity,
            harness_id="codex",
            cwd=Path("/tmp/l15-cwd"),
            argv=("codex", "app-server"),
            endpoint_root=Path("/tmp/l15-endpoint"),
        )
    )
    assert plain[3] == expected, "a capsule-free launch payload changed"

    # The same launch carrying a DECLARED legacy decision is still the same payload: a legacy
    # decision is recorded in the response, never smuggled into the child's argv.
    declared = control_runner_command(
        RunnerConfig(
            identity=identity,
            harness_id="codex",
            cwd=Path("/tmp/l15-cwd"),
            argv=("codex", "app-server"),
            endpoint_root=Path("/tmp/l15-endpoint"),
        )
    )
    assert declared[3] == expected
    assert "capsuleDelivery" not in json.loads(base64.urlsafe_b64decode(declared[3]))


def test_the_delivered_payload_carries_the_capsule_once_and_no_task_context(
    world: tuple[Path, McpRuntimeConfig, _FakeHost, Path],
) -> None:
    """D12's bound: one carrier, no second copy, and a measured encoded size."""

    tmp_path, config, _host, _repo = world
    launched = compile_launch_capsule(
        config,
        LaunchCapsuleRequest(
            role="worker",
            workspace_root=tmp_path,
            task_document_ref=LEAF_REF,
            harness="codex",
        ),
    )
    assert launched.codex_delivery is not None, launched.explain()

    kwargs = {
        "identity": ControlIdentity(
            ar_session_id="ar-session-l15-size",
            tmux_name="ar-codex-l15-size",
            created_at="2026-09-17T00:00:00+00:00",
        ),
        "harness_id": "codex",
        "cwd": tmp_path,
        "argv": ("codex", "app-server"),
        "endpoint_root": tmp_path / "endpoint",
    }
    without = control_runner_command(RunnerConfig(**kwargs))  # type: ignore[arg-type]
    with_capsule = control_runner_command(
        RunnerConfig(capsule_delivery=launched.codex_delivery, **kwargs)  # type: ignore[arg-type]
    )
    decoded = cast(dict[str, object], json.loads(base64.urlsafe_b64decode(with_capsule[3])))
    assert "capsuleDelivery" in decoded
    delivery = cast(dict[str, object], decoded["capsuleDelivery"])
    assert str(delivery["trustedInstructions"]).count("#") >= 1
    instructions = launched.codex_delivery.trusted_instructions
    # One carrier and no second copy: exactly one instruction key in the whole payload.
    assert json.dumps(decoded).count('"trustedInstructions"') == 1, decoded
    assert delivery["trustedInstructions"] == instructions
    # The task context stays out of the argv entirely: it is the ordinary turn channel's content.
    assert str(launched.report["taskReference"]) in json.dumps(decoded)
    assert "taskContext" not in delivery
    added = len(with_capsule[3]) - len(without[3])
    # Measured, not asserted: the capsule's own bytes, base64-expanded once, and no more.
    assert added < len(instructions.encode()) * 2, added
    assert cast(int, launched.report["instructionBytes"]) == len(instructions.encode())


def test_every_production_launch_request_site_is_wired_or_declares_its_legacy_chain() -> None:
    """A launch point is never silently legacy: each is wired, or it names its decision.

    The failure message names the file, the line and the two admissible dispositions, so the next
    reader cannot satisfy this by accident — the defect this leaf exists to fix was a launch point
    that simply did not set the field and looked exactly like one that had.
    """

    sites: dict[str, tuple[str, ...]] = {
        "mcp/src/agents_remember/application/terminal_tools.py": ("capsule=capsule",),
        "mcp/src/agents_remember/serving/_app_terminal_routes.py": ("capsule=capsule",),
        "mcp/src/agents_remember/serving/conversation/library/open_service.py": (
            "legacy_launch_capsule(",
        ),
    }
    found: dict[str, int] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = f"mcp/src/{path.relative_to(SRC_ROOT.parent).as_posix()}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        lines = path.read_text(encoding="utf-8").splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "TerminalLaunchRequest":
                continue
            found[relative] = found.get(relative, 0) + 1
            window = "\n".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])
            admissible = sites.get(relative, ())
            assert any(marker in window for marker in admissible), (
                f"{relative}:{node.lineno} constructs a TerminalLaunchRequest without an "
                "instruction decision. A production launch point must either supply the resolved "
                "capsule (`capsule=capsule`) or declare its legacy chain explicitly "
                "(`legacy_launch_capsule(...)` with a named reason). Recorded dispositions: "
                f"{sites}"
            )
    assert found == dict.fromkeys(sites, 1), (
        "the set of production TerminalLaunchRequest sites changed; every new site needs an "
        f"instruction decision and a row in this case. Found: {found}"
    )


def test_the_declared_legacy_reopen_names_why_it_cannot_carry_a_capsule() -> None:
    """The library reopen's exclusion is a decision with a reason, not an absent field."""

    assert "FRESH thread" in LIBRARY_REOPEN_LEGACY_REASON
    assert "identity proof" in LIBRARY_REOPEN_LEGACY_REASON
    answer = legacy_launch_capsule("worker", LIBRARY_REOPEN_LEGACY_REASON)
    assert answer.mode is LaunchCapsuleMode.LEGACY
    assert answer.report["reason"] == LIBRARY_REOPEN_LEGACY_REASON
    with pytest.raises(ValueError):
        legacy_launch_capsule("worker", "   ")


@pytest.mark.anyio
async def test_a_production_eve_launch_runs_where_its_capsule_admits_and_the_consumer_accepts(
    world: tuple[Path, McpRuntimeConfig, _FakeHost, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real chain, with no value supplied by this test but the config's own workspace root.

    The dashboard route is driven as production drives it, with a ``config.workspace_root`` that is
    deliberately **not** the admitted worktree; the resulting ``TerminalSessionSpec`` (cwd and env)
    is captured from the opener and fed to the consumer's own gate. A launch whose cwd were
    ``config.workspace_root`` is the reviewer's exact reproduction: it must be ACCEPTED here only
    because the launch runs where its capsule admits.
    """

    tmp_path, config, host, _repo = world
    _write_settings(tmp_path, harness="eve")
    _declare_eve_launchable(tmp_path, monkeypatch)
    reset_ambient()
    collaborators = replace(
        serving_collaborators(config),
        terminal_host=cast(TerminalHost, host),
        terminal_catalog=TerminalCatalog(
            tmp_path / "logs" / "dashboard" / "terminal-sessions.json"
        ),
    )
    app = create_app(config, cadence=ProjectionCadence(interval=100), collaborators=collaborators)
    with TestClient(app) as client:
        response = client.post(
            "/api/terminal/l15-eve",
            json={
                "kind": "harness",
                "harness": "eve",
                "role": "worker",
                "taskDocumentRef": LEAF_REF.model_dump(),
                "label": "L15 eve acceptance",
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["instructionMode"]["mode"] == "capsule", body["instructionMode"]

    spec = host.last_spec()
    admitted = _admitted_worktree(tmp_path)
    assert config.workspace_root != admitted, "the case needs a config that is not the worktree"
    # The reconciliation under test: the session runs where the carrier admits, and the carrier's
    # own AR_WORKSPACE_ROOT agrees with the cwd because both come from the same resolution.
    assert spec.cwd == admitted, "the launch did not follow its capsule's admitted workspace"
    assert spec.env is not None and Path(spec.env["AR_WORKSPACE_ROOT"]) == spec.cwd

    # The consumer's own gate, fed the launch point's own token and environment.
    parsed = parse_runner_config(spec.command[-1])
    adapter, launch_spec = await _prepare_controlled_launch(parsed, env=dict(spec.env))
    del adapter
    assert launch_spec.cwd == spec.cwd
    carrier = verify_capsule_binding(launch_spec_binding(launch_spec))
    assert carrier is not None, "the runtime's own verifier refused the launch point's carrier"
    assert Path(carrier.workspace.root) == spec.cwd
    assert carrier.identity.role == "worker"


def test_the_spawn_launch_agrees_with_its_capsule_about_the_workspace(
    world: tuple[Path, McpRuntimeConfig, _FakeHost, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Site 1: the spawn's cwd, its settings selection and the carrier name one workspace.

    The runner refuses a launch whose settings selection names another workspace than the session's
    own, so this case parses the launch point's own token back with the production parser: that
    check passing is the proof the selection followed the workspace the capsule admits.
    """

    tmp_path, config, host, _repo = world
    _write_settings(tmp_path, harness="eve")
    monkeypatch.setenv("AR_EVE_NODE", str(_version_reporting_node(tmp_path)))
    payload = spawn_agent_session_tool(
        config,
        seat=SpawnSeat(
            kind="harness",
            task_document_ref=LEAF_REF,
            label="L15 eve spawn",
            env={"AR_SPAWN_ROLE": "worker"},
        ),
        overrides=SpawnOverrides(
            session_id="l15-eve-spawn", host=cast(TerminalHost, host), which=_installed
        ),
    )
    assert payload["ok"] is True, payload
    spec = host.last_spec()
    admitted = _admitted_worktree(tmp_path)
    assert config.workspace_root != admitted
    assert spec.cwd == admitted
    assert spec.env is not None and Path(spec.env["AR_WORKSPACE_ROOT"]) == spec.cwd

    parsed = parse_runner_config(spec.command[-1])
    assert parsed.cwd == admitted
    assert parsed.resolved_launch is not None
    assert parsed.resolved_launch.workspace == admitted, (
        "the settings selection did not follow the workspace the capsule admits; the runner's own "
        "agreement check would refuse this launch"
    )


# --------------------------------------------------------------------------------------------
# The free agent's admission: a named absence, and an identity that moves with the seat.
# --------------------------------------------------------------------------------------------


def test_the_free_agent_admission_names_its_absent_task_plane(tmp_path: Path) -> None:
    """No task document, said out loud — and not a value the task layer would accept as one."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FreeAgentSeatAdmission(
        role="bootstrap",
        operation="orientation",
        altitude="free-agent",
        repository_id="workspace",
        work_branch="<unversioned>",
    )
    assert admission.task_reference == f"{FREE_AGENT_TASK_REFERENCE_PREFIX}:bootstrap"
    assert admission.is_taskless
    facts = admission.admitted_facts(tool_policy=None)
    assert facts.task_reference == "free-agent:bootstrap"
    # The task layer's own parser refuses it: this is not a task document reference, and nothing
    # that expects one may absorb it quietly.
    with pytest.raises(TaskProjectionSourceError) as refused:
        parse_task_reference(facts.task_reference)
    assert facts.task_reference in str(refused.value), (
        "the task layer must refuse the value BY NAME rather than absorb it as a document"
    )
    assert facts.task_document_digest.startswith("sha256:")


def test_the_free_agent_capsule_identity_moves_with_the_seat(tmp_path: Path) -> None:
    """Two different seats must not share an identity: the digest covers the admission."""

    other = tmp_path / "other-workspace"
    other.mkdir()
    config, _host, repo = _scratch_world(tmp_path)

    def digest_for(role: str, workspace: Path) -> CapsuleDigest:
        answer = compile_launch_capsule(
            config,
            LaunchCapsuleRequest(role=role, workspace_root=workspace, harness="codex"),
        )
        assert answer.codex_delivery is not None, answer.explain()
        return cast(CapsuleDigest, answer.codex_delivery.semantic_digest)

    bootstrap_here = digest_for("bootstrap", tmp_path)
    bootstrap_there = digest_for("bootstrap", other)
    assert bootstrap_here != bootstrap_there, "the seat's workspace must move the identity"
    assert repo  # the scratch repo exists; the free agent is not bound to it, and says so
    admission = free_agent_seat_admission(config, role="bootstrap", workspace_root=tmp_path)
    assert admission.repository_id == tmp_path.name
    assert admission.altitude == "free-agent", "the altitude is the corpus's own declaration"


# --------------------------------------------------------------------------------------------
# D13 — the registered MCP operation resolves a repository through its declared schema.
# --------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_registered_capsule_operation_resolves_a_repository_through_its_schema(
    world: tuple[Path, McpRuntimeConfig, _FakeHost, Path],
) -> None:
    """D13: a call through the REGISTERED tool returns an admitted capsule.

    The repository is resolved out of the enclosure contract the tool already receives, because the
    tool's declared schema exposes no repository field at all. L4's suite built the request object
    in Python, which is exactly why it could not see that its own shipped boundary was unusable.
    """

    tmp_path, config, _host, _repo = world
    server = FastMCP("l15-d13")
    register_capsule_and_skill_tools(server, config)

    tools = await server.list_tools()
    declared = next(tool for tool in tools if tool.name == "role_capsule_compile")
    properties = set(declared.inputSchema.get("properties", {}))
    required = set(declared.inputSchema.get("required", []))
    # The case fails if the declared schema loses the field the resolution depends on.
    assert "contract_path" in properties, properties
    assert {"contract_path", "task_path", "role"} <= required, required

    contract = tmp_path / "tasks" / REPO / MASTER / "enclosures" / LEAF_ID / "series-contract.md"
    assert contract.is_file(), f"the scratch world must have a real enclosure: {contract}"
    result = await server.call_tool(
        "role_capsule_compile",
        {"contract_path": str(contract), "task_path": LEAF_REF.path, "role": "worker"},
    )
    body = _tool_payload(result)
    assert body["ok"] is True, body
    assert body["repositoryId"] == REPO
    assert body["instructions"], "the registered tool returned no compiled instruction blocks"

    # And the hand-built request object L4's own suite used still answers identically.
    direct = role_capsule_compile_tool(
        config,
        CapsuleOperationRequest(
            enclosure_contract_path=str(contract),
            task_path=LEAF_REF.path,
            role="worker",
            operation="orientation",
        ),
    )
    assert direct.ok
    assert direct.semanticDigest == body["semanticDigest"]


# --------------------------------------------------------------------------------------------
# D20 — a refused stage refuses again.
# --------------------------------------------------------------------------------------------


def test_a_refused_stage_refuses_again_in_the_same_process(tmp_path: Path) -> None:
    """D20: the second staging call must refuse identically, never pass on a half-staged tree."""

    source = tmp_path / "app"
    (source / "agent").mkdir(parents=True)
    (source / "package.json").write_text("{}", encoding="utf-8")
    (source / "agent" / "agent.ts").write_text("export default {}", encoding="utf-8")
    destination = tmp_path / "epoch"

    first = second = ""
    for attempt in (1, 2):
        try:
            stage_runtime_root(source, destination)
        except HarnessControlError as error:
            message = str(error)
        else:  # pragma: no cover - the fail-open this case exists to catch
            pytest.fail(f"call {attempt} staged a runtime with no installed dependencies")
        if attempt == 1:
            first = message
        else:
            second = message
    assert first == second, (first, second)
    assert "no installed dependencies" in second
    assert not destination.exists(), "a refused stage left a destination behind"
    assert not (destination / "agent" / "agent.ts").exists()

    # And a provisioned source still stages, links, and is idempotent on the second call.
    (source / "node_modules").mkdir()
    (source / "node_modules" / "eve").mkdir()
    staged = stage_runtime_root(source, destination)
    assert (staged / "agent" / "agent.ts").is_file()
    assert (staged / "node_modules").is_symlink()
    assert stage_runtime_root(source, destination) == staged
