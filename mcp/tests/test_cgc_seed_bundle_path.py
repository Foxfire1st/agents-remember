"""OQ5: the cgc seed must write the rewritten bundle under the worktree's cgc instance root.

The `bundle import` runs inside the worktree's cgc runner, which only bind-mounts the cgc
instance runtime root (host:host) and is handed the bundle path verbatim. The caller's
`settings` resolve against the workspace coordination_root, landing the bundle under the
workspace runner root the worktree runner can't see -> "Bundle file not found" -> silent
fallback to a full re-index. `_seed_target_runtime_root` must resolve from the isolated
`--from-settings` the import itself uses.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

from agents_remember.providers.cgc import seed as seed_module
from agents_remember.providers.cgc.seed import _cgc_settings_path, _seed_target_runtime_root


class SeedTargetRuntimeRootTests(unittest.TestCase):
    def test_isolated_resolves_from_worktree_settings_not_passed_settings(self) -> None:
        isolated_settings = {"_isolated": True}
        instance_root = Path("/wt/pr/providers/runners/codegraphcontext/projects-x-ar/repo")
        args = cast(
            object,
            SimpleNamespace(
                cgc_isolated_runtime_root=Path("/wt/pr"),
                from_settings=Path("/wt/pr/settings/provider-settings.json"),
                coordination_root=Path("/ar"),
            ),
        )
        with (
            mock.patch.object(seed_module, "load_settings", return_value=isolated_settings) as ls,
            mock.patch.object(
                seed_module, "_seed_runtime_root", return_value=instance_root
            ) as srr,
        ):
            result = _seed_target_runtime_root(args, {"_workspace": True}, "repo")
        self.assertEqual(result, instance_root)
        ls.assert_called_once_with(args.from_settings)  # type: ignore[attr-defined]
        # resolved from the ISOLATED settings, never the caller's workspace `settings`
        srr.assert_called_once_with(args.coordination_root, isolated_settings, "repo")  # type: ignore[attr-defined]

    def test_isolated_falls_back_when_settings_unreadable(self) -> None:
        args = cast(
            object,
            SimpleNamespace(
                cgc_isolated_runtime_root=Path("/wt/pr"),
                from_settings=Path("/missing.json"),
                coordination_root=Path("/ar"),
            ),
        )
        with (
            mock.patch.object(seed_module, "load_settings", return_value=None),
            mock.patch.object(seed_module, "_seed_runtime_root", return_value={"skip": True}) as srr,
        ):
            result = _seed_target_runtime_root(args, {"_workspace": True}, "repo")
        self.assertEqual(result, {"skip": True})
        srr.assert_called_once_with(args.coordination_root, {"_workspace": True}, "repo")  # type: ignore[attr-defined]

    def test_settings_path_matches_the_import_priority_chain(self) -> None:
        # Must agree with cgc_extra_args: cgc_from_settings > provider_from_settings > from_settings.
        args = cast(
            object,
            SimpleNamespace(
                cgc_from_settings=Path("/cgc.json"),
                provider_from_settings=Path("/prov.json"),
                from_settings=Path("/from.json"),
            ),
        )
        self.assertEqual(_cgc_settings_path(args), Path("/cgc.json"))
        args2 = cast(object, SimpleNamespace(from_settings=Path("/from.json")))
        self.assertEqual(_cgc_settings_path(args2), Path("/from.json"))

    def test_non_isolated_uses_passed_settings(self) -> None:
        args = cast(
            object, SimpleNamespace(cgc_isolated_runtime_root=None, coordination_root=Path("/ar"))
        )
        with mock.patch.object(
            seed_module, "_seed_runtime_root", return_value={"skip": True}
        ) as srr:
            result = _seed_target_runtime_root(args, {"_workspace": True}, "repo")
        self.assertEqual(result, {"skip": True})
        srr.assert_called_once_with(args.coordination_root, {"_workspace": True}, "repo")  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
