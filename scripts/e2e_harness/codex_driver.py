"""Pinned Codex app-server driver and MCP discovery evidence for the E2E scenario."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.models.conversations.control_wire import ControlIdentity, LaunchSpec
from agents_remember.serving.codex_app_server_protocol import (
    JsonObject,
)
from agents_remember.serving.codex_app_server_session import (
    CodexAppServerSession,
    CodexAppServerSettings,
)
from agents_remember.serving.codex_mcp_readiness import wait_for_codex_mcp_tool
from fixture import CODEX_MODEL, E2EFixture
from mcp.client.stdio import stdio_client
from responses_server import AMBIENT_PROMPT

from mcp import ClientSession, StdioServerParameters

WAIT_SECONDS = 180.0
EXPECTED_CODEX_VERSION = "0.151.0"


def codex_mcp_registration(fixture: E2EFixture) -> dict[str, object]:
    """Read Codex's installed MCP configuration without bypassing its CLI."""

    result = _run_codex_command(fixture, "mcp", "list", "--json")
    return _process_evidence(result)


def probe_candidate_mcp(fixture: E2EFixture) -> dict[str, object]:
    """Prove the configured stdio command independently speaks MCP."""

    return asyncio.run(_probe_candidate_mcp(fixture))


def run_ambient_codex(
    fixture: E2EFixture,
    *,
    prompt: str = AMBIENT_PROMPT,
) -> dict[str, object]:
    """Wait for app-server MCP readiness, then submit the ambient launch turn."""

    return asyncio.run(_run_ambient_codex(fixture, prompt=prompt))


def codex_log_evidence(codex_home: Path) -> list[dict[str, str]]:
    """Return bounded Codex logs when the installed client emitted any."""

    evidence: list[dict[str, str]] = []
    for path in sorted(codex_home.rglob("*.log")):
        with contextlib.suppress(OSError, UnicodeError):
            evidence.append(
                {
                    "path": path.relative_to(codex_home).as_posix(),
                    "tail": path.read_text(encoding="utf-8")[-6000:],
                }
            )
    return evidence


async def _probe_candidate_mcp(fixture: E2EFixture) -> dict[str, object]:
    parameters = StdioServerParameters(
        command="/opt/ar-venv/bin/python",
        args=["-m", "agents_remember.mcp", "--config", fixture.authority_path.as_posix()],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        try:
            async with (
                stdio_client(parameters, errlog=errlog) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await asyncio.wait_for(session.initialize(), timeout=30)
                listed = await asyncio.wait_for(session.list_tools(), timeout=30)
            names = [tool.name for tool in listed.tools]
            result: dict[str, object] = {
                "status": "connected",
                "toolCount": len(names),
                "dispatchTools": [name for name in names if name == "dispatch_agent"],
            }
        except Exception as exc:
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        errlog.flush()
        errlog.seek(0)
        result["stderrTail"] = errlog.read()[-6000:]
        return result


async def _run_ambient_codex(
    fixture: E2EFixture,
    *,
    prompt: str,
) -> dict[str, object]:
    session = CodexAppServerSession(
        CodexAppServerSettings(
            model=CODEX_MODEL,
            reasoning_effort="low",
            ephemeral=True,
            client_name="arspawn_e2e",
            client_title="ARSPAWN E2E",
        )
    )
    launch_environment = _codex_environment(fixture)
    launch = LaunchSpec(
        identity=ControlIdentity(
            ar_session_id=f"arspawn-ambient-{uuid.uuid4().hex}",
            tmux_name="arspawn-ambient",
            created_at=datetime.now(UTC).isoformat(),
        ),
        harness_id="codex",
        cwd=fixture.workspace_repo,
        argv=("codex", "app-server"),
        env=launch_environment,
    )
    notifications: list[dict[str, object]] = []
    completion: asyncio.Future[dict[str, object]] | None = None
    consumer: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(session.connect(launch, resume_thread_id=None), timeout=60)
        transport = session.transport
        thread_id = session.thread_id
        if transport is None or thread_id is None:
            raise RuntimeError("Codex app-server opened no transport/thread")

        completion = asyncio.get_running_loop().create_future()
        consumer = asyncio.create_task(
            _consume_notifications(transport.messages(), notifications, completion)
        )
        mcp_status = (
            await wait_for_codex_mcp_tool(
                transport,
                thread_id=thread_id,
                tool_name="dispatch_agent",
            )
        ).to_json()
        started = await asyncio.wait_for(
            transport.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "clientUserMessageId": f"arspawn-{uuid.uuid4().hex}",
                    "input": [{"type": "text", "text": prompt}],
                    "cwd": fixture.workspace_repo.as_posix(),
                    "model": CODEX_MODEL,
                    "effort": "low",
                },
            ),
            timeout=60,
        )
        completed = await asyncio.wait_for(completion, timeout=WAIT_SECONDS)
        started_turn = _turn_summary(started)
        completed_turn = _turn_summary(completed)
        if started_turn.get("id") != completed_turn.get("id"):
            raise RuntimeError(
                "Codex turn/completed did not match the submitted ambient turn: "
                f"start={started_turn!r}, completed={completed_turn!r}"
            )
        if completed_turn.get("status") != "completed":
            raise RuntimeError(f"Codex ambient turn did not complete: {completed_turn!r}")
        return {
            "status": "completed",
            "cliVersion": session.cli_version,
            "callerIdentity": _ambient_identity_evidence(launch_environment),
            "threadId": thread_id,
            "mcp": mcp_status,
            "turnStart": started_turn,
            "turnCompleted": completed_turn,
            "notifications": notifications[-20:],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "cliVersion": session.cli_version,
            "callerIdentity": _ambient_identity_evidence(launch_environment),
            "threadId": session.thread_id,
            "notifications": notifications[-20:],
        }
    finally:
        if consumer is not None:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await session.stop()


async def _consume_notifications(
    messages: AsyncIterator[JsonObject],
    notifications: list[dict[str, object]],
    completion: asyncio.Future[dict[str, object]],
) -> None:
    try:
        async for message in messages:
            summary = _notification_summary(message)
            notifications.append(summary)
            if message.get("method") == "turn/completed" and not completion.done():
                params = message.get("params")
                if not isinstance(params, dict):
                    raise RuntimeError("Codex turn/completed omitted object params")
                completion.set_result(params)
            elif "id" in message:
                raise RuntimeError(
                    f"Codex ambient E2E received an unexpected server request: {summary!r}"
                )
    except Exception as exc:
        if not completion.done():
            completion.set_exception(exc)
        raise


def _turn_summary(payload: JsonObject) -> dict[str, object]:
    raw = payload.get("turn", payload)
    if not isinstance(raw, dict):
        raise RuntimeError(f"Codex turn payload is not an object: {payload!r}")
    error = raw.get("error")
    return {
        "id": raw.get("id"),
        "status": raw.get("status"),
        "error": error
        if isinstance(error, (str, int, float, bool)) or error is None
        else str(error),
    }


def _notification_summary(message: JsonObject) -> dict[str, object]:
    params = message.get("params")
    summary: dict[str, object] = {"method": message.get("method")}
    if "id" in message:
        summary["requestId"] = message["id"]
    if isinstance(params, dict):
        summary.update(
            {
                key: params[key]
                for key in ("threadId", "name", "status", "failureReason")
                if key in params
            }
        )
        turn = params.get("turn")
        if isinstance(turn, dict):
            summary["turn"] = {key: turn[key] for key in ("id", "status") if key in turn}
    return summary


def _run_codex_command(
    fixture: E2EFixture,
    *arguments: str,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["codex", *arguments],
        cwd=fixture.workspace_repo,
        env=_codex_environment(fixture),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _codex_environment(fixture: E2EFixture) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("AR_HOSTED_SESSION_ID", None)
    env.pop("AR_SPAWN_ROLE", None)
    env.update(
        {
            "CODEX_HOME": fixture.codex_home.as_posix(),
            "OPENAI_API_KEY": "arspawn-e2e-non-secret",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def _ambient_identity_evidence(environment: dict[str, str]) -> dict[str, bool]:
    return {
        "AR_HOSTED_SESSION_ID_present": "AR_HOSTED_SESSION_ID" in environment,
        "AR_SPAWN_ROLE_present": "AR_SPAWN_ROLE" in environment,
    }


def _process_evidence(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return {
        "returnCode": result.returncode,
        "stdoutTail": result.stdout[-4000:],
        "stderrTail": result.stderr[-4000:],
    }
