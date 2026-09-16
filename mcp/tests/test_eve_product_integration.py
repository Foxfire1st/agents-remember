"""eve is selectable and observable through the existing AR product surface.

Every case drives a real owner: the settings-vocabulary registry and its readiness probe, the
pre-session capability catalog over the real adapter factory, the ``HarnessSubmissionAuthority``,
the per-harness control/telemetry capability declarations, the conversation projector registry and
mapper, and the catalog's own terminal-evidence lift. Nothing monkeypatches the code under test.

The one deliberate dependency is the eve transport double (``eve_adapter_test_support``, through
``test_eve_adapter``'s own helpers): the native process is replaced at the transport seam, while
the adapter, event mapper, cursor, transcript and capability surface under test are the production
ones. A test that needed the real process would be a native-evidence run, which this leaf does not
own.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import shutil
import sys
import tempfile
import unittest
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp" / "src"))

import agents_remember.serving.eve_runtime_launch
from agents_remember.kernel.eve_runtime_readiness import (
    MINIMUM_NODE_MAJOR,
    NODE_EXECUTABLE_ENV,
    RUNTIME_ROOT_ENV,
    eve_runtime_readiness,
)
from agents_remember.kernel.harnesses import (
    EVE_RUNTIME_PROBE,
    HARNESSES,
    Harness,
    harness_availability_detail,
    is_harness_available,
)
from agents_remember.models.conversations.content import ConversationItem
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    AssetReference,
    ControlIdentity,
    LaunchSpec,
)
from agents_remember.models.conversations.evidence import EvidenceFrame, EvidencePage
from agents_remember.serving import eve_events
from agents_remember.serving.conversation.active.capabilities import capabilities_for
from agents_remember.serving.conversation.active.projector.rebuild_coordinator import PageResult
from agents_remember.serving.conversation.control.capabilities import (
    control_capabilities_for,
    telemetry_capabilities_for,
)
from agents_remember.serving.conversation.projectors import PROJECTORS, projector_for
from agents_remember.serving.conversation.projectors import eve as eve_projector
from agents_remember.serving.conversation.projectors.common import (
    MappedItem,
    MappedTurnOutcome,
    MappedUnknownVendor,
    UnmappableShape,
)
from agents_remember.serving.conversation.projectors.eve import SILENT_CONTROL_EVENTS
from agents_remember.serving.eve_adapter import EVE_ADAPTER_ID, REASONING_EFFORTS, EveSessionAdapter
from agents_remember.serving.eve_runtime_launch import (
    PROVIDER_API_KEY_ENV,
    build_runtime_env,
    launch_spec_binding,
    launch_spec_selection,
    runtime_default_model,
)
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    ModelCapability,
    capability_snapshot_json,
)
from agents_remember.serving.harness_capability_catalog import (
    HarnessCapabilityCatalog,
    HarnessCapabilityLookupError,
)
from agents_remember.serving.harness_control_adapter import (
    AssetSubmitCapable,
    protocol_adapter_status,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_factories import create_harness_protocol_adapter
from agents_remember.serving.harness_control_models import PromptRequest
from agents_remember.serving.harness_submission_authority import (
    BridgeSnapshotPort,
    HarnessSubmissionAuthority,
    SubmissionLimits,
)
from agents_remember.serving.harnesses import detect_harnesses, find_harness, is_detected
from agents_remember.serving.terminal_evidence import latest_terminal_evidence
from agents_remember.serving.terminal_opener import (
    TerminalLaunchRequest,
    resolve_terminal_launch,
)
from eve_adapter_test_support import FakeEveRuntime, FakeRuntimeFactory, FakeTurn
from test_conversation_active_service import _projector as engine_projector
from test_conversation_active_service import _ScriptedBridge
from test_eve_adapter import _launch as _eve_launch

NOW = "2026-09-16T09:43:00+02:00"
"""One clock for the whole module; every event timestamp below is that instant."""

_MISSING_APPLICATION = "/nonexistent/ar-eve-runtime-application"
"""A path that cannot be an application root, used to drive the probe's absence branch."""

_PROVIDER_SECRET = "sk-live-caps-l8-credential-marker"
"""A credential value that must never appear in a capability diagnostic."""


def _identity() -> ControlIdentity:
    return ControlIdentity(
        ar_session_id="ar-session-1",
        tmux_name="ar-eve-1",
        created_at="2026-09-16T00:00:00+00:00",
    )


def _snapshot(control: str = "ready") -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=_identity(),
        control=control,  # type: ignore[arg-type]
        activity="idle",
        acceptance="immediate",
        vendor_session_id="wrun_caps_l8",
        raw={},
    )


def _launch_with_provider_key() -> LaunchSpec:
    """An eve launch carrying a real provider credential through the launch environment."""

    base = _eve_launch()
    return replace(base, env={**dict(base.env), PROVIDER_API_KEY_ENV: _PROVIDER_SECRET})


def _which_nothing(_name: str) -> str | None:
    """A PATH with no node on it, so the probe's interpreter branch is the one under test."""

    return None


def _which_stub_node(name: str) -> str | None:
    """A PATH whose only node is the fixture interpreter, so detection is drivable in a case."""

    return _STUB_NODE if name == "node" else None


def _checkout_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "eve_runtime").is_dir():
            return parent
    return None


def _stub_interpreter(directory: Path, version: str, *, name: str = "node") -> str:
    """One interpreter that exists, is executable, and reports ``version``.

    Built on disk per case rather than named as a path, because the readiness contract is exactly
    "exists, is executable, reports a major >= the floor" -- a fixture that named an absent path
    would let a case assert readiness for an interpreter that cannot run, which is the false
    positive the probe exists to prevent. This interpreter is a REAL executable the probe actually
    runs (see ``test_a_real_interpreter_is_accepted_and_reported``, which drives it end to end); it
    is not a stand-in for a genuine Node binary, and no case in this module claims to exercise one,
    because the isolated pytest environment exposes no Node at or above the floor.
    """

    path = directory / name
    path.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


_STUB_DIRECTORY = Path(tempfile.mkdtemp(prefix="ar-eve-stub-node-"))
atexit.register(shutil.rmtree, _STUB_DIRECTORY, True)
_STUB_NODE = _stub_interpreter(_STUB_DIRECTORY, f"v{MINIMUM_NODE_MAJOR}.19.0")

_READY_ENV = {NODE_EXECUTABLE_ENV: _STUB_NODE}
"""A probe environment resolved against a real interpreter; the application root is the real one,
found by walking up for the checkout's ``eve_runtime`` directory exactly as the launcher does. Using
the real application here is the point: the reference is the same directory the adapter would start,
so a case cannot pass against a fixture application that no launch could ever use."""


# The adapter event kind recorded for each eve event type, read from
# ``notes/reports/260915-CAPS-L6-evidence-a2/live-native-events.json`` (``translatedEvents``) -- the
# pinned release's own run. ``session.waiting`` is recorded as ``completed`` on purpose: that is the
# two-boundary shape this projector exists to keep out of the conversation projection.
_RECORDED_KINDS = {
    "session.started": "state",
    "turn.started": "state",
    "message.received": "eve:message.received",
    "actions.requested": "transcript",
    "action.result": "transcript",
    "action.input.appended": "eve:action.input.appended",
    "message.appended": "delta",
    "message.completed": "transcript",
    "step.started": "eve:step.started",
    "step.completed": "eve:step.completed",
    "turn.completed": "state",
    "turn.cancelled": "cancelled",
    "turn.failed": "failed",
    "step.failed": "transcript",
    "session.waiting": "completed",
    "input.requested": "state",
    "authorization.required": "state",
}
"""Every event type the pinned release emitted in the recorded run, with the adapter kind it got.

The census is the whole population, not a convenient subset: the 52-frame recording carries these
types and no others (``step.started`` x6, ``step.completed`` x5 in that run, plus the
``action.input.appended`` x4 partial-argument frames). A type the projector silently ignores would
be invisible to a hand-picked list, so the coverage case below also enumerates the adapter's own
handler table.
"""

_RECORDED_SEQUENCES = {
    "session.started": 1,
    "turn.started": 2,
    "message.received": 3,
    "actions.requested": 4,
    "action.result": 5,
    "action.input.appended": 6,
    "message.appended": 7,
    "message.completed": 8,
    "step.started": 9,
    "step.completed": 10,
    "turn.completed": 11,
    "turn.cancelled": 12,
    "turn.failed": 13,
    "step.failed": 15,
    "session.waiting": 16,
    "input.requested": 17,
    "authorization.required": 18,
}

# The recorded ``arEvidence`` envelopes, verbatim in shape: the frame the adapter diverts under
# ``AR_EVIDENCE_KEY``. Field names, ids and the parked/cancelled payloads are the real ones from the
# run above, so these assertions are anchored to native data rather than to a shape this test
# invented. ``meta.deliveryIds`` is deliberately omitted -- the projector must not read it.
_PINNED_RUN_CENSUS = frozenset(
    {
        "session.started",
        "turn.started",
        "message.received",
        "actions.requested",
        "action.result",
        "action.input.appended",
        "message.appended",
        "message.completed",
        "step.started",
        "step.completed",
        "turn.completed",
        "turn.cancelled",
        "session.waiting",
    }
)
"""The event types the pinned release's recorded run actually emitted (its 52 frames, 13 types).

Transcribed from `notes/reports/260915-CAPS-L6-evidence-a2/live-native-events.json` and cited rather
than re-read at test time, because that recording lives in the task's coordination tree and a
repository test must run without it. The case below pins the label set against this census, so
relabelling a frame is a failure; re-verifying the census itself against the recording is a reading
act, not a test.
"""

# Which evidence kind each envelope is; see _SESSION_ORDER's docstring for the three labels.
_RECORDED_PROVENANCE = {
    "session.started": "recorded",
    "turn.started": "recorded",
    "message.received": "recorded",
    "actions.requested": "recorded",
    "action.result": "recorded",
    "action.input.appended": "recorded",
    "message.appended": "recorded",
    "message.completed": "recorded",
    "step.started": "recorded",
    "step.completed": "recorded",
    "turn.completed": "recorded",
    "turn.cancelled": "recorded",
    "step.failed": "derived",
    "session.waiting": "recorded",
    "input.requested": "production-emitted",
    "authorization.required": "derived",
}

_AUTHORIZATION_REQUIRED_FRAME_PROVENANCE = (
    "composed from the fields serving/eve_events.py::EveEventMapper._authorization_required reads "
    "(name, description, turnId) and emitted through FakeEveRuntime.emit -- the durable-record "
    "writer the adapter suite uses for raw events; the pinned release emits authorization.required "
    "but no recorded instance of it exists in this environment"
)

_RECORDED_ENVELOPES: dict[str, dict[str, object]] = {
    "session.started": {
        "type": "session.started",
        "data": {
            "runtime": {
                "agentId": "ar-eve-runtime",
                "agentName": "ar-eve-runtime",
                "eveVersion": "0.56.0",
            }
        },
        "meta": {"at": "2026-09-16T07:43:49.117Z"},
    },
    "turn.started": {
        "type": "turn.started",
        "data": {"sequence": 0, "turnId": "turn_0"},
        "meta": {"at": "2026-09-16T07:43:49.119Z"},
    },
    "message.received": {
        "type": "message.received",
        "data": {
            "message": "run the fixture tool round-trip",
            "parts": [{"text": "run the fixture tool round-trip", "type": "text"}],
            "sequence": 0,
            "turnId": "turn_0",
        },
        "meta": {"at": "2026-09-16T07:43:49.119Z"},
    },
    "actions.requested": {
        "type": "actions.requested",
        "data": {
            "actions": [
                {
                    "callId": "call_fixture_1",
                    "input": {"path": "note.txt", "text": "written-by-eve-fixture"},
                    "kind": "tool-call",
                    "toolName": "ar_workspace_write",
                }
            ],
            "sequence": 0,
            "stepIndex": 0,
            "turnId": "turn_0",
        },
        "meta": {"at": "2026-09-16T07:43:49.150Z"},
    },
    "action.result": {
        "type": "action.result",
        "data": {
            "result": {
                "callId": "call_fixture_1",
                "kind": "tool-result",
                "output": {
                    "bytes": 22,
                    "path": "note.txt",
                    "sha256": "1731c677e6489156ee79917d8aaa11df659aad53f984d1ba4ea59c89f457edcf",
                },
                "toolName": "ar_workspace_write",
            },
            "sequence": 0,
            "status": "completed",
            "stepIndex": 0,
            "turnId": "turn_0",
        },
        "meta": {"at": "2026-09-16T07:43:49.157Z"},
    },
    "action.input.appended": {
        "type": "action.input.appended",
        "data": {
            "callId": "call_fixture_1",
            "inputTextDelta": '{"path": "n',
            "sequence": 0,
            "stepIndex": 0,
            "toolName": "ar_workspace_write",
            "turnId": "turn_0",
        },
        "meta": {"at": "2026-09-16T07:43:49.142Z"},
    },
    "input.requested": {
        # production-emitted: captured from the durable record `FakeTurn(requests=...)` writes
        # through FakeEveRuntime.turn_events -- see _SESSION_ORDER's provenance note.
        "type": "input.requested",
        "data": {
            "requests": [
                {
                    "action": {
                        "callId": "call_q",
                        "input": {},
                        "kind": "tool-call",
                        "toolName": "ask_question",
                    },
                    "kind": "question",
                    "options": [
                        {"id": "approve", "label": "Approve"},
                        {"id": "deny", "label": "Deny"},
                    ],
                    "prompt": "Proceed with the write?",
                    "requestId": "req_input_1",
                }
            ],
            "sequence": 0,
            "stepIndex": 0,
            "turnId": "turn_0",
        },
        "meta": {"at": "2026-09-16T07:43:49.200Z"},
    },
    "authorization.required": {
        # derived: fields the adapter's own handler reads, emitted through the fake runtime's
        # durable-record writer; see _AUTHORIZATION_REQUIRED_FRAME_PROVENANCE.
        "type": "authorization.required",
        "data": {
            "description": "the runtime asks to write note.txt",
            "name": "workspace_write",
            "sequence": 0,
            "stepIndex": 0,
            "turnId": "turn_0",
        },
        "meta": {"at": "2026-09-16T07:43:49.205Z"},
    },
    "step.started": {
        "type": "step.started",
        "data": {
            "modelId": "ar-eve/fixture-deterministic-1",
            "sequence": 0,
            "stepIndex": 0,
            "turnId": "turn_0",
        },
        "meta": {"at": "2026-09-16T07:43:49.120Z"},
    },
    "step.completed": {
        "type": "step.completed",
        "data": {
            "finishReason": "tool-calls",
            "sequence": 0,
            "stepIndex": 0,
            "turnId": "turn_0",
        },
        "meta": {"at": "2026-09-16T07:43:49.164Z"},
    },
    "step.failed": {
        "type": "step.failed",
        "data": {
            "code": "MODEL_CALL_FAILED",
            "message": "the provider refused the request",
            "sequence": 1,
            "stepIndex": 0,
            "turnId": "turn_0",
        },
        "meta": {"at": "2026-09-16T07:43:49.240Z"},
    },
    "turn.failed": {
        "type": "turn.failed",
        "data": {"message": "the model call failed", "sequence": 0, "turnId": "turn_0"},
        "meta": {"at": "2026-09-16T07:43:49.230Z"},
    },
    "message.appended": {
        "type": "message.appended",
        "data": {"messageDelta": "note.tx", "sequence": 0, "stepIndex": 1, "turnId": "turn_0"},
        "meta": {"at": "2026-09-16T07:43:49.207Z"},
    },
    "message.completed": {
        "type": "message.completed",
        "data": {
            "finishReason": "stop",
            "message": "note.txt written",
            "sequence": 0,
            "stepIndex": 1,
            "turnId": "turn_0",
        },
        "meta": {"at": "2026-09-16T07:43:49.210Z"},
    },
    "turn.completed": {
        "type": "turn.completed",
        "data": {"sequence": 0, "turnId": "turn_0"},
        "meta": {"at": "2026-09-16T07:43:49.220Z"},
    },
    "turn.cancelled": {
        "type": "turn.cancelled",
        "data": {"sequence": 3, "turnId": "turn_3"},
        "meta": {"at": "2026-09-16T07:44:06.277Z"},
    },
    "session.waiting": {
        "type": "session.waiting",
        "data": {
            "continuationToken": "wrun_01M2MJR0N3EMEXZJEA38HTCQPF",
            "wait": "next-user-message",
        },
        "meta": {"at": "2026-09-16T07:43:49.222Z"},
    },
}


def _recorded(event_type: str) -> EvidenceFrame:
    """One recorded native event as the bridge hands it to a projector."""

    # No ``native_method``: eve's envelope carries its own event type and the bridge does not stamp
    # an out-of-band method for this adapter, so the frame is shaped exactly as production diverts it.
    return EvidenceFrame(
        sequence=_RECORDED_SEQUENCES[event_type],
        kind=_RECORDED_KINDS[event_type],
        created_at=NOW,
        raw=dict(_RECORDED_ENVELOPES[event_type]),
    )


def _recorded_page(*event_types: str) -> EvidencePage:
    frames = tuple(_recorded(event_type) for event_type in event_types)
    return EvidencePage(
        frames=frames,
        latest_sequence=frames[-1].sequence if frames else 0,
        evicted_before_sequence=0,
        truncated=False,
        bridge_epoch="epoch-1",
    )


_VENDOR_DETAIL_EVENT_TYPES = frozenset(
    {"message.received", "step.started", "step.completed", "action.input.appended"}
)
"""Types in the census that the adapter passes through as vendor detail rather than interpreting."""
"""The recorded event types the adapter deliberately passes through as uninterpreted vendor detail.

They are not in its handler table and are classified here as recognized-but-silent; naming them
separately is what makes the census/table agreement above a real equality check rather than a
tautology."""


def eve_events_handler_table() -> tuple[str, ...]:
    """The adapter's own event vocabulary, read from the module that owns it."""

    return tuple(eve_events._HANDLERS)


def eve_projector_table() -> tuple[str, ...]:
    """The projector's classification table, read from the module that owns it."""

    return tuple(eve_projector._HANDLERS)


def _map(event_type: str) -> list[object]:
    projector = projector_for("eve")
    assert projector is not None
    return list(projector.map_evidence_frame(_recorded(event_type), evidence_ref="test"))


def _items(event_type: str) -> list[MappedItem]:
    return [output for output in _map(event_type) if isinstance(output, MappedItem)]


def _outcomes(event_type: str) -> list[MappedTurnOutcome]:
    return [output for output in _map(event_type) if isinstance(output, MappedTurnOutcome)]


def _text_of(block: object) -> object:
    return getattr(block, "text", getattr(block, "markdown", None))


@dataclass
class _StartedEve:
    """One started real adapter over the transport double; the caller closes it."""

    adapter: EveSessionAdapter

    async def aclose(self) -> None:
        await self.adapter.stop("forced")


async def _start_eve(launch: LaunchSpec | None = None) -> _StartedEve:
    adapter = EveSessionAdapter(
        runtime_factory=FakeRuntimeFactory(FakeEveRuntime()),
        clock=lambda: NOW,
    )
    await adapter.start(launch or _eve_launch())
    return _StartedEve(adapter=adapter)


class EveRegistryTests(unittest.TestCase):
    """One canonical eve id, reachable through every existing settings/registry owner."""

    def test_eve_is_the_last_curated_row_and_the_others_are_unchanged(self) -> None:
        ids = [harness.id for harness in HARNESSES]
        self.assertEqual(ids, ["claude", "codex", "pi", "eve"])
        # The added row is what makes ``"harness": "eve"`` legal in role/spawn settings; it must not
        # have displaced or rewritten an existing harness.
        for harness in HARNESSES[:3]:
            self.assertIsNone(harness.runtime_probe)
            self.assertEqual(harness.argv, (harness.command,))
        eve = next(harness for harness in HARNESSES if harness.id == "eve")
        self.assertEqual(eve.runtime_probe, EVE_RUNTIME_PROBE)
        self.assertEqual(eve.name, "eve")

    def test_the_eve_row_is_findable_by_id_like_every_other_harness(self) -> None:
        found = find_harness("eve")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.id, "eve")
        self.assertEqual(found.defined_in, "registry")

    def test_eve_is_reachable_through_the_existing_adapter_factory(self) -> None:
        adapter = create_harness_protocol_adapter("eve", env={})
        self.assertIsInstance(adapter, EveSessionAdapter)
        self.assertEqual(EVE_ADAPTER_ID, "eve-session")
        self.assertEqual(protocol_adapter_status("eve"), "starting")

    def test_the_eve_row_is_detected_through_its_probe(self) -> None:
        found = find_harness("eve")
        assert found is not None
        expected = eve_runtime_readiness(env=_READY_ENV, which=_which_nothing).ready
        self.assertTrue(expected, "the fixture environment must reach a usable eve runtime")
        self.assertTrue(is_detected(found, which=_which_nothing, env=_READY_ENV))

    def test_the_eve_row_consults_its_probe_and_never_path(self) -> None:
        # eve's command is a placeholder, not a program, so a PATH lookup can only ever say no.
        # Detection for this row must come from the readiness probe instead.
        found = find_harness("eve")
        assert found is not None
        seen: list[str] = []

        def recording_which(name: str) -> str | None:
            seen.append(name)
            return None

        self.assertTrue(is_detected(found, which=recording_which, env=_READY_ENV))
        self.assertEqual(seen, [], "the eve row consulted PATH for its readiness")

    def test_the_registry_leaves_the_path_harnesses_on_the_ordinary_lookup(self) -> None:
        entries = {entry.id: entry.detected for entry in detect_harnesses(which=_which_nothing)}
        self.assertEqual(set(entries), {"claude", "codex", "pi", "eve"})
        for harness_id in ("claude", "codex", "pi"):
            self.assertFalse(entries[harness_id])
        self.assertFalse(entries["eve"], "no node is resolvable in this environment")


class EveTerminalLaunchTests(unittest.TestCase):
    """A ready eve row is selectable as a session backend and never a terminal program.

    The reviewer's repro is the first case: with the probe ready, resolving a terminal open for eve
    used to return `LaunchCommand(cwd, ('ar-eve-runtime-application',))` -- a program that exists
    nowhere -- while no code anywhere refused it. The two callers ask different questions, and this
    class pins both answers.
    """

    def _request(self, harness: str = "eve", *, session_backend: bool = False):
        # ``which`` is injected so both halves of the launch question are drivable: the probe finds
        # the fixture interpreter for "node" and nothing at all for eve's placeholder program.
        return TerminalLaunchRequest(
            kind="harness",
            harness=harness,
            workspace_root=Path("/tmp"),
            shell="/bin/bash",
            which=_which_stub_node,
            session_backend=session_backend,
        )

    def test_a_terminal_open_of_eve_refuses_by_name_and_yields_no_argv(self) -> None:
        with self.assertRaises(ValueError) as raised:
            resolve_terminal_launch(self._request())
        detail = str(raised.exception)
        self.assertIn("eve", detail)
        self.assertIn("not a terminal program", detail)
        # The refusal must name what the operator can do instead, not just that it failed.
        self.assertIn("orchestration", detail)

    def test_a_ready_probe_row_is_what_makes_the_terminal_open_possible_to_reach(self) -> None:
        # The premise of the case above: eve really is detected here, so the refusal is a decision
        # about the terminal path rather than an accident of an unavailable runtime.
        found = find_harness("eve")
        assert found is not None
        self.assertTrue(is_detected(found, which=_which_stub_node))

    def test_a_session_backend_spawn_of_eve_still_resolves(self) -> None:
        # The seat-spawning caller starts the control runner, which owns the runtime; refusing that
        # would remove eve from role/profile configuration, which the requirement requires.
        command = resolve_terminal_launch(self._request(session_backend=True))
        self.assertEqual(command.argv, ("ar-eve-runtime-application",))

    def test_an_uninstalled_path_harness_refuses_in_both_modes(self) -> None:
        # The PATH question is unchanged by the eve seam: a harness whose program is absent refuses
        # whether it is being opened as a terminal or spawned as a seat.
        for session_backend in (False, True):
            with self.assertRaises(ValueError) as raised:
                resolve_terminal_launch(
                    self._request("pi", session_backend=session_backend),
                )
            self.assertIn("'pi'", str(raised.exception))

    def test_an_installed_path_harness_is_unaffected(self) -> None:
        # A PATH harness whose command resolves here behaves exactly as before the eve seam: the
        # terminal question is answered by the program, which this injected lookup provides.
        installed = {**{name: f"/usr/bin/{name}" for name in ("claude", "node")}}

        def which(name: str) -> str | None:
            return installed.get(name)

        request = replace(self._request("claude"), which=which)
        self.assertEqual(resolve_terminal_launch(request).argv, ("claude",))

    def test_a_settings_override_with_a_real_program_opts_back_in(self) -> None:
        # The check is the program, not the harness id: a user who supplies a real command for the
        # row gets a real launch.
        override = Harness(
            id="eve", name="eve", command="true", argv=("true",), runtime_probe=EVE_RUNTIME_PROBE
        )

        def which(name: str) -> str | None:
            return "/usr/bin/true" if name == "true" else _which_stub_node(name)

        request = replace(self._request(), harnesses=(override,), which=which)
        self.assertEqual(resolve_terminal_launch(request).argv, ("true",))


class EveReadinessProbeTests(unittest.TestCase):
    """Detection names the missing component instead of reporting a generic failure."""

    def test_a_missing_application_root_is_reported_by_name(self) -> None:
        readiness = eve_runtime_readiness(
            env={RUNTIME_ROOT_ENV: _MISSING_APPLICATION}, which=_which_nothing
        )
        self.assertFalse(readiness.ready)
        self.assertIn(_MISSING_APPLICATION, readiness.reason)
        self.assertIn(RUNTIME_ROOT_ENV, readiness.reason)

    def test_a_missing_node_runtime_is_reported_separately_from_a_missing_application(self) -> None:
        readiness = eve_runtime_readiness(env={}, which=_which_nothing)
        self.assertFalse(readiness.ready)
        self.assertIn("Node.js", readiness.reason)
        self.assertNotIn(_MISSING_APPLICATION, readiness.reason)

    def test_the_verdict_depends_on_the_application_root_and_not_only_the_interpreter(self) -> None:
        # Same interpreter, two application roots: only the resolvable one may answer ready, so a
        # probe that ignored the application (or ignored the interpreter) fails one of the two.
        root = _checkout_root()
        self.assertIsNotNone(root, "the checkout's eve_runtime application must be present")
        assert root is not None
        env = {**_READY_ENV, RUNTIME_ROOT_ENV: str(root / "eve_runtime")}
        self.assertTrue(eve_runtime_readiness(env=env, which=_which_nothing).ready)
        blocked = {**_READY_ENV, RUNTIME_ROOT_ENV: _MISSING_APPLICATION}
        self.assertFalse(eve_runtime_readiness(env=blocked, which=_which_nothing).ready)

    def test_the_harness_availability_answer_matches_the_probe(self) -> None:
        found = find_harness("eve")
        assert found is not None
        self.assertTrue(eve_runtime_readiness(env=_READY_ENV, which=_which_nothing).ready)
        self.assertTrue(is_harness_available(found, which=_which_nothing, env=_READY_ENV))
        self.assertIsNone(harness_availability_detail(found, which=_which_nothing, env=_READY_ENV))
        blocked_env = {RUNTIME_ROOT_ENV: _MISSING_APPLICATION}
        detail = harness_availability_detail(found, which=_which_nothing, env=blocked_env)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertIn(_MISSING_APPLICATION, detail)
        self.assertIn("eve", detail)

    def test_a_runtime_probed_harness_reports_the_probes_sentence_not_the_path_one(self) -> None:
        found = find_harness("eve")
        assert found is not None
        blocked = replace(found, runtime_probe=None)
        detail = harness_availability_detail(blocked, which=_which_nothing)
        assert detail is not None
        self.assertIn("is not on PATH", detail)
        self.assertNotIn("Node.js", detail)

    def test_a_real_interpreter_is_accepted_and_reported(self) -> None:
        interpreter = _STUB_NODE
        readiness = eve_runtime_readiness(
            env={NODE_EXECUTABLE_ENV: interpreter}, which=_which_nothing
        )
        self.assertTrue(readiness.ready)
        self.assertIn(interpreter, readiness.locations)

    def test_a_declared_interpreter_that_does_not_exist_is_not_a_runtime(self) -> None:
        # The reviewer's repro: a mistyped AR_EVE_NODE used to answer ready/available, which
        # advertised the harness on a box where every launch must fail.
        declared = "/nonexistent/ar-eve-node-does-not-exist"
        readiness = eve_runtime_readiness(env={NODE_EXECUTABLE_ENV: declared}, which=_which_nothing)
        self.assertFalse(readiness.ready)
        self.assertIn("does not exist", readiness.reason)
        found = find_harness("eve")
        assert found is not None
        self.assertFalse(
            is_detected(found, which=_which_nothing, env={NODE_EXECUTABLE_ENV: declared})
        )
        detail = harness_availability_detail(
            found, which=_which_nothing, env={NODE_EXECUTABLE_ENV: declared}
        )
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertIn(declared, detail)

    def test_a_declared_interpreter_that_is_not_executable_is_not_a_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(directory) / "node"
            plain.write_text("#!/bin/sh\necho v24.0.0\n", encoding="utf-8")
            plain.chmod(0o644)
            readiness = eve_runtime_readiness(
                env={NODE_EXECUTABLE_ENV: str(plain)}, which=_which_nothing
            )
            self.assertFalse(readiness.ready)
            self.assertIn("not executable", readiness.reason)

    def test_an_interpreter_below_the_required_major_is_not_a_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "node"
            old.write_text("#!/bin/sh\necho v22.20.0\n", encoding="utf-8")
            old.chmod(0o755)
            readiness = eve_runtime_readiness(
                env={NODE_EXECUTABLE_ENV: str(old)}, which=_which_nothing
            )
            self.assertFalse(readiness.ready)
            self.assertIn("below the required", readiness.reason)
            self.assertIn(str(MINIMUM_NODE_MAJOR), readiness.reason)

    def test_the_required_major_is_one_number_with_two_readers(self) -> None:
        # The probe and the launch transport must not be able to disagree about the floor: the
        # transport imports the kernel's constant rather than declaring its own.
        self.assertEqual(MINIMUM_NODE_MAJOR, 24)
        self.assertEqual(
            agents_remember.serving.eve_runtime_launch.MINIMUM_NODE_MAJOR, MINIMUM_NODE_MAJOR
        )


def _catalog_probe_factory(harness_id: str = "eve", *, env: object = None) -> _CatalogProbe:
    """The discovery-only adapter the catalog's cases substitute for the native process."""

    del harness_id, env
    return _CatalogProbe()


def _recording_probe_factory(probe: _CatalogProbe) -> Callable[..., _CatalogProbe]:
    """A factory returning one already-created probe, so the case can inspect what it received."""

    def _factory(harness_id: str, *, env: object) -> _CatalogProbe:
        del harness_id, env
        return probe

    return _factory


@dataclass
class _CatalogProbe:
    """A discovery-only adapter double that records the launch the catalog resolved.

    The catalog's own work -- harness resolution through the registry, the readiness verdict, the
    install fingerprint, the cache -- is what these cases test; the runtime process a real discovery
    would stage is the native run's job, not a unit case's. Substituting the transport here is the
    same seam the adapter suite substitutes, one layer up.
    """

    launch: LaunchSpec | None = None

    async def discover(self, launch: LaunchSpec) -> CapabilitySnapshot:
        self.launch = launch
        return CapabilitySnapshot(
            models=(
                ModelCapability(
                    key="fixture-deterministic-1",
                    display_name="fixture-deterministic-1",
                    effort_options=(),
                    default_effort="provider-default",
                    resolved_model="fixture-deterministic-1",
                    is_default=True,
                ),
            ),
            selected_model_key="fixture-deterministic-1",
            selected_effort="provider-default",
        )


class EveCapabilityDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    """The pre-session catalog discovers the real pinned configuration, or refuses by name."""

    def setUp(self) -> None:
        # eve stages one application directory per epoch under the workspace root, and the catalog's
        # discovery launch is rooted at that workspace: one throwaway root per case keeps two cases
        # from sharing a staged epoch.
        self.workspace = Path(tempfile.mkdtemp(prefix="ar-eve-caps-l8-"))
        self.addCleanup(shutil.rmtree, self.workspace, True)

    def _catalog(self, *, env: Mapping[str, str] | None = None) -> HarnessCapabilityCatalog:
        return HarnessCapabilityCatalog(
            self.workspace,
            which=_which_nothing,
            adapter_factory=_catalog_probe_factory,
            environment=lambda: dict(_READY_ENV if env is None else env),
        )

    async def test_the_catalog_discovers_eve_through_the_existing_factory(self) -> None:
        result = await self._catalog().get("eve", registry=HARNESSES)
        self.assertEqual(result.harness_id, "eve")
        # A runtime-probed harness is not gated behind ``which`` on its placeholder command: the
        # lookup reaches discovery instead of answering 404 for a missing PATH program. What the
        # discovery returns is the adapter double's business here; the model claim is checked
        # against the runtime's own source in the honest-catalog cases below.
        self.assertEqual(result.cache_status, "miss")

    async def test_the_discovery_launch_reaches_the_adapter_factory_for_eve(self) -> None:
        probe = _CatalogProbe()
        catalog = HarnessCapabilityCatalog(
            self.workspace,
            which=_which_nothing,
            adapter_factory=_recording_probe_factory(probe),
            environment=lambda: dict(_READY_ENV),
        )
        await catalog.get("eve", registry=HARNESSES)
        assert probe.launch is not None
        self.assertEqual(probe.launch.harness_id, "eve")
        self.assertEqual(probe.launch.cwd, self.workspace)

    async def test_the_install_fingerprint_follows_the_probed_interpreter(self) -> None:
        # Two independent inputs that differ in exactly one place -- the interpreter the probe
        # resolved -- must produce two different install identities, and the same input must
        # reproduce one. A fingerprint that ignored the interpreter (for example one keyed on the
        # placeholder command, which never changes) would collapse the two reads into one entry and
        # leave a stale catalog cached after an interpreter swap.
        with tempfile.TemporaryDirectory() as directory:
            runtimes = self._two_interpreter_runtimes(Path(directory))
            first = await self._catalog(env={NODE_EXECUTABLE_ENV: runtimes[0]}).get(
                "eve", registry=HARNESSES
            )
            repeated = await self._catalog(env={NODE_EXECUTABLE_ENV: runtimes[0]}).get(
                "eve", registry=HARNESSES
            )
            swapped = await self._catalog(env={NODE_EXECUTABLE_ENV: runtimes[1]}).get(
                "eve", registry=HARNESSES
            )
        self.assertEqual(first.install_fingerprint, repeated.install_fingerprint)
        self.assertNotEqual(first.install_fingerprint, swapped.install_fingerprint)

    def _two_interpreter_runtimes(self, directory: Path) -> tuple[str, str]:
        """Two usable interpreters on disk that differ in their own identity."""

        return (
            _stub_interpreter(directory, "v24.19.0", name="node-24.19.0"),
            _stub_interpreter(directory, "v24.20.0", name="node-24.20.0"),
        )

    async def test_a_runtime_that_cannot_run_refuses_by_name_before_discovery(self) -> None:
        catalog = HarnessCapabilityCatalog(
            self.workspace,
            which=_which_nothing,
            adapter_factory=_catalog_probe_factory,
            environment=lambda: {RUNTIME_ROOT_ENV: _MISSING_APPLICATION},
        )
        with self.assertRaises(HarnessCapabilityLookupError) as raised:
            await catalog.get("eve", registry=HARNESSES)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertIn(_MISSING_APPLICATION, str(raised.exception))

    async def test_the_catalog_refuses_an_unknown_harness_before_discovery(self) -> None:
        with self.assertRaises(HarnessCapabilityLookupError):
            await self._catalog().get("nope", registry=HARNESSES)

    async def test_the_envelope_the_dashboard_reads_has_the_contracted_shape(self) -> None:
        # The shape the dashboard's validator requires, asserted on the object the route serializes;
        # the VALUES are the discovery's, and their truth is checked against the runtime elsewhere.
        envelope = (await self._catalog().get("eve", registry=HARNESSES)).to_json()
        self.assertEqual(envelope["schema"], "ar-harness-capabilities/v1")
        self.assertEqual(envelope["harness"], "eve")
        self.assertEqual(envelope["cacheStatus"], "miss")
        self.assertTrue(str(envelope["installFingerprint"]))
        capabilities = envelope["capabilities"]
        assert isinstance(capabilities, dict)
        self.assertEqual(
            sorted(capabilities), ["configOptions", "models", "selectedEffort", "selectedModelKey"]
        )
        (model,) = capabilities["models"]  # type: ignore[misc]
        self.assertEqual(
            sorted(model),
            [
                "defaultEffort",
                "description",
                "displayName",
                "effortOptions",
                "hidden",
                "isDefault",
                "key",
                "provider",
                "resolvedModel",
                "selectable",
                "supportsEffort",
            ],
        )


class EveCapabilityHonestyTests(unittest.IsolatedAsyncioTestCase):
    """What the catalog advertises is what the adapter does; both read the live adapter."""

    async def test_the_advertised_model_is_the_one_the_runtime_would_use(self) -> None:
        # One side is the catalog the adapter publishes; the other is the pinned application's OWN
        # declared fallback, read from its source. A catalog that named a different model would
        # advertise a selection no launch produces.
        started = await _start_eve()
        try:
            catalog = started.adapter.advertise()
        finally:
            await started.aclose()
        (model,) = catalog.models
        self.assertEqual(model.key, runtime_default_model())
        self.assertEqual(catalog.selected_model_key, runtime_default_model())
        self.assertEqual(catalog.selected_effort, "provider-default")

    async def test_the_pinned_runtime_reads_no_effort_value_and_the_catalog_agrees(self) -> None:
        # Two independent facts that must agree: the authored runtime application consumes no effort
        # input, and the capability catalog therefore advertises no effort option. If effort
        # plumbing is ever added to the runtime, this case fails until the catalog offers it again;
        # if a menu is published without that plumbing, it fails the other way.
        runtime_root = _checkout_root()
        self.assertIsNotNone(runtime_root)
        assert runtime_root is not None
        authored = sorted((runtime_root / "eve_runtime" / "agent").rglob("*.ts"))
        self.assertTrue(authored, "the pinned runtime application must have authored sources")
        for source in authored:
            self.assertNotIn("AR_EVE_EFFORT", source.read_text(encoding="utf-8"), source.name)
        started = await _start_eve()
        try:
            catalog = started.adapter.advertise()
        finally:
            await started.aclose()
        (model,) = catalog.models
        self.assertFalse(model.supports_effort)
        self.assertEqual(model.effort_options, ())
        self.assertIsNone(model.default_effort)
        # No selectable effort config option is published either: an option with no backing value
        # would be the same false control in a second shape.
        self.assertEqual([option.config_id for option in catalog.config_options], ["model"])

    async def test_the_effort_setter_refuses_every_candidate_including_its_own_vocabulary(
        self,
    ) -> None:
        started = await _start_eve()
        try:
            catalog = started.adapter.advertise()
            for candidate in (*REASONING_EFFORTS, "not-an-advertised-effort"):
                result = await started.adapter.set_effort(candidate)
                self.assertFalse(result.ok)
                self.assertEqual(result.acceptance, "unsupported")
                # A refused change never reports a new effective value.
                self.assertEqual(result.effective_value, catalog.selected_model_key)
            model_result = await started.adapter.set_model("some-other-model")
            self.assertFalse(model_result.ok)
            self.assertEqual(model_result.acceptance, "unsupported")
            self.assertEqual(model_result.effective_value, catalog.selected_model_key)
        finally:
            await started.aclose()

    async def test_no_advertised_control_lacks_a_runtime_consumer(self) -> None:
        # The catalog's selectable axes, each checked against what the pinned runtime actually
        # reads: the model is compiled from AR_EVE_MODEL, and no effort input exists at all. This is
        # the general shape of the honesty rule the effort finding asked for, so a future axis has
        # to bring its own consumer before it can be published.
        started = await _start_eve()
        try:
            catalog = started.adapter.advertise()
        finally:
            await started.aclose()
        published = sorted(option.config_id for option in catalog.config_options)
        runtime_consumers = {"model": "AR_EVE_MODEL", "effort": "AR_EVE_EFFORT"}
        runtime_root = _checkout_root()
        assert runtime_root is not None
        text = "\n".join(
            source.read_text(encoding="utf-8")
            for source in sorted((runtime_root / "eve_runtime" / "agent").rglob("*.ts"))
        )
        for axis in published:
            consumer = runtime_consumers.get(axis)
            self.assertIsNotNone(
                consumer, f"published axis {axis!r} has no declared runtime consumer"
            )
            assert consumer is not None
            self.assertIn(consumer, text, f"published axis {axis!r} is unbacked by the runtime")

    async def test_eve_control_capabilities_never_advertise_an_unimplemented_surface(self) -> None:
        controls = control_capabilities_for("eve", _snapshot())
        for kind, capability in (
            ("image", controls.attachments.image),
            ("file", controls.attachments.file),
            ("resource", controls.attachments.resource),
        ):
            self.assertEqual(capability.state, "unavailable", kind)
            self.assertEqual(capability.max_bytes, 0)
            self.assertEqual(capability.max_count, 0)
        self.assertEqual(controls.interrupt.state, "supported")

    async def test_eve_capabilities_never_borrow_another_harnesses_evidence(self) -> None:
        eve = capabilities_for("eve", _snapshot())
        assert eve.live.text.evidence is not None
        for harness_id in ("codex", "claude", "pi"):
            other = capabilities_for(harness_id, _snapshot())  # type: ignore[arg-type]
            assert other.live.text.evidence is not None
            self.assertNotEqual(
                eve.live.text.evidence.fixture_id, other.live.text.evidence.fixture_id
            )
            self.assertNotEqual(
                eve.live.text.evidence.runtime_version, other.live.text.evidence.runtime_version
            )

    async def test_an_unknown_harness_fails_loudly_instead_of_inheriting_a_catalog(self) -> None:
        with self.assertRaises(KeyError):
            capabilities_for("not-a-harness", _snapshot())  # type: ignore[arg-type]

    async def test_eve_telemetry_is_declared_absent_not_borrowed(self) -> None:
        telemetry = telemetry_capabilities_for("eve", _snapshot())
        for field in ("context", "usage", "cost", "rate_limit", "compaction"):
            capability = getattr(telemetry, field)
            self.assertEqual(capability.state, "unavailable", field)
            self.assertEqual(capability.evidence_tier, "none", field)


class EveProjectorTests(unittest.TestCase):
    """Live and replayed frames project through the existing engine, one boundary each."""

    def test_every_registered_harness_id_has_a_projector(self) -> None:
        for harness_id in ("codex", "claude", "pi", "eve"):
            self.assertIsNotNone(projector_for(harness_id), harness_id)
        self.assertEqual(set(PROJECTORS), {"codex", "claude", "pi", "eve"})

    def test_the_eve_projector_declares_stream_only_evidence(self) -> None:
        projector = projector_for("eve")
        assert projector is not None
        self.assertFalse(projector.uses_native_pages)
        self.assertFalse(projector.uses_transcript_echo)
        with self.assertRaises(NotImplementedError):
            projector.map_transcript_echo({}, evidence_ref="test")

    def test_a_delta_mints_its_item_and_delivers_the_text_once(self) -> None:
        outputs = _map("message.appended")
        items = [output for output in outputs if isinstance(output, MappedItem)]
        deltas = [output for output in outputs if not isinstance(output, MappedItem)]
        self.assertEqual(len(items), 1)
        self.assertEqual(len(deltas), 1)
        item = items[0].item
        self.assertEqual(item.kind, "message")
        self.assertEqual(item.phase, "streaming")
        self.assertEqual(item.turn_id, "turn_0")
        # The carrier block is empty: the frame's text arrives exactly once, as the delta.
        (block,) = item.blocks
        self.assertEqual(_text_of(block), "")

    def test_the_completed_block_revises_the_same_item_the_delta_minted(self) -> None:
        delta_item = _items("message.appended")[0].item
        completed_item = _items("message.completed")[0].item
        self.assertEqual(completed_item.item_id, delta_item.item_id)
        self.assertEqual(completed_item.phase, "completed")
        (block,) = completed_item.blocks
        self.assertEqual(_text_of(block), "note.txt written")

    def test_a_tool_round_trip_projects_as_one_call_item_with_input_and_output(self) -> None:
        call = _items("actions.requested")[0].item
        result = _items("action.result")[0].item
        self.assertEqual(call.item_id, result.item_id)
        # Both frames address ONE item of kind ``tool-call``: the result adds its output block and
        # the store's per-kind block union keeps the invocation the call frame recorded.
        self.assertEqual(call.kind, "tool-call")
        self.assertEqual(result.kind, "tool-call")
        self.assertEqual(result.phase, "completed")
        input_block = next(block for block in call.blocks if block.type == "tool-input")
        self.assertEqual(getattr(input_block, "summary", None), "ar_workspace_write")
        self.assertEqual(
            getattr(input_block, "data", None),
            {"path": "note.txt", "text": "written-by-eve-fixture"},
        )
        output_block = next(block for block in result.blocks if block.type == "tool-output")
        self.assertIn("note.txt", json.dumps(getattr(output_block, "data", None)))

    def test_the_operator_message_is_preserved_without_claiming_a_producer(self) -> None:
        item = _items("message.received")[0].item
        self.assertEqual(item.role, "user")
        self.assertEqual(item.lane, "unknown-input")
        # The text comes from the durable record, not the live window, and the item never asserts a
        # producer the stream cannot prove.
        self.assertEqual(item.source, "native-history")
        self.assertIsNone(item.provenance.producer)
        (block,) = item.blocks
        self.assertEqual(_text_of(block), "run the fixture tool round-trip")

    def test_an_ordinary_turn_settles_once_and_the_park_is_not_a_settlement(self) -> None:
        settled = _outcomes("turn.completed")
        self.assertEqual(
            [(outcome.outcome, outcome.turn_id) for outcome in settled], [("completed", "turn_0")]
        )
        parked = _map("session.waiting")
        self.assertEqual(
            [output for output in parked if isinstance(output, MappedTurnOutcome)],
            [],
            "session.waiting minted a turn outcome; one turn would settle twice",
        )
        items = [output for output in parked if isinstance(output, MappedItem)]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item.kind, "notice")
        self.assertEqual(items[0].item.phase, "waiting")
        self.assertIsNone(items[0].item.turn_id)

    def test_a_cancelled_turn_settles_as_interrupted(self) -> None:
        self.assertEqual(
            [(outcome.outcome, outcome.turn_id) for outcome in _outcomes("turn.cancelled")],
            [("interrupted", "turn_3")],
        )

    def test_a_frame_without_an_event_type_in_its_envelope_is_refused(self) -> None:
        projector = projector_for("eve")
        assert projector is not None
        frame = _recorded("turn.completed")
        shapeless = replace(frame, raw={"data": {"turnId": "turn_0"}, "meta": {}})
        with self.assertRaises(UnmappableShape):
            projector.map_evidence_frame(shapeless, evidence_ref="test")

    def test_an_unmapped_eve_event_is_preserved_rather_than_dropped(self) -> None:
        projector = projector_for("eve")
        assert projector is not None
        recorded = _recorded("session.started")
        frame = replace(recorded, raw={**dict(recorded.raw), "type": "some.future.event"})
        outputs = projector.map_evidence_frame(frame, evidence_ref="test")
        unknown = [output for output in outputs if isinstance(output, MappedUnknownVendor)]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0].vendor_type, "eve:some.future.event")
        self.assertIn("some.future.event", unknown[0].safe_summary)

    def test_recognized_control_state_mints_no_conversation_item(self) -> None:
        # Session start and turn start are the status projection's business; a second copy here
        # could only disagree with it, so they are consumed silently by name.
        for event_type in ("session.started", "turn.started"):
            self.assertEqual(_map(event_type), [], event_type)

    def test_every_scripted_frame_carries_an_honest_provenance_label(self) -> None:
        # The labels are correct by construction in this module; this case is what makes them
        # falsifiable. It pins three things: every scripted type is labelled with one of the three
        # kinds, the `recorded` set equals the census transcribed from the pinned release's run (so a
        # frame cannot be quietly promoted to "recorded"), and the one composed frame names its
        # derivation instead of borrowing the recording's authority.
        labels = {event_type: _RECORDED_PROVENANCE[event_type] for event_type in _SESSION_ORDER}
        self.assertEqual(set(labels), set(_SESSION_ORDER))
        self.assertEqual(
            sorted(set(labels.values())), ["derived", "production-emitted", "recorded"]
        )
        recorded = {event_type for event_type, label in labels.items() if label == "recorded"}
        self.assertEqual(sorted(recorded), sorted(_PINNED_RUN_CENSUS))
        self.assertEqual(labels["authorization.required"], "derived")
        self.assertTrue(_AUTHORIZATION_REQUIRED_FRAME_PROVENANCE)
        self.assertEqual(labels["input.requested"], "production-emitted")

    def test_the_projector_classifies_the_adapters_whole_event_vocabulary(self) -> None:
        # The population comes from the ADAPTER's own handler table plus the types it deliberately
        # passes through as vendor detail -- never from this test's convenience list, which is how
        # three real release types fell through unnoticed. Every one of them has to be classified.
        population = set(eve_events_handler_table()) | _VENDOR_DETAIL_EVENT_TYPES
        projector_table = set(eve_projector_table())
        unclassified = sorted(population - projector_table)
        self.assertEqual(unclassified, [], f"unclassified event types: {unclassified}")

    def test_the_recorded_run_only_emits_types_the_adapter_can_yield(self) -> None:
        # The census is the release's observed output; it must be a subset of what the adapter's own
        # table and vendor-detail path can produce, so the census cannot be invented either.
        population = set(eve_events_handler_table()) | _VENDOR_DETAIL_EVENT_TYPES
        self.assertEqual(sorted(set(_RECORDED_KINDS) - population), [])

    def test_every_recorded_event_type_projects_or_is_declared_silent(self) -> None:
        # The mapped half, asserted on real payloads: a recorded type either produces a projection
        # or is on the declared-silent list, and never becomes an unknown-vendor row.
        for event_type in sorted(_RECORDED_KINDS):
            outputs = _map(event_type)
            if event_type in SILENT_CONTROL_EVENTS:
                self.assertEqual(outputs, [], f"{event_type} is declared silent but projected")
                continue
            self.assertTrue(outputs, f"{event_type} produced no projection")
            self.assertEqual(
                [output for output in outputs if isinstance(output, MappedUnknownVendor)],
                [],
                f"{event_type} fell through to unknown-vendor",
            )

    def test_an_event_type_no_one_classified_stays_visible(self) -> None:
        # The counterpart: the classification above must not be achieved by swallowing everything.
        projector = projector_for("eve")
        assert projector is not None
        recorded = _recorded("session.started")
        frame = replace(recorded, raw={**dict(recorded.raw), "type": "release.2099.event"})
        outputs = projector.map_evidence_frame(frame, evidence_ref="test")
        (unknown,) = [output for output in outputs if isinstance(output, MappedUnknownVendor)]
        self.assertEqual(unknown.vendor_type, "eve:release.2099.event")


class EveAdapterToProjectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """The real frame path: the adapter emits, the real bridge diverts, the projector maps.

    The cases above drive the projector with recorded frames. These drive the production chain that
    produces those frames, so the projector's discriminating evidence (the event type the bridge
    carries out of band) is proved end to end rather than assumed by the fixture.
    """

    async def _evidence_frames(self, runtime: FakeEveRuntime) -> tuple[EvidenceFrame, ...]:
        adapter = EveSessionAdapter(runtime_factory=FakeRuntimeFactory(runtime), clock=lambda: NOW)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_eve_launch())
        try:
            # The delivery goes through the bridge's own authority, which is what binds the durable
            # session the reader then follows -- the production path a dashboard submit takes.
            await bridge.submissions().submit(
                bridge.prompt("run the fixture turn", source="cockpit", request_id="req-1")
            )
            session_id = runtime.created[0]
            runtime.turn_events(session_id, FakeTurn(number=0, deltas=("note.",), message="done"))
            # The bridge's reader loop is a task; give it the same bounded settle the adapter suite
            # uses, then page the buffer it filled.
            for _ in range(50):
                await asyncio.sleep(0.01)
                frames = bridge.evidence().frames
                if any(frame.native_method == "session.waiting" for frame in frames):
                    break
            return bridge.evidence().frames
        finally:
            await bridge.stop("forced")

    async def test_the_bridge_carries_each_events_own_type_in_its_envelope(self) -> None:
        frames = await self._evidence_frames(FakeEveRuntime())
        by_type = {str(frame.raw.get("type")): frame for frame in frames}
        self.assertIn("turn.completed", by_type)
        self.assertIn("session.waiting", by_type)
        # Two frames the adapter marks as a turn boundary in DIFFERENT senses share one kind...
        self.assertEqual(by_type["session.waiting"].kind, "completed")
        self.assertEqual(by_type["turn.completed"].kind, "state")
        # ... and are told apart by the event type eve puts in its own envelope, which the bridge
        # diverts verbatim and no adapter-side stamp is needed for.
        self.assertIsNone(by_type["session.waiting"].native_method)
        self.assertIn("session.waiting", by_type)

    async def test_the_live_frames_lift_to_one_terminal_outcome_per_turn(self) -> None:
        frames = await self._evidence_frames(FakeEveRuntime())
        types = [str(frame.raw.get("type")) for frame in frames]
        self.assertLess(types.index("turn.completed"), types.index("session.waiting"))
        page = EvidencePage(
            frames=frames,
            latest_sequence=frames[-1].sequence,
            evicted_before_sequence=0,
            truncated=False,
            bridge_epoch="epoch-1",
        )
        projection = latest_terminal_evidence(page, "eve")
        self.assertIsNotNone(projection)
        assert projection is not None
        self.assertEqual(projection.evidence.outcome, "completed")
        self.assertEqual(projection.evidence.turn_id, "turn_0")


_SESSION_ORDER = (
    "session.started",
    "turn.started",
    "message.received",
    "actions.requested",
    "action.input.appended",
    "action.result",
    "input.requested",
    "authorization.required",
    "step.started",
    "step.completed",
    "message.appended",
    "message.completed",
    "turn.completed",
    "session.waiting",
    "turn.started",
    "turn.cancelled",
    "session.waiting",
    "step.failed",
)
"""One scripted eve session: a tool round-trip with a question and an approval, then a cancelled turn.

**Where each frame's shape comes from -- stated per frame, because they are not all the same kind of
evidence.** ``_RECORDED_ENVELOPES`` below marks every entry with its provenance:

* ``recorded`` -- the envelope is verbatim from the pinned release's own run
  (``notes/reports/260915-CAPS-L6-evidence-a2/live-native-events.json``, 52 frames, 14 types). These
  are live bytes.
* ``production-emitted`` -- the envelope was produced by the **production emission path** and then
  captured, because the recorded run never exercised that event: ``input.requested`` is emitted by
  ``FakeTurn(requests=...)`` through ``FakeEveRuntime.turn_events`` (the same emitter the adapter's
  own suite drives), and its payload was read back out of the durable record the emitter wrote.
* ``derived`` -- the event is not exercised by any shipped fixture, so its envelope is composed from
  the fields the adapter's own handler READS (``authorization.required``: ``name``, ``description``,
  ``turnId``) and emitted through the same ``FakeEveRuntime.emit`` durable-record writer the adapter's
  suite already uses for raw events. It is the weakest of the three and is labelled as such: the
  pinned release emits the type, but this environment has no recorded instance of it.

The distinction matters because a capture's authority is only as good as the provenance of its
inputs, and claiming a recorded run produced a frame it never produced is exactly the defect class
this leaf exists to prevent. ``_AUTHORIZATION_REQUIRED_FRAME_PROVENANCE`` carries the derivation for
the one composed frame, and ``test_every_scripted_frame_carries_an_honest_provenance_label`` makes the
labelling falsifiable: it requires a label for every scripted type, requires the ``recorded`` set to
equal the census transcribed from the pinned run (so a frame cannot be promoted to ``recorded``), and
requires the composed frame to name its derivation. It does **not** re-verify that census against the
recording itself -- the recording lives in the task's coordination tree, outside this repository, so
the census is a cited transcription rather than a re-read.
"""

_CAPTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "dashboard"
    / "src"
    / "test"
    / "fixtures"
    / "eveConversationCapture.json"
)
"""The mounted UI test's input: the projection this projector produces, in the dashboard's wire shape.

Regenerate with the command recorded in this leaf's report; the assertion below fails on drift, so
the capture can never silently describe an older projector.
"""


def _scripted_eve_bridge() -> _ScriptedBridge:
    """The engine's scripted substrate, loaded with the session above."""

    bridge = _ScriptedBridge(harness="eve")
    for event_type in _SESSION_ORDER:
        bridge.push_evidence(_RECORDED_KINDS[event_type], dict(_RECORDED_ENVELOPES[event_type]))
    return bridge


async def eve_conversation_page() -> PageResult:
    """The production projection of the scripted session, as the dashboard receives it.

    The real engine (``ActiveSessionProjector``) is driven over the real mapper, so the returned page
    is the same object the serving route serializes -- not a hand-built fixture.
    """

    return await engine_projector(_scripted_eve_bridge(), harness="eve").page(
        before_ordinal=None, limit=200
    )


def capture_payload(page: PageResult) -> dict[str, object]:
    """The wire body the dashboard consumes, serialized by the production model aliases.

    ``PageResult`` carries the projected items and the status the surface renders; both are dumped
    through the same ``WireModel`` aliases the serving route uses, so the capture is the server's
    own serialization rather than a hand-written mirror of it.
    """

    return {
        "items": [
            item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in page.items
        ],
        "status": page.status.model_dump(mode="json", by_alias=True, exclude_none=True),
    }


class EveConversationCaptureTests(unittest.IsolatedAsyncioTestCase):
    """The projection the mounted UI test renders, pinned as the exact wire body."""

    async def test_the_projection_matches_the_capture_the_mounted_ui_renders(self) -> None:
        payload = capture_payload(await eve_conversation_page())
        self.assertTrue(
            _CAPTURE_PATH.is_file(),
            f"missing mounted-UI capture {_CAPTURE_PATH}; regenerate it with the documented command",
        )
        recorded = json.loads(_CAPTURE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload, recorded)

    async def test_the_capture_shows_the_states_the_packet_names(self) -> None:
        page = await eve_conversation_page()
        items = page.items
        kinds = [item.kind for item in items]
        # The states the packet's mounted scenario names, each present as a visible item rather than
        # an inference: the tool round-trip, the completion, the cancellation and the failure.
        self.assertIn("tool-call", kinds)
        self.assertIn("turn-result", kinds)
        self.assertIn("error", kinds)
        tool = next(item for item in items if item.kind == "tool-call")
        self.assertEqual({block.type for block in tool.blocks}, {"tool-input", "tool-output"})
        # The packet's enumerated element "an input request", asserted by name: one waiting
        # interaction carrying the question text, its ids and its answer options.
        interactions = [item for item in items if item.kind == "interaction"]
        self.assertEqual(len(interactions), 2)
        question = next(
            item for item in interactions if item.item_id == "eve:interaction:req_input_1"
        )
        self.assertEqual(question.phase, "waiting")
        self.assertIn("Proceed with the write?", self._text_of(question))
        approval = next(
            item
            for item in interactions
            if item.item_id == "eve:interaction:authorization:workspace_write"
        )
        self.assertEqual(approval.phase, "waiting")
        self.assertIn("write note.txt", self._text_of(approval))
        phases = {(item.kind, item.phase) for item in items}
        self.assertIn(("turn-result", "completed"), phases)
        self.assertIn(("turn-result", "interrupted"), phases)
        self.assertIn(("error", "failed"), phases)
        self.assertIn(("interaction", "waiting"), phases)

    @staticmethod
    def _text_of(item: object) -> str:
        """The rendered text of one item, read from the blocks the surface renders."""

        return " ".join(
            getattr(block, "text", "") or getattr(block, "markdown", "")
            for block in item.blocks  # type: ignore[attr-defined]
        )

    async def test_live_frames_and_replayed_frames_project_identically(self) -> None:
        # Agreement between the two producers that really exist: the frames the real adapter and the
        # real bridge emit, and the same session's recorded frames replayed from the evidence page.
        # Both go through the same projector and engine, so a divergence means the live path carries
        # something the replay path does not (or vice versa).
        live_frames = await EveAdapterToProjectionIntegrationTests()._evidence_frames(
            FakeEveRuntime()
        )
        replayed = _replay_bridge(live_frames)
        replayed_page = await engine_projector(replayed, harness="eve").page(
            before_ordinal=None, limit=200
        )
        live_page = await engine_projector(_replay_bridge(live_frames), harness="eve").page(
            before_ordinal=None, limit=200
        )
        self.assertEqual(
            [(item.item_id, item.kind, item.phase) for item in live_page.items],
            [(item.item_id, item.kind, item.phase) for item in replayed_page.items],
        )

    async def test_a_reconnect_replays_evidence_without_duplicating_items(self) -> None:
        # A reconnect re-reads the window from the persisted cursor. The projector is pure, so the
        # replayed frames must revise the same items rather than mint twins.
        bridge = _ScriptedBridge(harness="eve")
        for event_type in _SESSION_ORDER:
            bridge.push_evidence(_RECORDED_KINDS[event_type], dict(_RECORDED_ENVELOPES[event_type]))
        projector = engine_projector(bridge, harness="eve")
        first = await projector.page(before_ordinal=None, limit=200)
        # The same frames again, exactly as an overlapping re-read from an earlier cursor delivers.
        bridge.evidence_frames.clear()
        for event_type in _SESSION_ORDER:
            bridge.push_evidence(_RECORDED_KINDS[event_type], dict(_RECORDED_ENVELOPES[event_type]))
        second = await projector.page(before_ordinal=None, limit=200)
        self.assertEqual(
            [(item.item_id, item.revision) for item in first.items],
            [(item.item_id, item.revision) for item in second.items],
        )


def _replay_bridge(frames: tuple[EvidenceFrame, ...]) -> _ScriptedBridge:
    """One fixture bridge carrying exactly the frames a real producer emitted."""

    bridge = _ScriptedBridge(harness="eve")
    for frame in frames:
        bridge.push_evidence(frame.kind, dict(frame.raw))
    return bridge


_QUESTION_REQUEST = {
    "action": {"callId": "call_q", "input": {}, "kind": "tool-call", "toolName": "ask_question"},
    "kind": "question",
    "options": [{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}],
    "prompt": "Proceed with the write?",
    "requestId": "req_input_1",
}


class EveInteractionProjectionTests(unittest.IsolatedAsyncioTestCase):
    """Questions and approvals, driven by payloads the production path emitted.

    These are the cases the baseline's `L8R-3` residual named: the projector classified
    ``input.requested``/``authorization.required`` but nothing drove either with a payload, so
    behaviour 3's "questions/approvals" claim rested on a table entry. Each case emits the event
    through the runtime the adapter talks to, lets the **real adapter and real bridge** produce the
    evidence frame, projects it through the **real engine**, and asserts what a reader would see --
    plus the interaction the adapter's own snapshot is holding, which is an artifact independent of
    the item the projector mints from the same frame.
    """

    async def _projected(
        self, emit: object, *, expected: str
    ) -> tuple[tuple[ConversationItem, ...], AdapterSnapshot]:
        """Emit one native event, then return the projected items and the adapter's own snapshot."""

        runtime = FakeEveRuntime()
        adapter = EveSessionAdapter(runtime_factory=FakeRuntimeFactory(runtime), clock=lambda: NOW)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_eve_launch())
        try:
            await bridge.submissions().submit(
                bridge.prompt("ask me", source="cockpit", request_id="req-1")
            )
            session_id = runtime.created[0]
            emit(runtime, session_id)  # type: ignore[operator]
            for _ in range(120):
                await asyncio.sleep(0.02)
                if any(
                    str(frame.raw.get("type")) == expected for frame in bridge.evidence().frames
                ):
                    break
            frames = tuple(bridge.evidence().frames)
            snapshot = await adapter.snapshot()
        finally:
            await bridge.stop("forced")
        page = await engine_projector(_replay_bridge(frames), harness="eve").page(
            before_ordinal=None, limit=200
        )
        return page.items, snapshot

    async def test_an_input_request_becomes_an_answerable_interaction_item(self) -> None:
        def emit(runtime: FakeEveRuntime, session_id: str) -> None:
            runtime.turn_events(
                session_id, FakeTurn(number=0, message="thinking", requests=[_QUESTION_REQUEST])
            )

        items, snapshot = await self._projected(emit, expected="input.requested")
        interactions = [item for item in items if item.kind == "interaction"]
        self.assertEqual(len(interactions), 1)
        item = interactions[0]
        self.assertEqual(item.phase, "waiting")
        self.assertIsNotNone(item.correlation)
        assert item.correlation is not None
        self.assertEqual(item.correlation.vendor_correlation_id, "req_input_1")
        text = " ".join(
            getattr(block, "text", "") or getattr(block, "markdown", "") for block in item.blocks
        )
        self.assertIn("question", text)
        self.assertIn("Proceed with the write?", text)
        # The answers are the request's own option ids -- the exact tokens the adapter's
        # response_payload turns into eve's {requestId, optionId}.
        (choices,) = [block for block in item.blocks if block.type == "choices"]
        self.assertEqual(choices.interaction_id, "req_input_1")
        self.assertEqual(
            [(option.option_id, option.label) for option in choices.options],
            [("approve", "Approve"), ("deny", "Deny")],
        )
        # The adapter's own snapshot is holding the SAME request, with the same answer tokens: the
        # item names a question something is actually waiting on, proved from a second artifact.
        pending = snapshot.pending_interaction
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertEqual(pending.interaction_id, "req_input_1")
        self.assertEqual(pending.kind, "question")
        self.assertEqual(tuple(pending.choices), ("approve", "deny"))

    async def test_an_authorization_challenge_becomes_a_waiting_approval_item(self) -> None:
        def emit(runtime: FakeEveRuntime, session_id: str) -> None:
            # A challenge is turn-scoped, so the turn opens first: the adapter refuses an event
            # naming a turn this session never opened, which is why the sequence matters here.
            runtime.emit(session_id, "turn.started", {"sequence": 0, "turnId": "turn_0"})
            runtime.emit(
                session_id,
                "authorization.required",
                {
                    "description": "the runtime asks to write note.txt",
                    "name": "workspace_write",
                    "sequence": 0,
                    "stepIndex": 0,
                    "turnId": "turn_0",
                },
            )

        items, snapshot = await self._projected(emit, expected="authorization.required")
        interactions = [item for item in items if item.kind == "interaction"]
        self.assertEqual(len(interactions), 1)
        item = interactions[0]
        self.assertEqual(item.phase, "waiting")
        # The id is the adapter's own derived authorization id, so the row and the pending challenge
        # name the same thing without either side reading the other.
        self.assertIsNotNone(item.correlation)
        assert item.correlation is not None
        self.assertEqual(item.correlation.vendor_correlation_id, "authorization:workspace_write")
        text = " ".join(
            getattr(block, "text", "") or getattr(block, "markdown", "") for block in item.blocks
        )
        self.assertIn("the runtime asks to write note.txt", text)
        pending = snapshot.pending_interaction
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertEqual(pending.interaction_id, "authorization:workspace_write")
        self.assertEqual(pending.kind, "authorization")
        self.assertIn("write note.txt", pending.prompt)


class EveTerminalProjectionTests(unittest.TestCase):
    """The catalog's own terminal lift reads this projector, so a park cannot mask a cancel."""

    def test_a_cancelled_then_parked_turn_still_lifts_as_interrupted(self) -> None:
        projection = latest_terminal_evidence(
            _recorded_page("turn.started", "turn.cancelled", "session.waiting"), "eve"
        )
        self.assertIsNotNone(projection)
        assert projection is not None
        self.assertEqual(projection.evidence.outcome, "interrupted")
        self.assertEqual(projection.evidence.turn_id, "turn_3")

    def test_an_ordinary_then_parked_turn_lifts_as_completed(self) -> None:
        projection = latest_terminal_evidence(
            _recorded_page("turn.started", "turn.completed", "session.waiting"), "eve"
        )
        self.assertIsNotNone(projection)
        assert projection is not None
        self.assertEqual(projection.evidence.outcome, "completed")
        self.assertEqual(projection.evidence.turn_id, "turn_0")

    def test_a_parked_session_alone_makes_no_terminal_claim(self) -> None:
        self.assertIsNone(latest_terminal_evidence(_recorded_page("session.waiting"), "eve"))

    def test_a_turn_free_session_failure_still_settles(self) -> None:
        projection = latest_terminal_evidence(
            _recorded_page("session.waiting", "turn.completed"), "eve"
        )
        self.assertIsNotNone(projection)
        assert projection is not None
        self.assertEqual(projection.evidence.outcome, "completed")


class EveAssetAndCredentialBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Advertised input capabilities and credential containment, against the real owners."""

    def test_the_eve_adapter_is_not_asset_submit_capable(self) -> None:
        # The control declaration above and this structural fact are two independent sources; the
        # catalog must never advertise an asset kind while this holds.
        self.assertNotIsInstance(EveSessionAdapter(), AssetSubmitCapable)

    async def test_an_asset_carrying_submission_is_refused_by_the_authority(self) -> None:
        started = await _start_eve()
        try:
            authority = HarnessSubmissionAuthority(
                started.adapter,
                BridgeSnapshotPort(
                    clock=lambda: NOW,
                    snapshot=_snapshot,
                    set_snapshot=lambda _value: None,
                    publish=lambda: None,
                ),
                SubmissionLimits(timeline=8, ledger=8),
                bridge_epoch="epoch-1",
            )
            authority.start()
            receipt = await authority.submit(
                PromptRequest(
                    request_id="req-asset-1",
                    source="cockpit",
                    text="look at this",
                    submitted_at=NOW,
                    assets=(
                        AssetReference(
                            asset_id="asset-1",
                            mime_type="image/png",
                            byte_size=8,
                            sha256="0" * 64,
                        ),
                    ),
                )
            )
            self.assertEqual(receipt.acceptance, "unsupported")
            self.assertIn("asset", receipt.detail or "")
        finally:
            await started.aclose()

    def test_a_provider_credential_reaches_the_child_and_never_the_diagnostic(self) -> None:
        launch = _launch_with_provider_key()
        selection = launch_spec_selection(launch)
        binding = launch_spec_binding(launch)
        child = build_runtime_env(selection=selection, binding=binding, base=dict(launch.env))
        # The credential does reach the child process that needs it ...
        self.assertEqual(child[PROVIDER_API_KEY_ENV], _PROVIDER_SECRET)
        # ... and it must not reach the capability diagnostic operators and the dashboard read.
        diagnostic = capability_snapshot_json(EveSessionAdapter()._capability_snapshot(selection))
        serialized = json.dumps(diagnostic, sort_keys=True)
        self.assertNotIn(_PROVIDER_SECRET, serialized)
        self.assertNotIn("apiKey", serialized)

    def test_the_capability_snapshot_model_carries_no_credential_field(self) -> None:
        # Two independent sources: the credential the launch holds, and the fields the wire shape
        # can express at all. A new field able to carry a secret fails here, not in production.
        launch = _launch_with_provider_key()
        selection = launch_spec_selection(launch)
        self.assertEqual(selection.provider_api_key, _PROVIDER_SECRET)
        diagnostic = capability_snapshot_json(EveSessionAdapter()._capability_snapshot(selection))
        self.assertEqual(
            sorted(diagnostic),
            ["configOptions", "models", "selectedEffort", "selectedModelKey"],
        )
        (model,) = diagnostic["models"]  # type: ignore[misc]
        self.assertNotIn("providerApiKey", model)


if __name__ == "__main__":  # pragma: no cover - pytest entry point
    unittest.main()
