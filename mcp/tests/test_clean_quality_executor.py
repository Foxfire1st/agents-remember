from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

from agents_remember.worktrees.modules import clean_quality_executor
from agents_remember.worktrees.modules.clean_quality_executor import (
    CleanQualityRequest,
    run_clean_quality,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def repository(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "dagger.json").write_text('{"engineVersion":"v0.21.8"}\n', encoding="utf-8")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    return repo


class CleanQualityExecutorTests(unittest.TestCase):
    def test_exact_staged_candidate_is_passed_to_one_pinned_dagger_pipeline(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            repo = repository(root)
            (repo / "tracked.txt").write_text("candidate\n", encoding="utf-8")
            (repo / "created.txt").write_text("created\n", encoding="utf-8")
            git(repo, "add", "-A")
            group = root / "enclosure"
            calls: list[list[str]] = []

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                del cwd, env
                calls.append(command)
                if "export" in command:
                    export = Path(
                        next(
                            item.split("=", 1)[1] for item in command if item.startswith("--path=")
                        )
                    )
                    export.mkdir(parents=True)
                    (export / "clean-quality-results.json").write_text(
                        '{"status":"passed"}\n', encoding="utf-8"
                    )
                    (export / "coverage.data").write_bytes(b"\x00coverage")
                    return subprocess.CompletedProcess(command, 0, stdout="exported\n", stderr="")
                return subprocess.CompletedProcess(command, 0, stdout="0\n", stderr="")

            result = run_clean_quality(
                CleanQualityRequest(
                    code_worktree=repo,
                    worktree_group=group,
                    mode="targeted",
                    diff_base=git(repo, "rev-parse", "HEAD"),
                    memory_cap_bytes=1024,
                ),
                runner=runner,
                dagger_resolver=lambda _env: "/usr/local/bin/dagger",
            )

            self.assertEqual(result.returncode, 0)
            sandbox = group / "reports/test-sandbox"
            source = sandbox / "source"
            self.assertEqual((source / "tracked.txt").read_text(encoding="utf-8"), "candidate\n")
            self.assertEqual((source / "created.txt").read_text(encoding="utf-8"), "created\n")
            self.assertEqual(
                git(source, "diff", "--cached", "--name-only"), "created.txt\ntracked.txt"
            )
            manifest = json.loads((sandbox / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["daggerVersion"], "v0.21.8")
            self.assertEqual(manifest["codexVersion"], "0.147.0")
            self.assertEqual(len(manifest["bundleSha256"]), 64)
            bundle = sandbox / "candidate.bundle"
            self.assertTrue(bundle.is_file())
            self.assertEqual(len(calls), 2)
            self.assertIn(f"--source={source.as_posix()}", calls[0])
            self.assertIn(f"--repository-bundle={bundle.as_posix()}", calls[0])
            self.assertNotIn("--candidate-head", " ".join(calls[0]))
            self.assertNotIn("docker", " ".join(item for call in calls for item in call).lower())
            self.assertEqual((group / "reports/coverage.data").read_bytes(), b"\x00coverage")

            (sandbox / "obsolete").write_text("old", encoding="utf-8")
            run_clean_quality(
                CleanQualityRequest(repo, group, "full", ""),
                runner=runner,
                dagger_resolver=lambda _env: "/usr/local/bin/dagger",
            )
            self.assertFalse((sandbox / "obsolete").exists())

    def test_public_runner_refuses_invalid_mode_and_windows_interop_root(self) -> None:
        request = CleanQualityRequest(Path("/repo"), Path("/enclosure"), "quick", "")
        with self.assertRaisesRegex(ValueError, "unknown clean quality mode"):
            run_clean_quality(request)

        request = CleanQualityRequest(Path("/mnt/c/repo"), Path("/enclosure"), "full", "")
        with self.assertRaisesRegex(RuntimeError, "Windows-mounted WSL path"):
            run_clean_quality(request)

    def test_export_failure_is_returned_without_running_status_or_publishing(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            repo = repository(root)
            calls: list[list[str]] = []

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                del cwd, env
                calls.append(command)
                return subprocess.CompletedProcess(command, 8, stdout="", stderr="engine red")

            result = run_clean_quality(
                CleanQualityRequest(repo, root / "group", "full", ""),
                runner=runner,
                dagger_resolver=lambda _env: "dagger",
            )

            self.assertEqual(result.returncode, 8)
            self.assertEqual(len(calls), 1)
            progress = json.loads(
                (root / "group/reports/quality-progress.json").read_text(encoding="utf-8")
            )
            self.assertEqual(progress["status"], "failed")

    def test_invalid_exported_exit_code_refuses_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            repo = repository(root)

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                del cwd, env
                if "export" in command:
                    export = Path(
                        next(
                            item.split("=", 1)[1] for item in command if item.startswith("--path=")
                        )
                    )
                    export.mkdir(parents=True)
                    return subprocess.CompletedProcess(command, 0, stdout="exported\n", stderr="")
                return subprocess.CompletedProcess(command, 0, stdout="not-an-integer\n", stderr="")

            with self.assertRaisesRegex(RuntimeError, "no valid exit code"):
                run_clean_quality(
                    CleanQualityRequest(repo, root / "group", "full", ""),
                    runner=runner,
                    dagger_resolver=lambda _env: "dagger",
                )

    def test_report_publish_git_guard_and_streaming_progress_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "did not export"):
                clean_quality_executor._publish_reports(root / "missing", root / "reports")
            empty = root / "empty"
            empty.mkdir()
            (empty / "nested").mkdir()
            clean_quality_executor._publish_reports(empty, root / "reports")
            failed = subprocess.CompletedProcess(["git"], 2, stdout="", stderr="bad ref\n")
            with self.assertRaisesRegex(RuntimeError, "could not resolve base: bad ref"):
                clean_quality_executor._git_ok(failed, "resolve base")
            passed = subprocess.CompletedProcess(["git"], 0, stdout=" patch\n", stderr="")
            self.assertEqual(
                clean_quality_executor._git_ok(passed, "read patch", preserve_output=True),
                " patch\n",
            )

            class Process:
                stdout = iter(["first\n", "\x1b[31mred\x1b[0m\n", "\n"])

                @staticmethod
                def wait() -> int:
                    return 3

            progress = root / "reports/dagger-progress.log"
            progress.parent.mkdir(parents=True)
            progress.write_text("prior\n", encoding="utf-8")
            with mock.patch.object(
                clean_quality_executor.subprocess, "Popen", return_value=Process()
            ):
                completed = clean_quality_executor._stream_dagger(
                    ["dagger"], root, {"PATH": "/usr/bin"}, progress_path=progress
                )
            self.assertEqual(completed.returncode, 3)
            self.assertEqual(completed.stdout, "prior\nfirst\n\x1b[31mred\x1b[0m\n\n")
            current = json.loads(
                (progress.parent / "quality-progress.json").read_text(encoding="utf-8")
            )
            self.assertEqual(current["detail"], "red")

    def test_dagger_resolution_uses_the_native_command_boundary(self) -> None:
        with mock.patch.object(
            clean_quality_executor, "native_command", return_value=["/usr/bin/dagger"]
        ) as native:
            self.assertEqual(
                clean_quality_executor._resolve_dagger({"PATH": "/usr/bin"}),
                "/usr/bin/dagger",
            )
        native.assert_called_once_with(["dagger"], {"PATH": "/usr/bin"})


if __name__ == "__main__":
    unittest.main()
