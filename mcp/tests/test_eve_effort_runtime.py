"""The effort axis, executed: the shipped application's own consumer, at the provider boundary.

The claim this module exists to falsify is narrow and load-bearing: `eve_runtime/agent/agent.ts`
reads ``AR_EVE_EFFORT`` and applies it through eve's own ``defineAgent({ reasoning })``, so a
settings-selected level arrives at the provider that the runtime actually calls. No Python case can
observe that -- the consumer is TypeScript inside the pinned application, and the field under test is
the request body a direct provider receives -- so these cases start the real runtime, with a
complete verified capsule binding, and read the body verbatim.

Two things make the measurement trustworthy rather than self-confirming:

* the application root each case starts is the **checkout's own** ``eve_runtime/agent`` tree (linked
  into a scratch root, because ``node_modules`` is machine-local), and the staged copy is compared
  byte for byte against that tree before the assertion is read;
* **one application root and one process per case**. The authored definition is read when the runtime
  boots, so a shared root measures the previous case's source; that failure mode was observed while
  building this measurement and it is invisible in a summary table.

The provider is a recording HTTP server, not the product's deterministic fixture model: the fixture
traces a normalized projection of the request, and a projection is exactly what must not be trusted
when the request body is the value under test.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from pathlib import Path
from unittest import mock

import httpx
import pytest
from agents_remember.errors import HarnessControlError
from agents_remember.serving.eve_adapter import (
    PROVIDER_DEFAULT_EFFORT,
    REASONING_EFFORTS,
)
from agents_remember.serving.eve_protocol import EveRuntimeLaunch
from agents_remember.serving.eve_runtime_client import EveRuntimeProcess
from agents_remember.serving.eve_runtime_launch import (
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    NODE_EXECUTABLE_ENV,
    PROVIDER_API_KEY_ENV,
    PROVIDER_BASE_URL_ENV,
    PROVIDER_NAME_ENV,
    RUNTIME_ROOT_ENV,
    EveLaunchSelection,
    EveWorkspaceBinding,
    resolve_node_executable,
    resolve_runtime_spec,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from eve_adapter_test_support import (
    EVE_APPLICATION_ROOT,
    IsolatedEveRuntimeProcess,
    RecordedModelRequest,
    require_installed_eve_application,
    serve_recording_provider,
    staged_runtime_root,
)
from eve_capsule_test_support import (
    FixtureCarrierRequest,
    binding_env,
    fixture_carrier_for,
    repository_with_commit,
)

pytestmark = pytest.mark.integration

FIXTURE_MODEL = "fixture-deterministic-1"
BINDING_REF = "ar-binding:effort-runtime-fixture"
START_TIMEOUT_SECONDS = 240.0
CALL_TIMEOUT_SECONDS = 120.0
AUTHORED_AGENT = EVE_APPLICATION_ROOT / "agent" / "agent.ts"


def _admitted_workspace(root: Path) -> dict[str, str]:
    """A complete, verified capsule binding: a real git worktree on its admitted branch.

    The runtime refuses every session route without one, so this is what makes the case observe the
    production consumer rather than a stub: only the transport's peer is a recording server.
    """

    workspace = root / "workspace"
    branch = "ar/effort-runtime-fixture"
    base_commit = repository_with_commit(workspace, branch=branch)
    carrier_path, digest = fixture_carrier_for(
        FixtureCarrierRequest(
            workspace=workspace,
            carrier_directory=root / "capsule",
            branch=branch,
            base_commit=base_commit,
            instructions=("EFFORT RUNTIME FIXTURE capsule instruction.\n",),
            binding_ref=BINDING_REF,
        )
    )
    return binding_env(
        carrier_path=carrier_path,
        digest=digest,
        workspace_root=workspace,
        binding_ref=BINDING_REF,
    )


async def _one_level(tmp_path: Path, effort: str) -> tuple[list[RecordedModelRequest], Path]:
    """Start the shipped application for ONE configured level and return what the provider saw."""

    require_installed_eve_application()
    authored = AUTHORED_AGENT.read_text(encoding="utf-8")
    assert "AR_EVE_EFFORT" in authored, "the consumer under test is not in the authored source"

    root = tmp_path / effort
    binding = _admitted_workspace(root)
    runtime_root = staged_runtime_root(root / "runtime")
    recorded: list[RecordedModelRequest] = []
    provider, port = serve_recording_provider(recorded)
    spec = resolve_runtime_spec(
        selection=EveLaunchSelection(
            model_key=FIXTURE_MODEL,
            effort=effort,
            provider_base_url=f"http://127.0.0.1:{port}/v1",
            provider_api_key="effort-runtime-fixture-key",
            provider_name="ar-eve",
            context_window_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS,
        ),
        binding=EveWorkspaceBinding(
            workspace_root=Path(binding["AR_WORKSPACE_ROOT"]),
            binding_ref=binding["AR_BINDING_REF"],
            capsule_digest=binding["AR_CAPSULE_DIGEST"],
            capsule_path=Path(binding["AR_CAPSULE_PATH"]),
        ),
        env={
            **binding,
            RUNTIME_ROOT_ENV: str(runtime_root),
            NODE_EXECUTABLE_ENV: _node_executable(),
            PROVIDER_BASE_URL_ENV: f"http://127.0.0.1:{port}/v1",
            PROVIDER_API_KEY_ENV: "effort-runtime-fixture-key",
            PROVIDER_NAME_ENV: "ar-eve",
        },
        port=0,
        state_root=root / "epoch",
    )
    # The staged copy is the checkout's own authored bytes: the assertion below is about the source
    # this repository ships, not about a reduced application written for the case.
    assert (spec.root / "agent" / "agent.ts").read_text(encoding="utf-8") == authored

    runtime = IsolatedEveRuntimeProcess(spec.launch, health_timeout_seconds=START_TIMEOUT_SECONDS)
    try:
        await runtime.start()
        await runtime.health()
        await runtime.create_session("effort axis fixture prompt")
        deadline = time.monotonic() + CALL_TIMEOUT_SECONDS
        while not recorded and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
    finally:
        with contextlib.suppress(Exception):
            await runtime.stop("graceful")
        provider.shutdown()
        provider.server_close()
    return recorded, spec.root


def _node_executable() -> str:
    """The interpreter the runtime launches under, or a skip when this host has none."""

    try:
        return resolve_node_executable()
    except HarnessControlError as error:  # pragma: no cover - host without a usable Node
        pytest.skip(f"no Node >= 24 available for the real runtime: {error}")


class EveEffortConsumerTests:
    """The authored consumer, one process per level, read at the provider boundary."""

    def test_every_advertised_level_is_the_body_the_provider_receives(self, tmp_path: Path) -> None:
        # The case the packet's behaviour 1 rests on, run across the WHOLE accepted vocabulary
        # rather than an example: each level gets its own application root and its own process,
        # and the body recorded for it must carry exactly that level.
        for effort in REASONING_EFFORTS:
            if effort == PROVIDER_DEFAULT_EFFORT:
                continue
            recorded, staged_root = asyncio.run(_one_level(tmp_path, effort))
            assert recorded, f"no model call reached the provider for {effort!r} ({staged_root})"
            for request in recorded:
                received = request.reasoning_effort
                assert received == effort, (
                    f"configured {effort!r} but the provider received {received!r}; "
                    f"body keys: {request.top_level_keys()}"
                )

    def test_the_sentinel_sends_no_reasoning_key_at_all(self, tmp_path: Path) -> None:
        # `provider-default` means NO explicit reasoning, so the key must be absent from the body --
        # not present with the token, and not present as null. This is the AR sentinel's own rule,
        # applied by the authored source rather than by a provider's tolerance for the token.
        recorded, staged_root = asyncio.run(_one_level(tmp_path, PROVIDER_DEFAULT_EFFORT))
        assert recorded, f"no model call reached the provider ({staged_root})"
        for request in recorded:
            assert "reasoning_effort" not in request.body, (
                "the sentinel must omit the key; the provider received "
                f"{request.body.get('reasoning_effort')!r}"
            )

    def test_the_model_the_selection_names_is_the_one_the_provider_receives(
        self, tmp_path: Path
    ) -> None:
        # The control for the case above: this is the SAME runtime and the same body, so a case that
        # passed on an empty or defaulted request rather than on the effort key would fail here.
        recorded, staged_root = asyncio.run(_one_level(tmp_path, "high"))
        assert recorded, f"no model call reached the provider ({staged_root})"
        for request in recorded:
            assert request.body.get("model") == FIXTURE_MODEL
            assert "messages" in request.body


def test_an_observed_bind_collision_retries_and_other_start_failures_do_not() -> None:
    async def exercise() -> None:
        ports: list[int] = []

        async def first_bind_fails(runtime: EveRuntimeProcess) -> None:
            ports.append(runtime._launch.port)
            if len(ports) == 1:
                runtime._stderr.extend(b"EADDRINUSE: address already in use")
                raise HarnessControlError("the Eve child exited before readiness")

        runtime = IsolatedEveRuntimeProcess(EveRuntimeLaunch(runtime_root="unused", port=65000))
        with (
            mock.patch.object(EveRuntimeProcess, "start", first_bind_fails),
            mock.patch.object(EveRuntimeProcess, "stop", new_callable=mock.AsyncMock) as stopped,
            mock.patch("eve_adapter_test_support.choose_runtime_port", return_value=65001),
        ):
            await runtime.start()
            assert ports == [65000, 65001]
            stopped.assert_awaited_once_with("graceful")
            assert runtime.stderr_tail == ""

        original_failure = HarnessControlError("missing runtime dependency")
        with (
            mock.patch.object(EveRuntimeProcess, "start", new_callable=mock.AsyncMock) as start,
            mock.patch.object(EveRuntimeProcess, "stop", new_callable=mock.AsyncMock) as stopped,
            mock.patch("eve_adapter_test_support.choose_runtime_port") as choose,
        ):
            start.side_effect = original_failure
            with pytest.raises(HarnessControlError) as failure:
                await runtime.start()
            assert failure.value is original_failure
            start.assert_awaited_once()
            stopped.assert_awaited_once_with("graceful")
            choose.assert_not_called()

    asyncio.run(exercise())


async def _stall_after_owning_startup_resources(
    runtime: EveRuntimeProcess, started: asyncio.Event, release: asyncio.Event
) -> None:
    runtime._process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal; signal.pause()",
        stderr=asyncio.subprocess.PIPE,
    )
    runtime._client = httpx.AsyncClient()
    runtime._stderr_task = asyncio.create_task(runtime._drain_stderr())
    started.set()
    await release.wait()


@pytest.mark.parametrize("failure_kind", ["cancellation", "failure"])
def test_failed_start_reaps_its_actual_child_client_and_stderr_task(failure_kind: str) -> None:
    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        cancellations: list[asyncio.CancelledError] = []
        failure = RuntimeError("startup failed after acquiring its resources")

        async def stalled_start(runtime: EveRuntimeProcess) -> None:
            try:
                await _stall_after_owning_startup_resources(runtime, started, release)
            except asyncio.CancelledError as cancellation:
                cancellations.append(cancellation)
                raise
            raise failure

        runtime = IsolatedEveRuntimeProcess(EveRuntimeLaunch(runtime_root="unused", port=65000))
        startup: asyncio.Task[None] | None = None
        try:
            with (
                mock.patch.object(EveRuntimeProcess, "start", stalled_start),
                mock.patch.object(runtime, "stop", wraps=runtime.stop) as stopped,
                mock.patch("eve_adapter_test_support.choose_runtime_port") as choose,
            ):
                startup = asyncio.create_task(runtime.start())
                await asyncio.wait_for(started.wait(), timeout=HANG_GUARD_SECONDS)
                child, client, stderr_task = runtime._process, runtime._client, runtime._stderr_task
                assert child is not None and client is not None and stderr_task is not None
                assert child.returncode is None
                assert not client.is_closed
                assert not stderr_task.done()
                if failure_kind == "cancellation":
                    startup.cancel("cancel after all three resources exist")
                    with pytest.raises(asyncio.CancelledError) as cancelled:
                        await asyncio.wait_for(startup, timeout=HANG_GUARD_SECONDS)
                    assert cancelled.value is cancellations[0]
                else:
                    release.set()
                    with pytest.raises(RuntimeError) as failed:
                        await asyncio.wait_for(startup, timeout=HANG_GUARD_SECONDS)
                    assert failed.value is failure
                stopped.assert_awaited_once_with("forced")
                choose.assert_not_called()
                assert child.returncode is not None
                assert client.is_closed
                assert stderr_task.done()
                assert runtime._process is None
                assert runtime._client is None
                assert runtime._stderr_task is None
        finally:
            if startup is not None and not startup.done():
                startup.cancel()
                with contextlib.suppress(asyncio.CancelledError, RuntimeError):
                    await startup
            await runtime.stop("forced")

    asyncio.run(exercise())
