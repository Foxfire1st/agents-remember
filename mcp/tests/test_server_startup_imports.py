"""MIK-R204 @ v1 rule 6: the import boundary of a fresh AR server process.

The guard is a **fixed, named list**: a fresh process that reaches tool discovery -- without calling a
tool -- must not have loaded the implementation modules below, or anything beneath them. A guard over
"whatever happens to be imported" could not fail for a reason anyone can read, so the list is written
out here and compared by prefix.

The boundary is the **protocol** boundary, not an in-process one: every launch below is a fresh
interpreter that runs the product's own process entry, answers a real ``initialize``, completes a real
``tools/list``, and only then records what it loaded -- in the same process that answered, so "what
answered discovery" and "what was imported" are one fact. No tool is called.

Three valid launches are covered, because they are three different paths into the same server:

* **ordinary** -- no binding at all;
* **ordinary-role-bound** -- a real ordinary role binding, built by the product's own type;
* **explainer** -- a valid explainer binding, which adds the explainer's four tools to the general ones
  (MIK-R211 v5 rule 3).

A fourth launch carries a binding that names another capability: it must be **refused**, and a refused
launch answers nothing. A positive control runs last: a child that eagerly imports an implementation
module must be **caught** by the same checker, or the guard is proving nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import anyio
import pytest
from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.canvas.explainer_binding import EXPLAINER_SURFACE
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.server import create_server
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.build_info import process_serving_build, runtime_source_digest

REPO = "agents-remember"
TASK = "260928_import-guard"
LEAF = "260928-MIK-L999"
#: Every launch serves AR's general tools; an explainer binding adds its own four (MIK-R211 v5 rule 3).
GENERAL_TOOLS = 44
EXPLAINER_TOOLS = GENERAL_TOOLS + len(EXPLAINER_SURFACE)

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "guard", "version": "1"},
    },
}

#: MIK-R204 rule 6's list, verbatim. Bare entries are module roots; a leading dot on an entry above
#: continues the package prefix of the entry before it, spelled out here in full so a reader can grep it.
FORBIDDEN_MODULES: tuple[str, ...] = (
    "agents_remember.mcp.tools",
    "agents_remember.application.worktree_services",
    "agents_remember.application.worktree_tools",
    "agents_remember.application.role_launch_context",
    "agents_remember.application.knowledge_gate",
    "agents_remember.application.context_packet",
    "agents_remember.application.memory_tools",
    "agents_remember.application.task_docs.task_doc_tools",
    "agents_remember.application.runtime.install",
    "agents_remember.worktrees",
    "agents_remember.models.tokens",
    "agents_remember.canvas.explainer_server",
    "agents_remember.canvas.presenter_tools",
    "agents_remember.canvas.prospective",
    "agents_remember.serving.app",
    "fastapi",
    "tiktoken",
)

#: The child: the product's own process entry over the real stdio protocol, then the load report.
#:
#: The report is written to a file, never to stdout -- stdout carries the JSON-RPC answers -- and it is
#: taken after the stdio loop ends, so it is the load state of the process that answered ``tools/list``.
#: ``# EAGER-IMPORT-CONTROL`` is where the positive control puts its eager import.
_PROTOCOL_CHILD = """
import json, sys
from pathlib import Path

snapshot = Path(sys.argv[3])
# EAGER-IMPORT-CONTROL
from agents_remember.mcp.server import main

# A refused launch raises here, before any session: it writes NO load report, because it served nothing.
# The report is written only for a process that actually ran the stdio loop to its end.
code = main(["--config", sys.argv[2]])
snapshot.write_text(json.dumps({"modules": sorted(sys.modules)}), encoding="utf-8")
raise SystemExit(code or 0)
"""


def _protocol_child(*, eager: str = "") -> str:
    """The child source, with the positive control's eager import inserted when one is asked for."""

    return _PROTOCOL_CHILD.replace("# EAGER-IMPORT-CONTROL", eager)


def _exchange(process: subprocess.Popen[str], payload: dict[str, object]) -> dict[str, Any]:
    """Send one JSON-RPC message and read one answer line, taking the process explicitly.

    A closure over the process kept it alive past the test and pytest reported the resulting unraisable
    exceptions against the test itself, which looked like a protocol failure and was not one.
    """

    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    assert line, (process.stderr.read() if process.stderr else "")[-2000:]
    return json.loads(line)


def _world(tmp_path: Path, *, mode: str) -> tuple[Path, Path, dict[str, str]]:
    """A minimal coordination root, its settings file and the child's environment."""

    root = tmp_path / "coordination"
    (root / "tasks" / REPO / TASK).mkdir(parents=True, exist_ok=True)
    (root / "repo").mkdir(exist_ok=True)
    (root / "memory").mkdir(exist_ok=True)
    workspace = root / "explainer" / REPO / TASK / LEAF
    workspace.mkdir(parents=True, exist_ok=True)
    if mode == "explainer":
        (root / "explainer" / REPO / TASK / f"{LEAF}.generation").write_text(
            "1\n", encoding="utf-8"
        )
        (workspace / "generation.json").write_text(
            json.dumps({"generation": 1, "agentId": "guard-agent"}), encoding="utf-8"
        )
        (workspace / "binding.json").write_text(
            json.dumps(
                {
                    "repository": REPO,
                    "master": TASK,
                    "leaf": LEAF,
                    "generation": 1,
                    "agentId": "guard-agent",
                    "seatId": "guard-agent",
                }
            ),
            encoding="utf-8",
        )
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "coordinationRoot": str(root),
                "workspaceRoot": str(root / "workspace"),
                "transcriptRoot": str(root / "transcripts"),
                "repositories": {
                    REPO: {"path": str(root / "repo"), "memoryRoot": str(root / "memory")}
                },
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            "AR_"
        )  # a launch's own binding must never be inherited from the harness
    }
    if mode == "ordinary-role-bound":
        # A real ordinary role binding, built by the product's own type so it cannot be invalid: canonical
        # UUIDs, an absolute report path, no task-document references, and no explainer binding at all.
        # A worker carries all three task references (`agent_binding._references_of`), and a binding that
        # omits them is rejected by `read_agent_binding` before any launch happens.
        sprint_ref = TaskDocumentRef(repository=REPO, path=f"{TASK}/task.json")
        master_ref = TaskDocumentRef(repository=REPO, path="task.json")
        task_ref = TaskDocumentRef(repository=REPO, path=f"{TASK}/{LEAF}.json")
        env.update(
            AgentBinding(
                agent_id="3b7c1d2e-5f4a-4c6b-9d8e-0a1b2c3d4e5f",
                role="worker",
                request_id="9f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a",
                report_path=str(tmp_path / "seat-report.md"),
                sprint_ref=sprint_ref,
                master_ref=master_ref,
                task_ref=task_ref,
            ).environment()
        )
    if mode in ("explainer", "another-capability"):
        binding = {
            "capability": "explainer" if mode == "explainer" else "worker",
            "agent": "guard-agent",
            "request": "guard-1",
            "repository": REPO,
            "task": TASK,
            "leaf": LEAF,
            "workspace": str(workspace),
            "generation": 1,
            "seat": "guard-agent",
        }
        env["AR_EXPLAINER_BINDING"] = json.dumps(binding)
    return root, settings, env


def _environment(env: dict[str, str]) -> dict[str, str]:
    """The child's environment with this worktree's source on the path (the seat launch's own setting)."""

    environment = dict(env)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return environment


def _spawn(
    root: Path, settings: Path, env: dict[str, str], snapshot: Path, *, eager: str = ""
) -> subprocess.Popen[str]:
    """Start one fresh interpreter on the product's own entry, the way a seat launch starts it."""

    return subprocess.Popen(
        [
            sys.executable,
            "-P",
            "-c",
            _protocol_child(eager=eager),
            str(root),
            str(settings),
            str(snapshot),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_environment(env),
        text=True,
        bufsize=1,
        cwd=str(Path(__file__).resolve().parents[2]),
    )


def _stop(process: subprocess.Popen[str]) -> tuple[int, str, str]:
    """End the child inside the caller's scope and report how it ended: returncode, stdout and stderr.

    stdin is closed so the stdio loop ends by itself, then a short wait, then a kill only if it refuses.
    Killing first leaves readers open and pytest reports the resulting unraisable exceptions against the
    test, which reads as a protocol failure.
    """

    if process.stdin is not None:
        process.stdin.close()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=30)
    stdout = process.stdout.read() if process.stdout is not None else ""
    stderr = process.stderr.read() if process.stderr is not None else ""
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
    return process.returncode, stdout, stderr


def _discovery(tmp_path: Path, *, mode: str, eager: str = "") -> dict[str, object]:
    """One fresh launch: ``initialize``, completed ``tools/list``, then its own load report."""

    root, settings, env = _world(tmp_path, mode=mode)
    snapshot = tmp_path / "modules.json"
    started = time.monotonic()
    process = _spawn(root, settings, env, snapshot, eager=eager)
    names: list[str] = []
    timings: dict[str, float] = {}
    try:
        initialized = _exchange(process, dict(INITIALIZE))
        timings["initialize"] = time.monotonic() - started
        assert "result" in initialized, initialized
        assert process.stdin is not None
        process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        process.stdin.flush()
        listed = _exchange(
            process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        timings["toolsList"] = time.monotonic() - started
        names = [tool["name"] for tool in listed["result"]["tools"]]
    finally:
        returncode, _stdout, stderr = _stop(process)
    assert returncode == 0, (
        f"{mode}: the seat command did not exit cleanly ({returncode}): {stderr[-2000:]}"
    )
    modules = json.loads(snapshot.read_text(encoding="utf-8"))["modules"]
    return {"tools": names, "modules": modules, "timings": timings, "stderr": stderr}


def _forbidden_loaded(modules: object) -> list[str]:
    """Every forbidden module (or descendant of one) that the process actually loaded."""

    loaded = set(modules) if isinstance(modules, list) else set()
    found = []
    for name in FORBIDDEN_MODULES:
        prefix = f"{name}."
        if name in loaded or any(module.startswith(prefix) for module in loaded):
            found.append(name)
    return found


@pytest.mark.parametrize("mode", ["ordinary", "ordinary-role-bound", "explainer"])
def test_discovery_loads_no_implementation_module(tmp_path: Path, mode: str) -> None:
    """Rule 6: ``initialize`` and a completed ``tools/list`` must not load the tools behind the surface."""

    _root, _settings, env = _world(tmp_path, mode=mode)
    if mode == "ordinary-role-bound":
        # R204 rule 6: a real ordinary role binding. Preflight it with the product's own reader, so the mode
        # cannot silently measure an unreadable binding, and assert no explainer binding is present.
        read_back = read_agent_binding(env)
        assert read_back is not None, (
            "the role binding did not read back; the mode measures nothing"
        )
        assert read_back.agent_id == "3b7c1d2e-5f4a-4c6b-9d8e-0a1b2c3d4e5f", read_back
        assert read_back.role == "worker", read_back
        assert read_back.report_path.endswith("seat-report.md"), read_back
        assert read_back.sprint_ref == TaskDocumentRef(repository=REPO, path=f"{TASK}/task.json")
        assert read_back.master_ref == TaskDocumentRef(repository=REPO, path="task.json")
        assert read_back.task_ref == TaskDocumentRef(repository=REPO, path=f"{TASK}/{LEAF}.json")
        assert "AR_EXPLAINER_BINDING" not in env
    report = _discovery(tmp_path, mode=mode)

    tools, modules, timings = report["tools"], report["modules"], report["timings"]
    assert isinstance(tools, list) and isinstance(modules, list) and isinstance(timings, dict)
    expected = EXPLAINER_TOOLS if mode == "explainer" else GENERAL_TOOLS
    assert len(tools) == expected, f"{mode}: {len(tools)} tools, expected {expected}"
    found = _forbidden_loaded(modules)
    # A machine-readable line on the passing path too: a run that prints nothing is a failed launch, and
    # reading silence as "no forbidden module" is how a broken launch once looked like a clean guard.
    print(
        f"\nSTARTUP-GUARD mode={mode} tools={len(tools)} modules={len(modules)} forbidden={len(found)} "
        f"initialize={timings['initialize']:.3f}s toolsList={timings['toolsList']:.3f}s"
    )
    assert found == [], f"{mode}: discovery loaded {found}"


def test_the_guard_catches_an_eager_import(tmp_path: Path) -> None:
    """The positive control: without this, a guard that never fires would look like a pass.

    The same child imports one implementation module before the product's entry runs. The checker must
    report it, which is the only thing that makes the guard above meaningful.
    """

    report = _discovery(
        tmp_path, mode="ordinary", eager="import agents_remember.application.worktree_tools"
    )

    found = _forbidden_loaded(report["modules"])
    assert "agents_remember.application.worktree_tools" in found, found


def test_a_binding_for_another_capability_is_refused_at_launch(tmp_path: Path) -> None:
    """A binding that is not the explainer's must be refused, not served.

    NOTE — the earlier report that this case was fail-open was MY FIXTURE'S FAULT, not the product's: the
    mode had stopped setting a binding at all, so the launch correctly took the ordinary branch for want of
    one. With a foreign binding actually present, the launch is refused. The defect claim is withdrawn.

    This replaces the old `ordinary-role-bound` mode, which set `AR_EXPLAINER_BINDING` with
    `capability: "worker"` and claimed to be an ordinary role-bound launch. The reader refuses that
    binding, so the launch was never a valid seat: it either failed, or — worse — was counted as a
    clean ordinary launch. Neither is evidence. The property worth pinning is the refusal itself,
    and it is pinned here without widening explainer admission.
    """

    root, settings, env = _world(tmp_path, mode="another-capability")
    # The premise, asserted rather than assumed: the child is launched WITH a foreign binding present.
    present = json.loads(env["AR_EXPLAINER_BINDING"])
    assert present["capability"] != "explainer", present
    assert present["agent"], present
    snapshot = tmp_path / "modules.json"
    process = _spawn(root, settings, env, snapshot)
    returncode, stdout, stderr = _stop(process)

    assert returncode != 0, "a foreign-capability binding was served instead of refused"
    flattened = stderr.replace("\n", " ")
    assert "explainer-binding-invalid" in flattened, flattened[-2000:]
    # A refused launch answers no JSON-RPC request at all: the refusal happened at server creation, so
    # nothing was served. The absence of an answer is the assertion here, not the absence of a log line.
    assert stdout.strip() == "", stdout[-2000:]
    assert not snapshot.exists(), "a refused launch still built a server and reported its loads"


def test_the_real_command_answers_initialize_and_tools_list(tmp_path: Path) -> None:
    """R204's bound is a protocol endpoint, and this pins it: the seat command, ``initialize``, ``tools/list``.

    The guard above runs the product's entry in a fresh interpreter; this one starts the command the host
    actually launches -- ``python -P -m agents_remember.mcp --config <settings>`` -- so the module entry
    point itself is covered, for all three valid launches. Both response times are reported.
    """

    for mode, expected in (
        ("ordinary", GENERAL_TOOLS),
        ("ordinary-role-bound", GENERAL_TOOLS),
        ("explainer", EXPLAINER_TOOLS),
    ):
        _root, settings, env = _world(tmp_path / mode, mode=mode)
        started = time.monotonic()
        process = subprocess.Popen(
            [sys.executable, "-P", "-m", "agents_remember.mcp", "--config", str(settings)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_environment(env),
            text=True,
            bufsize=1,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        initialized_at = listed_at = 0.0
        try:
            initialized = _exchange(process, dict(INITIALIZE))
            initialized_at = time.monotonic() - started
            assert "result" in initialized, initialized
            assert process.stdin is not None
            process.stdin.write(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            )
            process.stdin.flush()
            listed = _exchange(
                process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            )
            listed_at = time.monotonic() - started
            names = [tool["name"] for tool in listed["result"]["tools"]]
            assert len(names) == expected, f"{mode}: {len(names)} tools, expected {expected}"
        finally:
            returncode, _stdout, stderr = _stop(process)
            # Graceful exit is part of the claim, not a detail: a child that had to be killed did not prove
            # the endpoint answers and stops on its own.
            assert returncode == 0, (
                f"{mode}: the seat command did not exit cleanly ({returncode}): {stderr[-2000:]}"
            )
            print(
                f"\nPROTOCOL-ENDPOINT mode={mode} tools={expected} "
                f"initialize={initialized_at:.3f}s toolsList={listed_at:.3f}s"
            )


def test_first_call_preserves_immutable_boot_identity_and_an_explainer_seat_calls_general_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R204 rules 1-5: first-call lazy loading and immutable boot identity; MIK-R211 v5 rule 3:
    an explainer-bound server serves and answers AR's general tools, refusing none for the binding."""

    root, settings, _env = _world(tmp_path / "ordinary", mode="ordinary")
    config = load_config(str(settings))
    server = create_server(config)
    boot_identity = process_serving_build().payload()
    assert callable(runtime_source_digest)

    # Even if runtime_source_digest would return a different digest after startup,
    # the first server_info call reports the immutable boot identity captured at server startup.
    monkeypatch.setattr(
        "agents_remember.serving.build_info.runtime_source_digest",
        lambda _root: "sha256:" + "f" * 64,
    )
    _, first_info = anyio.run(server.call_tool, "server_info", {})
    assert first_info["ok"] is True
    assert first_info["servingBuild"]["sourceDigest"] == boot_identity.sourceDigest
    assert first_info["servingBuild"]["bootedAt"] == boot_identity.bootedAt

    # Removed read_ar_files.refresh argument still reaches its boundary refusal on first call.
    _, refresh_refusal = anyio.run(
        server.call_tool,
        "read_ar_files",
        {"repo_id": REPO, "files": ["README.md"], "refresh": True},
    )
    assert refresh_refusal["state"] == "refused"
    assert refresh_refusal["refusalCode"] == "argument-removed"

    # Explainer-bound server: the general tools and the four, and a general tool answers as it does
    # for any seat (no confinement, nothing refused for being the explainer).
    _exp_root, exp_settings, exp_env = _world(tmp_path / "explainer", mode="explainer")
    monkeypatch.setenv("AR_EXPLAINER_BINDING", exp_env["AR_EXPLAINER_BINDING"])
    exp_server = create_server(load_config(str(exp_settings)))
    exp_tools = [tool.name for tool in anyio.run(exp_server.list_tools)]
    assert len(exp_tools) == EXPLAINER_TOOLS
    assert "server_info" in exp_tools and set(EXPLAINER_SURFACE) <= set(exp_tools)
    _, exp_info = anyio.run(exp_server.call_tool, "server_info", {})
    assert exp_info["ok"] is True
    assert root.is_dir()
