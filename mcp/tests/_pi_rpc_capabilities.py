"""Observe an installed Pi's RPC capability surface by driving the real process.

``fixtures/pi_rpc/<version>-capabilities.json`` is a recording, not a hand-maintained file.
Every field in it is produced here from a Pi this suite installed and drove: the version
from ``pi --version``, the framing from the bytes the process actually wrote and read, the
state fields from a live ``get_state``, the events from a real offline agent run, and the
extension UI methods from a probe extension that calls every one of them.

``test_pi_rpc_real_smoke.py`` compares this observation against the committed fixture and
fails on any disagreement, so a Pi bump that moves the surface cannot land quietly.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

CAPABILITY_SCHEMA = "ar-pi-rpc-capabilities/v1"

# No Pi build dispatches this. It is the probe's negative control: unless the runtime
# rejects it as an unknown command, "every recorded command was accepted" proves nothing,
# because a runtime that silently swallowed everything would look identical.
UNKNOWN_COMMAND_CONTROL = "ar_probe_not_a_pi_command"

LINE_SEPARATOR = "\u2028"
PARAGRAPH_SEPARATOR = "\u2029"

# Pi documents these as ordinary JSON string content that must not split a record, so the
# probe puts both inside a session name and checks the value survives one LF-framed line.
SEPARATOR_PROBE_NAME = f"ar-probe{LINE_SEPARATOR}separator{PARAGRAPH_SEPARATOR}content"

# Calls every extension UI method Pi exposes in RPC mode. The handler returns without
# awaiting, so startup completes and the process stays alive; each dialog announces its own
# resolution through notify, which is what proves it was waiting for a reply rather than
# being fire-and-forget.
PROBE_EXTENSION_SOURCE = """\
export default function (pi: any) {
  pi.on("session_start", (_event: any, ctx: any) => {
    const done = (method: string) => () => ctx.ui.notify(`resolved:${method}`, "info");

    ctx.ui.notify("ar-probe-notify", "info");
    ctx.ui.setStatus("ar-probe-status", "ar-probe-status-text");
    ctx.ui.setWidget("ar-probe-widget", ["ar-probe-widget-line"]);
    ctx.ui.setTitle("ar-probe-title");
    ctx.ui.setEditorText("ar-probe-editor-text");

    void ctx.ui.select("ar-probe-select", ["a", "b"]).then(done("select"));
    void ctx.ui.confirm("ar-probe-confirm", "ar-probe-confirm-message").then(done("confirm"));
    void ctx.ui.input("ar-probe-input", "ar-probe-placeholder").then(done("input"));
    void ctx.ui.editor("ar-probe-editor", "ar-probe-prefill").then(done("editor"));
  });
}
"""

# 127.0.0.1:9 is the discard port: reachable enough to fail immediately, so a prompt runs
# the real streaming path -- and the real auto-retry path -- without any network egress.
UNREACHABLE_ENDPOINT = "http://127.0.0.1:9/v1"

PROBE_MODELS: Mapping[str, object] = {
    "providers": {
        "smoke": {
            "baseUrl": UNREACHABLE_ENDPOINT,
            "api": "openai-completions",
            "apiKey": "smoke",
            "models": [
                {
                    "id": "idle",
                    "name": "Capability probe idle",
                    "reasoning": False,
                    "input": ["text"],
                    "contextWindow": 4096,
                    "maxTokens": 64,
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                }
            ],
        }
    }
}

OFFLINE_FLAGS = (
    "--offline",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--no-context-files",
)

# ``prompt`` starts a turn and ``abort`` ends one, so they are driven by the agent-run
# exercise rather than by the plain dispatch sweep, which must not leave Pi streaming.
# ``extension_ui_response`` is not a command at all -- it is an inbound reply frame, proven
# instead by the dialog round-trip in the UI probe.
DRIVEN_SEPARATELY = frozenset({"prompt", "abort", "extension_ui_response"})


@dataclass(frozen=True)
class ObservedCapabilities:
    """What one installed Pi reported about itself, all of it read off the wire."""

    version: str
    framing: Mapping[str, object]
    dispatched_commands: frozenset[str]
    unknown_command_rejected: bool
    state_fields: frozenset[str]
    events: frozenset[str]
    dialog_methods: frozenset[str]
    fire_and_forget_methods: frozenset[str]


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:116).
def _require_path() -> str:  # pragma: no cover
    path = os.environ.get("PATH")
    if not path:
        raise RuntimeError("PATH must be set to launch the installed Pi")
    return path


class PiRpcProbe:
    """One installed Pi process driven with strict LF-delimited JSONL.

    Deliberately not the product adapter: the adapter is the thing whose assumptions are
    under test, so a probe that reused it could only ever agree with it.
    """

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:130).
    def __init__(  # pragma: no cover
        self,
        executable: str,
        *,
        workspace: Path,
        extension: Path | None = None,
        sessions: bool = False,
    ) -> None:
        home, config, cwd = workspace / "home", workspace / "config", workspace / "project"
        for path in (home, config, cwd):
            path.mkdir(parents=True, exist_ok=True)
        (config / "models.json").write_text(json.dumps(PROBE_MODELS), encoding="utf-8")
        argv = [executable, "--mode", "rpc", "--model", "smoke/idle"]
        if not sessions:
            argv.append("--no-session")
        argv.extend(OFFLINE_FLAGS)
        argv.append("--no-extensions")
        if extension is not None:
            argv.extend(["-e", str(extension)])
        self.argv = tuple(argv)
        self._process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": _require_path(),
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(config),
                "NO_COLOR": "1",
            },
        )
        self._raw = bytearray()
        self._frames: list[Mapping[str, object]] = []
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:169).
    def __enter__(self) -> PiRpcProbe:  # pragma: no cover
        return self

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:172).
    def __exit__(self, *_exc: object) -> None:  # pragma: no cover
        self.close()

    # 260731-EFA-L7 R10: AR_RUN_PI_RPC_SMOKE-gated probe; needs the installed Pi RPC build.
    def _read_stdout(self) -> None:  # pragma: no cover
        stream = self._process.stdout
        assert stream is not None
        descriptor = stream.fileno()
        pending = bytearray()
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                return
            with self._lock:
                self._raw.extend(chunk)
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, rest = bytes(pending).partition(b"\n")
                pending = bytearray(rest)
                if line.strip():
                    with self._lock:
                        self._frames.append(json.loads(line))

    @property
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:196).
    def raw(self) -> bytes:  # pragma: no cover
        with self._lock:
            return bytes(self._raw)

    @property
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:201).
    def frames(self) -> tuple[Mapping[str, object], ...]:  # pragma: no cover
        with self._lock:
            return tuple(self._frames)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:205).
    def send(self, command: Mapping[str, object]) -> None:  # pragma: no cover
        self.send_bytes(json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:208).
    def send_bytes(self, payload: bytes) -> None:  # pragma: no cover
        stdin = self._process.stdin
        assert stdin is not None
        stdin.write(payload)
        stdin.flush()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:214).
    def settle(self, seconds: float) -> None:  # pragma: no cover
        """Give the process a bounded window to emit whatever the last input provoked."""
        time.sleep(seconds)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:218).
    def wait_for_event(self, event_type: str, *, timeout: float = 30.0) -> bool:  # pragma: no cover
        return self._wait(lambda frame: frame.get("type") == event_type, timeout)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:221).
    def wait_for_response(
        self, request_id: str, *, timeout: float = 30.0
    ) -> bool:  # pragma: no cover
        return self._wait(
            lambda frame: frame.get("type") == "response" and frame.get("id") == request_id,
            timeout,
        )

    # 260731-EFA-L7 R10: AR_RUN_PI_RPC_SMOKE-gated probe; needs the installed Pi RPC build.
    def _wait(self, matches: object, timeout: float) -> bool:  # pragma: no cover
        assert callable(matches)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(matches(frame) for frame in self.frames):
                return True
            time.sleep(0.05)
        return False

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:237).
    def response_for(self, request_id: str) -> Mapping[str, object] | None:  # pragma: no cover
        for frame in self.frames:
            if frame.get("type") == "response" and frame.get("id") == request_id:
                return frame
        return None

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:243).
    def event_types(self) -> frozenset[str]:  # pragma: no cover
        return frozenset(
            str(frame["type"])
            for frame in self.frames
            if frame.get("type") not in (None, "response")
        )

    # 260731-EFA-L7 R10: AR_RUN_PI_RPC_SMOKE-gated probe; needs the installed Pi RPC build.
    def close(self) -> None:  # pragma: no cover
        stdin = self._process.stdin
        if stdin is not None and not stdin.closed:
            # The child exits on its own once its work is done, so the close can race it.
            with contextlib.suppress(BrokenPipeError):
                stdin.close()
        try:
            self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=15)
        self._reader.join(timeout=5)
        for stream in (self._process.stdout, self._process.stderr):
            if stream is not None:
                stream.close()


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:268).
def observe_version(executable: str) -> str:  # pragma: no cover
    """The version string the installed binary reports for itself."""
    return subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": _require_path(), "HOME": os.environ.get("HOME", "/nonexistent")},
        timeout=60,
    ).stdout.strip()


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:280).
def _dispatch_probe(probe: PiRpcProbe, name: str) -> None:  # pragma: no cover
    """Send one command with the minimum arguments its handler needs."""
    arguments: Mapping[str, object] = {}
    if name == "set_model":
        arguments = {"provider": "smoke", "modelId": "idle"}
    elif name == "set_thinking_level":
        arguments = {"level": "off"}
    elif name == "prompt":
        arguments = {"message": "ar capability probe"}
    probe.send({"id": f"probe-{name}", "type": name, **arguments})


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:292).
def _accepted(probe: PiRpcProbe, name: str) -> bool:  # pragma: no cover
    """True when the runtime dispatched the command rather than rejecting it as unknown.

    A handler that answers ``success: false`` for its own reasons still counts: the point is
    that the command reached a handler at all.
    """
    frame = probe.response_for(f"probe-{name}")
    if frame is None:
        return False
    error = frame.get("error")
    return not (isinstance(error, str) and error.startswith("Unknown command"))


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:305).
def _sweep_commands(probe: PiRpcProbe, names: Sequence[str]) -> set[str]:  # pragma: no cover
    accepted: set[str] = set()
    for name in names:
        _dispatch_probe(probe, name)
        probe.wait_for_response(f"probe-{name}", timeout=20)
        if _accepted(probe, name):
            accepted.add(name)
    return accepted


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:315).
def _exercise_agent_run(probe: PiRpcProbe) -> None:  # pragma: no cover
    """Drive one real offline turn so the agent lifecycle and retry events actually fire.

    The configured endpoint is the discard port, so the turn fails immediately and Pi enters
    its auto-retry path -- which is the only way ``auto_retry_start`` and ``auto_retry_end``
    become observable without a provider account.
    """
    probe.send({"id": "probe-set_auto_retry", "type": "set_auto_retry", "enabled": True})
    probe.wait_for_response("probe-set_auto_retry", timeout=20)
    _dispatch_probe(probe, "prompt")
    probe.wait_for_event("auto_retry_start")
    probe.send({"id": "probe-abort_retry", "type": "abort_retry"})
    probe.wait_for_event("auto_retry_end")
    _dispatch_probe(probe, "abort")
    probe.wait_for_event("agent_settled")


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:332).
def _exercise_queue_and_compaction(probe: PiRpcProbe) -> None:  # pragma: no cover
    """Provoke the queue and compaction events without needing a provider."""
    probe.send({"id": "probe-steer", "type": "steer", "message": "ar probe steer"})
    probe.wait_for_response("probe-steer", timeout=20)
    probe.send({"id": "probe-follow_up", "type": "follow_up", "message": "ar probe follow up"})
    probe.wait_for_response("probe-follow_up", timeout=20)
    probe.send({"id": "probe-compact", "type": "compact"})
    probe.wait_for_event("compaction_end")
    _dispatch_probe(probe, "abort")
    probe.settle(1.0)


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:344).
def _observe_framing(probe: PiRpcProbe) -> dict[str, object]:  # pragma: no cover
    """Read the framing contract off the bytes, not off the documentation."""
    probe.send({"id": "probe-name", "type": "set_session_name", "name": SEPARATOR_PROBE_NAME})
    probe.wait_for_event("session_info_changed")

    # A CRLF-terminated record: the reader must strip the trailing CR and still parse it.
    probe.send_bytes(b'{"id":"probe-crlf","type":"get_state"}\r\n')
    probe.wait_for_response("probe-crlf", timeout=20)
    crlf = probe.response_for("probe-crlf")

    raw = probe.raw
    return {
        "delimiter": "LF" if b"\r" not in raw else "CRLF",
        "acceptsTrailingCR": crlf is not None and crlf.get("success") is True,
        "unicodeLineSeparatorsAreContent": _separators_stay_on_one_line(raw),
    }


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:362).
def _separators_stay_on_one_line(raw: bytes) -> bool:  # pragma: no cover
    """The separators must sit inside a single LF-delimited record that still parses."""
    marker = LINE_SEPARATOR.encode("utf-8")
    for line in raw.split(b"\n"):
        if marker not in line:
            continue
        if json.loads(line).get("name") == SEPARATOR_PROBE_NAME:
            return True
    return False


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:373).
def _ui_requests(probe: PiRpcProbe) -> tuple[Mapping[str, object], ...]:  # pragma: no cover
    return tuple(frame for frame in probe.frames if frame.get("type") == "extension_ui_request")


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:377).
def _answer_every_request(
    probe: PiRpcProbe, frames: Sequence[Mapping[str, object]]
) -> None:  # pragma: no cover
    """Reply to every UI request seen so far.

    The probe does not assume which methods are dialogs -- that is the thing being measured
    -- so it answers all of them and lets the extension report which promises resolved.
    """
    for frame in frames:
        reply: dict[str, object] = {"type": "extension_ui_response", "id": frame.get("id")}
        if str(frame.get("method")) == "confirm":
            reply["confirmed"] = True
        else:
            reply["value"] = "a"
        probe.send(reply)
        probe.settle(0.6)
    probe.settle(2.0)


# 260731-EFA-L7 R10: AR_RUN_PI_RPC_SMOKE-gated helper; needs the installed Pi RPC build.
def _resolved_methods(probe: PiRpcProbe) -> set[str]:  # pragma: no cover
    """The methods whose promise the extension saw resolve after we replied."""
    resolved: set[str] = set()
    for frame in probe.frames:
        if frame.get("type") != "extension_ui_request" or frame.get("method") != "notify":
            continue
        message = str(frame.get("message", ""))
        if message.startswith("resolved:"):
            resolved.add(message.removeprefix("resolved:"))
    return resolved


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/_pi_rpc_capabilities.py:407).
def _observe_ui_methods(  # pragma: no cover
    executable: str, *, workspace: Path
) -> tuple[frozenset[str], frozenset[str]]:
    """Run the probe extension and split its UI calls by whether they awaited a reply."""
    extension = workspace / "ar-ui-probe.ts"
    extension.write_text(PROBE_EXTENSION_SOURCE, encoding="utf-8")
    with PiRpcProbe(executable, workspace=workspace / "ui", extension=extension) as probe:
        probe.settle(8.0)
        requests = _ui_requests(probe)
        _answer_every_request(probe, [f for f in requests if f.get("method") != "notify"])
        resolved = _resolved_methods(probe)
        emitted = {str(frame["method"]) for frame in _ui_requests(probe)}
    dialog = emitted & resolved
    return frozenset(dialog), frozenset(emitted - dialog)


# 260731-EFA-L7 R10: AR_RUN_PI_RPC_SMOKE-gated helper; needs the installed Pi RPC build.
def _observe_state_fields(probe: PiRpcProbe) -> frozenset[str]:  # pragma: no cover
    probe.send({"id": "probe-state", "type": "get_state"})
    if not probe.wait_for_response("probe-state", timeout=20):
        raise RuntimeError("installed Pi did not answer get_state")
    frame = probe.response_for("probe-state")
    assert frame is not None
    data = frame.get("data")
    if not isinstance(data, Mapping):
        raise RuntimeError("installed Pi returned a get_state response with no data mapping")
    return frozenset(str(key) for key in data)


# 260731-EFA-L7 R10: AR_RUN_PI_RPC_SMOKE-gated helper; needs the installed Pi RPC build.
def observe_capabilities(  # pragma: no cover
    executable: str, *, workspace: Path, commands: Sequence[str]
) -> ObservedCapabilities:
    """Drive the installed Pi and report the capability surface it demonstrated.

    ``commands`` names the RPC commands the recording claims; each is probed for real, and
    ``UNKNOWN_COMMAND_CONTROL`` is probed alongside them so a runtime that accepted anything
    could not pass.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    dialog, fire_and_forget = _observe_ui_methods(executable, workspace=workspace)

    with PiRpcProbe(executable, workspace=workspace / "session", sessions=True) as probe:
        probe.settle(4.0)
        state_fields = _observe_state_fields(probe)
        accepted = _sweep_commands(probe, [n for n in commands if n not in DRIVEN_SEPARATELY])
        _exercise_agent_run(probe)
        _exercise_queue_and_compaction(probe)
        turn_commands = (name for name in ("prompt", "abort") if name in commands)
        accepted.update(name for name in turn_commands if _accepted(probe, name))
        framing = _observe_framing(probe)
        probe.send({"id": f"probe-{UNKNOWN_COMMAND_CONTROL}", "type": UNKNOWN_COMMAND_CONTROL})
        probe.wait_for_response(f"probe-{UNKNOWN_COMMAND_CONTROL}", timeout=20)
        rejected = not _accepted(probe, UNKNOWN_COMMAND_CONTROL)
        events = set(probe.event_types())

    if dialog:
        # Every dialog promise resolved only because an inbound extension_ui_response was
        # delivered, which is the proof that Pi accepts that frame and emits its request.
        if "extension_ui_response" in commands:
            accepted.add("extension_ui_response")
        events.add("extension_ui_request")

    return ObservedCapabilities(
        version=observe_version(executable),
        framing=framing,
        dispatched_commands=frozenset(accepted),
        unknown_command_rejected=rejected,
        state_fields=state_fields,
        events=frozenset(events),
        dialog_methods=dialog,
        fire_and_forget_methods=fire_and_forget,
    )
