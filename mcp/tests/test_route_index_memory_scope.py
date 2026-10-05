from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents_remember.application.memory_scope import MemoryScope, MemoryScopeIdentity
from agents_remember.application.memory_tools import (
    _refuse_official_memory,
    route_index_refresh_tool,
)
from agents_remember.errors import AuthorityError
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from test_worktree_support import open_external_contract_fixture


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
            with patch(
                "agents_remember.worktrees.integration.configured_contract_authority.load_config",
                return_value=configured,
            ), self.assertRaisesRegex(AuthorityError, "OFFICIAL memory repo"):
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


if __name__ == "__main__":
    unittest.main()
