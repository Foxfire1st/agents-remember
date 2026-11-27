"""Drive one native stdio server through the fixed memory regression workload."""

from __future__ import annotations

import json
import os
from asyncio import wait_for
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import monotonic_ns
from typing import TYPE_CHECKING, Literal, TextIO, TypedDict

from agents_remember.kernel.git_command import run_git
from agents_remember.serving.build_info import runtime_source_digest
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent
from mcp_memory_bounded_corpus import INVARIANT_IDS, SOURCE_CONTEXT_SEEDS
from mcp_memory_bounded_receipts import requires_read_continuation

from mcp import ClientSession, StdioServerParameters

if TYPE_CHECKING:
    from mcp_memory_bounded_fixture import Fixture

type Payload = dict[str, object]

_INITIALIZE_TIMEOUT = 60
# The full quality call took up to 86 seconds on the measured base. Leave ample
# room for slower runners while still diagnosing a call that never completes.
_CALL_TIMEOUT = 300
_CYCLES = (1, 2, 3)
_CHILD = """
from agents_remember.application.runtime import startup
from agents_remember.mcp.server import main

# The discovery sweep belongs to the installation, outside this test's world.
startup.reconcile_outside_scratch_roots = lambda: {"removed": [], "reported": []}
raise SystemExit(main())
"""


@dataclass(frozen=True)
class Case:
    """Position and sequence in the source workload, before selection."""

    position: int
    sequence: int
    tool: str
    variant: str
    lane: int | None


CASES = (
    Case(1, 5, "task_doc", "replace", None),
    Case(2, 8, "task_doc", "set_section", None),
    Case(3, 17, "task_doc", "set_section", None),
    Case(4, 25, "task_doc", "set_section", None),
    Case(5, 32, "task_doc", "set_section", None),
    Case(6, 42, "knowledge_read", "source_context/first_page", 1),
    Case(7, 50, "knowledge_read", "source_context/first_page", 1),
    Case(8, 58, "knowledge_read", "source_context/first_page", 1),
    Case(9, 66, "knowledge_read", "source_context/first_page", 1),
    Case(10, 70, "knowledge_read", "source_context/continuation", 1),
    Case(11, 78, "knowledge_read", "source_context/continuation", 1),
    Case(12, 86, "knowledge_read", "source_context/continuation", 1),
    Case(13, 94, "knowledge_read", "source_context/continuation", 1),
    Case(14, 102, "knowledge_read", "source_context/continuation", 1),
    Case(15, 111, "knowledge_read", "source_context/continuation", 1),
    Case(16, 120, "knowledge_read", "source_context/first_page", 1),
    Case(17, 128, "knowledge_read", "source_context/first_page", 1),
    Case(18, 135, "knowledge_read", "source_context/continuation", 1),
    Case(19, 147, "knowledge_read", "dependent/continuation", 1),
    Case(20, 152, "knowledge_read", "dependent/first_page", 1),
    Case(21, 158, "knowledge_read", "invariant/first_page", 1),
    Case(22, 166, "knowledge_read", "invariant/first_page", 1),
    Case(23, 174, "knowledge_read", "invariant/continuation", 1),
    Case(24, 180, "memory_quality_check", "redacted", None),
    Case(25, 187, "knowledge_read", "invariant/first_page", 1),
    Case(26, 205, "knowledge_read", "invariant/continuation", 1),
    Case(27, 209, "knowledge_write", "preview", None),
    Case(68, 515, "knowledge_write", "write", None),
)


@dataclass(frozen=True)
class _ServerProcess:
    pid: int
    start_ticks: str
    cgroup: Path | None


class _CgroupReading(TypedDict):
    current_bytes: int
    peak_bytes: int
    events: dict[str, int]


class _CallMeta(TypedDict, total=False):
    phase: Literal["binding"]
    cycle: int
    position: int
    log_sequence: int
    variant: str
    requested_continuation: bool | None
    actual_view: object
    actual_action: None


class _CallReading(TypedDict):
    elapsed_ns: int
    memory_kib: dict[str, int]
    cgroup: _CgroupReading | None


def _object(value: object, owner: str) -> Payload:
    if not isinstance(value, dict):
        raise ValueError(f"{owner} did not provide a JSON object")
    return value


def _read_object(path: Path) -> Payload:
    return _object(json.loads(path.read_bytes()), str(path))


def _start_ticks(pid: int) -> str:
    # The command field may contain spaces or parentheses.
    return (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[19]


def _cgroup_path(pid: int) -> Path | None:
    entries = (Path("/proc") / str(pid) / "cgroup").read_text().splitlines()
    for entry in entries:
        if entry.startswith("0::/"):
            return Path("/sys/fs/cgroup") / entry.removeprefix("0::/")
    return None


def _server_process(fixture: Fixture) -> _ServerProcess:
    registrations = sorted((fixture.coordination / "runtime" / "servers").glob("*.json"))
    assert len(registrations) == 1, "Expected the private world's one live server record"
    record = _read_object(registrations[0])
    pid, start_ticks = record.get("pid"), record.get("processStart")
    assert type(pid) is int and pid > 0 and pid != os.getpid(), "Invalid child PID"
    assert registrations[0].stem == str(pid), "Registry name and PID differ"
    assert record["role"] == "mcp" and record["configPath"] == str(fixture.settings)
    assert isinstance(start_ticks, str) and start_ticks == _start_ticks(pid)
    scope = _cgroup_path(pid)
    assert scope == _cgroup_path(os.getpid()), "The native server left the runner's cgroup"
    return _ServerProcess(pid, start_ticks, scope)


def _memory_kib(process: _ServerProcess) -> dict[str, int]:
    assert _start_ticks(process.pid) == process.start_ticks, "The native server PID was reused"
    fields = {}
    for line in (Path("/proc") / str(process.pid) / "status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"VmRSS", "VmSwap", "VmHWM", "Threads"}:
            fields[key] = int(value.split()[0])
    fields["VmRSS+VmSwap"] = fields["VmRSS"] + fields["VmSwap"]
    assert "VmHWM" in fields, "The native server did not expose its Linux high-water reading"
    return fields


def _cgroup_reading(path: Path | None) -> _CgroupReading | None:
    if path is None or not (path / "memory.peak").is_file():
        return None
    return {
        "current_bytes": int((path / "memory.current").read_text()),
        "peak_bytes": int((path / "memory.peak").read_text()),
        "events": {
            key: int(value)
            for key, value in (
                line.split() for line in (path / "memory.events").read_text().splitlines()
            )
        },
    }


def _child_parameters(fixture: Fixture, source: Path) -> StdioServerParameters:
    for folder in ("config", "cache", "data", "state", "tmp"):
        (fixture.root / "child" / folder).mkdir(parents=True)
    return StdioServerParameters(
        command=str(fixture.interpreter),
        args=["-P", "-B", "-c", _CHILD, "--config", str(fixture.settings)],
        env={
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "PYTHONPATH": str(source / "mcp" / "src"),
            "XDG_CONFIG_HOME": str(fixture.root / "child" / "config"),
            "XDG_CACHE_HOME": str(fixture.root / "child" / "cache"),
            "XDG_DATA_HOME": str(fixture.root / "child" / "data"),
            "XDG_STATE_HOME": str(fixture.root / "child" / "state"),
            "TMPDIR": str(fixture.root / "child" / "tmp"),
        },
        cwd=fixture.root,
    )


def _bind_server(info: Payload, process: _ServerProcess, fixture: Fixture, source: Path) -> None:
    build = _object(info.get("servingBuild"), "server_info serving build")
    package = source / "mcp" / "src" / "agents_remember"
    digest = runtime_source_digest(package)
    assert digest and build.get("sourceDigest") == digest, "Unexpected server source digest"
    assert build.get("packageRoot") == str(package), "Unexpected server package root"
    head = run_git(source, ["rev-parse", "--short", "HEAD"])
    assert head.returncode == 0 and build.get("commit") == head.stdout.strip()
    assert build.get("pythonExecutable") == str(fixture.interpreter.resolve(strict=True))
    assert info.get("transport") == "stdio" and info.get("ok") is True
    assert info.get("configPath") == str(fixture.settings)
    assert info.get("coordinationRoot") == str(fixture.coordination)
    assert info.get("workspaceRoot") == _read_object(fixture.settings)["workspaceRoot"]
    servers = info.get("runningServers")
    assert isinstance(servers, list) and len(servers) == 1
    registered = _object(servers[0], "server_info runtime registration")
    assert registered.get("pid") == process.pid
    assert registered.get("loadedDigest") == digest


def _wire_payload(result: CallToolResult) -> Payload:
    if result.structured_content is not None:
        return _object(result.structured_content, "MCP structured result")
    (content,) = result.content
    assert isinstance(content, TextContent), "Expected a JSON MCP text result"
    return _object(json.loads(content.text), "MCP text result")


def _full_payload(answer: Payload, log_root: Path) -> Payload:
    if answer.get("overflow") is not True:
        return answer
    log = answer.get("log")
    assert isinstance(log, str), "The overflow result did not retain its whole tool log"
    path = Path(log).resolve(strict=True)
    assert path.is_relative_to(log_root.resolve()), "Whole tool log escaped the private log root"
    return _read_object(path)


def _response_fields(answer: Payload) -> Payload:
    written = answer.get("written")
    return {
        "ok": answer.get("ok"),
        "action": answer.get("action"),
        "state": answer.get("state"),
        "overflow": answer.get("overflow", False),
        "detail_unavailable": answer.get("overflow") is True and "log" not in answer,
        "log_error": answer.get("logError"),
        "refusal": answer.get("refusalCode") or answer.get("code"),
        "status": answer.get("status"),
        "checklist_status": answer.get("checklistStatus"),
        "report_path": answer.get("reportPath"),
        "attestation_path": answer.get("attestationPath"),
        "candidates_path": answer.get("candidatesPath"),
        "written_count": len(written) if isinstance(written, list) else None,
    }


@dataclass
class _CallCapture:
    session: ClientSession
    process: _ServerProcess
    output: TextIO
    log_root: Path

    def emit(self, receipt: Mapping[str, object]) -> None:
        self.output.write(json.dumps(dict(receipt), sort_keys=True) + "\n")

    async def call(self, tool: str, arguments: Payload, meta: _CallMeta) -> Payload:
        identity = {"tool": tool, "pid": self.process.pid, **meta}
        started = monotonic_ns()
        self.emit({"event": "call-start", **identity})
        result = await wait_for(self.session.call_tool(tool, arguments), timeout=_CALL_TIMEOUT)
        reading: _CallReading = {
            "elapsed_ns": monotonic_ns() - started,
            "memory_kib": _memory_kib(self.process),
            "cgroup": _cgroup_reading(self.process.cgroup),
        }
        receipt = {"event": "call-end", **identity, **reading, "tool_error": result.is_error}
        if result.is_error:
            detail = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            self.emit({**receipt, "ok": False, "tool_error_result": detail})
            raise AssertionError(f"The native {tool} call returned an MCP tool error")
        try:
            answer = _wire_payload(result)
            assert type(answer.get("ok")) is bool, f"{tool} did not report a Boolean ok"
            full = _full_payload(answer, self.log_root)
        except (AssertionError, OSError, ValueError) as error:
            self.emit({**receipt, "invalid_payload": True, "payload_error": str(error)})
            raise
        read_on = full.get("readOn")
        returned_cursor = bool(full.get("continuation")) or (
            isinstance(read_on, dict) and bool(read_on.get("continuation"))
        )
        self.emit({**receipt, **_response_fields(answer), "returned_continuation": returned_cursor})
        return full


class _ReadPlan:
    """Repeat the read selectors and keep each cursor with its producing selection."""

    def __init__(self, fixture: Fixture) -> None:
        self.roots = {
            "memoryRoot": str(fixture.memory_root),
            "repositoryRoot": str(fixture.code_root),
            "codeTreeId": fixture.code_tree,
        }
        self.selectors: dict[str, tuple[Payload, ...]] = {
            "source_context": tuple({"sourcePath": seed} for seed in SOURCE_CONTEXT_SEEDS),
            "invariant": tuple({"recordId": seed} for seed in INVARIANT_IDS),
            "dependent": ({"limit": 20}, {"limit": 20, "sourcePath": "docs"}),
        }
        self.fresh: dict[tuple[int | None, str], int] = {}
        self.cursors: dict[tuple[int | None, str], Payload] = {}

    def arguments(self, case: Case) -> Payload:
        view = case.variant.split("/", 1)[0]
        key = (case.lane, view)
        saved = self.cursors.pop(key, None)
        if requires_read_continuation(case):
            assert saved is not None, f"No {view} cursor for source sequence {case.sequence}"
            return {**self.roots, **saved}
        offset = self.fresh.get(key, 0)
        selectors = self.selectors[view]
        self.fresh[key] = offset + 1
        return {**self.roots, "view": view, **selectors[offset % len(selectors)]}

    def observe(self, case: Case, arguments: Payload, answer: Payload) -> None:
        original_view = case.variant.split("/", 1)[0]
        key = (case.lane, original_view)
        if (
            original_view == "dependent"
            or answer.get("ok") is not True
            or answer.get("state") == "refused"
        ):
            return
        token = answer.get("continuation")
        if isinstance(token, str) and token:
            payload = answer.get("payload")
            cursor_view = payload.get("continuationView") if isinstance(payload, dict) else None
            self.cursors[key] = {"view": cursor_view or arguments["view"], "continuation": token}


def _task_document_args(fixture: Fixture, variant: str, ordinal: int) -> Payload:
    ref = PurePosixPath(fixture.task_ref["path"])
    repository = fixture.task_ref["repository"]
    target = {
        "repo_id": repository,
        "task_name": ref.parent.as_posix(),
        "slug": ref.stem,
        "contract_path": str(fixture.contract),
        "operation": variant,
    }
    if variant == "replace":
        leaf = fixture.coordination / "tasks" / repository / ref
        return {**target, "fields": _read_object(leaf), "dry_run": True}
    section = {"heading": "Synthetic measurement", "body": f"Record replay event {ordinal}."}
    return {**target, "section": section}


def _knowledge_write_args(fixture: Fixture, case: Case, cycle: int) -> Payload:
    commit = case.variant == "write"
    suffix = f"-{cycle}" if commit else "-preview"
    ref = fixture.task_ref
    leaf = fixture.coordination / "tasks" / ref["repository"] / ref["path"]
    leaf_id = _read_object(leaf)["id"]
    record = {
        "key": f"memory-sample{suffix}",
        "kind": "weakness",
        "slug": f"memory-sample{suffix}",
        "fields": {
            "kind": "time",
            "statement": "The generated fixture supplies a stable anchor for memory observation.",
            "anchor": {
                "path": "pkg/observe.py",
                "locator": {"kind": "symbol", "value": "observe"},
            },
            "seen": {"task": leaf_id, "role": "worker"},
            "state": "open",
        },
    }
    return {
        "contractPath": str(fixture.contract),
        "handoffName": f"memory-sample{suffix}.json",
        "authorizationRef": "Generated stdio regression",
        "commit": commit,
        "handoff": {"records": [record], "history": []},
    }


def _case_arguments(fixture: Fixture, readers: _ReadPlan, case: Case, cycle: int) -> Payload:
    if case.tool == "knowledge_read":
        return readers.arguments(case)
    if case.tool == "task_doc":
        ordinal = (cycle - 1) * len(CASES) + case.position
        return _task_document_args(fixture, case.variant, ordinal)
    if case.tool == "knowledge_write":
        return _knowledge_write_args(fixture, case, cycle)
    assert case.tool == "memory_quality_check"
    return {
        "request": {
            "mode": "sync",
            "repo_id": fixture.task_ref["repository"],
            "contract_path": str(fixture.contract),
        },
    }


async def _run_cycles(fixture: Fixture, capture: _CallCapture) -> None:
    for cycle in _CYCLES:
        readers = _ReadPlan(fixture)
        for position, case in enumerate(CASES, start=1):
            args = _case_arguments(fixture, readers, case, cycle)
            answer = await capture.call(
                case.tool,
                args,
                {
                    "cycle": cycle,
                    "position": position,
                    "log_sequence": case.sequence,
                    "variant": case.variant,
                    "requested_continuation": (
                        "continuation" in args if case.tool == "knowledge_read" else None
                    ),
                    "actual_view": args.get("view"),
                    "actual_action": None,
                },
            )
            if case.tool == "knowledge_read":
                readers.observe(case, args, answer)
            # A write ends the cycle. The next cycle opens a new read plan for its memory tree.


async def run_selection(fixture: Fixture, source: Path, capture: Path) -> None:
    """Capture the binding and three identical selections on one native session."""

    capture.mkdir()
    source = source.resolve(strict=True)
    parameters = _child_parameters(fixture, source)
    with (
        (capture / "server.stderr").open("x", encoding="utf-8") as stderr,
        (capture / "calls.jsonl").open("x", encoding="utf-8", buffering=1) as output,
    ):
        async with (
            stdio_client(parameters, errlog=stderr) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await wait_for(session.initialize(), timeout=_INITIALIZE_TIMEOUT)
            process = _server_process(fixture)
            (capture / "server.pid").write_text(f"{process.pid}\n", encoding="utf-8")
            recorder = _CallCapture(session, process, output, fixture.logs)
            info = await recorder.call("server_info", {}, {"phase": "binding"})
            (capture / "server-info.json").write_text(
                json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            _bind_server(info, process, fixture, source)
            await _run_cycles(fixture, recorder)
