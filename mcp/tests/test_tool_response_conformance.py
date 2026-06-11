"""Dev-time conformance tests for public MCP tool response contracts.

Production code already validates every tool payload against its registered model
in ``agents_remember.mcp.tools._tool_payload`` (``model_validate(...).model_dump(
mode="json", exclude_none=True)``). Strict models use ``extra="forbid"`` so
controller drift fails loudly at runtime. These tests move that guarantee into the
suite so drift is caught at dev time instead of in a live call.

For every public tool we obtain a *representative* response payload by invoking the
real ``*_payload`` builder against a temporary fixture workspace, then assert:

* the payload validates against the registered model with no error, and
* round-tripping the payload through the model does not fabricate keys.

We also assert the strict/flexible split is exactly what the response-model
taxonomy intends: every model that is not built on ``FlexibleResponseModel`` keeps
``extra="forbid"``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.mcp import tools
from agents_remember.mcp.config import load_config
from agents_remember.models.base import FlexibleResponseModel
from agents_remember.models.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS
from test_config import settings_payload
from test_worktree_support import (
    commit_file,
    git,
    init_repo,
    initialized_memory_repo,
    write_file_onboarding,
)

REPO = "agents-remember-md"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _run_git(repo: Path, args: list[str]) -> None:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


def _base_fixture(root: Path):
    """Code repo + memory layer + ``.codex/mcp`` settings for the simple tools."""
    repo = root / "workspace" / REPO
    memory = root / "ar-coordination" / "memory-repos" / f"ar-{REPO}"
    (memory / "system").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, ["init", "-b", "main"])
    _run_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    _run_git(repo, ["config", "user.name", "Agents Remember"])
    (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
    _run_git(repo, ["add", "README.md"])
    _run_git(repo, ["commit", "-m", "init"])
    path = root / ".codex" / "mcp" / "settings.json"
    _write_json(path, settings_payload(root))
    return load_config(path)


def _simple_payloads(config) -> dict[str, dict]:
    """Tools whose real ``*_payload`` builder runs against the base fixture."""
    return {
        "ping": tools.ping_payload(),
        "server_info": tools.server_info_payload(config),
        "context_packet": tools.context_packet_payload(config, REPO),
        "runtime_install": tools.runtime_install_payload(config, install_provider_deps=False),
        "resolve_context": tools.resolve_context_payload(config, REPO),
        "drift_check": tools.drift_check_payload(config, REPO),
        "memory_quality_check": tools.memory_quality_check_payload(config, REPO),
        "route_index_refresh": tools.route_index_refresh_payload(config, REPO),
        "memory_init": tools.memory_init_payload(config, REPO),
        "skills_install": tools.skills_install_payload(config),
        "provider_status": tools.provider_status_payload(config),
        "provider_diagnostics": tools.provider_diagnostics_payload(config),
        "provider_watchers": tools.provider_watchers_payload(config, action="status"),
        "grepai_search": tools.grepai_search_payload(config, "query", dry_run=True),
        "grepai_trace": tools.grepai_trace_payload(config, "graph", "sym", dry_run=True),
        "cgc_symbol_search": tools.cgc_symbol_search_payload(config, REPO, "sym", dry_run=True),
        "cgc_callers": tools.cgc_callers_payload(config, REPO, "fn", dry_run=True),
        "cgc_callees": tools.cgc_callees_payload(config, REPO, "fn", dry_run=True),
        "cgc_dependencies": tools.cgc_dependencies_payload(config, REPO, "mod", dry_run=True),
        "cgc_complexity": tools.cgc_complexity_payload(config, REPO, dry_run=True),
        "cgc_visualize": tools.cgc_visualize_payload(config, REPO, dry_run=True),
        "memory_baseline_status": tools.memory_baseline_status_payload(config, REPO),
        "memory_baseline_adopt": tools.memory_baseline_adopt_payload(config, REPO),
        "codex_benchmark_prepare": tools.codex_benchmark_prepare_payload(config),
        "codex_benchmark_run": tools.codex_benchmark_run_payload(config),
    }


def _worktree_payloads(root: Path) -> dict[str, dict]:
    """Drive a real worktree lifecycle (disabled memory) and capture every step."""
    config = _base_fixture(root)
    # worktree_start needs a memory git repo to exist even when memory is disabled.
    tools.memory_init_payload(config, REPO, dry_run=False, initialize_git=True)
    memory_root = root / "ar-coordination" / "memory-repos" / f"ar-{REPO}"
    (memory_root / "memory.md").write_text("# Memory ledger\n", encoding="utf-8")
    _run_git(memory_root, ["add", "-A"])
    _run_git(memory_root, ["commit", "-m", "seed"])

    payloads: dict[str, dict] = {}
    payloads["worktree_start"] = tools.worktree_start_payload(
        config,
        REPO,
        "demo-task",
        "demo-wt",
        dry_run=False,
        skip_provider_setup=True,
        memory_choice="disabled-memory",
    )
    contract_path = payloads["worktree_start"]["contract_path"]
    payloads["worktree_status"] = tools.worktree_status_payload(
        config, REPO, contract_path=contract_path
    )
    payloads["worktree_attach"] = tools.worktree_attach_payload(
        config, REPO, contract_path=contract_path
    )
    payloads["worktree_sync"] = tools.worktree_sync_payload(
        config, contract_path, dry_run=True
    )
    payloads["worktree_closeout_preview"] = tools.worktree_closeout_preview_payload(
        config, contract_path, "code commit message"
    )
    payloads["worktree_closeout_apply"] = tools.worktree_closeout_apply_payload(
        config, contract_path, "intent note", "code commit message", dry_run=False
    )
    payloads["worktree_integrate"] = tools.worktree_integrate_payload(
        config, contract_path, dry_run=False
    )
    payloads["worktree_cleanup"] = tools.worktree_cleanup_payload(
        config, contract_path, dry_run=False
    )
    abandon_start = tools.worktree_start_payload(
        config,
        REPO,
        "abandon-task",
        "abandon-wt",
        dry_run=False,
        skip_provider_setup=True,
        memory_choice="disabled-memory",
    )
    payloads["worktree_abandon"] = tools.worktree_abandon_payload(
        config, abandon_start["contract_path"], dry_run=False, force=True
    )
    return payloads


def _carryover_payloads(root: Path) -> dict[str, dict]:
    """Landed-branch fixture for the c-11-memory-carryover-from-branch skill carryover tools."""
    code_repo = root / "repo-a"
    old_base = init_repo(code_repo, "main")
    git(code_repo, "checkout", "-b", "workbench/reado/v1.2")
    source_head = commit_file(
        code_repo, "feature.py", "def feature():\n    return 'landed'\n", "Add feature"
    )
    git(code_repo, "checkout", "main")
    git(code_repo, "merge", "--ff-only", "workbench/reado/v1.2")

    official_memory = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
    initialized_memory_repo(official_memory, "repo-a", "main", "main", old_base)
    source_memory = root / "ar-coordination" / "memory-source-branch" / "ar-repo-a"
    write_file_onboarding(source_memory / "onboarding", "repo-a", "feature.py", source_head)
    onboarding_file = source_memory / "onboarding" / "feature.py.md"
    onboarding_file.write_text(
        onboarding_file.read_text(encoding="utf-8") + "Branch-learned behavior.\n",
        encoding="utf-8",
    )

    settings = settings_payload(root)
    settings["workspaceRoot"] = str(root)
    settings["repositories"] = {"repo-a": {}}
    path = root / ".codex" / "mcp" / "settings.json"
    _write_json(path, settings)
    config = load_config(path)
    source = source_memory.as_posix()
    return {
        "memory_carryover_plan": tools.memory_carryover_plan_payload(
            config, "repo-a", source, "main", "workbench/reado/v1.2", old_base
        ),
        "memory_carryover_apply": tools.memory_carryover_apply_payload(
            config, "repo-a", source, "main", "workbench/reado/v1.2", old_base, "intent note"
        ),
    }


def _allowed_keys(model) -> set[str]:
    """Serialized keys the model is allowed to emit (field names plus aliases)."""
    allowed: set[str] = set()
    for name, info in model.model_fields.items():
        allowed.add(name)
        if info.alias:
            allowed.add(info.alias)
        serialization_alias = getattr(info, "serialization_alias", None)
        if serialization_alias:
            allowed.add(serialization_alias)
    return allowed


class ToolResponseConformanceTests(unittest.TestCase):
    payloads: dict[str, dict]
    _temp_dirs: list[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls._temp_dirs = [tempfile.mkdtemp() for _ in range(3)]
        base, worktree, carryover = (Path(d) for d in cls._temp_dirs)
        cls.payloads = {}
        cls.payloads.update(_simple_payloads(_base_fixture(base)))
        cls.payloads.update(_worktree_payloads(worktree))
        cls.payloads.update(_carryover_payloads(carryover))

    @classmethod
    def tearDownClass(cls) -> None:
        # Git worktrees leave read-only pack files; ignore_errors avoids flaky
        # cleanup failures on Windows.
        for path in cls._temp_dirs:
            shutil.rmtree(path, ignore_errors=True)

    def test_every_public_tool_has_a_representative_payload(self) -> None:
        self.assertEqual(set(self.payloads), set(PUBLIC_TOOL_RESPONSE_MODELS))

    def test_representative_payloads_conform_to_registered_models(self) -> None:
        for tool_name, model in PUBLIC_TOOL_RESPONSE_MODELS.items():
            with self.subTest(tool=tool_name):
                payload = self.payloads[tool_name]
                # (a) The representative payload validates against the model.
                model.model_validate(payload)
                # (b) Round-tripping does not fabricate keys. Strict models may
                # only emit declared fields; intentionally flexible models may
                # also pass through keys that were present on the input payload,
                # so the round trip must not invent keys that are neither
                # declared nor part of the input.
                round_trip = model.model_validate(payload).model_dump(
                    mode="json", exclude_none=True
                )
                allowed = _allowed_keys(model)
                if issubclass(model, FlexibleResponseModel):
                    allowed |= set(payload)
                self.assertLessEqual(
                    set(round_trip),
                    allowed,
                    f"{tool_name} round trip produced undeclared keys: "
                    f"{sorted(set(round_trip) - allowed)}",
                )

    def test_strict_response_models_forbid_extra_fields(self) -> None:
        for tool_name, model in PUBLIC_TOOL_RESPONSE_MODELS.items():
            with self.subTest(tool=tool_name):
                # The response-model taxonomy decides strictness: anything not
                # built on FlexibleResponseModel is a strict contract and must
                # keep extra="forbid"; the flexible base must keep extra="allow".
                expected = "allow" if issubclass(model, FlexibleResponseModel) else "forbid"
                self.assertEqual(
                    model.model_config.get("extra"),
                    expected,
                    f"{tool_name} ({model.__name__}) must use extra={expected!r}",
                )


if __name__ == "__main__":
    unittest.main()
