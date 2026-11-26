"""Live MCP server binding records, process ancestry, and harness root discovery (L51-R1)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from agents_remember.application.agent_binding import AGENT_ID_VARIABLE, AgentBinding
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp import server as mcp_server
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving import server_bindings as bindings
from agents_remember.serving import server_registry as registry
from agents_remember.serving.build_info import ServingBuild

_AGENT_ID = "3b7c1d2e-5f4a-4c6b-9d8e-0a1b2c3d4e5f"
_REQUEST_ID = "9f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a"
_REPO = "agents-remember"


def _stat_line(pid: int, *, ppid: int, start: str, comm: str = "proc") -> str:
    # Fields after '(comm)': [0]=state, [1]=ppid, [2..18]=17 placeholders, [19]=starttime.
    middle = " ".join(["0"] * 17)
    return f"{pid} ({comm}) S {ppid} {middle} {start}\n"


@dataclass(frozen=True)
class _FakeProc:
    ppid: int
    start: str
    comm: str = "proc"
    environ: dict[str, str] | None = None
    cmdline: list[str] | None = None


def _write_proc_entry(proc_root: Path, pid: int, spec: _FakeProc) -> None:
    proc_dir = proc_root / str(pid)
    proc_dir.mkdir(parents=True, exist_ok=True)
    (proc_dir / "stat").write_text(
        _stat_line(pid, ppid=spec.ppid, start=spec.start, comm=spec.comm)
    )
    if spec.environ is not None:
        raw_env = b"\x00".join(f"{key}={value}".encode() for key, value in spec.environ.items())
        if raw_env:
            raw_env += b"\x00"
        (proc_dir / "environ").write_bytes(raw_env)
    if spec.cmdline is not None:
        raw_cmd = b"\x00".join(part.encode() for part in spec.cmdline)
        if raw_cmd:
            raw_cmd += b"\x00"
        (proc_dir / "cmdline").write_bytes(raw_cmd)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> McpRuntimeConfig:
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("value = 'A'\n")
    build = ServingBuild(
        version="test",
        commit=None,
        booted_at="2026-11-25T00:00:00Z",
        source_digest="sha256:test",
        package_root=str(package),
    )
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path / "coordination",
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs",
    )
    monkeypatch.setattr(registry, "process_serving_build", lambda: build)
    monkeypatch.setattr(registry.atexit, "register", lambda _cleanup: None)
    monkeypatch.setattr(bindings.atexit, "register", lambda _cleanup: None)
    return config


def _worker_binding(tmp_path: Path) -> AgentBinding:
    return AgentBinding(
        agent_id=_AGENT_ID,
        role="worker",
        request_id=_REQUEST_ID,
        report_path=str(tmp_path / "report.md"),
        sprint_ref=TaskDocumentRef(repository=_REPO, path="sprint/task.json"),
        master_ref=TaskDocumentRef(repository=_REPO, path="master/task.json"),
        task_ref=TaskDocumentRef(repository=_REPO, path="master/leaf.json"),
    )


def test_register_and_cleanup_bound_unbound_and_unreadable_records(
    world: McpRuntimeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    retained: list[object] = []
    monkeypatch.setattr(bindings.atexit, "register", retained.append)
    monkeypatch.setattr(
        bindings.atexit,
        "unregister",
        lambda callback: retained.remove(callback) if callback in retained else None,
    )
    payload = bindings.BoundIdentityPayload(
        agentId=_AGENT_ID,
        requestId=_REQUEST_ID,
        role="worker",
        sprintDocumentRef={"repository": _REPO, "path": "sprint/task.json"},
        masterDocumentRef={"repository": _REPO, "path": "master/task.json"},
        taskDocumentRef={"repository": _REPO, "path": "master/leaf.json"},
    )
    cases: tuple[
        tuple[
            Literal["bound", "unbound", "unreadable"],
            bindings.BoundIdentityPayload | None,
            str | None,
        ],
        ...,
    ] = (
        ("bound", payload, None),
        ("unbound", None, None),
        ("unreadable", None, "The agent binding of this tool server has no AR_ROLE."),
    )
    record_path = bindings.bindings_directory(world) / f"{os.getpid()}.json"
    for state, binding_payload, reason in cases:
        cleanup = bindings.register_server_binding(
            world,
            binding_state=state,
            binding=binding_payload,
            unreadable_reason=reason,
        )
        assert record_path.is_file()
        assert retained == [cleanup]
        live = bindings.live_server_bindings(world)
        assert len(live) == 1
        record = live[0]
        assert record.pid == os.getpid()
        assert record.parentPid == os.getppid()
        assert record.configPath == str(world.config_path)
        assert record.bindingState == state
        assert record.binding == binding_payload
        assert record.unreadableReason == reason
        if os.getppid() > 1:
            assert record.ancestors and record.ancestors[0].pid == os.getppid()
        cleanup()
        cleanup()
        assert not record_path.exists()
        assert retained == []
        assert bindings.live_server_bindings(world) == []


@pytest.mark.parametrize("mode", ["bound", "unbound", "unreadable"])
def test_run_server_registers_and_cleans_up_both_records(
    world: McpRuntimeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    for key in list(os.environ):
        if key.startswith("AR_"):
            monkeypatch.delenv(key, raising=False)
    if mode == "bound":
        for key, value in _worker_binding(tmp_path).environment().items():
            monkeypatch.setenv(key, value)
    elif mode == "unreadable":
        monkeypatch.setenv(AGENT_ID_VARIABLE, "not-a-uuid")

    observed: list[bindings.ServerBindingRecord] = []
    server_path = world.coordination_root / "runtime" / "servers" / f"{os.getpid()}.json"
    binding_path = bindings.bindings_directory(world) / f"{os.getpid()}.json"

    class _FakeServer:
        def run(self) -> None:
            assert server_path.is_file()
            assert binding_path.is_file()
            observed.extend(bindings.live_server_bindings(world))

    monkeypatch.setattr(mcp_server, "create_server", lambda _config: _FakeServer())
    mcp_server.run_server(world)

    assert len(observed) == 1
    record = observed[0]
    assert record.bindingState == mode
    if mode == "bound":
        assert record.binding is not None
        assert record.binding.agentId == _AGENT_ID
        assert record.binding.requestId == _REQUEST_ID
        assert record.binding.role == "worker"
        assert record.binding.taskDocumentRef == {
            "repository": _REPO,
            "path": "master/leaf.json",
        }
        assert record.unreadableReason is None
    elif mode == "unbound":
        assert record.binding is None
        assert record.unreadableReason is None
    else:
        assert record.binding is None
        assert record.unreadableReason and AGENT_ID_VARIABLE in record.unreadableReason
    assert not server_path.exists()
    assert not binding_path.exists()


def test_run_server_cleans_up_both_records_when_server_raises(
    world: McpRuntimeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_path = world.coordination_root / "runtime" / "servers" / f"{os.getpid()}.json"
    binding_path = bindings.bindings_directory(world) / f"{os.getpid()}.json"

    class _FailingServer:
        def run(self) -> None:
            assert server_path.is_file() and binding_path.is_file()
            raise RuntimeError("transport failed")

    monkeypatch.setattr(mcp_server, "create_server", lambda _config: _FailingServer())
    with pytest.raises(RuntimeError, match="transport failed"):
        mcp_server.run_server(world)
    assert not server_path.exists()
    assert not binding_path.exists()


def test_older_server_inventory_sees_only_pid_record_unchanged(
    world: McpRuntimeConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L51-R1 compatibility: runtime/servers/<pid>.json stays untouched by binding records."""
    for key, value in _worker_binding(tmp_path).environment().items():
        monkeypatch.setenv(key, value)

    servers_dir = world.coordination_root / "runtime" / "servers"
    expected_server_file = servers_dir / f"{os.getpid()}.json"
    checked = False

    class _CheckingServer:
        def run(self) -> None:
            nonlocal checked
            assert sorted(servers_dir.glob("*.json")) == [expected_server_file]
            inventory = registry.server_inventory(world)
            assert len(inventory) == 1
            assert inventory[0].pid == os.getpid()
            assert inventory[0].role == "mcp"
            live_bindings = bindings.live_server_bindings(world)
            assert len(live_bindings) == 1
            assert live_bindings[0].bindingState == "bound"
            checked = True

    monkeypatch.setattr(mcp_server, "create_server", lambda _config: _CheckingServer())
    mcp_server.run_server(world)
    assert checked is True


def test_dead_and_recycled_pid_reclamation_at_two_population_sizes(
    world: McpRuntimeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_root = world.workspace_root / "fake-proc"
    proc_root.mkdir()
    monkeypatch.setattr(bindings, "PROC_ROOT", proc_root)
    live_pids: dict[int, str] = {}

    def _fake_kill(pid: int, _sig: int) -> None:
        if pid not in live_pids:
            raise ProcessLookupError(pid)

    monkeypatch.setattr(bindings.os, "kill", _fake_kill)
    directory = bindings.bindings_directory(world)
    directory.mkdir(parents=True)
    (directory / "corrupt.json").write_text("not json")
    (directory / "other-schema.json").write_text(json.dumps({"unexpected": True}))

    for population in (4, 16):
        live_pids.clear()
        for index in range(population):
            live_pid = population * 1000 + index
            dead_pid = population * 2000 + index
            recycled_pid = population * 3000 + index
            live_pids[live_pid] = f"start-{live_pid}"
            live_pids[recycled_pid] = f"new-start-{recycled_pid}"
            _write_proc_entry(proc_root, live_pid, _FakeProc(ppid=1, start=f"start-{live_pid}"))
            _write_proc_entry(
                proc_root, recycled_pid, _FakeProc(ppid=1, start=f"new-start-{recycled_pid}")
            )

            for pid, start_token, filename in (
                (live_pid, f"start-{live_pid}", f"{live_pid}.json"),
                (dead_pid, f"dead-{dead_pid}", f"{dead_pid}.json"),
                (recycled_pid, f"stale-start-{recycled_pid}", f"recycled-{recycled_pid}.json"),
            ):
                record = bindings.ServerBindingRecord(
                    pid=pid,
                    processStart=start_token,
                    startedAt="2026-11-25T00:00:00Z",
                    configPath=str(world.config_path),
                    parentPid=1,
                    parentProcessStart=None,
                    ancestors=[],
                    bindingState="unbound",
                )
                (directory / filename).write_text(record.model_dump_json())

        returned = bindings.live_server_bindings(world)
        assert len(returned) == population
        assert {item.pid for item in returned} == {
            population * 1000 + index for index in range(population)
        }
        # Only the live records plus the two untouched non-record files remain on disk.
        assert len(list(directory.glob("*.json"))) == population + 2

        live_pids.clear()
        assert bindings.live_server_bindings(world) == []
        assert sorted(path.name for path in directory.glob("*.json")) == [
            "corrupt.json",
            "other-schema.json",
        ]


def test_registration_write_failures_logged_once_without_stopping_server(
    world: McpRuntimeConfig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(bindings, "_WARNINGS", bindings._BindingWarnings())

    def _fail(_path: Path, _text: str) -> None:
        raise OSError("read-only binding registry")

    monkeypatch.setattr(bindings, "atomic_write_text", _fail)
    first = bindings.register_server_binding(world, binding_state="unbound")
    second = bindings.register_server_binding(world, binding_state="unbound")
    assert caplog.text.count("could not be written") == 1
    first()
    second()


def test_process_ancestors_handles_depth_limit_cycles_parens_and_absent_proc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    monkeypatch.setattr(bindings, "PROC_ROOT", proc_root)

    # 10-deep chain: 110 -> 109 -> ... -> 101 -> 1.
    for pid in range(101, 111):
        parent = 1 if pid == 101 else pid - 1
        comm = "weird (comm) name" if pid == 109 else f"p{pid}"
        _write_proc_entry(proc_root, pid, _FakeProc(ppid=parent, start=f"t{pid}", comm=comm))
    ancestors = bindings.process_ancestors(110)
    assert len(ancestors) == bindings.BINDING_ANCESTOR_LIMIT
    assert [(item.pid, item.processStart) for item in ancestors] == [
        (109 - offset, f"t{109 - offset}") for offset in range(bindings.BINDING_ANCESTOR_LIMIT)
    ]

    # Cycle: 200 -> 201 -> 200 terminates cleanly.
    _write_proc_entry(proc_root, 200, _FakeProc(ppid=201, start="t200"))
    _write_proc_entry(proc_root, 201, _FakeProc(ppid=200, start="t201"))
    cycle = bindings.process_ancestors(200)
    assert [(item.pid, item.processStart) for item in cycle] == [(201, "t201")]

    # Absent procfs: non-self returns [], self keeps immediate parent if > 1.
    monkeypatch.setattr(bindings, "PROC_ROOT", tmp_path / "no-such-proc")
    assert bindings.proc_available() is False
    assert bindings.process_start(110) is None
    assert bindings.process_ancestors(110) == []
    expected_self = (
        [bindings.ProcessAncestor(pid=os.getppid(), processStart=None)] if os.getppid() > 1 else []
    )
    assert bindings.process_ancestors(os.getpid()) == expected_self


def test_harness_root_pids_absent_proc_and_synthetic_proc_filtering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bindings, "PROC_ROOT", tmp_path / "missing-proc")
    assert bindings.harness_root_pids(_AGENT_ID) is None

    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    monkeypatch.setattr(bindings, "PROC_ROOT", proc_root)
    (proc_root / "cpuinfo").write_text("processor: 0\n")

    # Root harness process (kept).
    _write_proc_entry(
        proc_root,
        100,
        _FakeProc(
            ppid=1,
            start="s100",
            environ={"PASEO_AGENT_ID": _AGENT_ID, "HOME": "/tmp"},
            cmdline=["node", "/opt/claude/cli.js"],
        ),
    )
    # Child of matching harness root (excluded because parent 100 is in matching set).
    _write_proc_entry(
        proc_root,
        101,
        _FakeProc(
            ppid=100,
            start="s101",
            environ={"PASEO_AGENT_ID": _AGENT_ID},
            cmdline=["bash", "-c", "pytest"],
        ),
    )
    # MCP server child of harness root (excluded by cmdline marker).
    _write_proc_entry(
        proc_root,
        102,
        _FakeProc(
            ppid=100,
            start="s102",
            environ={"PASEO_AGENT_ID": _AGENT_ID},
            cmdline=["python", "-P", "-m", "agents_remember.mcp", "--config", "/tmp/s.json"],
        ),
    )
    # Standalone MCP server carrying PASEO_AGENT_ID (still excluded by cmdline marker).
    _write_proc_entry(
        proc_root,
        103,
        _FakeProc(
            ppid=1,
            start="s103",
            environ={"PASEO_AGENT_ID": _AGENT_ID},
            cmdline=["python", "-P", "-m", "agents_remember.mcp", "--config", "/tmp/s.json"],
        ),
    )
    # Different variable name and prefix-colliding value (both excluded).
    _write_proc_entry(
        proc_root,
        104,
        _FakeProc(
            ppid=1,
            start="s104",
            environ={"AR_PASEO_AGENT_ID": _AGENT_ID},
            cmdline=["node", "other"],
        ),
    )
    _write_proc_entry(
        proc_root,
        105,
        _FakeProc(
            ppid=1,
            start="s105",
            environ={"PASEO_AGENT_ID": f"{_AGENT_ID}-extra"},
            cmdline=["node", "other"],
        ),
    )
    # Second independent root whose parent 50 does not carry PASEO_AGENT_ID (kept).
    _write_proc_entry(
        proc_root,
        50,
        _FakeProc(
            ppid=1,
            start="s50",
            environ={"PATH": "/usr/bin"},
            cmdline=["paseo-daemon"],
        ),
    )
    _write_proc_entry(
        proc_root,
        106,
        _FakeProc(
            ppid=50,
            start="s106",
            environ={"PASEO_AGENT_ID": _AGENT_ID},
            cmdline=["codex", "exec"],
        ),
    )

    roots = bindings.harness_root_pids(_AGENT_ID)
    assert roots == [
        bindings.ProcessAncestor(pid=100, processStart="s100"),
        bindings.ProcessAncestor(pid=106, processStart="s106"),
    ]
    assert bindings.harness_root_pids("") == []
