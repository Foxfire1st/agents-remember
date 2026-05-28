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
from agents_remember.providers.identity import provider_instance_id


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
            self.assertEqual(payload["ready"], True)
            self.assertEqual(payload["state"], "ok")
            self.assertEqual(payload["failedPhases"], [])
            self.assertEqual(payload["results"], [])
            self.assertFalse(payload["setupSummary"]["written"])
            self.assertEqual(payload["setupSummary"]["reason"], "dry-run")

    def test_run_provider_setup_writes_compact_summary(self) -> None:
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
                    dry_run=False,
                )
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["state"], "ok")
            summary = payload["setupSummary"]
            self.assertTrue(summary["written"])
            self.assertEqual(
                summary["last"],
                (root / "logs" / "providers" / "setup" / "last-prepare.json").as_posix(),
            )
            summary_payload = json.loads(Path(summary["last"]).read_text(encoding="utf-8"))
            self.assertEqual(summary_payload["kind"], "provider-setup-summary")
            self.assertEqual(summary_payload["state"], "ok")
            self.assertEqual(summary_payload["results"], [])

    def test_run_provider_setup_reports_recovered_final_status(self) -> None:
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
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            original = provider_setup._action_results

            def fake_action_results(args, settings):
                return [
                    {
                        "provider": "codegraphcontext",
                        "action": "refresh-all",
                        "ok": False,
                        "json": {
                            "provider": "codegraphcontext",
                            "action": "refresh-all",
                            "ok": False,
                            "results": [
                                {
                                    "provider": "codegraphcontext",
                                    "action": "refresh",
                                    "repoId": "repo-a",
                                    "ok": False,
                                    "error": "index refresh failed",
                                }
                            ],
                        },
                    },
                    {
                        "provider": "watchers",
                        "action": "status",
                        "ok": True,
                        "payload": {
                            "provider": "watchers",
                            "action": "status",
                            "ok": True,
                            "partial": False,
                            "results": [
                                {
                                    "provider": "codegraphcontext",
                                    "ok": True,
                                    "results": [
                                        {
                                            "repoId": "repo-a",
                                            "ok": True,
                                            "process": {"alive": True},
                                        }
                                    ],
                                }
                            ],
                        },
                    },
                ]

            provider_setup._action_results = fake_action_results
            try:
                payload = provider_setup.run_provider_setup(
                    provider_setup.ProviderSetupRequest(
                        action="prepare",
                        coordination_root=root,
                        settings_path=settings_path,
                        dry_run=False,
                    )
                )
            finally:
                provider_setup._action_results = original

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["ready"], True)
            self.assertEqual(payload["state"], "ready-with-failed-phases")
            self.assertEqual(payload["resultCounts"]["failed"], 1)
            self.assertEqual(payload["failedPhases"][0]["action"], "refresh-all")
            self.assertEqual(
                payload["failedPhases"][0]["payload"]["results"][0]["repoId"],
                "repo-a",
            )
            self.assertEqual(payload["finalStatus"]["ok"], True)
            self.assertEqual(
                payload["finalStatus"]["results"][0]["results"][0]["process"],
                {"alive": True},
            )
            summary_payload = json.loads(
                Path(payload["setupSummary"]["last"]).read_text(encoding="utf-8")
            )
            self.assertEqual(summary_payload["state"], "ready-with-failed-phases")
            self.assertNotIn("stdout", json.dumps(summary_payload))

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

    def test_cgc_seed_uses_container_mounted_runtime_bundle_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_coordination = root / "source" / "ar-coordination"
            target_coordination = root / "target" / "ar-coordination"
            source_repo = root / "source" / "repo-a"
            target_repo = root / "target" / "repo-a"
            source_repo.mkdir(parents=True)
            target_repo.mkdir(parents=True)
            source_settings = root / "source-settings.json"
            source_settings.write_text(
                json.dumps(
                    {
                        "contextProviders": {
                            "enabled": True,
                            "providers": {
                                "codegraphcontext-code": {
                                    "enabled": True,
                                    "runtimeRoot": (
                                        source_coordination / "providers" / "runners" / "cgc"
                                    ).as_posix(),
                                    "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                                    "roots": [
                                        {
                                            "repoId": "repo-a",
                                            "path": source_repo.as_posix(),
                                        }
                                    ],
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            target_settings = {
                "contextProviders": {
                    "enabled": True,
                    "providers": {
                        "codegraphcontext-code": {
                            "enabled": True,
                            "runtimeRoot": (
                                target_coordination / "providers" / "runners" / "cgc"
                            ).as_posix(),
                            "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                            "roots": [
                                {
                                    "repoId": "repo-a",
                                    "path": target_repo.as_posix(),
                                }
                            ],
                        }
                    },
                }
            }
            args = argparse.Namespace(
                coordination_root=target_coordination,
                from_settings=root / "target-settings.json",
                cgc_from_settings=root / "target-settings.json",
                provider_from_settings=None,
                cgc_seed_source_coordination_root=source_coordination,
                cgc_seed_source_from_settings=source_settings,
                cgc_seed_repo_id="repo-a",
                cgc_seed_source_repo_id=None,
                cgc_seed_source_repo_root=None,
                cgc_seed_target_repo_root=None,
                cgc_seed_bundle_dir=None,
                cgc_seed_allow_commit_mismatch=False,
                cgc_seed_allow_same_coordination_root=False,
                cgc_isolated_runtime_root=None,
                timeout=30,
                dry_run=True,
            )

            result = provider_setup.cgc_seed_bundle(args, target_settings)

        self.assertTrue(result["ok"])
        export_command = " ".join(result["export"]["command"])
        load_command = " ".join(result["load"]["command"])
        self.assertIn("bundle import", load_command)
        self.assertIn(
            (source_coordination / "providers" / "runners" / "cgc" / "repo-a" / "seed-bundles").as_posix(),
            export_command,
        )
        self.assertIn(
            (target_coordination / "providers" / "runners" / "cgc" / "repo-a" / "seed-bundles").as_posix(),
            load_command,
        )

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
            instance_id = provider_instance_id(
                "worktree",
                isolated_root,
                workspace_name=coordination_root.parent.name,
            )

            self.assertEqual(
                cgc["roots"],
                [{"repoId": "agents-remember-md", "path": target_repo.resolve().as_posix()}],
            )
            self.assertEqual(cgc["instance"]["id"], instance_id)
            self.assertEqual(cgc["instance"]["scope"], "worktree")
            self.assertEqual(
                cgc["instance"]["labels"]["agents-remember.provider"],
                "codegraphcontext-code",
            )
            self.assertEqual(
                cgc["runtimeRoot"],
                (
                    isolated_root
                    / "providers"
                    / "runners"
                    / "codegraphcontext"
                    / instance_id
                ).as_posix(),
            )
            self.assertEqual(
                cgc["watch"]["logFileTemplate"],
                (
                    isolated_root
                    / "logs"
                    / "providers"
                    / "codegraphcontext"
                    / instance_id
                    / "<repoId>"
                    / "watch.log"
                ).as_posix(),
            )
            self.assertEqual(
                cgc["runtime"]["composeProject"],
                f"agents-remember-cgc-{instance_id}",
            )
            self.assertEqual(
                cgc["runtime"]["runner"]["containerNameTemplate"],
                f"ar-cgc-watcher-{instance_id}-<repoId>",
            )
            self.assertEqual(
                cgc["backend"]["runtimeRoot"],
                (
                    isolated_root
                    / "providers"
                    / "data"
                    / "codegraphcontext"
                    / instance_id
                    / "falkordb"
                ).as_posix(),
            )
            self.assertEqual(
                cgc["backend"]["containerName"],
                f"ar-cgc-falkordb-{instance_id}-agents-remember-md",
            )
            self.assertEqual(
                cgc["backend"]["network"]["name"],
                f"ar-cgc-code-{instance_id}",
            )

    def test_isolated_grepai_settings_swaps_only_active_memory_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            isolated_root = root / "worktrees" / "agents-remember-md" / "task" / "provider-runtime"
            source_memory = root / "ar-coordination" / "memory-repos" / "ar-agents-remember-md"
            target_memory = root / "worktrees" / "agents-remember-md" / "task" / "memory"
            other_memory = root / "ar-coordination" / "memory-repos" / "ar-other-repo"
            args = argparse.Namespace(
                coordination_root=coordination_root,
                grepai_isolated_runtime_root=isolated_root,
                grepai_seed_project_id="agents-remember-md",
                grepai_seed_target_memory_root=target_memory,
                grepai_allow_missing_roots=True,
            )
            settings = {
                "contextProviders": {
                    "enabled": True,
                    "providers": {
                        "grepai-memory": {
                            "enabled": True,
                            "workspace": "agents-remember-memory",
                            "runtimeRoot": "<coordination_root>/providers/runners/grepai",
                            "roots": [
                                {
                                    "projectId": "agents-remember-md",
                                    "path": source_memory.as_posix(),
                                },
                                {"projectId": "other-repo", "path": other_memory.as_posix()},
                            ],
                        }
                    },
                }
            }

            isolated = provider_setup.isolated_grepai_settings(args, settings)
            self.assertIsNotNone(isolated)
            grepai = isolated["contextProviders"]["providers"]["grepai-memory"]
            instance_id = provider_instance_id(
                "worktree",
                isolated_root,
                workspace_name=coordination_root.parent.name,
            )

            self.assertEqual(grepai["instance"]["id"], instance_id)
            self.assertEqual(grepai["instance"]["scope"], "worktree")
            self.assertEqual(grepai["allowMissingRoots"], True)
            self.assertEqual(
                grepai["workspace"],
                f"agents-remember-memory-{instance_id}",
            )
            self.assertEqual(
                grepai["runtimeRoot"],
                (isolated_root / "providers" / "runners" / "grepai" / instance_id).as_posix(),
            )
            self.assertEqual(
                grepai["watch"]["logDir"],
                (isolated_root / "logs" / "providers" / "grepai" / instance_id).as_posix(),
            )
            self.assertEqual(
                grepai["runtime"]["runner"]["containerName"],
                f"ar-grepai-watcher-{instance_id}",
            )
            roots = {root["projectId"]: root["path"] for root in grepai["roots"]}
            self.assertEqual(roots["agents-remember-md"], target_memory.resolve().as_posix())
            self.assertEqual(roots["other-repo"], other_memory.as_posix())

    def test_run_provider_setup_warm_starts_isolated_grepai_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            source_memory = coordination_root / "memory-repos" / "ar-agents-remember-md"
            other_memory = coordination_root / "memory-repos" / "ar-other-repo"
            target_memory = root / "worktrees" / "agents-remember-md" / "task" / "memory"
            isolated_root = root / "worktrees" / "agents-remember-md" / "task" / "provider-runtime"
            source_memory.mkdir(parents=True)
            other_memory.mkdir(parents=True)
            settings_path = root / "provider-settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "contextProviders": {
                            "enabled": True,
                            "providers": {
                                "grepai-memory": {
                                    "enabled": True,
                                    "workspace": "agents-remember-memory",
                                    "runtimeRoot": (
                                        coordination_root / "providers" / "runners" / "grepai"
                                    ).as_posix(),
                                    "roots": [
                                        {
                                            "projectId": "agents-remember-md",
                                            "path": source_memory.as_posix(),
                                        },
                                        {
                                            "projectId": "other-repo",
                                            "path": other_memory.as_posix(),
                                        },
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
                    coordination_root=coordination_root,
                    settings_path=settings_path,
                    dry_run=True,
                    grepai_seed=provider_setup.GrepaiSeedOptions(
                        source_coordination_root=coordination_root,
                        source_settings_path=settings_path,
                        project_id="agents-remember-md",
                        target_memory_root=target_memory,
                    ),
                    grepai_isolated=provider_setup.IsolatedGrepaiOptions(
                        runtime_root=isolated_root,
                        project_id="agents-remember-md",
                        target_memory_root=target_memory,
                        allow_missing_roots=True,
                    ),
                )
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(
                payload["isolatedProviderSettings"]["providers"],
                ["grepai-memory"],
            )
            self.assertNotIn("isolatedCgcSettings", payload)
            self.assertNotIn("isolatedGrepaiSettings", payload)
            clone = next(result for result in payload["results"] if result["action"] == "clone-db")
            self.assertTrue(clone["ok"])
            self.assertEqual(clone["strategy"], "database-clone-active-project-reconcile")
            self.assertIn("pg_dump", clone["clone"]["commands"]["dump"])
            self.assertNotIn("--single-transaction", clone["clone"]["commands"]["dump"])
            target_settings = payload["isolatedProviderSettings"]["settings"]
            grepai = target_settings["contextProviders"]["providers"]["grepai-memory"]
            roots = {root["projectId"]: root["path"] for root in grepai["roots"]}
            self.assertEqual(roots["agents-remember-md"], target_memory.resolve().as_posix())
            self.assertEqual(roots["other-repo"], other_memory.as_posix())

    def test_grepai_clone_uses_non_isolated_target_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_coordination = root / "source" / "ar-coordination"
            target_coordination = root / "target" / "ar-coordination"
            source_memory = source_coordination / "memory-repos" / "ar-repo-a"
            target_memory = target_coordination / "memory-repos" / "ar-repo-a"
            source_memory.mkdir(parents=True)
            target_memory.mkdir(parents=True)
            source_settings = root / "source-settings.json"
            target_settings = root / "target-settings.json"
            source_settings.write_text(
                json.dumps(
                    {
                        "contextProviders": {
                            "enabled": True,
                            "providers": {
                                "grepai-memory": {
                                    "enabled": True,
                                    "workspace": "source-memory",
                                    "runtimeRoot": (
                                        source_coordination / "providers" / "runners" / "grepai"
                                    ).as_posix(),
                                    "roots": [
                                        {
                                            "projectId": "repo-a",
                                            "path": source_memory.as_posix(),
                                        }
                                    ],
                                    "backend": {
                                        "containerName": "ar-grepai-postgres-source",
                                    },
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            target_settings.write_text(
                json.dumps(
                    {
                        "contextProviders": {
                            "enabled": True,
                            "providers": {
                                "grepai-memory": {
                                    "enabled": True,
                                    "workspace": "target-memory",
                                    "runtimeRoot": (
                                        target_coordination / "providers" / "runners" / "grepai"
                                    ).as_posix(),
                                    "roots": [
                                        {
                                            "projectId": "repo-a",
                                            "path": target_memory.as_posix(),
                                        }
                                    ],
                                    "backend": {
                                        "containerName": "ar-grepai-postgres-target",
                                    },
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
                    coordination_root=target_coordination,
                    settings_path=target_settings,
                    dry_run=True,
                    grepai_seed=provider_setup.GrepaiSeedOptions(
                        source_coordination_root=source_coordination,
                        source_settings_path=source_settings,
                        project_id="repo-a",
                        target_memory_root=target_memory,
                    ),
                )
            )

        self.assertTrue(payload["ok"])
        clone = next(result for result in payload["results"] if result["action"] == "clone-db")
        self.assertTrue(clone["ok"])
        self.assertEqual(clone["targetSettingsFile"], target_settings.resolve().as_posix())

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
                    ["python", "-m", "agents_remember.providers.lifecycle"],
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
