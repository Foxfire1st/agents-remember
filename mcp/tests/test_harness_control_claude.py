"""Pinned fake-protocol coverage for the Claude Code stream-json adapter."""

import asyncio
import json
import sys
import unittest
from collections.abc import (
    Callable,
    Mapping,
)
from itertools import count
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.harness_capabilities import SetResult
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_claude import (
    ClaudeAdapterLimits,
    ClaudeStreamJsonAdapter,
)
from agents_remember.serving.harness_control_models import (
    ControlIdentity,
    ControlOperationKind,
    ControlOperationRef,
    LaunchSpec,
    ShutdownMode,
)
from agents_remember.serving.harness_launch import ResolvedLaunch

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "claude_stream_json" / "2.1.210"
INTERRUPT_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "claude_stream_json" / "2.1.217"
SESSION_ID = "11111111-1111-4111-8111-111111111111"
FIRST_CORRELATION = "22222222-2222-4222-8222-222222222222"
NOW = "2026-07-14T10:00:00+00:00"
_OPERATION_SEQUENCE = count(1)
_STUB_EFFORTS = ["low", "medium", "high"]


def _load_fixture(name: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in (FIXTURE_ROOT / name).read_text().splitlines()]


class _FakeClaudeTransport:
    def __init__(self, frames: list[dict[str, object]] | None = None) -> None:
        self.frames: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        for frame in frames or []:
            self.frames.put_nowait(frame)
        self.writes: list[dict[str, object]] = []
        self.argv: tuple[str, ...] | None = None
        self.start_argvs: list[tuple[str, ...]] = []
        self.restart_frames: list[dict[str, object]] | None = None
        self.cwd: Path | None = None
        self.env: dict[str, str] | None = None
        self.stop_modes: list[ShutdownMode] = []
        self.started = False
        self._returncode: int | None = None
        self._write_event = asyncio.Event()

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def start(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> None:
        if self.start_argvs and self.restart_frames is not None:
            # A scripted re-launch (the subagent-text floor probe stop/start): drain the
            # stop sentinel and replay the scripted startup frames.
            while not self.frames.empty():
                self.frames.get_nowait()
            for frame in self.restart_frames:
                self.frames.put_nowait(frame)
        self.started = True
        self.argv = argv
        self.start_argvs.append(argv)
        self.cwd = cwd
        self.env = dict(env)

    async def read_frame(self) -> dict[str, object] | None:
        return await self.frames.get()

    async def write_frame(
        self,
        frame: Mapping[str, object],
        *,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        if before_write is not None:
            before_write()
        self.writes.append(dict(frame))
        self._write_event.set()

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self._returncode = 0
        self.frames.put_nowait(None)

    def feed(self, frame: dict[str, object]) -> None:
        self.frames.put_nowait(frame)

    def disconnect(self, *, returncode: int = 0) -> None:
        self._returncode = returncode
        self.frames.put_nowait(None)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_claude.py:111).
    async def wait_for_writes(self, count: int) -> None:  # pragma: no cover
        while len(self.writes) < count:
            self._write_event.clear()
            if len(self.writes) < count:
                await asyncio.wait_for(self._write_event.wait(), timeout=1.0)


def _identity() -> ControlIdentity:
    return ControlIdentity(
        ar_session_id="ar-claude-session",
        tmux_name="ar-claude-session",
        created_at=NOW,
    )


def _launch(
    *,
    argv: tuple[str, ...] = (
        "/opt/claude",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--settings",
        "/home/test/.claude/settings.json",
        "--resume",
        SESSION_ID,
    ),
) -> LaunchSpec:
    return LaunchSpec(
        identity=_identity(),
        harness_id="claude",
        cwd=Path("/workspace"),
        argv=argv,
        env={"HOME": "/home/test", "AUTH_TOKEN_FOR_TEST": "not-exposed"},
    )


def _adapter(
    transport: _FakeClaudeTransport,
    *,
    correlations: list[str] | None = None,
    limits: ClaudeAdapterLimits | None = None,
    expected_launch: ResolvedLaunch | None = None,
) -> ClaudeStreamJsonAdapter:
    values = iter(correlations or [FIRST_CORRELATION])

    return ClaudeStreamJsonAdapter(
        transport_factory=lambda: transport,
        clock=lambda: NOW,
        correlation_factory=lambda: next(values),
        limits=limits,
        expected_launch=expected_launch,
    )


def _wire_text(frame: Mapping[str, object]) -> str:
    message = frame["message"]
    assert isinstance(message, dict)
    content = message["content"]
    assert isinstance(content, list)
    block = content[0]
    assert isinstance(block, dict)
    text = block["text"]
    assert isinstance(text, str)
    return text


def _replay(written: Mapping[str, object]) -> dict[str, object]:
    replay = {**written, "isReplay": True, "session_id": SESSION_ID, "timestamp": NOW}
    text = _wire_text(written)
    stripped = text.lstrip()
    if stripped.startswith("/"):
        command_text = stripped[1:]
        command, separator, arguments = command_text.partition(" ")
        if not separator:
            arguments = ""
        replay["message"] = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"<command-name>/{command}</command-name>\n"
                        f"            <command-message>{command}</command-message>\n"
                        f"            <command-args>{arguments}</command-args>"
                    ),
                }
            ],
        }
    return replay


def _result(text: str = "done") -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "result": text,
        "stop_reason": "end_turn",
        "total_cost_usd": 0,
        "usage": {},
        "modelUsage": {},
        "permission_denials": [],
        "uuid": f"result-{text}",
        "session_id": SESSION_ID,
    }


def _assistant(text: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        "uuid": f"assistant-{text}",
        "session_id": SESSION_ID,
    }


async def _settle() -> None:
    for _ in range(4):
        await asyncio.sleep(0)


def _operation(kind: ControlOperationKind) -> ControlOperationRef:
    sequence = next(_OPERATION_SEQUENCE)
    return ControlOperationRef(
        bridge_epoch="claude-test-epoch",
        sequence=sequence,
        operation_id=f"claude-test-{kind}-{sequence}",
        kind=kind,
    )


async def _set_model(adapter: ClaudeStreamJsonAdapter, model_key: str) -> SetResult:
    operation = _operation("set-model")
    await adapter.preflight_operation(operation)
    return await adapter.set_model(model_key, operation=operation)


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_claude.py:253).
async def _set_effort(
    adapter: ClaudeStreamJsonAdapter, effort: str
) -> SetResult:  # pragma: no cover
    operation = _operation("set-effort")
    await adapter.preflight_operation(operation)
    return await adapter.set_effort(effort, operation=operation)


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_claude.py:259).
async def _wait_for_activity(
    adapter: ClaudeStreamJsonAdapter, expected: str
) -> None:  # pragma: no cover
    for _ in range(20):
        if (await adapter.snapshot()).activity == expected:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"Claude adapter did not reach activity={expected}")


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_claude.py:267).
async def _wait_for_snapshot_raw(
    bridge: HarnessControlBridge, key: str, expected: object
) -> None:  # pragma: no cover
    for _ in range(20):
        if bridge.snapshot().raw.get(key) == expected:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"bridge snapshot raw {key!r} never became {expected!r}")


# A stream-json speaker that reports a version at the forwarding floor, so the adapter takes its
# probe/relaunch branch. It logs each launch argv, which is how the relaunch flag is proven.
_STUB_CLAUDE_SOURCE = """
import json, os, sys

with open(os.environ["AR_STUB_CLAUDE_ARGV_LOG"], "a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")

SESSION = "33333333-3333-4333-8333-333333333333"
MODELS = [
    {
        "value": "default",
        "displayName": "Default",
        "description": "the configured default",
        "resolvedModel": "claude-stub-5",
        "supportsEffort": True,
        "supportedEffortLevels": ["low", "medium", "high"],
    },
    {
        "value": "sonnet",
        "displayName": "Sonnet",
        "description": "a faster peer",
        "supportsEffort": False,
        "supportedEffortLevels": [],
    },
]


def emit(frame):
    sys.stdout.write(json.dumps(frame) + "\\n")
    sys.stdout.flush()


while True:
    line = sys.stdin.readline()
    if not line:
        break
    frame = json.loads(line)
    if frame.get("type") == "control_request":
        subtype = frame["request"]["subtype"]
        payload = {"commands": []} if subtype == "initialize" else {"models": MODELS}
        emit(
            {
                "type": "control_response",
                "response": {
                    "request_id": frame["request_id"],
                    "subtype": "success",
                    "response": payload,
                },
            }
        )
    elif frame.get("type") == "user":
        emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": SESSION,
                "claude_code_version": "2.1.220",
                "cwd": os.getcwd(),
                "model": "claude-stub-5",
                "permissionMode": "auto",
                "tools": [],
                "slash_commands": [],
            }
        )
        emit(
            {
                "type": "result",
                "session_id": SESSION,
                "subtype": "success",
                "is_error": False,
            }
        )
"""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
