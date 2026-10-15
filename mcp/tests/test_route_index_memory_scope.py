from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Any, cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from agents_remember.application.memory_quality import controller
from agents_remember.application.memory_scope import MemoryScope, MemoryScopeIdentity
from agents_remember.application.memory_tools import (
    _refuse_official_memory,
    route_index_refresh_tool,
)
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.errors import AuthorityError
from agents_remember.kernel.coordination_context.models import CoordinationContext, StorageSettings
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.mcp.tools.memory import memory_quality_check_payload
from agents_remember.memory_quality.check import AVAILABLE_CHECKS
from agents_remember.models.memory import MemoryQualityStartRequest, MemoryQualitySyncRequest
from test_tool_answer_rows_quality import _scoped_pair, _settle
from test_worktree_support import git, init_repo, open_external_contract_fixture


def _cache_counts(built, stale, errors):
    return dict(builtCount=built, staleCount=stale, buildErrorCount=errors)


def test_public_full_names_missing_onboarding_without_creating_a_cache(tmp_path: Path):
    contract, configured, _scoped = _scoped_pair(tmp_path)
    onboarding = contract.memory_repo_path / "onboarding"
    assert not onboarding.exists()
    with tool_call_context("memory_quality_check", config=configured):
        answer = memory_quality_check_payload(
            configured, MemoryQualitySyncRequest(mode="sync", repo_id=contract.repo_name)
        )
    whole = json.loads(Path(answer["log"]).read_text())
    built = whole["routeIndexCacheBuild"]
    assert built["builtIndexes"] == []
    assert built["builtCount"] == 0
    assert built["buildErrors"][0]["path"] == str(onboarding)
    assert "onboarding root does not exist" in built["buildErrors"][0]["error"]
    assert answer["routeIndexCacheBuild"]["buildErrorCount"] == 1
    assert not whole["acceptanceEligible"]
    assert not onboarding.exists()


@pytest.mark.parametrize("branch", ["repo", "leaf"])
def test_public_full_prepares_repo_and_leaf_once_but_subset_never_builds(tmp_path: Path, branch):
    contract, configured, scoped = _scoped_pair(tmp_path)
    config = configured if branch == "repo" else scoped
    memory = contract.memory_repo_path if branch == "repo" else contract.memory_worktree
    onboarding = memory / "onboarding"
    onboarding.mkdir(exist_ok=True)
    (onboarding / "overview.md").write_text("# Root\n", encoding="utf-8")
    cache = onboarding / "overview.index.json"
    Path(
        git(memory, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
    ).write_text("overview.index.json\n")
    request = dict(mode="sync", repo_id=contract.repo_name)
    if branch == "leaf":
        request["contract_path"] = str(contract.contract_path)

    def call(**fields):
        with tool_call_context("memory_quality_check", config=config):
            answer = memory_quality_check_payload(
                config, MemoryQualitySyncRequest(**request, **fields)
            )
        return (answer, json.loads(Path(answer["log"]).read_text()))

    with (
        patch(
            "agents_remember.worktrees.integration.configured_contract_authority.load_config",
            return_value=configured,
        ),
        patch.object(
            controller, "build_route_indexes", wraps=controller.build_route_indexes
        ) as build,
    ):
        subset, whole = call(checks=["style.citations.range_resolution"])
        assert "routeIndexCacheBuild" not in whole
        assert "routeIndexCacheBuild" not in subset
        assert not cache.exists()
        assert build.call_count == 0
        answer, whole = call()
        assert build.call_count == 1
        assert cache.is_file()
        summary = whole["routeIndexCacheBuild"]
        assert summary["builtIndexes"] == ["overview.index.json"]
        assert summary["staleIndexes"] == []
        assert answer["routeIndexCacheBuild"] == _cache_counts(1, 0, 0)
        authority = "official-diagnostic" if branch == "repo" else "leaf-candidate"
        assert whole["scopeAuthority"] == authority
        cache.write_text("stale bytes", encoding="utf-8")
        answer, whole = call()
        assert build.call_count == 2
        assert cache.read_text() == "stale bytes"
        assert whole["routeIndexCacheBuild"]["staleIndexes"] == ["overview.index.json"]
        assert answer["routeIndexCacheBuild"] == _cache_counts(0, 1, 0)
        cache.unlink()
        original = Path.write_text

        def denied(path, *args, **kwargs):
            if path == cache:
                raise PermissionError("cache write denied")
            return original(path, *args, **kwargs)

        with patch.object(Path, "write_text", denied):
            answer, whole = call()
        assert build.call_count == 3
        assert not cache.exists()
        assert whole["routeIndexCacheBuild"]["buildErrors"] == [
            {"path": "overview.index.json", "error": "cache write denied"}
        ]
        assert answer["routeIndexCacheBuild"] == _cache_counts(0, 1, 1)
        assert answer.get("overflow") is not True
    if branch == "repo":
        _assert_distinct_full_run(config, contract, cache)


def _assert_distinct_full_run(config, contract, cache):
    entered, release = (Event(), Event())
    check = controller.run_memory_quality_check
    request: dict[str, Any] = dict(mode="start", repo_id=contract.repo_name)
    subset = MemoryQualityStartRequest(**request, checks=sorted(AVAILABLE_CHECKS))
    ordinary = MemoryQualityStartRequest(**request)

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(30)
        return check(*args, **kwargs)

    with patch.object(controller, "run_memory_quality_check", paused):
        try:
            started = controller.start_memory_quality_request(config, subset)
            assert entered.wait(30)
            assert not cache.exists()
            full = controller.start_memory_quality_request(config, ordinary)
            assert full["runId"] != started["runId"]
        finally:
            release.set()
            _settle(contract.repo_name, str(started["runId"]))
    _settle(contract.repo_name, str(full["runId"]))
    assert cache.exists()


class RouteIndexMemoryScopeTests(unittest.TestCase):
    def test_leaf_profile_uses_configured_official_root_and_official_scope_still_refuses(self):
        with TemporaryDirectory() as temporary:
            contract = open_external_contract_fixture(Path(temporary))
            assert contract.memory_repo_path is not None
            assert contract.memory_worktree is not None

            config_path = Path(temporary) / "settings" / "mcp.json"
            configured_repository = RepositoryScope(
                repo_id=contract.repo_name,
                path=contract.code_repo_path,
                memory_root=contract.memory_repo_path,
            )
            configured = McpRuntimeConfig(
                config_path=config_path,
                coordination_root=contract.coordination_root,
                workspace_root=contract.worktree_group,
                transcript_root=contract.coordination_root / "logs" / "mcp",
                repositories={contract.repo_name: configured_repository},
            )
            scoped_repository = RepositoryScope(
                repo_id=contract.repo_name,
                path=contract.code_worktree,
                memory_root=contract.memory_worktree,
                contract_path=contract.contract_path,
            )
            scoped = McpRuntimeConfig(
                config_path=config_path,
                coordination_root=contract.coordination_root,
                workspace_root=contract.worktree_group,
                transcript_root=configured.transcript_root,
                repositories={contract.repo_name: scoped_repository},
            )
            leaf_onboarding = contract.memory_worktree / "onboarding"
            leaf_onboarding.mkdir(parents=True)
            (leaf_onboarding / "overview.md").write_text("# Fixture\n", encoding="utf-8")

            # The profile uses the leaf roots, while authority validation rereads the original
            # registered roots and proves the exact leaf contract and Git worktree identities.
            with patch(
                "agents_remember.worktrees.integration.configured_contract_authority.load_config",
                return_value=configured,
            ):
                result = route_index_refresh_tool(
                    scoped,
                    repo_id=contract.repo_name,
                    contract_path=contract.contract_path.as_posix(),
                    dry_run=False,
                )
            self.assertEqual(result["onboardingRoot"], leaf_onboarding.as_posix())
            self.assertTrue((leaf_onboarding / "overview.index.json").is_file())
            self.assertFalse(
                (contract.memory_repo_path / "onboarding" / "overview.index.json").exists()
            )

            forged_leaf_scope = MemoryScope(
                repo_id=contract.repo_name,
                identity=MemoryScopeIdentity(
                    authority="leaf",
                    authority_path=contract.contract_path.as_posix(),
                    code_root=contract.code_worktree.as_posix(),
                    onboarding_root=(contract.memory_repo_path / "onboarding").as_posix(),
                ),
                code_root=contract.code_worktree,
                onboarding_root=contract.memory_repo_path / "onboarding",
                context=cast(CoordinationContext, None),
                contract=contract,
            )
            with (
                patch(
                    "agents_remember.worktrees.integration.configured_contract_authority.load_config",
                    return_value=configured,
                ),
                self.assertRaisesRegex(AuthorityError, "OFFICIAL memory repo"),
            ):
                _refuse_official_memory(scoped, scoped_repository, forged_leaf_scope)

            official_scope = MemoryScope(
                repo_id=contract.repo_name,
                identity=MemoryScopeIdentity(
                    authority="official",
                    authority_path=contract.memory_repo_path.as_posix(),
                    code_root=contract.code_repo_path.as_posix(),
                    onboarding_root=(contract.memory_repo_path / "onboarding").as_posix(),
                ),
                code_root=contract.code_repo_path,
                onboarding_root=contract.memory_repo_path / "onboarding",
                context=cast(CoordinationContext, None),
            )
            with self.assertRaisesRegex(AuthorityError, "OFFICIAL memory repo"):
                _refuse_official_memory(configured, configured_repository, official_scope)

            with self.assertRaisesRegex(AuthorityError, "requires a leaf contract_path"):
                route_index_refresh_tool(configured, repo_id=contract.repo_name, dry_run=False)


def test_quality_builds_only_missing_route_index_caches(tmp_path: Path) -> None:
    code = tmp_path / "code"
    init_repo(code)
    onboarding = tmp_path / "memory" / "onboarding"
    onboarding.mkdir(parents=True)
    (onboarding / "overview.md").write_text("# Root\n")
    (onboarding / "pkg").mkdir()
    (onboarding / "pkg/overview.md").write_text("# Package\n")
    stale = onboarding / "pkg/overview.index.json"
    stale.write_text("stale bytes")
    result = controller.build_route_indexes(
        code_root=code,
        onboarding_root=onboarding,
        repository="fixture",
        storage=StorageSettings(),
    )
    assert result.stale_indexes == ["pkg/overview.index.json"]
    assert (onboarding / "overview.index.json").is_file()
    assert stale.read_text() == "stale bytes"


def test_quality_reports_a_missing_cache_that_cannot_be_rendered(tmp_path: Path) -> None:
    code = tmp_path / "code"
    init_repo(code)
    onboarding = tmp_path / "onboarding"
    onboarding.mkdir()
    overview = onboarding / "overview.md"
    overview.write_text("# Root\n")
    original = Path.read_text

    def denied(path: Path, *args, **kwargs):
        if path == overview:
            raise PermissionError("actual overview read denied")
        return original(path, *args, **kwargs)

    with patch.object(Path, "read_text", denied):
        result = controller.build_route_indexes(
            code_root=code,
            onboarding_root=onboarding,
            repository="fixture",
            storage=StorageSettings(),
        )
    assert result.stale_indexes == ["overview.index.json"]
    assert result.built_indexes == []
    assert result.build_errors == [
        {"path": "overview.index.json", "error": "actual overview read denied"}
    ]
    assert not (onboarding / "overview.index.json").exists()


if __name__ == "__main__":
    unittest.main()
