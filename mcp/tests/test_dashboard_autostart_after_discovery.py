"""MIK-R204 @ v1 rule 3 as amended (80f71dff): optional work starts right after discovery.

The rule forbids a dashboard, host, watcher or poll from starting during initialization or
discovery, and the amendment starts the post-discovery work once the first ``tools/list`` answer
has been produced -- from the event loop, so neither initialize nor the answer waits for it. That
one start runs the outside-scratch sweep always (MIK-R207) and dashboard supervision when
``dashboard.autoStart`` is true. The first tool call stays as the fallback for a client that never
lists; one latch covers both.

This case runs two fresh processes against a scratch settings file whose ``dashboard.autoStart`` is
true, stubs the dashboard and host launchers so nothing real starts, and reads each process's own
thread list and launcher calls after every step:

* discovery process: no ``ar-dashboard-autostart`` thread, no ``ensure`` call and no sweep after
  process entry, after server creation, or while the first list answer exists before the loop runs
  its scheduled start; exactly one of each after that start; none more on a later tool call or a
  second ``tools/list``;
* fallback process: no thread, call or sweep before a tool call; the first call (with no list at
  all) starts exactly one of each; the second call starts nothing.

Ruling ``b4340091`` moved this hook from process entry; amendment ``80f71dff`` moved it from the
first tool call to right after discovery. The tool-event writer stays at startup (rule 4) and is
not this case's subject.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

MCP_ROOT = Path(__file__).resolve().parents[1]
SRC = MCP_ROOT / "src"
AUTOSTART_THREAD = "ar-dashboard-autostart"

# The child is a fresh interpreter, so no module or latch state is inherited from this test
# process. It patches the launchers before any server is built, drives one of the two paths, and
# reports what each step did; the assertions in the parent test are the gates.
_CHILD = '''
"""One fresh MCP process, with the dashboard and host launchers replaced by recorders."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from agents_remember.application.runtime import startup as server_startup
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.server import create_server
from agents_remember.serving import daemon

MODE = sys.argv[1]
SETTINGS = Path(sys.argv[2])
REPORT = Path(sys.argv[3])
AUTOSTART_THREAD = "ar-dashboard-autostart"

calls: list[str] = []
started = threading.Event()
release = threading.Event()
sweep_calls: list[str] = []
sweep_started = threading.Event()


def autostart_threads() -> list[str]:
    return [thread.name for thread in threading.enumerate() if thread.name == AUTOSTART_THREAD]


def snapshot(**extra: object) -> dict[str, object]:
    return {
        "threads": autostart_threads(),
        "calls": list(calls),
        "sweeps": list(sweep_calls),
        **extra,
    }


def ensure(config, endpoint, **kwargs):
    calls.append("ensure")
    started.set()
    release.wait(30)
    return daemon.EnsureResult("started", None, "stub dashboard, nothing started")


def ensure_host(config):
    calls.append("ensure_host")
    return SimpleNamespace(line="host stub, nothing started")


def drain() -> None:
    release.set()
    for _ in range(3000):
        if not autostart_threads():
            return
        time.sleep(0.01)
    raise SystemExit("the dashboard autostart thread never finished")


def reconcile_outside_scratch_roots():
    sweep_calls.append("sweep")
    sweep_started.set()
    return {"removed": [], "reported": []}


daemon.ensure = ensure
daemon.ensure_host = ensure_host
# The real sweep owner is under agents_remember.worktrees; replace the startup seam so this
# process records the trigger without importing that package.
server_startup.reconcile_outside_scratch_roots = reconcile_outside_scratch_roots

report: dict[str, object] = {"mode": MODE}
server_startup.prepare_mcp_process()
report["after_prepare"] = snapshot()
config = load_config(SETTINGS)
server = create_server(config)
report["after_create"] = snapshot()

loop = asyncio.new_event_loop()


async def list_and_step(label: str) -> object:
    """List, snapshot while the answer exists but the loop has not run its start, then step."""

    answer = await server.list_tools()
    report[f"{label}_answer"] = snapshot()
    await asyncio.sleep(0)
    if not sweep_started.wait(30):
        raise SystemExit("the post-discovery start never ran the outside-scratch sweep")
    if not started.wait(30):
        raise SystemExit("the post-discovery start never started dashboard supervision")
    report[f"{label}_started"] = snapshot()
    return answer


if MODE == "discovery":
    loop.run_until_complete(list_and_step("first_list"))
    if not started.wait(30):
        raise SystemExit("the first tool-list answer started no dashboard autostart")
    drain()
    report["after_first_drained"] = snapshot()
    loop.run_until_complete(server.call_tool("server_info", {}))
    time.sleep(0.1)
    report["after_later_tool_call"] = snapshot(answered=True)
    loop.run_until_complete(list_and_step("second_list"))
    report["after_second_list"] = snapshot()
elif MODE == "fallback":
    loop.run_until_complete(server.call_tool("server_info", {}))
    if not started.wait(30):
        raise SystemExit("the first tool call started no dashboard autostart")
    if not sweep_started.wait(30):
        raise SystemExit("the first tool call started no outside-scratch sweep")
    report["after_first_call"] = snapshot(answered=True)
    drain()
    report["after_first_drained"] = snapshot()
    loop.run_until_complete(server.call_tool("server_info", {}))
    time.sleep(0.1)
    report["after_second_call"] = snapshot(answered=True)
else:
    raise SystemExit(f"unknown mode {MODE!r}")
loop.close()
REPORT.write_text(json.dumps(report), encoding="utf-8")
'''


def _settings(tmp_path: Path) -> Path:
    """A scratch coordination root and a settings file whose dashboard autoStart is true."""

    root = tmp_path / "coordination"
    (root / "tasks" / "agents-remember" / "260928_W9").mkdir(parents=True)
    (root / "repo").mkdir(parents=True)
    (root / "memory").mkdir()
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "coordinationRoot": str(root),
                "workspaceRoot": str(tmp_path / "workspace"),
                "transcriptRoot": str(tmp_path / "transcripts"),
                "repositories": {
                    "agents-remember": {
                        "path": str(root / "repo"),
                        "memoryRoot": str(root / "memory"),
                    }
                },
                "providers": {},
                "dashboard": {"autoStart": True, "port": 8765},
            }
        ),
        encoding="utf-8",
    )
    return settings


def _environment() -> dict[str, str]:
    """The child's environment: the worktree's sources, no inherited AR launch binding."""

    env = {key: value for key, value in os.environ.items() if not key.startswith("AR_")}
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(tmp_path: Path, child: Path, settings: Path, mode: str) -> dict[str, Any]:
    report_path = tmp_path / f"report-{mode}.json"
    completed = subprocess.run(
        [sys.executable, "-P", str(child), mode, str(settings), str(report_path)],
        cwd=tmp_path,
        env=_environment(),
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECONDS * 2,
        check=False,
    )
    assert completed.returncode == 0, (mode, completed.stdout, completed.stderr)
    return json.loads(report_path.read_text(encoding="utf-8"))


def _nothing_started(step: dict[str, object], label: str) -> None:
    assert step["threads"] == [], (label, step)
    assert step["calls"] == [], (label, step)
    assert step["sweeps"] == [], (label, step)


def _one_sweep_and_ensure(step: dict[str, object], label: str) -> None:
    assert step["threads"] == [AUTOSTART_THREAD], (label, step)
    assert step["calls"] == ["ensure"], (label, step)
    assert step["sweeps"] == ["sweep"], (label, step)


def test_dashboard_autostart_starts_after_discovery_and_only_once(tmp_path: Path) -> None:
    """Rule 3 amended: the discovery answer starts sweep + dashboard once; a call is the fallback."""

    settings = _settings(tmp_path)
    child = tmp_path / "after_discovery_child.py"
    child.write_text(_CHILD, encoding="utf-8")

    discovery = _run(tmp_path, child, settings, "discovery")
    _nothing_started(discovery["after_prepare"], "prepare")
    _nothing_started(discovery["after_create"], "create")
    # The answer exists before the loop runs the scheduled start: still nothing has started.
    _nothing_started(discovery["first_list_answer"], "first list answer")
    _one_sweep_and_ensure(discovery["first_list_started"], "first list started")
    assert discovery["after_first_drained"]["threads"] == [], discovery["after_first_drained"]
    assert discovery["after_first_drained"]["calls"] == [
        "ensure",
        "ensure_host",
    ], discovery["after_first_drained"]
    assert discovery["after_first_drained"]["sweeps"] == ["sweep"]
    assert discovery["after_later_tool_call"]["threads"] == [], discovery["after_later_tool_call"]
    assert discovery["after_later_tool_call"]["calls"] == ["ensure", "ensure_host"]
    assert discovery["after_later_tool_call"]["sweeps"] == ["sweep"]
    assert discovery["second_list_answer"]["threads"] == [], discovery["second_list_answer"]
    assert discovery["second_list_answer"]["calls"] == ["ensure", "ensure_host"]
    assert discovery["second_list_answer"]["sweeps"] == ["sweep"]
    assert discovery["after_second_list"]["threads"] == [], discovery["after_second_list"]
    assert discovery["after_second_list"]["calls"] == ["ensure", "ensure_host"]
    assert discovery["after_second_list"]["sweeps"] == ["sweep"]

    fallback = _run(tmp_path, child, settings, "fallback")
    _nothing_started(fallback["after_prepare"], "fallback prepare")
    _nothing_started(fallback["after_create"], "fallback create")
    _one_sweep_and_ensure(fallback["after_first_call"], "fallback first call")
    assert fallback["after_first_drained"]["threads"] == [], fallback["after_first_drained"]
    assert fallback["after_first_drained"]["calls"] == [
        "ensure",
        "ensure_host",
    ], fallback["after_first_drained"]
    assert fallback["after_first_drained"]["sweeps"] == ["sweep"]
    assert fallback["after_second_call"]["threads"] == [], fallback["after_second_call"]
    assert fallback["after_second_call"]["calls"] == ["ensure", "ensure_host"]
    assert fallback["after_second_call"]["sweeps"] == ["sweep"]
