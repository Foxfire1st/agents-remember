from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
INSTALLER_PATH = REPO_ROOT / "installer" / "install-runtime.py"
SPEC = importlib.util.spec_from_file_location("install_runtime", INSTALLER_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Unable to load runtime installer from {INSTALLER_PATH}")
install_runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = install_runtime
SPEC.loader.exec_module(install_runtime)


def write_file(path: Path, content: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_runtime_source(root: Path) -> Path:
    source_root = root / "source"
    runtime_root = source_root / "runtime"
    for source_rel in install_runtime.AGENTS_MD_TARGETS:
        write_file(runtime_root / source_rel)
    write_file(runtime_root / "skills" / "U-01-core-skills" / "C-04" / "SKILL.md")
    write_file(runtime_root / "scripts" / "provider-lifecycle.py")
    write_file(runtime_root / "providers" / "requirements" / "codegraphcontext.txt")
    write_file(runtime_root / "providers" / "requirements" / "grepai.txt")
    write_file(runtime_root / "providers" / "patches" / "codegraphcontext" / "patch.diff")
    return source_root


class InstallRuntimeTests(unittest.TestCase):
    def test_skip_provider_deps_preserves_installed_provider_runtimes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"
            write_file(coordination_root / "providers" / "_bin" / "grepai.exe", "live grepai\n")
            write_file(coordination_root / "providers" / "_venvs" / "codegraphcontext" / "Scripts" / "cgc.exe", "live cgc\n")
            write_file(coordination_root / "providers" / "codegraphcontext" / "repo" / ".codegraphcontext" / "state.json")
            write_file(coordination_root / "providers" / "grepai" / "memory-repos" / "logs" / "watch.log")
            write_file(coordination_root / "providers" / "old.txt")

            summary = install_runtime.install_runtime(
                source_root,
                coordination_root,
                dry_run=False,
                install_provider_deps=False,
            )

            self.assertEqual(summary.dependency_runs, 0)
            self.assertTrue((coordination_root / "providers" / "_bin" / "grepai.exe").exists())
            self.assertTrue((coordination_root / "providers" / "_venvs" / "codegraphcontext" / "Scripts" / "cgc.exe").exists())
            self.assertTrue(
                (
                    coordination_root
                    / "providers"
                    / "codegraphcontext"
                    / "repo"
                    / ".codegraphcontext"
                    / "state.json"
                ).exists()
            )
            self.assertTrue((coordination_root / "providers" / "grepai" / "memory-repos" / "logs" / "watch.log").exists())
            self.assertFalse((coordination_root / "providers" / "old.txt").exists())
            self.assertTrue((coordination_root / "providers" / "requirements" / "grepai.txt").exists())


if __name__ == "__main__":
    unittest.main()
