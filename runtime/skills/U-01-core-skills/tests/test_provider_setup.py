from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[3]
PROVIDER_SETUP_PATH = RUNTIME_ROOT / "scripts" / "provider-setup.py"
SPEC = importlib.util.spec_from_file_location("provider_setup", PROVIDER_SETUP_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Unable to load provider setup module from {PROVIDER_SETUP_PATH}")
provider_setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provider_setup)


class ProviderSetupTests(unittest.TestCase):
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
                    json.dumps({"repo_path": source_text, "nested": {"path": f"{source_text}/file.py"}}),
                )
                bundle.writestr(
                    "nodes.jsonl",
                    json.dumps({"path": f"{source_text}/file.py", "name": "file"}) + "\n",
                )
                bundle.writestr(
                    "edges.jsonl",
                    json.dumps({"from_path": f"{source_text}/file.py", "to_path": f"{source_text}/other.py"}) + "\n",
                )
                bundle.writestr("README.md", f"Indexed repository: {source_text}\n")

            result = provider_setup.rewrite_cgc_bundle_paths(source_bundle, target_bundle, source_root, target_root)

            self.assertGreaterEqual(result["replacementCount"], 4)
            with zipfile.ZipFile(target_bundle, "r") as bundle:
                combined = "\n".join(bundle.read(name).decode("utf-8") for name in bundle.namelist())
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
                            "runtimeRoot": "<coordination_root>/providers/codegraphcontext",
                            "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                            "venvRoot": "<coordination_root>/providers/_venvs/codegraphcontext",
                            "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
                            "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
                            "stateFileTemplate": "<instanceRoot>/provider-state.json",
                            "backend": {
                                "image": "falkordb/falkordb:v4.18.7",
                                "runtimeRoot": "<coordination_root>/provider-data/codegraphcontext/falkordb",
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

            self.assertEqual(cgc["roots"], [{"repoId": "agents-remember-md", "path": target_repo.resolve().as_posix()}])
            self.assertEqual(cgc["runtimeRoot"], (isolated_root / "providers" / "codegraphcontext").as_posix())
            self.assertEqual(cgc["venvRoot"], (coordination_root / "providers" / "_venvs" / "codegraphcontext").as_posix())
            self.assertEqual(
                cgc["backend"]["runtimeRoot"],
                (isolated_root / "provider-data" / "codegraphcontext" / "falkordb").as_posix(),
            )
            self.assertTrue(cgc["backend"]["containerName"].startswith("ar-cgc-falkordb-agents-remember-md-"))


if __name__ == "__main__":
    unittest.main()
