from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.install import runtime as install_runtime
from agents_remember.install.assets import packaged_source_root


def write_file(path: Path, content: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_runtime_source(root: Path) -> Path:
    source_root = root / "source"
    runtime_root = source_root / "runtime"
    write_file(source_root / "mcp" / "src" / "agents_remember" / "mcp" / "__init__.py")
    write_file(source_root / "mcp" / "requirements.txt", "mcp==1.12.4\n")
    for source_rel in install_runtime.AGENTS_MD_TARGETS:
        write_file(runtime_root / source_rel)
    write_file(runtime_root / "skills" / "U-01-core-skills" / "C-04" / "SKILL.md")
    write_file(runtime_root / "providers" / "requirements" / "codegraphcontext.txt")
    write_file(runtime_root / "providers" / "requirements" / "grepai.txt")
    write_file(runtime_root / "providers" / "patches" / "codegraphcontext" / "patch.diff")
    return source_root


class InstallRuntimeTests(unittest.TestCase):
    def test_packaged_source_root_contains_runtime_and_benchmarks(self) -> None:
        with packaged_source_root() as source_root:
            self.assertTrue((source_root / "runtime" / "skills").is_dir())
            self.assertTrue((source_root / "runtime" / "providers").is_dir())
            self.assertTrue((source_root / "benchmarks" / "cases").is_dir())

    def test_runtime_install_preserves_docker_provider_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"
            providers_root = coordination_root / "providers"
            cgc_exe = providers_root / "_venvs" / "codegraphcontext" / "Scripts" / "cgc.exe"
            cgc_state = (
                providers_root
                / "runners"
                / "codegraphcontext"
                / "repo"
                / ".codegraphcontext"
                / "state.json"
            )
            grepai_watch_log = (
                providers_root / "runners" / "grepai" / "memory-repos" / "logs" / "watch.log"
            )
            cgc_data = providers_root / "data" / "codegraphcontext" / "graph.db"
            cgc_log = providers_root / "logs" / "codegraphcontext" / "watch.log"
            central_cgc_log = (
                coordination_root / "logs" / "providers" / "codegraphcontext" / "watch.log"
            )
            old_script = coordination_root / "scripts" / "install-skills.sh"

            write_file(providers_root / "_bin" / "grepai.exe", "legacy grepai\n")
            write_file(cgc_exe, "live cgc\n")
            write_file(cgc_state)
            write_file(grepai_watch_log)
            write_file(cgc_data, "live graph\n")
            write_file(cgc_log, "live log\n")
            write_file(central_cgc_log, "live central log\n")
            write_file(providers_root / "old.txt")
            write_file(old_script)

            summary = install_runtime.install_runtime(
                source_root,
                coordination_root,
                dry_run=False,
                install_provider_deps=False,
                provider_settings={},
            )

            self.assertEqual(summary.dependency_runs, 0)
            self.assertFalse((providers_root / "_bin" / "grepai.exe").exists())
            self.assertFalse(cgc_exe.exists())
            self.assertTrue(cgc_state.exists())
            self.assertTrue(grepai_watch_log.exists())
            self.assertTrue(cgc_data.exists())
            self.assertFalse(cgc_log.exists())
            self.assertTrue(central_cgc_log.exists())
            self.assertFalse((providers_root / "old.txt").exists())
            self.assertTrue((providers_root / "requirements" / "grepai.txt").exists())
            self.assertFalse((coordination_root / "scripts").exists())

    def test_runtime_install_preserves_provider_state_and_ignores_mcp_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"
            providers_root = coordination_root / "providers"
            grepai_data = providers_root / "data" / "grepai" / "postgres.db"
            grepai_log = providers_root / "logs" / "grepai" / "watch.log"
            central_grepai_log = (
                coordination_root / "logs" / "providers" / "grepai" / "watch.log"
            )
            old_venv = providers_root / "_venvs" / "old" / "python.exe"
            mcp_package = coordination_root / "src" / "agents_remember" / "mcp" / "__init__.py"

            write_file(grepai_data, "live data\n")
            write_file(grepai_log, "live log\n")
            write_file(central_grepai_log, "live central log\n")
            write_file(old_venv, "old venv\n")

            summary = install_runtime.install_runtime(
                source_root,
                coordination_root,
                dry_run=False,
                install_provider_deps=False,
                provider_settings={},
            )

            self.assertEqual(summary.dependency_runs, 0)
            self.assertTrue(grepai_data.exists())
            self.assertFalse(grepai_log.exists())
            self.assertTrue(central_grepai_log.exists())
            self.assertFalse(old_venv.exists())
            self.assertTrue((providers_root / "data" / "codegraphcontext").is_dir())
            self.assertTrue((providers_root / "data" / "grepai").is_dir())
            self.assertTrue((coordination_root / "logs" / "mcp").is_dir())
            self.assertTrue(
                (coordination_root / "logs" / "providers" / "codegraphcontext").is_dir()
            )
            self.assertTrue((coordination_root / "logs" / "providers" / "grepai").is_dir())
            self.assertTrue((coordination_root / "logs" / "providers" / "setup").is_dir())
            self.assertTrue((providers_root / "runners" / "codegraphcontext").is_dir())
            self.assertTrue((providers_root / "runners" / "grepai").is_dir())
            self.assertTrue((providers_root / "requirements" / "codegraphcontext.txt").exists())
            self.assertFalse((coordination_root / "mcp").exists())
            self.assertFalse(mcp_package.exists())


if __name__ == "__main__":
    unittest.main()
