from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.providers import provider_setup


class ProviderSetupTests(unittest.TestCase):
    def test_settings_path_requires_explicit_provider_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "requires an explicit settings path"):
                provider_setup.settings_path(root, None)

    def test_parser_requires_from_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parser = provider_setup.build_parser()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(["prepare", "--coordination-root", tmp])

    def test_run_provider_setup_accepts_typed_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_path = root / "provider-settings.json"
            settings_path.write_text(
                json.dumps({"contextProviders": {"enabled": False, "providers": {}}}),
                encoding="utf-8",
            )

            payload = provider_setup.run_provider_setup(
                provider_setup.ProviderSetupRequest(
                    action="prepare",
                    coordination_root=root,
                    settings_path=settings_path,
                    dry_run=True,
                )
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["settingsFile"], settings_path.as_posix())
            self.assertEqual(payload["enabled"]["grepai-memory"], False)
            self.assertEqual(payload["enabled"]["codegraphcontext-code"], False)
            self.assertEqual(payload["results"], [])

    def test_cgc_prepare_is_ok_when_seed_falls_back_to_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_path = root / "provider-settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "contextProviders": {
                            "enabled": True,
                            "providers": {
                                "codegraphcontext-code": {
                                    "enabled": True,
                                    "runtimeRoot": (root / "providers" / "runners").as_posix(),
                                    "venvRoot": (root / "providers" / "_venvs").as_posix(),
                                    "roots": [
                                        {
                                            "repoId": "repo-a",
                                            "path": (root / "repo-a").as_posix(),
                                        }
                                    ],
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            payload = provider_setup.run_provider_setup(
                provider_setup.ProviderSetupRequest(
                    action="prepare",
                    coordination_root=root,
                    settings_path=settings_path,
                    dry_run=True,
                )
            )

            self.assertTrue(payload["ok"])
            seed = next(result for result in payload["results"] if result["action"] == "seed")
            self.assertFalse(seed["ok"])
            self.assertTrue(
                any(result["action"] == "refresh-all" for result in payload["results"])
            )

    def test_rewrite_cgc_bundle_paths_rewrites_json_jsonl_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "source repo"
            target_root = root / "target repo"
            source_root.mkdir()
            target_root.mkdir()
            source_text = source_root.resolve().as_posix()

            source_bundle = root / "source.cgc"
            target_bundle = root / "target.cgc"
            with zipfile.ZipFile(source_bundle, "w") as bundle:
                bundle.writestr(
                    "metadata.json",
                    json.dumps(
                        {"repo_path": source_text, "nested": {"path": f"{source_text}/file.py"}}
                    ),
                )
                bundle.writestr(
                    "nodes.jsonl",
                    json.dumps({"path": f"{source_text}/file.py", "name": "file"}) + "\n",
                )
                bundle.writestr(
                    "edges.jsonl",
                    json.dumps(
                        {
                            "from_path": f"{source_text}/file.py",
                            "to_path": f"{source_text}/other.py",
                        }
                    )
                    + "\n",
                )
                bundle.writestr("README.md", f"Indexed repository: {source_text}\n")

            result = provider_setup.rewrite_cgc_bundle_paths(
                source_bundle, target_bundle, source_root, target_root
            )

            self.assertGreaterEqual(result["replacementCount"], 4)
            with zipfile.ZipFile(target_bundle, "r") as bundle:
                combined = "\n".join(
                    bundle.read(name).decode("utf-8") for name in bundle.namelist()
                )
            self.assertNotIn(source_root.resolve().as_posix(), combined)
            self.assertIn(target_root.resolve().as_posix(), combined)

    def test_isolated_cgc_settings_targets_worktree_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            isolated_root = root / "worktrees" / "agents-remember-md" / "task" / "provider-runtime"
            target_repo = root / "worktrees" / "agents-remember-md" / "task" / "code"
            args = argparse.Namespace(
                coordination_root=coordination_root,
                cgc_isolated_runtime_root=isolated_root,
                cgc_seed_target_repo_root=target_repo,
                cgc_seed_repo_id="agents-remember-md",
                cgc_isolated_container_name=None,
            )
            settings = {
                "contextProviders": {
                    "enabled": True,
                    "providers": {
                        "codegraphcontext-code": {
                            "enabled": True,
                            "runtimeRoot": "<coordination_root>/providers/runners/codegraphcontext",
                            "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                            "venvRoot": "<coordination_root>/providers/_venvs/codegraphcontext",
                            "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
                            "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
                            "stateFileTemplate": "<instanceRoot>/provider-state.json",
                            "backend": {
                                "image": "falkordb/falkordb:v4.18.7",
                                "runtimeRoot": "<coordination_root>/providers/data/codegraphcontext/falkordb",
                                "dataRoot": "<backendRuntimeRoot>/data",
                                "containerName": "ar-cgc-falkordb",
                            },
                            "roots": [
                                {
                                    "repoId": "agents-remember-md",
                                    "path": "<workspace_root>/agents-remember-md",
                                }
                            ],
                        }
                    },
                }
            }

            isolated = provider_setup.isolated_cgc_settings(args, settings)
            self.assertIsNotNone(isolated)
            cgc = isolated["contextProviders"]["providers"]["codegraphcontext-code"]

            self.assertEqual(
                cgc["roots"],
                [{"repoId": "agents-remember-md", "path": target_repo.resolve().as_posix()}],
            )
            self.assertEqual(
                cgc["runtimeRoot"],
                (isolated_root / "providers" / "runners" / "codegraphcontext").as_posix(),
            )
            self.assertEqual(
                cgc["venvRoot"],
                (coordination_root / "providers" / "_venvs" / "codegraphcontext").as_posix(),
            )
            self.assertEqual(
                cgc["backend"]["runtimeRoot"],
                (isolated_root / "providers" / "data" / "codegraphcontext" / "falkordb").as_posix(),
            )
            self.assertTrue(
                cgc["backend"]["containerName"].startswith("ar-cgc-falkordb-agents-remember-md-")
            )

    def test_run_command_forces_utf8_for_lifecycle_children(self) -> None:
        captured = {}
        original = provider_setup.subprocess.run

        def fake_run(command, **kwargs):
            captured.update(kwargs)
            return provider_setup.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        provider_setup.subprocess.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = provider_setup.run_command(
                    ["python", "-m", "agents_remember.providers.provider_lifecycle"],
                    cwd=Path(tmp),
                    timeout=1,
                    dry_run=False,
                )
        finally:
            provider_setup.subprocess.run = original

        self.assertTrue(result["ok"])
        self.assertEqual(captured["env"]["PYTHONUTF8"], "1")
        self.assertEqual(captured["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertIs(captured["stdin"], provider_setup.subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
