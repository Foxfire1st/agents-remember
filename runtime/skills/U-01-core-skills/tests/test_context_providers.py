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
    CGC_PATCH_MARKER,
    CGC_PIN,
    CGC_ORIGINAL_SNIPPET,
    ContextProviderError,
    apply_cgc_cgcignore_patch,
    assert_no_source_provider_artifacts,
    cgc_cgcignore_patch_applied,
    cgc_runtime_layout,
    ensure_cgc_runtime_layout,
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
            self.assertEqual(layout.db_path, layout.cgc_root / "db" / "kuzu")
            self.assertEqual(layout.venv_root, root / "ar-coordination" / "providers" / "_venvs" / "codegraphcontext")
            self.assertEqual(
                layout.requirements_file,
                root / "ar-coordination" / "providers" / "requirements" / "codegraphcontext.txt",
            )
            self.assertEqual(
                layout.patches_root,
                root / "ar-coordination" / "providers" / "patches" / "codegraphcontext",
            )

            env = layout.env()
            self.assertEqual(env["HOME"], layout.runtime_root.as_posix())
            self.assertEqual(env["KUZUDB_PATH"], layout.db_path.as_posix())
            self.assertEqual(env["CGC_RUNTIME_DB_PATH"], layout.db_path.as_posix())
            self.assertTrue(env["LOG_FILE_PATH"].endswith("/.codegraphcontext/logs/cgc.log"))

    def test_ensure_cgc_runtime_layout_writes_pinned_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = cgc_runtime_layout(
                coordination_root=root / "ar-coordination",
                repo_id="agents-remember-md",
                code_repo_root=root / "agents-remember-md",
            )

            ensure_cgc_runtime_layout(layout)

            self.assertEqual(layout.requirements_file.read_text(encoding="utf-8"), f"{CGC_PIN}\n")
            self.assertIn("database: kuzudb", layout.config_file.read_text(encoding="utf-8"))
            env_text = layout.env_file.read_text(encoding="utf-8")
            self.assertIn("DEFAULT_DATABASE=kuzudb", env_text)
            self.assertNotIn("CGC_RUNTIME_DB_TYPE=", env_text)
            self.assertNotIn("KUZUDB_PATH=", env_text)
            self.assertNotIn("CGC_RUNTIME_DB_PATH=", env_text)
            self.assertTrue(layout.cgcignore_path.read_text(encoding="utf-8").startswith("# Managed by Agents Remember"))
            self.assertTrue(layout.logs_root.is_dir())
            self.assertTrue(layout.run_root.is_dir())

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
            self.assertFalse(apply_cgc_cgcignore_patch(target))

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
        self.assertEqual(CGC_CGCIGNORE_PATCH_ID, "codegraphcontext-0.4.10-cgcignore-runtime-root")


if __name__ == "__main__":
    unittest.main()
