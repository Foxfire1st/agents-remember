"""Production-shaped Codex history path: stdio -> adapter -> bridge -> IPC -> projector."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from _agent_wire_fixtures import (
    CollabAgents,
    agent_message_item,
    collab_agent_tool_call_item,
    item_completed_params,
    turn_completed_params,
    turn_started_params,
)
from agents_remember.models.conversations.content import (
    MarkdownBlock,
)
from agents_remember.models.conversations.control_wire import (
    ControlIdentity,
    LaunchSpec,
    SubmissionAuthorityDescriptor,
)
from agents_remember.models.conversations.evidence import (
    EvidenceFrame,
    EvidencePage,
)
from agents_remember.models.conversations.identity import (
    ActiveConversationRef,
    AuthorizationBinding,
)
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.conversation.active.projector import ActiveSessionProjector
from agents_remember.serving.conversation.active.projector.facade import ProjectedSession
from agents_remember.serving.conversation.active.projector.wiring import BridgeReaders
from agents_remember.serving.conversation.projectors import projector_for
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    read_control_native_page,
    read_control_snapshot,
    read_control_transcript,
    read_submission_authority,
    read_submission_provenance,
)
from agents_remember.serving.harness_control_ipc import (
    HarnessControlServer,
    LocalControlEndpoint,
)

NOW = "2026-07-27T11:00:00+00:00"
PARENT = "thread-1"
WAVE_ONE = "agent-wave-one"
WAVE_TWO_CYCLE = "agent-wave-two-cycle"
WAVE_TWO_GOOD = "agent-wave-two-good"
MEASURED_RESPONSE_BYTES = 4_846_576
SECRET = b"p" * 32


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _ControlledEntry:
    def __init__(self, identity: ControlIdentity, endpoint: Path) -> None:
        self.id = identity.ar_session_id
        self.tmux_name = identity.tmux_name
        self.created_at = identity.created_at
        self.control_endpoint = endpoint


class _RosterEvidence:
    """The only test double: already-normalized live frames that discover three children."""

    def __init__(self, epoch: str) -> None:
        self.epoch = epoch
        self.frames: tuple[EvidenceFrame, ...] = self._frames()

    @staticmethod
    def _frames() -> tuple[EvidenceFrame, ...]:
        raw_frames: list[tuple[Mapping[str, object], str, str]] = [
            (
                item_completed_params(
                    PARENT,
                    "turn-parent",
                    agent_message_item("parent-live", "parent remains visible"),
                ),
                PARENT,
                "item/completed",
            )
        ]
        for index, child in enumerate(
            (WAVE_ONE, WAVE_TWO_CYCLE, WAVE_TWO_GOOD),
            start=1,
        ):
            raw_frames.extend(
                [
                    (
                        item_completed_params(
                            PARENT,
                            "turn-parent",
                            collab_agent_tool_call_item(
                                f"spawn-{index}",
                                "spawnAgent",
                                agents=CollabAgents(PARENT, receiver_thread_ids=[child]),
                            ),
                        ),
                        PARENT,
                        "item/completed",
                    ),
                    (
                        turn_started_params(child, f"agent-turn-{index}"),
                        child,
                        "turn/started",
                    ),
                    (
                        turn_completed_params(child, f"agent-turn-{index}"),
                        child,
                        "turn/completed",
                    ),
                ]
            )
        return tuple(
            EvidenceFrame(
                sequence=index,
                kind="codex-notification",
                created_at=NOW,
                raw=dict(raw),
                native_method=method,
                thread_id=thread_id,
            )
            for index, (raw, thread_id, method) in enumerate(raw_frames, start=1)
        )

    def read(
        self,
        entry: object,
        *,
        after_sequence: int = 0,
        limit: int = 500,
        expected_bridge_epoch: str | None = None,
    ) -> EvidencePage:
        del entry
        assert expected_bridge_epoch in {None, self.epoch}
        available = [frame for frame in self.frames if frame.sequence > after_sequence]
        selected = available[:limit]
        return EvidencePage(
            frames=tuple(selected),
            latest_sequence=self.frames[-1].sequence,
            evicted_before_sequence=0,
            truncated=len(selected) < len(available),
            bridge_epoch=self.epoch,
        )


def _app_server_script(log_path: Path, cwd: Path) -> str:
    """Return a deterministic installed-0.145-shaped JSONL peer."""

    return f"""
import json
import sys

LOG_PATH = {str(log_path)!r}
TARGET = {MEASURED_RESPONSE_BYTES}

def record(method, params, **outcome):
    with open(LOG_PATH, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({{"method": method, "params": params, **outcome}}, separators=(",", ":")) + "\\n")

def send(request_id, result):
    sys.stdout.write(json.dumps({{"id": request_id, "result": result}}, separators=(",", ":")) + "\\n")
    sys.stdout.flush()

def send_error(request_id, code, message):
    sys.stdout.write(json.dumps({{"id": request_id, "error": {{"code": code, "message": message}}}}, separators=(",", ":")) + "\\n")
    sys.stdout.flush()

def turn_page(item_id, text, next_cursor):
    return {{
        "data": [{{
            "id": "turn-" + item_id,
            "items": [{{"id": item_id, "type": "agentMessage", "text": text}}],
        }}],
        "nextCursor": next_cursor,
        "backwardsCursor": None,
    }}

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if "id" not in message:
        record(method, message.get("params", {{}}), notification=True)
        continue
    request_id = message["id"]
    params = message.get("params", {{}})
    if method == "initialize":
        record(method, params, response="success")
        send(request_id, {{
            "userAgent": "agents_remember/0.145.0 (Linux; x86_64) (agents_remember; 3.0.0)",
            "codexHome": "/tmp/codex-home",
            "platformFamily": "unix",
            "platformOs": "linux",
        }})
    elif method == "model/list":
        record(method, params, response="success")
        send(request_id, {{
            "data": [{{
                "id": "model-gpt-5.6-sol",
                "model": "gpt-5.6-sol",
                "displayName": "GPT-5.6 Sol",
                "description": "production-path fixture",
                "hidden": False,
                "isDefault": True,
                "defaultReasoningEffort": "xhigh",
                "supportedReasoningEfforts": [
                    {{"reasoningEffort": "xhigh", "description": "Extra high"}}
                ],
            }}],
            "nextCursor": None,
        }})
    elif method == "thread/start":
        record(method, params, response="success")
        send(request_id, {{
            "model": "gpt-5.6-sol",
            "modelProvider": "openai",
            "cwd": {str(cwd)!r},
            "reasoningEffort": "xhigh",
            "thread": {{
                "id": "{PARENT}",
                "cliVersion": "0.145.0",
                "status": {{"type": "idle"}},
                "turns": [],
            }},
        }})
    elif method == "thread/items/list":
        record(method, params, responseError=-32601)
        send_error(request_id, -32601, "Method not found")
    elif method == "thread/turns/list":
        thread_id = params["threadId"]
        cursor = params.get("cursor")
        if thread_id == "{PARENT}":
            record(method, params, response="success")
            send(request_id, {{"data": [], "nextCursor": None, "backwardsCursor": None}})
        elif thread_id == "{WAVE_ONE}":
            marker = "__FILL__"
            template = {{
                "id": request_id,
                "result": turn_page("history-measured", marker, None),
            }}
            encoded = json.dumps(template, separators=(",", ":"))
            prefix, suffix = encoded.split(marker)
            fill_size = TARGET - len(prefix.encode()) - len(suffix.encode())
            payload = prefix + ("x" * fill_size) + suffix
            assert len(payload.encode()) == TARGET
            record(method, params, response="success", responseBytes=len(payload.encode()))
            sys.stdout.write(payload + "\\n")
            sys.stdout.flush()
        elif thread_id == "{WAVE_TWO_CYCLE}":
            item_id, next_cursor = {{
                None: ("cycle-item-0", "opaque-A"),
                "opaque-A": ("cycle-item-1", "opaque-B"),
                "opaque-B": ("cycle-item-2", "opaque-A"),
            }}[cursor]
            record(method, params, response="success")
            send(request_id, turn_page(item_id, "cycle", next_cursor))
        elif thread_id == "{WAVE_TWO_GOOD}":
            record(method, params, response="success")
            send(request_id, turn_page("history-good", "second wave survived", None))
        else:
            record(method, params, response="success")
            send(request_id, {{"data": [], "nextCursor": None, "backwardsCursor": None}})
    else:
        record(method, params, responseError=-32601)
        send_error(request_id, -32601, "Method not found")
"""


@pytest.mark.anyio
# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_history_production_path.py:283).
async def test_measured_history_crosses_transport_probe_ipc_and_selected_projection(  # pragma: no cover
    tmp_path: Path,
) -> None:
    """A large first-wave response and cyclic second-wave child stay child-local."""

    log_path = tmp_path / "method-log.jsonl"
    control_identity = ControlIdentity(
        ar_session_id=f"production-history-{tmp_path.name}",
        tmux_name="ar-production-history",
        created_at=NOW,
    )
    launch = LaunchSpec(
        identity=control_identity,
        harness_id="codex",
        cwd=tmp_path,
        argv=(
            sys.executable,
            "-u",
            "-c",
            _app_server_script(log_path, tmp_path),
        ),
    )
    adapter = CodexAppServerAdapter(
        CodexAppServerSettings(
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            ephemeral=True,
        ),
        clock=lambda: NOW,
    )
    bridge = HarnessControlBridge(control_identity, adapter, clock=lambda: NOW)
    # pytest's nested per-test tmp_path exceeds Linux's Unix-domain socket path limit once
    # the endpoint digest is appended. A short test-owned sibling keeps its private chmod.
    socket_root = tmp_path.parent / "hist"
    endpoint = LocalControlEndpoint.for_session(socket_root, control_identity)
    server = HarnessControlServer(endpoint, bridge)
    projector: ActiveSessionProjector | None = None
    await bridge.start(launch)
    await server.start()
    entry = _ControlledEntry(control_identity, endpoint.path)
    try:
        descriptor = await asyncio.to_thread(read_submission_authority, entry)
        projector = _projector_over_control_entry(entry, control_identity, descriptor, tmp_path)

        initial = await projector.page(before_ordinal=None, limit=100)
        _assert_initial_page(initial)

        first_wave = await projector.refresh_agent_native(WAVE_ONE)
        assert first_wave.status == "hydrated"
        after_first = await projector.page(before_ordinal=None, limit=100)
        measured_text = _measured_markdown(after_first.items)
        assert "chars omitted" in measured_text

        cyclic = await projector.refresh_agent_native(WAVE_TWO_CYCLE)
        assert cyclic.status == "unavailable"
        assert cyclic.code == "source-cursor-cycle"
        good = await projector.refresh_agent_native(WAVE_TWO_GOOD)
        assert good.status == "hydrated"

        final = await projector.page(before_ordinal=None, limit=100)
        _assert_final_page(final)
        assert (await asyncio.to_thread(read_control_snapshot, entry)).control == "ready"

        _assert_wire_evidence(log_path)
    finally:
        if projector is not None:
            await projector.close()
        await server.close()
        await bridge.stop("forced")


def _assert_initial_page(initial: Any) -> None:
    """The first page carries the parent plus all three agent rosters."""
    initial_ids = {item.item_id for item in initial.items}
    assert "parent-live" in initial_ids
    assert {
        f"codex-agent-{WAVE_ONE}",
        f"codex-agent-{WAVE_TWO_CYCLE}",
        f"codex-agent-{WAVE_TWO_GOOD}",
    }.issubset(initial_ids)


def _measured_markdown(items: Any) -> str:
    """The markdown block of the measured first-wave history item."""
    measured = next(item for item in items if item.item_id == f"{WAVE_ONE}:history-measured")
    return next(block.markdown for block in measured.blocks if isinstance(block, MarkdownBlock))


def _assert_final_page(final: Any) -> None:
    """The final page keeps the parent and each wave's retained history rows."""
    final_ids = {item.item_id for item in final.items}
    assert "parent-live" in final_ids
    assert f"{WAVE_ONE}:history-measured" in final_ids
    assert f"agent-history:{WAVE_TWO_CYCLE}" in final_ids
    assert f"{WAVE_TWO_GOOD}:history-good" in final_ids


def _projector_over_control_entry(
    entry: _ControlledEntry,
    control_identity: ControlIdentity,
    descriptor: SubmissionAuthorityDescriptor,
    tmp_path: Path,
) -> ActiveSessionProjector:
    """The projector the production routes build, reading only through the control entry."""
    mapper = projector_for("codex")
    assert mapper is not None
    roster = _RosterEvidence(descriptor.bridge_epoch)
    return ActiveSessionProjector(
        ProjectedSession(
            identity=ActiveConversationRef(
                harness_id="codex",
                vendor_conversation_id=PARENT,
                project_scope=str(tmp_path),
                identity_digest="production-history-digest",
                ar_session_id=control_identity.ar_session_id,
                bridge_epoch=descriptor.bridge_epoch,
            ),
            authorization=AuthorizationBinding(
                principal_id="local-operator:1000",
                tenant_id=str(tmp_path),
            ),
            entry=entry,  # type: ignore[arg-type]
            mapper=mapper,
            secret=SECRET,
        ),
        clock=lambda: NOW,
        readers=BridgeReaders(
            evidence=roster.read,
            native_page=read_control_native_page,
            transcript=read_control_transcript,
            provenance=read_submission_provenance,
            snapshot=read_control_snapshot,
        ),
    )


def _assert_wire_evidence(log_path: Path) -> None:
    """What actually crossed the transport: one refused probe, one measured wave, one cycle.

    The child records every method it served, so this is the difference between "the page
    looked right" and "the page was built from these calls and no others".
    """
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    item_probes = [record for record in records if record["method"] == "thread/items/list"]
    assert len(item_probes) == 1
    assert item_probes[0]["responseError"] == -32601
    turns = [record for record in records if record["method"] == "thread/turns/list"]
    measured_record = next(record for record in turns if record["params"]["threadId"] == WAVE_ONE)
    assert measured_record["responseBytes"] == MEASURED_RESPONSE_BYTES
    cycle_records = [record for record in turns if record["params"]["threadId"] == WAVE_TWO_CYCLE]
    assert [record["params"].get("cursor") for record in cycle_records] == [
        None,
        "opaque-A",
        "opaque-B",
    ]
    assert any(record["params"]["threadId"] == WAVE_TWO_GOOD for record in turns)
    assert all(record["method"] != "thread/read" for record in records)
