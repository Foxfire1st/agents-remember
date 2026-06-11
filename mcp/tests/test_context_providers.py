from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.providers.context import (
    CGC_CGCIGNORE_PATCH_ID,
    CGC_DELETE_CONTAINS_ORIGINAL_SNIPPET,
    CGC_DELETE_NODE_ORIGINAL_SNIPPET,
    CGC_DELETE_PATCH_ID,
    CGC_DELETE_PATCH_MARKER,
    CGC_DELETE_PREFIX_ORIGINAL_SNIPPET,
    CGC_DELETE_REL_ORIGINAL_SNIPPET,
    CGC_DISCOVERY_EXTENSIONS_PATCH_ID,
    CGC_DISCOVERY_GENERIC_ORIGINAL_SNIPPET,
    CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_ID,
    CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_MARKER,
    CGC_GRAPH_BUILDER_GENERIC_ORIGINAL_SNIPPET,
    CGC_GRAPH_BUILDER_PARSER_ORIGINAL_SNIPPET,
    CGC_GRAPH_BUILDER_PRESCAN_ORIGINAL_SNIPPET,
    CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER,
    CGC_ORIGINAL_SNIPPET,
    CGC_PATCH_MARKER,
    CGC_REQUIREMENTS,
    CGC_VIZ_CLI_ROUTE_PATCH_ID,
    CGC_VIZ_CLI_ROUTE_PATCH_MARKER,
    CGC_VIZ_CLI_RUN_ORIGINAL_SNIPPET,
    CGC_VIZ_CLI_URL_ORIGINAL_SNIPPET,
    CGC_VIZ_REPO_QUERY_ORIGINAL_SNIPPET,
    CGC_VIZ_REPO_QUERY_PATCH_ID,
    CGC_VIZ_REPO_QUERY_PATCH_MARKER,
    CGC_VIZ_SERVER_FALLBACK_ORIGINAL_SNIPPET,
    CGC_VIZ_SERVER_GLOBAL_ORIGINAL_SNIPPET,
    CGC_VIZ_SERVER_RESPONSES_ORIGINAL_SNIPPET,
    CGC_VIZ_SERVER_ROUTE_PATCH_ID,
    CGC_VIZ_SERVER_ROUTE_PATCH_MARKER,
    CGC_VIZ_SERVER_RUN_ORIGINAL_SNIPPET,
    GREPAI_PIN,
    ContextProviderError,
    GrepaiMemoryRoot,
    apply_cgc_cgcignore_patch,
    apply_cgc_delete_patch,
    apply_cgc_discovery_extensions_patch,
    apply_cgc_graph_builder_extensions_patch,
    apply_cgc_viz_cli_route_patch,
    apply_cgc_viz_repo_query_patch,
    apply_cgc_viz_server_route_patch,
    assert_no_source_provider_artifacts,
    cgc_cgcignore_patch_applied,
    cgc_delete_patch_applied,
    cgc_discovery_extensions_patch_applied,
    cgc_graph_builder_extensions_patch_applied,
    cgc_runtime_layout,
    cgc_runtime_layout_from_provider_settings,
    cgc_viz_cli_route_patch_applied,
    cgc_viz_repo_query_patch_applied,
    cgc_viz_server_route_patch_applied,
    cleanup_cgc_runtime_artifacts,
    ensure_cgc_runtime_layout,
    ensure_grepai_requirements_file,
    ensure_grepai_root_gitignore,
    ensure_grepai_runtime_layout,
    grepai_runtime_layout,
    grepai_runtime_layout_from_provider_settings,
    grepai_workspace_config_text,
    read_provider_pin,
    source_provider_artifacts,
    stable_provider_id,
    to_container_path,
    write_grepai_workspace_config,
)


class ContextProviderLayoutTests(unittest.TestCase):
    def test_cgc_layout_uses_managed_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "repos" / "My App"
            layout = cgc_runtime_layout(
                coordination_root=root / "ar-coordination",
                repo_id="My App",
                code_repo_root=code_root,
            )

            self.assertEqual(layout.repo_id, "my-app")
            self.assertEqual(
                layout.runtime_root,
                root / "ar-coordination" / "providers" / "runners" / "codegraphcontext" / "my-app",
            )
            self.assertEqual(layout.cgc_root, layout.runtime_root / ".codegraphcontext")
            self.assertEqual(
                layout.backend_root,
                root / "ar-coordination" / "providers" / "data" / "codegraphcontext" / "falkordb",
            )
            self.assertEqual(layout.backend_data_root, layout.backend_root / "data")
            self.assertEqual(
                layout.requirements_file,
                root / "ar-coordination" / "providers" / "requirements" / "codegraphcontext.txt",
            )
            self.assertEqual(
                layout.patches_root,
                root / "ar-coordination" / "providers" / "patches" / "codegraphcontext",
            )

            env = layout.env()
            self.assertEqual(env["HOME"], (layout.run_root / "home").as_posix())
            self.assertEqual(env["CGC_RUNTIME_DB_TYPE"], "falkordb-remote")
            self.assertEqual(env["DEFAULT_DATABASE"], "falkordb-remote")
            self.assertEqual(env["FALKORDB_HOST"], "127.0.0.1")
            self.assertEqual(env["FALKORDB_PORT"], "6379")
            self.assertEqual(env["FALKORDB_GRAPH_NAME"], "cgc_my_app")
            self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
            if sys.platform == "win32":
                self.assertEqual(env["USERPROFILE"], str(layout.run_root / "home"))
            self.assertTrue(env["LOG_FILE_PATH"].endswith("/.codegraphcontext/logs/cgc.log"))

    def test_to_container_path_strips_windows_drive(self) -> None:
        self.assertEqual(to_container_path("C:/ew/foo"), "/ew/foo")
        self.assertEqual(to_container_path("C:\\ew\\foo"), "/ew/foo")
        self.assertEqual(to_container_path("D:/x"), "/x")
        self.assertEqual(to_container_path("C:/"), "/")
        # POSIX paths are returned unchanged (no-op on Linux/macOS).
        self.assertEqual(to_container_path("/ew/foo"), "/ew/foo")
        self.assertEqual(to_container_path(Path("/ew/foo")), "/ew/foo")

    def test_cgc_container_paths_are_driveless_posix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = cgc_runtime_layout(
                coordination_root=root / "ar-coordination",
                repo_id="My App",
                code_repo_root=root / "repos" / "My App",
            )

            self.assertEqual(
                layout.container_runtime_root, to_container_path(layout.runtime_root)
            )
            self.assertEqual(
                layout.container_code_repo_root, to_container_path(layout.code_repo_root)
            )
            # Container mount targets must never carry a Windows drive-letter colon,
            # which is what triggered Docker's "too many colons" mount error.
            self.assertNotIn(":", layout.container_runtime_root)
            self.assertNotIn(":", layout.container_code_repo_root)

    def test_cgc_container_env_is_posix_and_omits_windows_vars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = cgc_runtime_layout(
                coordination_root=root / "ar-coordination",
                repo_id="My App",
                code_repo_root=root / "repos" / "My App",
            )

            container_env = layout.env(for_container=True)

            # Path-valued entries are driveless POSIX paths inside the container.
            self.assertNotIn(":", container_env["HOME"])
            self.assertNotIn(":", container_env["LOG_FILE_PATH"])
            self.assertEqual(
                container_env["HOME"], to_container_path(layout.run_root / "home")
            )
            # Host-only Windows variables must not be injected into a Linux container.
            for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA"):
                self.assertNotIn(key, container_env)
            # Non-path values match the host environment.
            self.assertEqual(
                container_env["FALKORDB_GRAPH_NAME"],
                layout.env()["FALKORDB_GRAPH_NAME"],
            )

    def test_cgc_layout_ignores_host_falkordb_environment_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_root = root / "repos" / "My App"
            layout = cgc_runtime_layout(
                coordination_root=root / "ar-coordination",
                repo_id="My App",
                code_repo_root=code_root,
            )

            with patch.dict(
                os.environ,
                {"FALKORDB_HOST": "192.0.2.10", "FALKORDB_PORT": "26379"},
            ):
                env = layout.env()

            self.assertEqual(env["FALKORDB_HOST"], "127.0.0.1")
            self.assertEqual(env["FALKORDB_PORT"], "6379")

    def test_ensure_cgc_runtime_layout_writes_pinned_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = cgc_runtime_layout(
                coordination_root=root / "ar-coordination",
                repo_id="agents-remember",
                code_repo_root=root / "agents-remember",
                cgcignore_patterns=("tools/ffmpeg/",),
            )

            layout.code_repo_root.mkdir(parents=True)
            (layout.code_repo_root / ".gitignore").write_text(
                "/samples\n.tmp_drt\n", encoding="utf-8"
            )
            layout.env_file.parent.mkdir(parents=True)
            layout.env_file.write_text("DEFAULT_DATABASE=kuzudb\n", encoding="utf-8")

            ensure_cgc_runtime_layout(layout)

            self.assertEqual(
                layout.requirements_file.read_text(encoding="utf-8"),
                "\n".join(CGC_REQUIREMENTS) + "\n",
            )
            self.assertIn(
                "database: falkordb-remote", layout.config_file.read_text(encoding="utf-8")
            )
            env_text = layout.env_file.read_text(encoding="utf-8")
            self.assertIn("DEFAULT_DATABASE=falkordb-remote", env_text)
            self.assertNotIn("CGC_RUNTIME_DB_TYPE=", env_text)
            self.assertNotIn("FALKORDB_HOST=", env_text)
            self.assertNotIn("FALKORDB_PORT=", env_text)
            self.assertNotIn("FALKORDB_GRAPH_NAME=", env_text)
            self.assertNotIn("PYTHONIOENCODING=", env_text)
            self.assertNotIn("USERPROFILE=", env_text)
            cgcignore_text = layout.cgcignore_path.read_text(encoding="utf-8")
            self.assertTrue(cgcignore_text.startswith("# Managed by Agents Remember"))
            self.assertIn("# Inherited from source .gitignore", cgcignore_text)
            self.assertIn("/samples", cgcignore_text)
            self.assertIn(".tmp_drt", cgcignore_text)
            self.assertIn("tools/ffmpeg/", cgcignore_text)
            self.assertTrue(layout.logs_root.is_dir())
            self.assertTrue(layout.run_root.is_dir())

    def test_cleanup_cgc_runtime_artifacts_removes_stale_runtime_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            layout = cgc_runtime_layout(
                coordination_root=coordination_root,
                repo_id="agents-remember",
                code_repo_root=root / "agents-remember",
            )
            ensure_cgc_runtime_layout(layout)
            stale = layout.runtime_root.parent / "my-app" / ".codegraphcontext"
            stale.mkdir(parents=True)
            (stale / ".env").write_text("DEFAULT_DATABASE=falkordb-remote\n", encoding="utf-8")
            legacy_db = layout.cgc_root / "db"
            legacy_global = layout.cgc_root / "global"
            legacy_kuzu = layout.cgc_root / "kuzu"
            legacy_db.mkdir()
            legacy_global.mkdir()
            (legacy_db / "kuzu").write_text("legacy", encoding="utf-8")
            legacy_kuzu.write_text("legacy", encoding="utf-8")
            layout.backend_data_root.mkdir(parents=True, exist_ok=True)
            (layout.backend_data_root / "falkordb.rdb").write_text("keep", encoding="utf-8")

            removals = cleanup_cgc_runtime_artifacts([layout])

            self.assertFalse((layout.runtime_root.parent / "my-app").exists())
            self.assertFalse(legacy_db.exists())
            self.assertFalse(legacy_global.exists())
            self.assertFalse(legacy_kuzu.exists())
            self.assertTrue(layout.backend_data_root.exists())
            self.assertEqual(
                sorted(item["reason"] for item in removals),
                [
                    "legacy-embedded-db",
                    "legacy-embedded-global",
                    "legacy-embedded-kuzu",
                    "unconfigured-cgc-instance",
                ],
            )

    def test_cgc_layout_expands_provider_settings_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "my-app").mkdir()
            provider = {
                "runtimeRoot": "<coordination_root>/providers/runners/codegraphcontext",
                "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
                "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
                "stateFileTemplate": "<instanceRoot>/provider-state.json",
                "backend": {
                    "runtimeRoot": "<coordination_root>/providers/data/codegraphcontext/falkordb",
                    "dataRoot": "<backendRuntimeRoot>/data",
                },
            }
            layout = cgc_runtime_layout_from_provider_settings(
                coordination_root=root / "ar-coordination",
                provider_settings=provider,
                root_settings={
                    "repoId": "My App",
                    "path": str(root / "my-app"),
                    "cgcignorePatterns": ["vendor/generated/"],
                },
            )

            self.assertEqual(layout.repo_id, "my-app")
            self.assertEqual(
                layout.runtime_root,
                root / "ar-coordination" / "providers" / "runners" / "codegraphcontext" / "my-app",
            )
            self.assertEqual(
                layout.backend_data_root,
                root
                / "ar-coordination"
                / "providers"
                / "data"
                / "codegraphcontext"
                / "falkordb"
                / "data",
            )
            self.assertEqual(layout.state_file, layout.runtime_root / "provider-state.json")
            self.assertEqual(layout.cgcignore_patterns, ("vendor/generated/",))

    def test_cgc_provider_settings_auto_port_ignores_host_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "my-app").mkdir()
            provider = {
                "runtimeRoot": "<coordination_root>/providers/runners/codegraphcontext",
                "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
                "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
                "stateFileTemplate": "<instanceRoot>/provider-state.json",
                "backend": {
                    "runtimeRoot": "<coordination_root>/providers/data/codegraphcontext/falkordb",
                    "dataRoot": "<backendRuntimeRoot>/data",
                    "ports": {
                        "falkordb": {
                            "bindHost": "127.0.0.1",
                            "hostPort": "auto",
                        }
                    },
                },
                "processEnvTemplate": {
                    "FALKORDB_HOST": "<backend.ports.falkordb.bindHost>",
                    "FALKORDB_PORT": "<backend.ports.falkordb.hostPort>",
                },
            }

            with patch.dict(os.environ, {"FALKORDB_PORT": "26379"}):
                layout = cgc_runtime_layout_from_provider_settings(
                    coordination_root=root / "ar-coordination",
                    provider_settings=provider,
                    root_settings={"repoId": "My App", "path": str(root / "my-app")},
                )

            self.assertEqual(layout.env()["FALKORDB_HOST"], "127.0.0.1")
            self.assertEqual(layout.env()["FALKORDB_PORT"], "6379")

    def test_cgc_layout_rejects_missing_provider_settings_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = {
                "runtimeRoot": "<coordination_root>/providers/runners/codegraphcontext",
                "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
                "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
                "stateFileTemplate": "<instanceRoot>/provider-state.json",
                "backend": {},
            }
            with self.assertRaisesRegex(ContextProviderError, "does not exist"):
                cgc_runtime_layout_from_provider_settings(
                    coordination_root=root / "ar-coordination",
                    provider_settings=provider,
                    root_settings={"repoId": "missing-app", "path": str(root / "missing-app")},
                )

    def test_cgc_layout_rejects_removed_venv_root_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "my-app").mkdir()
            provider = {
                "runtimeRoot": "<coordination_root>/providers/runners/codegraphcontext",
                "venvRoot": "<coordination_root>/providers/_venvs/codegraphcontext",
            }
            with self.assertRaisesRegex(ContextProviderError, "may not define venvRoot"):
                cgc_runtime_layout_from_provider_settings(
                    coordination_root=root / "ar-coordination",
                    provider_settings=provider,
                    root_settings={"repoId": "my-app", "path": str(root / "my-app")},
                )

    def test_grepai_requirements_pin_is_created_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = ensure_grepai_requirements_file(Path(tmp))
            self.assertEqual(path.read_text(encoding="utf-8"), f"{GREPAI_PIN}\n")
            self.assertEqual(read_provider_pin(path, "grepai"), "0.35.0")

    def test_grepai_layout_uses_workspace_runtime_and_postgres_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_root = root / "ar-coordination" / "memory-repos" / "ar-my-app"
            memory_root.mkdir(parents=True)
            layout = grepai_runtime_layout(
                coordination_root=root / "ar-coordination",
                workspace_name="Agents Remember Memory",
                roots=(GrepaiMemoryRoot(project_id="ar-my-app", path=memory_root),),
            )

            self.assertEqual(layout.workspace_name, "agents-remember-memory")
            self.assertEqual(
                layout.runtime_root, root / "ar-coordination" / "providers" / "runners" / "grepai"
            )
            self.assertEqual(
                layout.workspace_config_file, layout.home_root / ".grepai" / "workspace.yaml"
            )
            self.assertEqual(
                layout.state_file, layout.runtime_root / "state" / "provider-state.json"
            )
            self.assertEqual(layout.logs_root, layout.runtime_root / "logs")
            self.assertEqual(
                layout.backend_root,
                root / "ar-coordination" / "providers" / "data" / "grepai" / "postgres",
            )
            self.assertEqual(layout.backend_data_root, layout.backend_root / "data")
            self.assertEqual(layout.env()["HOME"], layout.home_root.as_posix())
            self.assertEqual(layout.env()["XDG_STATE_HOME"], (layout.state_root / "xdg").as_posix())

    def test_grepai_layout_expands_provider_settings_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external_memory = root / "ar-coordination" / "memory-repos" / "ar-my-app"
            internal_memory = root / "my-app" / "ar-memory"
            external_memory.mkdir(parents=True)
            internal_memory.mkdir(parents=True)
            provider = {
                "workspace": "agents-remember-memory",
                "runtimeRoot": "<coordination_root>/providers/runners/grepai",
                "requirementsFile": "<coordination_root>/providers/requirements/grepai.txt",
                "stateFile": "<runtimeRoot>/state/provider-state.json",
                "watch": {
                    "logDir": "<coordination_root>/logs/providers/grepai",
                },
                "backend": {
                    "runtimeRoot": "<coordination_root>/providers/data/grepai/postgres",
                    "dataRoot": "<backendRuntimeRoot>/data",
                },
                "roots": [
                    {
                        "projectId": "ar-my-app",
                        "path": "<coordination_root>/memory-repos/ar-my-app",
                    },
                    {"projectId": "my-app-internal", "path": str(internal_memory)},
                ],
            }

            layout = grepai_runtime_layout_from_provider_settings(
                coordination_root=root / "ar-coordination",
                provider_settings=provider,
            )

            self.assertEqual(layout.workspace_name, "agents-remember-memory")
            self.assertEqual(
                [item.project_id for item in layout.roots], ["ar-my-app", "my-app-internal"]
            )
            # roots are indexed live, in place -- no mirror redirect
            self.assertEqual(layout.roots[0].path, external_memory)
            self.assertEqual(layout.roots[1].path, internal_memory)
            self.assertEqual(
                layout.backend_data_root,
                root / "ar-coordination" / "providers" / "data" / "grepai" / "postgres" / "data",
            )
            self.assertEqual(
                layout.logs_root, root / "ar-coordination" / "logs" / "providers" / "grepai"
            )

    def test_grepai_root_gitignore_ignores_working_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ar-coordination" / "memory-repos"
            with_existing = root / "ar-with-gitignore"
            without = root / "ar-without-gitignore"
            with_existing.mkdir(parents=True)
            without.mkdir(parents=True)
            (with_existing / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
            roots = (
                GrepaiMemoryRoot(project_id="ar-with-gitignore", path=with_existing),
                GrepaiMemoryRoot(project_id="ar-without-gitignore", path=without),
            )

            updated = ensure_grepai_root_gitignore(roots)

            # appends to an existing .gitignore, creates a fresh one otherwise
            self.assertEqual(
                {entry["projectId"] for entry in updated},
                {"ar-with-gitignore", "ar-without-gitignore"},
            )
            self.assertEqual(
                (with_existing / ".gitignore").read_text(encoding="utf-8"), "*.pyc\n.grepai/\n"
            )
            self.assertEqual((without / ".gitignore").read_text(encoding="utf-8"), ".grepai/\n")

    def test_grepai_root_gitignore_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_root = Path(tmp) / "memory-repos" / "ar-my-app"
            memory_root.mkdir(parents=True)
            roots = (GrepaiMemoryRoot(project_id="ar-my-app", path=memory_root),)

            first = ensure_grepai_root_gitignore(roots)
            second = ensure_grepai_root_gitignore(roots)

            self.assertEqual(first[0]["projectId"], "ar-my-app")
            self.assertEqual(second, [])
            self.assertEqual(
                (memory_root / ".gitignore").read_text(encoding="utf-8"), ".grepai/\n"
            )

    def test_grepai_workspace_config_is_provider_owned_and_names_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_root = root / "ar-coordination" / "memory-repos" / "ar-my-app"
            memory_root.mkdir(parents=True)
            layout = grepai_runtime_layout(
                coordination_root=root / "ar-coordination",
                roots=(GrepaiMemoryRoot(project_id="ar-my-app", path=memory_root),),
            )

            ensure_grepai_runtime_layout(layout)
            write_grepai_workspace_config(
                layout,
                dsn="postgres://grepai:grepai@127.0.0.1:5432/grepai?sslmode=disable",
                embedder_settings={"provider": "ollama", "model": "nomic-embed-text"},
            )

            text = layout.workspace_config_file.read_text(encoding="utf-8")
            self.assertIn("workspaces:", text)
            self.assertIn("store:", text)
            self.assertIn("backend: postgres", text)
            self.assertIn('endpoint: "http://localhost:11434"', text)
            self.assertIn("dimensions: 768", text)
            self.assertIn('name: "ar-my-app"', text)
            self.assertIn(f'path: "{memory_root.as_posix()}"', text)
            self.assertTrue(layout.workspace_config_file.is_relative_to(layout.runtime_root))
            self.assertFalse((memory_root / ".grepai").exists())
            self.assertEqual(
                grepai_workspace_config_text(
                    layout=layout,
                    dsn="postgres://grepai:grepai@127.0.0.1:5432/grepai?sslmode=disable",
                    embedder_settings={"provider": "ollama", "model": "nomic-embed-text"},
                ),
                text,
            )

    def test_detects_forbidden_source_provider_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code_root = Path(tmp) / "repo"
            code_root.mkdir()
            self.assertEqual(source_provider_artifacts(code_root), [])

            (code_root / ".cgcignore").write_text("# generated\n", encoding="utf-8")
            self.assertEqual(
                [path.name for path in source_provider_artifacts(code_root)], [".cgcignore"]
            )

            with self.assertRaises(ContextProviderError):
                assert_no_source_provider_artifacts(code_root)

    def test_cgc_cgcignore_patch_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cgcignore.py"
            target.write_text(f"def build_ignore_spec():\n{CGC_ORIGINAL_SNIPPET}", encoding="utf-8")

            self.assertTrue(apply_cgc_cgcignore_patch(target))
            self.assertTrue(cgc_cgcignore_patch_applied(target))
            self.assertIn(CGC_PATCH_MARKER, target.read_text(encoding="utf-8"))
            self.assertIn(
                "if not local_cgcignore_path.exists():", target.read_text(encoding="utf-8")
            )
            self.assertFalse(apply_cgc_cgcignore_patch(target))

    def test_cgc_delete_patch_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "writer.py"
            target.write_text(
                "\n".join(
                    [
                        "def delete_repository_from_graph(self, repo_path):",
                        CGC_DELETE_PREFIX_ORIGINAL_SNIPPET.rstrip("\n"),
                        CGC_DELETE_REL_ORIGINAL_SNIPPET.rstrip("\n"),
                        CGC_DELETE_CONTAINS_ORIGINAL_SNIPPET.rstrip("\n"),
                        CGC_DELETE_NODE_ORIGINAL_SNIPPET.rstrip("\n"),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertTrue(apply_cgc_delete_patch(target))
            text = target.read_text(encoding="utf-8")
            self.assertTrue(cgc_delete_patch_applied(target))
            self.assertIn(CGC_DELETE_PATCH_MARKER, text)
            self.assertIn('path_prefix_backslash = repo_path_str + "\\\\"', text)
            self.assertIn("prefix_backslash=path_prefix_backslash", text)
            self.assertFalse(apply_cgc_delete_patch(target))

    def test_cgc_graph_builder_extensions_patch_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph_builder.py"
            target.write_text(
                "\n".join(
                    [
                        "class GraphBuilder:",
                        "    def __init__(self):",
                        CGC_GRAPH_BUILDER_PARSER_ORIGINAL_SNIPPET.rstrip("\n"),
                        CGC_GRAPH_BUILDER_GENERIC_ORIGINAL_SNIPPET.rstrip("\n"),
                        "    def _pre_scan_for_imports(self, files):",
                        CGC_GRAPH_BUILDER_PRESCAN_ORIGINAL_SNIPPET.rstrip("\n"),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertTrue(apply_cgc_graph_builder_extensions_patch(target))
            text = target.read_text(encoding="utf-8")
            self.assertTrue(cgc_graph_builder_extensions_patch_applied(target))
            self.assertIn(CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_MARKER, text)
            self.assertIn(CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER, text)
            self.assertIn('".cc": "cpp"', text)
            self.assertIn('".td",', text)
            self.assertIn(
                "for cpp_ext in ('.cpp', '.cc', '.cxx', '.c++', '.C', '.h', '.hpp', '.hh')", text
            )
            self.assertFalse(apply_cgc_graph_builder_extensions_patch(target))

    def test_cgc_discovery_extensions_patch_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "discovery.py"
            target.write_text(CGC_DISCOVERY_GENERIC_ORIGINAL_SNIPPET, encoding="utf-8")

            self.assertTrue(apply_cgc_discovery_extensions_patch(target))
            text = target.read_text(encoding="utf-8")
            self.assertTrue(cgc_discovery_extensions_patch_applied(target))
            self.assertIn(CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER, text)
            self.assertIn('".td",', text)
            self.assertFalse(apply_cgc_discovery_extensions_patch(target))

    def test_cgc_viz_repo_query_patch_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "server.py"
            target.write_text(
                f"def get_graph():\n{CGC_VIZ_REPO_QUERY_ORIGINAL_SNIPPET}", encoding="utf-8"
            )

            self.assertTrue(apply_cgc_viz_repo_query_patch(target))
            text = target.read_text(encoding="utf-8")
            self.assertTrue(cgc_viz_repo_query_patch_applied(target))
            self.assertIn(CGC_VIZ_REPO_QUERY_PATCH_MARKER, text)
            self.assertIn("WITH repo_path, repo_prefix, node LIMIT 3000", text)
            self.assertIn("LIMIT 5000", text)
            self.assertFalse(apply_cgc_viz_repo_query_patch(target))

    def test_cgc_viz_server_route_patch_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "server.py"
            target.write_text(
                CGC_VIZ_SERVER_RESPONSES_ORIGINAL_SNIPPET
                + "from typing import Optional\n"
                + CGC_VIZ_SERVER_GLOBAL_ORIGINAL_SNIPPET
                + "async def spa_fallback(request, full_path: str):\n"
                + CGC_VIZ_SERVER_FALLBACK_ORIGINAL_SNIPPET
                + "\n"
                + CGC_VIZ_SERVER_RUN_ORIGINAL_SNIPPET,
                encoding="utf-8",
            )

            self.assertTrue(apply_cgc_viz_server_route_patch(target))
            text = target.read_text(encoding="utf-8")
            self.assertTrue(cgc_viz_server_route_patch_applied(target))
            self.assertIn(CGC_VIZ_SERVER_ROUTE_PATCH_MARKER, text)
            self.assertIn("JSONResponse", text)
            self.assertIn("RedirectResponse(_default_route)", text)
            self.assertIn('full_path.startswith("api/")', text)
            self.assertIn("default_route: Optional[str] = None", text)
            self.assertFalse(apply_cgc_viz_server_route_patch(target))

    def test_cgc_viz_cli_route_patch_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cli_helpers.py"
            target.write_text(
                "def visualize_helper():\n"
                + CGC_VIZ_CLI_URL_ORIGINAL_SNIPPET
                + "    try:\n"
                + CGC_VIZ_CLI_RUN_ORIGINAL_SNIPPET,
                encoding="utf-8",
            )

            self.assertTrue(apply_cgc_viz_cli_route_patch(target))
            text = target.read_text(encoding="utf-8")
            self.assertTrue(cgc_viz_cli_route_patch_applied(target))
            self.assertIn(CGC_VIZ_CLI_ROUTE_PATCH_MARKER, text)
            self.assertIn('default_route = f"/explore?{query_string}"', text)
            self.assertIn('visualization_url = f"{backend_url}{default_route}"', text)
            self.assertIn("default_route=default_route", text)
            self.assertFalse(apply_cgc_viz_cli_route_patch(target))

    def test_patch_rejects_unexpected_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cgcignore.py"
            target.write_text("def build_ignore_spec():\n    pass\n", encoding="utf-8")

            with self.assertRaises(ContextProviderError):
                apply_cgc_cgcignore_patch(target)

    def test_stable_provider_id_never_returns_empty(self) -> None:
        self.assertEqual(stable_provider_id("TensorFlow++ Repo"), "tensorflow-repo")
        self.assertEqual(stable_provider_id("   "), "repo")

    def test_patch_id_is_stable(self) -> None:
        self.assertEqual(
            CGC_CGCIGNORE_PATCH_ID, "codegraphcontext-0.4.10-cgcignore-runtime-root-v2"
        )
        self.assertEqual(CGC_DELETE_PATCH_ID, "codegraphcontext-0.4.10-windows-delete-prefix-v1")
        self.assertEqual(
            CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_ID, "codegraphcontext-0.4.10-cpp-cc-td-extensions-v1"
        )
        self.assertEqual(
            CGC_DISCOVERY_EXTENSIONS_PATCH_ID, "codegraphcontext-0.4.10-td-generic-discovery-v1"
        )
        self.assertEqual(CGC_VIZ_REPO_QUERY_PATCH_ID, "codegraphcontext-0.4.10-viz-repo-query-v1")
        self.assertEqual(
            CGC_VIZ_SERVER_ROUTE_PATCH_ID, "codegraphcontext-0.4.10-viz-server-route-v1"
        )
        self.assertEqual(CGC_VIZ_CLI_ROUTE_PATCH_ID, "codegraphcontext-0.4.10-viz-cli-route-v1")


if __name__ == "__main__":
    unittest.main()
