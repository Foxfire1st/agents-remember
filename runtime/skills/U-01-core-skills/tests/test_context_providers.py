from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = CORE_ROOT / "_shared"
sys.path.insert(0, str(SHARED_ROOT))

from agents_remember.context_providers import (  # noqa: E402
    CGC_CGCIGNORE_PATCH_ID,
    CGC_DELETE_PATCH_ID,
    CGC_DISCOVERY_EXTENSIONS_PATCH_ID,
    CGC_DELETE_PATCH_MARKER,
    CGC_DELETE_PREFIX_ORIGINAL_SNIPPET,
    CGC_DELETE_REL_ORIGINAL_SNIPPET,
    CGC_DELETE_CONTAINS_ORIGINAL_SNIPPET,
    CGC_DELETE_NODE_ORIGINAL_SNIPPET,
    CGC_DISCOVERY_GENERIC_ORIGINAL_SNIPPET,
    CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_ID,
    CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_MARKER,
    CGC_GRAPH_BUILDER_GENERIC_ORIGINAL_SNIPPET,
    CGC_GRAPH_BUILDER_PARSER_ORIGINAL_SNIPPET,
    CGC_GRAPH_BUILDER_PRESCAN_ORIGINAL_SNIPPET,
    CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER,
    CGC_PATCH_MARKER,
    CGC_PIN,
    CGC_REQUIREMENTS,
    CGC_ORIGINAL_SNIPPET,
    ContextProviderError,
    GREPAI_PIN,
    apply_cgc_cgcignore_patch,
    apply_cgc_delete_patch,
    apply_cgc_discovery_extensions_patch,
    apply_cgc_graph_builder_extensions_patch,
    assert_no_source_provider_artifacts,
    cgc_cgcignore_patch_applied,
    cgc_delete_patch_applied,
    cgc_discovery_extensions_patch_applied,
    cgc_graph_builder_extensions_patch_applied,
    cleanup_cgc_runtime_artifacts,
    cgc_runtime_layout,
    cgc_runtime_layout_from_provider_settings,
    ensure_cgc_runtime_layout,
    ensure_grepai_requirements_file,
    find_cgc_cgcignore_module,
    find_cgc_discovery_module,
    find_cgc_graph_builder_module,
    find_cgc_writer_module,
    read_provider_pin,
    source_provider_artifacts,
    stable_provider_id,
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
                root / "ar-coordination" / "providers" / "codegraphcontext" / "my-app",
            )
            self.assertEqual(layout.cgc_root, layout.runtime_root / ".codegraphcontext")
            self.assertEqual(
                layout.backend_root,
                root / "ar-coordination" / "provider-data" / "codegraphcontext" / "falkordb",
            )
            self.assertEqual(layout.backend_data_root, layout.backend_root / "data")
            self.assertEqual(layout.venv_root, root / "ar-coordination" / "providers" / "_venvs" / "codegraphcontext")
            self.assertEqual(
                layout.requirements_file,
                root / "ar-coordination" / "providers" / "requirements" / "codegraphcontext.txt",
            )
            self.assertEqual(
                layout.patches_root,
                root / "ar-coordination" / "providers" / "patches" / "codegraphcontext",
            )
            if sys.platform == "win32":
                self.assertEqual(layout.cgc_executable(), layout.venv_root / "Scripts" / "cgc.exe")
            else:
                self.assertEqual(layout.cgc_executable(), layout.venv_root / "bin" / "cgc")

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

    def test_ensure_cgc_runtime_layout_writes_pinned_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = cgc_runtime_layout(
                coordination_root=root / "ar-coordination",
                repo_id="agents-remember-md",
                code_repo_root=root / "agents-remember-md",
                cgcignore_patterns=("tools/ffmpeg/",),
            )

            layout.code_repo_root.mkdir(parents=True)
            (layout.code_repo_root / ".gitignore").write_text("/samples\n.tmp_drt\n", encoding="utf-8")
            layout.env_file.parent.mkdir(parents=True)
            layout.env_file.write_text("DEFAULT_DATABASE=kuzudb\n", encoding="utf-8")

            ensure_cgc_runtime_layout(layout)

            self.assertEqual(
                layout.requirements_file.read_text(encoding="utf-8"),
                "\n".join(CGC_REQUIREMENTS) + "\n",
            )
            self.assertIn("database: falkordb-remote", layout.config_file.read_text(encoding="utf-8"))
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
                repo_id="agents-remember-md",
                code_repo_root=root / "agents-remember-md",
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
                ["legacy-embedded-db", "legacy-embedded-global", "legacy-embedded-kuzu", "unconfigured-cgc-instance"],
            )

    def test_cgc_layout_expands_provider_settings_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "my-app").mkdir()
            provider = {
                "runtimeRoot": "<coordination_root>/providers/codegraphcontext",
                "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                "venvRoot": "<coordination_root>/providers/_venvs/codegraphcontext",
                "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
                "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
                "stateFileTemplate": "<instanceRoot>/provider-state.json",
                "backend": {
                    "runtimeRoot": "<coordination_root>/provider-data/codegraphcontext/falkordb",
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
            self.assertEqual(layout.runtime_root, root / "ar-coordination" / "providers" / "codegraphcontext" / "my-app")
            self.assertEqual(
                layout.backend_data_root,
                root / "ar-coordination" / "provider-data" / "codegraphcontext" / "falkordb" / "data",
            )
            self.assertEqual(layout.state_file, layout.runtime_root / "provider-state.json")
            self.assertEqual(layout.cgcignore_patterns, ("vendor/generated/",))

    def test_cgc_layout_rejects_missing_provider_settings_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = {
                "runtimeRoot": "<coordination_root>/providers/codegraphcontext",
                "instanceRootTemplate": "<runtimeRoot>/<repoId>",
                "venvRoot": "<coordination_root>/providers/_venvs/codegraphcontext",
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

    def test_grepai_requirements_pin_is_created_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = ensure_grepai_requirements_file(Path(tmp))
            self.assertEqual(path.read_text(encoding="utf-8"), f"{GREPAI_PIN}\n")
            self.assertEqual(read_provider_pin(path, "grepai"), "0.35.0")

    def test_detects_forbidden_source_provider_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code_root = Path(tmp) / "repo"
            code_root.mkdir()
            self.assertEqual(source_provider_artifacts(code_root), [])

            (code_root / ".cgcignore").write_text("# generated\n", encoding="utf-8")
            self.assertEqual([path.name for path in source_provider_artifacts(code_root)], [".cgcignore"])

            with self.assertRaises(ContextProviderError):
                assert_no_source_provider_artifacts(code_root)

    def test_cgc_cgcignore_patch_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cgcignore.py"
            target.write_text(f"def build_ignore_spec():\n{CGC_ORIGINAL_SNIPPET}", encoding="utf-8")

            self.assertTrue(apply_cgc_cgcignore_patch(target))
            self.assertTrue(cgc_cgcignore_patch_applied(target))
            self.assertIn(CGC_PATCH_MARKER, target.read_text(encoding="utf-8"))
            self.assertIn("if not local_cgcignore_path.exists():", target.read_text(encoding="utf-8"))
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
            self.assertIn("for cpp_ext in ('.cpp', '.cc', '.cxx', '.c++', '.C', '.h', '.hpp', '.hh')", text)
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

    def test_find_cgc_cgcignore_module_accepts_windows_venv_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = (
                Path(tmp)
                / "Lib"
                / "site-packages"
                / "codegraphcontext"
                / "core"
                / "cgcignore.py"
            )
            target.parent.mkdir(parents=True)
            target.write_text("# module\n", encoding="utf-8")

            self.assertEqual(find_cgc_cgcignore_module(Path(tmp)), target.resolve())

    def test_find_cgc_writer_module_accepts_windows_venv_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = (
                Path(tmp)
                / "Lib"
                / "site-packages"
                / "codegraphcontext"
                / "tools"
                / "indexing"
                / "persistence"
                / "writer.py"
            )
            target.parent.mkdir(parents=True)
            target.write_text("# module\n", encoding="utf-8")

            self.assertEqual(find_cgc_writer_module(Path(tmp)), target.resolve())

    def test_find_cgc_graph_builder_module_accepts_windows_venv_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = (
                Path(tmp)
                / "Lib"
                / "site-packages"
                / "codegraphcontext"
                / "tools"
                / "graph_builder.py"
            )
            target.parent.mkdir(parents=True)
            target.write_text("# module\n", encoding="utf-8")

            self.assertEqual(find_cgc_graph_builder_module(Path(tmp)), target.resolve())

    def test_find_cgc_discovery_module_accepts_windows_venv_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = (
                Path(tmp)
                / "Lib"
                / "site-packages"
                / "codegraphcontext"
                / "tools"
                / "indexing"
                / "discovery.py"
            )
            target.parent.mkdir(parents=True)
            target.write_text("# module\n", encoding="utf-8")

            self.assertEqual(find_cgc_discovery_module(Path(tmp)), target.resolve())

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
        self.assertEqual(CGC_CGCIGNORE_PATCH_ID, "codegraphcontext-0.4.10-cgcignore-runtime-root-v2")
        self.assertEqual(CGC_DELETE_PATCH_ID, "codegraphcontext-0.4.10-windows-delete-prefix-v1")
        self.assertEqual(CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_ID, "codegraphcontext-0.4.10-cpp-cc-td-extensions-v1")
        self.assertEqual(CGC_DISCOVERY_EXTENSIONS_PATCH_ID, "codegraphcontext-0.4.10-td-generic-discovery-v1")


if __name__ == "__main__":
    unittest.main()
