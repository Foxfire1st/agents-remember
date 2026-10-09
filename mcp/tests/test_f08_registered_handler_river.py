"""Original L114-CODE-R1-F08 proof through the actual registered handler/startup path.

The retained river tests install a writer by hand and call
``complete_tool_response`` directly. These four cases instead drive the
registered ``ping`` handler through a server built by ``create_server`` over a
real in-memory MCP client session -- the same entry shape the retained
entry-point sweep uses -- and read the workspace river the server writes:

* bound first-call fields (role, agent, task id/title from the launch binding);
* unbound chat/harness caller;
* a genuinely fresh spawn-guarded child process writing its first call;
* one caller read at startup that stays fixed while the process runs.

The vehicle is ``ping`` on purpose: read-only, no side effects, no host,
Paseo, dashboard, paid actor, or E8 involvement. ``AR_F08_MCP_SRC`` selects the
product source under proof (default: this checkout's ``mcp/src``); every
``agents_remember`` import happens inside functions from that root only, so the
same file runs unmodified against a read-only base export for the
expected-negative discriminator. No product module is imported at top level,
which also keeps a spawned child re-import side-effect free. All process entry
code is guarded.

When ``AR_F08_EVIDENCE_DIR`` names a directory, each case additionally retains
its source origin, process id, and river rows there as JSON (documentary
retention outside fixture deletion); without it the cases assert only.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

_TESTS_DIR = Path(__file__).resolve().parent
_MCP_SRC = Path(os.environ.get("AR_F08_MCP_SRC") or (_TESTS_DIR.parent / "src"))
if str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

REPO = "repo-a"
MASTER = "m"
LEAF_STEM = "1_leaf"
SESSION_TIMEOUT_SECONDS = 60
CHILD_JOIN_SECONDS = 120

AGENT_A = "f08a9de8-0000-4000-8000-000000000001"
REQUEST_A = "f08a9de8-0000-4000-8000-000000000002"
AGENT_B = "f08a9de8-0000-4000-8000-000000000003"
REQUEST_B = "f08a9de8-0000-4000-8000-000000000004"
DOC_ID = "F08"
DOC_TITLE = "F08 fixture leaf"

BINDING_VARIABLES = (
    "AR_PASEO_AGENT_ID",
    "AR_ROLE",
    "AR_REQUEST_ID",
    "AR_REPORT_PATH",
    "AR_SPRINT_REF",
    "AR_MASTER_REF",
    "AR_TASK_REF",
    "AR_SPAWN_ROLE",
    "AR_HOSTED_SESSION_ID",
)


# NOTE (PLC0415 exemptions below): every ``agents_remember`` import stays
# function-local on purpose. The module must select one immutable source root
# (``AR_F08_MCP_SRC``) before any product import; a top-level import would pin
# the candidate checkout and break the read-only base-export discriminator,
# which runs this same file with the base root first on ``sys.path``.


def _product_origin() -> str:
    import agents_remember  # noqa: PLC0415

    origin = getattr(agents_remember, "__file__", None)
    return str(origin) if origin else ""


def _has_river() -> bool:
    try:
        import agents_remember.observer.tool_events  # noqa: F401,PLC0415
    except ImportError:
        return False
    return True


def _evidence_dir() -> Path | None:
    raw = os.environ.get("AR_F08_EVIDENCE_DIR")
    if not raw:
        return None
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _retain_evidence(name: str, payload: dict) -> None:
    target = _evidence_dir()
    if target is None:
        return
    (target / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _declare_test_process() -> None:
    from agents_remember.kernel.primitives.checkout_coordination import (  # noqa: PLC0415
        declare_test_process,
    )

    declare_test_process()


def _settings_document(coord: Path, root: Path, skills: Path) -> dict:
    return {
        "version": 1,
        "coordinationRoot": coord.as_posix(),
        "workspaceRoot": root.as_posix(),
        "harnessSkillRoot": skills.as_posix(),
        "repositories": {
            REPO: {
                "path": (coord / "repo").as_posix(),
                "memoryRoot": (coord / "memory").as_posix(),
            },
        },
        "providers": {},
        "benchmarksEnabled": True,
    }


def _leaf_document() -> dict:
    return {
        "schema": "ar-task-document/v1",
        "id": DOC_ID,
        "slug": "f08-leaf",
        "title": DOC_TITLE,
        "kind": "subTask",
        "repo": REPO,
        "createdAt": "2026-10-09T00:00:00Z",
    }


def _build_world(root: Path):
    """Author a disposable coordination root and build a server on it."""
    from agents_remember.kernel.primitives.runtime_config import (  # noqa: PLC0415
        McpRuntimeConfig,
        RepositoryScope,
    )
    from agents_remember.mcp.server import create_server  # noqa: PLC0415

    _declare_test_process()
    coord = root / "coord"
    code = coord / "repo"
    memory = coord / "memory"
    skills = root / "skills"
    for directory in (code, memory, skills):
        directory.mkdir(parents=True, exist_ok=True)
    settings = root / "mcp" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(_settings_document(coord, root, skills)), encoding="utf-8")
    system = coord / "system"
    system.mkdir(parents=True, exist_ok=True)
    (system / "settings.json").write_text(
        json.dumps(
            {
                "version": 1,
                "orchestration": {"gateDelegation": {"policy": "manager-decides-leaf-gates"}},
            }
        ),
        encoding="utf-8",
    )
    leaf = coord / "tasks" / REPO / MASTER / f"{LEAF_STEM}.json"
    leaf.parent.mkdir(parents=True, exist_ok=True)
    leaf.write_text(json.dumps(_leaf_document()), encoding="utf-8")
    config = McpRuntimeConfig(
        config_path=settings,
        coordination_root=coord,
        workspace_root=root,
        transcript_root=coord / "logs" / "mcp",
        harness_skill_root=skills,
        benchmarks_enabled=True,
        repositories={
            REPO: RepositoryScope(repo_id=REPO, path=code, memory_root=memory),
        },
    )
    server = create_server(config)
    return coord, server


def _binding_environment(root: Path, *, agent_id: str, request_id: str) -> dict[str, str]:
    from agents_remember.application.agent_binding import AgentBinding  # noqa: PLC0415
    from agents_remember.models.task_document_ref import TaskDocumentRef  # noqa: PLC0415

    binding = AgentBinding(
        agent_id=str(uuid.UUID(agent_id)),
        role="worker",
        request_id=str(uuid.UUID(request_id)),
        report_path=(root / "reports" / "w.md").as_posix(),
        sprint_ref=TaskDocumentRef(repository=REPO, path="s/task.json"),
        master_ref=TaskDocumentRef(repository=REPO, path=f"{MASTER}/task.json"),
        task_ref=TaskDocumentRef(repository=REPO, path=f"{MASTER}/{LEAF_STEM}.json"),
    )
    return binding.environment()


def _empty_binding_environment() -> dict[str, str]:
    return dict.fromkeys(BINDING_VARIABLES, "")


def _isolated_environment(root: Path, binding: dict[str, str]) -> dict[str, str]:
    environment = dict(binding)
    home = root / "home"
    home.mkdir(exist_ok=True)
    environment.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_CACHE_HOME": str(root / "cache"),
        }
    )
    return environment


def _result_payload(result: object) -> dict:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured
    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            decoded = json.loads(text)
            if isinstance(decoded, dict):
                return decoded
    raise AssertionError(f"registered ping answered without a mapping payload: {result!r}")


async def _call_ping(server: FastMCP) -> dict:
    inner = server._mcp_server
    async with create_connected_server_and_client_session(inner) as client:
        result = await asyncio.wait_for(
            client.call_tool("ping", {}), timeout=SESSION_TIMEOUT_SECONDS
        )
    return _result_payload(result)


def _river_rows(coord: Path) -> list[dict]:
    path = coord / "logs" / "observer" / "workspace" / "events.jsonl"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        decoded = json.loads(line)
        if isinstance(decoded, dict) and decoded.get("kind") == "tool.completed":
            rows.append(decoded)
    return rows


def _assert_bound_row(row: dict, *, agent_id: str, root_name: str) -> None:
    data = row.get("data")
    assert isinstance(data, dict), f"river row carries no data mapping: {row!r}"
    assert data.get("tool") == "ping", f"unexpected tool in river row: {data!r}"
    assert data.get("ok") is True, f"river row is not ok: {data!r}"
    assert data.get("role") == "worker", f"river row caller role mismatch: {data!r}"
    assert data.get("agentId") == agent_id, f"river row agent mismatch: {data!r}"
    assert data.get("taskId") == DOC_ID, f"river row task id mismatch: {data!r}"
    assert data.get("taskTitle") == DOC_TITLE, f"river row task title mismatch: {data!r}"
    assert row.get("repoId") == REPO, f"river row repository mismatch: {row!r}"
    assert data.get("harness", root_name) == root_name, f"river row harness mismatch: {data!r}"


def test_f08_bound_first_call_through_registered_handler() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f08-") as name:
        root = Path(name)
        if _has_river():
            environment = _isolated_environment(
                root, _binding_environment(root, agent_id=AGENT_A, request_id=REQUEST_A)
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                coord, server = _build_world(root)
                payload = asyncio.run(_call_ping(server))
                assert payload.get("ok") is True, f"registered ping refused: {payload!r}"
                rows = _river_rows(coord)
                _retain_evidence(
                    "bound-first-call",
                    {"origin": _product_origin(), "pid": os.getpid(), "rows": rows},
                )
            assert len(rows) == 1, f"expected exactly one river row, saw {len(rows)}"
            _assert_bound_row(rows[0], agent_id=AGENT_A, root_name=root.name)
        else:
            environment = _isolated_environment(root, _empty_binding_environment())
            with mock.patch.dict(os.environ, environment, clear=False):
                coord, server = _build_world(root)
                payload = asyncio.run(_call_ping(server))
                assert payload.get("ok") is True, f"registered ping refused: {payload!r}"
                rows = _river_rows(coord)
                _retain_evidence(
                    "bound-first-call",
                    {"origin": _product_origin(), "pid": os.getpid(), "rows": rows},
                )
            assert rows == [], f"base must write no river rows, saw {rows!r}"


def test_f08_unbound_chat_harness_through_registered_handler() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f08-") as name:
        root = Path(name)
        environment = _isolated_environment(root, _empty_binding_environment())
        with mock.patch.dict(os.environ, environment, clear=False):
            coord, server = _build_world(root)
            payload = asyncio.run(_call_ping(server))
            assert payload.get("ok") is True, f"registered ping refused: {payload!r}"
            rows = _river_rows(coord)
            _retain_evidence(
                "unbound-chat-harness",
                {"origin": _product_origin(), "pid": os.getpid(), "rows": rows},
            )
        if _has_river():
            assert len(rows) == 1, f"expected exactly one river row, saw {len(rows)}"
            data = rows[0].get("data")
            assert isinstance(data, dict), f"river row carries no data: {rows[0]!r}"
            assert data.get("role") == "chat", f"unbound caller is not chat: {data!r}"
            assert data.get("harness") == root.name, f"harness mismatch: {data!r}"
        else:
            assert rows == [], f"base must write no river rows, saw {rows!r}"


def test_f08_caller_fixed_after_startup_binding_rewrite() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f08-") as name:
        root = Path(name)
        home = root / "home"
        home.mkdir(exist_ok=True)
        base_environment = {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_CACHE_HOME": str(root / "cache"),
        }
        with mock.patch.dict(os.environ, base_environment, clear=False):
            with mock.patch.dict(
                os.environ,
                _binding_environment(root, agent_id=AGENT_A, request_id=REQUEST_A),
                clear=False,
            ):
                coord, server = _build_world(root)
                first = asyncio.run(_call_ping(server))
            assert first.get("ok") is True, f"registered ping refused: {first!r}"
            with mock.patch.dict(
                os.environ,
                _binding_environment(root, agent_id=AGENT_B, request_id=REQUEST_B),
                clear=False,
            ):
                second = asyncio.run(_call_ping(server))
            assert second.get("ok") is True, f"registered ping refused: {second!r}"
            rows = _river_rows(coord)
            _retain_evidence(
                "caller-fixed",
                {"origin": _product_origin(), "pid": os.getpid(), "rows": rows},
            )
        if _has_river():
            assert len(rows) == 2, f"expected two river rows, saw {len(rows)}"
            for row in rows:
                _assert_bound_row(row, agent_id=AGENT_A, root_name=root.name)
        else:
            assert rows == [], f"base must write no river rows, saw {rows!r}"


def _f08_spawn_child_main(payload: dict) -> None:
    """Child entry: own world, own server, one registered ping, row to disk."""
    child_src = str(payload["src_root"])
    if child_src not in sys.path:
        sys.path.insert(0, child_src)
    result_path = Path(str(payload["result_path"]))
    with tempfile.TemporaryDirectory(prefix="ar-f08-child-") as name:
        root = Path(name)
        environment = _isolated_environment(root, dict(payload["binding"]))
        with mock.patch.dict(os.environ, environment, clear=False):
            coord, server = _build_world(root)
            answer = asyncio.run(_call_ping(server))
            rows = _river_rows(coord)
    record = {
        "pid": os.getpid(),
        "origin": _product_origin(),
        "answer_ok": answer.get("ok"),
        "rows": rows,
        "has_river": _has_river(),
    }
    result_path.write_text(json.dumps(record), encoding="utf-8")


def test_f08_fresh_process_first_call_through_registered_handler() -> None:
    with tempfile.TemporaryDirectory(prefix="ar-f08-parent-") as name:
        parent = Path(name)
        result_path = parent / "child-row.json"
        with mock.patch.dict(
            os.environ,
            _binding_environment(parent, agent_id=AGENT_A, request_id=REQUEST_A),
            clear=False,
        ):
            binding = {name: os.environ[name] for name in BINDING_VARIABLES if name in os.environ}
        payload = {
            "src_root": str(_MCP_SRC),
            "result_path": str(result_path),
            "binding": binding,
        }
        tests_on_path = str(_TESTS_DIR) in sys.path
        with mock.patch.dict(
            os.environ,
            {
                "PYTHONPATH": os.pathsep.join(
                    [str(_TESTS_DIR)]
                    + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else [])
                )
            },
            clear=False,
        ):
            context = multiprocessing.get_context("spawn")
            process = context.Process(target=_f08_spawn_child_main, args=(payload,))
            process.start()
            process.join(CHILD_JOIN_SECONDS)
            if process.is_alive():
                process.terminate()
                process.join(30)
                raise AssertionError("fresh child process did not finish in time")
        assert tests_on_path, "parent must import the test module from its own directory"
        assert process.exitcode == 0, f"fresh child exited {process.exitcode}"
        record = json.loads(result_path.read_text(encoding="utf-8"))
        _retain_evidence("fresh-process-child", record)
        assert record.get("pid") != os.getpid(), "child must be a fresh process"
        assert str(record.get("origin", "")).startswith(str(_MCP_SRC)), (
            f"child imported product code from elsewhere: {record.get('origin')!r}"
        )
        assert record.get("answer_ok") is True, f"child ping refused: {record!r}"
        rows = record.get("rows")
        assert isinstance(rows, list), f"child record carries no rows: {record!r}"
        if record.get("has_river"):
            assert len(rows) == 1, f"child must write exactly one row, saw {rows!r}"
            data = rows[0].get("data", {})
            assert data.get("tool") == "ping" and data.get("ok") is True
            assert data.get("agentId") == AGENT_A, f"child row agent mismatch: {data!r}"
            assert data.get("taskId") == DOC_ID and data.get("taskTitle") == DOC_TITLE, (
                f"child row task mismatch: {data!r}"
            )
        else:
            assert rows == [], f"base child must write no rows, saw {rows!r}"


if __name__ == "__main__":  # pragma: no cover - process-entry guard, never a runner
    raise SystemExit("test_f08_registered_handler_river is imported by pytest, not executed")
