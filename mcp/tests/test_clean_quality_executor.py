from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from unittest import mock

import pytest
from agents_remember.certification.models import CandidateIdentity
from agents_remember.certification.repository_profiles.authority import load_repository_profile
from agents_remember.certification.repository_profiles.execution import (
    AdmittedRepositoryProfileExecution,
    admit_repository_profile_execution,
)
from agents_remember.certification.repository_profiles.models import ProfileMode
from agents_remember.worktrees.modules.quality import clean_executor as clean_quality_executor
from agents_remember.worktrees.modules.quality import (
    dagger_authority as authority_module,
)
from agents_remember.worktrees.modules.quality.clean_executor import (
    CleanQualityRequest,
    run_clean_quality,
)
from repository_profile_test_support import (
    AGENTS_REMEMBER_PROFILE_REFERENCE,
    agents_remember_profile_execution,
    install_agents_remember_profile,
)

CANDIDATE_TREE = "c" * 40


def quality_request(
    repository_root: Path,
    worktree_group: Path,
    mode: str,
    diff_base: str = "HEAD",
    *,
    memory_cap_bytes: int | None = None,
) -> CleanQualityRequest:
    return CleanQualityRequest(
        code_worktree=repository_root,
        worktree_group=worktree_group,
        repository_id="agents-remember",
        profile_reference=AGENTS_REMEMBER_PROFILE_REFERENCE,
        mode=cast(ProfileMode, mode),
        diff_base=diff_base,
        memory_cap_bytes=memory_cap_bytes,
    )


class _AuthorityProbe:
    """Connection-only engine probe standing in for DockerEngineInspector."""

    def inspect(self, declaration):
        return authority_module.InspectedRuntime(
            engine_id="clean-quality-test-engine",
            engine_running=True,
            store_mounted_path=declaration.layer_store,
            store_source="/var/lib/docker/volumes/clean-quality-test-engine/_data",
            observed_version="dagger 0.21.8",
        )


def _host_declaration(endpoint: str) -> str:
    declaration = {
        "schemaVersion": "dagger-host-declaration/v1",
        "endpoint": endpoint,
        "layerStore": "/var/lib/dagger",
        "engineVersion": "v0.21.8",
    }
    return json.dumps(declaration)


def _test_authority(**environ: str) -> authority_module.AdmittedDaggerAuthority:
    return authority_module.admit_dagger_authority(
        environ={
            **environ,
            authority_module.HOST_DECLARATION_ENV: _host_declaration(
                "container://clean-quality-test-engine"
            ),
        },
        inspector=_AuthorityProbe(),
    )


def publish_reports(
    source: Path,
    destination: Path,
    *,
    candidate_tree: str = CANDIDATE_TREE,
    profile_execution: AdmittedRepositoryProfileExecution | None = None,
):
    execution = profile_execution or agents_remember_profile_execution(
        candidate_tree=candidate_tree
    )
    return clean_quality_executor._publish_reports(
        source,
        destination,
        candidate_tree=candidate_tree,
        profile_execution=execution,
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
    install_agents_remember_profile(repo)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    return repo


def _write_docker_stub(path: Path, client: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s|%s|%s|%s\\n' '{client}' \"$DOCKER_CONTEXT\" \"$DOCKER_CONFIG\" "
        '"$*" >> "$DOCKER_CALLS"\n'
        'case "$1:$3" in\n'
        "  'inspect:{{.State.Running}}') printf '%s\\n' true ;;\n"
        "  'inspect:{{json .Mounts}}') printf '%s\\n' "
        """'[{"Destination":"/var/lib/dagger","Source":"/stub-store"}]' ;;\n"""
        "  'exec:dagger') printf '%s\\n' 'dagger 0.21.8' ;;\n"
        "  'exec:dagger-child') exit 8 ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


def test_clean_quality_inspects_and_launches_through_the_same_docker_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repository(tmp_path)
    local_bin = tmp_path / "home/.local/bin"
    _write_docker_stub(tmp_path / "caller-bin/docker", "caller")
    _write_docker_stub(local_bin / "docker", "native")
    dagger = local_bin / "dagger"
    dagger.write_text(
        "#!/bin/sh\n"
        'exec docker exec "${_EXPERIMENTAL_DAGGER_RUNNER_HOST#container://}" dagger-child\n',
        encoding="utf-8",
    )
    dagger.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "caller-bin", prepend=os.pathsep)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOCKER_CONFIG", "docker-config")
    monkeypatch.setenv("DOCKER_CONTEXT", "test-engine-context")
    calls = tmp_path / "docker-calls"
    monkeypatch.setenv("DOCKER_CALLS", str(calls))
    monkeypatch.setenv(authority_module.HOST_REGISTRY_ROOT_ENV, str(tmp_path / "authority"))
    monkeypatch.setenv(
        authority_module.HOST_DECLARATION_ENV,
        _host_declaration("container://test.engine"),
    )
    monkeypatch.delenv("DAGGER_HOST", raising=False)
    monkeypatch.delenv("_EXPERIMENTAL_DAGGER_RUNNER_HOST", raising=False)

    def run_stub(
        command: list[str], cwd: Path, env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command, cwd=cwd, env=dict(env), text=True, capture_output=True, check=False
        )

    outcome = run_clean_quality(
        quality_request(repo, tmp_path / "enclosure", "targeted"), runner=run_stub
    )

    prefix = f"native|test-engine-context|{tmp_path / 'docker-config'}|"
    assert calls.read_text(encoding="utf-8").splitlines() == [
        prefix + "inspect --format {{.State.Running}} test.engine",
        prefix + "inspect --format {{json .Mounts}} test.engine",
        prefix + "exec test.engine dagger --version",
        prefix + "exec test.engine dagger-child",
    ]
    assert outcome.returncode == 8


class CleanQualityExecutorTests(unittest.TestCase):
    def _assert_exact_pipeline_publication(
        self,
        *,
        group: Path,
        calls: list[list[str]],
        result,
    ) -> None:
        self.assertEqual(result.returncode, 0)
        self.assertIsNotNone(result.evidence)
        sandbox = group / "reports/test-sandbox"
        source = sandbox / "source"
        self.assertEqual((source / "tracked.txt").read_text(encoding="utf-8"), "candidate\n")
        self.assertEqual((source / "created.txt").read_text(encoding="utf-8"), "created\n")
        self.assertEqual(git(source, "diff", "--cached", "--name-only"), "created.txt\ntracked.txt")
        manifest = json.loads((sandbox / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["profile"]["configuredReference"], "mcp/certification-profile-v1.json"
        )
        self.assertEqual(manifest["executorAdapter"]["functionName"], "quality")
        self.assertEqual(
            (len(manifest["profile"]["profileDigest"]), len(manifest["bundleSha256"])),
            (64, 64),
        )
        bundle = sandbox / "candidate.bundle"
        self.assertTrue(bundle.is_file())
        self.assertEqual(len(calls), 1)
        self.assertIn(f"--source={source.as_posix()}", calls[0])
        self.assertIn(f"--repository-bundle={bundle.as_posix()}", calls[0])
        self.assertIn(
            f"--execution-manifest={(sandbox / 'manifest.json').as_posix()}",
            calls[0],
        )
        candidate = CandidateIdentity(kind="git-tree", value=git(source, "write-tree"))
        expected = admit_repository_profile_execution(
            load_repository_profile("agents-remember", source, AGENTS_REMEMBER_PROFILE_REFERENCE),
            purpose="closeout",
            mode="targeted",
            candidate_identity=candidate,
        )
        self.assertEqual(manifest["profilePlan"], expected.plan.model_dump(mode="json"))
        self.assertEqual(manifest["profile"]["sourceSha256"], expected.admitted.source_sha256)
        self.assertEqual(
            manifest["profile"]["profileDigest"], expected.admitted.canonical.profileDigest
        )
        self.assertEqual(
            manifest["publishedArtifacts"],
            [artifact.model_dump(mode="json") for artifact in expected.published_artifacts],
        )
        self.assertNotIn("--attempt-nonce", " ".join(calls[0]))
        self.assertNotIn("--candidate-head", " ".join(calls[0]))
        self.assertNotIn("docker", " ".join(item for call in calls for item in call).lower())
        self.assertEqual(
            clean_quality_executor.published_report_path(
                group / "reports", "coverage.data"
            ).read_bytes(),
            b"\x00coverage",
        )
        for proof_name in ("python-runtime.json", "python-venv-runtime.json"):
            proof = clean_quality_executor.published_report_path(group / "reports", proof_name)
            self.assertEqual(
                json.loads(proof.read_text(encoding="utf-8"))["schema"],
                "ar-python-runtime-proof/v1",
            )

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
                del env
                calls.append(command)
                self.assertEqual(cwd, group / "reports/test-sandbox/source")
                export = Path(
                    next(item.split("=", 1)[1] for item in command if item.startswith("--path="))
                )
                export.mkdir(parents=True)
                (export / "clean-quality-results.json").write_text(
                    '{"status":"passed","exitCode":0}\n', encoding="utf-8"
                )
                (export / "coverage.data").write_bytes(b"\x00coverage")
                (export / "python-runtime.json").write_text(
                    '{"schema":"ar-python-runtime-proof/v1"}\n', encoding="utf-8"
                )
                (export / "python-venv-runtime.json").write_text(
                    '{"schema":"ar-python-runtime-proof/v1"}\n', encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0, stdout="exported\n", stderr="")

            result = run_clean_quality(
                quality_request(
                    repo,
                    group,
                    "targeted",
                    git(repo, "rev-parse", "HEAD"),
                    memory_cap_bytes=1024,
                ),
                runner=runner,
                executor_resolver=lambda _env: "/usr/local/bin/dagger",
                authority=_test_authority(),
            )

            sandbox = group / "reports/test-sandbox"
            self._assert_exact_pipeline_publication(group=group, calls=calls, result=result)

            (sandbox / "obsolete").write_text("old", encoding="utf-8")
            run_clean_quality(
                quality_request(repo, group, "full"),
                runner=runner,
                executor_resolver=lambda _env: "/usr/local/bin/dagger",
                authority=_test_authority(),
            )
            self.assertFalse((sandbox / "obsolete").exists())

    def test_dagger_launch_receipt_and_result_name_dropped_selectors(self) -> None:
        dropped = {
            "DAGGER_CLOUD_ENGINE": "",
            "DAGGER_SESSION_PORT": "fixture-port",
            "DAGGER_SESSION_TOKEN": "fixture-token",
        }
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            repo = repository(root)
            group = root / "enclosure"
            admitted = _test_authority(**dropped)
            receipt = group / "reports/test-sandbox/manifest.json"

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                self.assertTrue(dropped.keys().isdisjoint(env))
                self.assertEqual(
                    env["_EXPERIMENTAL_DAGGER_RUNNER_HOST"], admitted.snapshot.endpoint
                )
                self.assertTrue(receipt.is_file())
                return subprocess.CompletedProcess(command, 1, stdout="fixture failure", stderr="")

            result = run_clean_quality(
                quality_request(repo, group, "full"),
                runner=runner,
                executor_resolver=lambda _env: "/usr/local/bin/dagger",
                authority=admitted,
            )

            receipt_text = receipt.read_text(encoding="utf-8")
            self.assertEqual(json.loads(receipt_text)["droppedDaggerSelectors"], sorted(dropped))
            self.assertEqual(list(result.dropped_selectors), sorted(dropped))
            self.assertEqual(result.returncode, 1)
            for value in ("fixture-port", "fixture-token"):
                self.assertNotIn(value, receipt_text)
                self.assertNotIn(value, repr(result))

    def test_exported_failure_is_the_authoritative_pipeline_exit(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            repo = repository(root)

            def runner(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                del cwd, env
                export = Path(
                    next(item.split("=", 1)[1] for item in command if item.startswith("--path="))
                )
                export.mkdir(parents=True)
                (export / "clean-quality-results.json").write_text(
                    '{"status":"failed","exitCode":8}\n', encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0, stdout="exported\n", stderr="")

            result = run_clean_quality(
                quality_request(repo, root / "group", "full", git(repo, "rev-parse", "HEAD")),
                runner=runner,
                executor_resolver=lambda _env: "dagger",
                authority=_test_authority(),
            )

            self.assertEqual(result.returncode, 8)
            progress = json.loads(
                (root / "group/reports/quality-progress.json").read_text(encoding="utf-8")
            )
            self.assertEqual(progress["status"], "failed")
            self.assertEqual(progress["detail"], "repository certification adapter failed")

    def test_invalid_or_unowned_exports_never_replace_durable_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            repo = repository(root)
            reports = root / "group/reports"
            reports.mkdir(parents=True)
            durable = reports / "clean-quality-results.json"
            durable.write_text('{"status":"passed","exitCode":0}\n', encoding="utf-8")
            coverage = reports / "coverage.json"
            coverage.write_text("prior", encoding="utf-8")

            def contradictory(
                command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> subprocess.CompletedProcess[str]:
                del cwd, env
                export = Path(
                    next(item.split("=", 1)[1] for item in command if item.startswith("--path="))
                )
                export.mkdir(parents=True)
                (export / "clean-quality-results.json").write_text(
                    '{"status":"passed","exitCode":2}\n', encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with self.assertRaisesRegex(RuntimeError, "contradictory result"):
                run_clean_quality(
                    quality_request(repo, root / "group", "full", git(repo, "rev-parse", "HEAD")),
                    runner=contradictory,
                    executor_resolver=lambda _env: "dagger",
                    authority=_test_authority(),
                )
            self.assertEqual(
                durable.read_text(encoding="utf-8"), '{"status":"passed","exitCode":0}\n'
            )

            self.assertEqual(coverage.read_text(encoding="utf-8"), "prior")

            export = root / "malicious"
            export.mkdir()
            (export / "clean-quality-results.json").write_text(
                '{"status":"passed","exitCode":0}\n', encoding="utf-8"
            )
            (export / "closeout-operation.json").write_text("forged", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unexpected report files"):
                publish_reports(export, reports)
            self.assertEqual(
                durable.read_text(encoding="utf-8"), '{"status":"passed","exitCode":0}\n'
            )

            (export / "closeout-operation.json").unlink()
            nested = export / "rail-evidence"
            nested.mkdir()
            (nested / "undeclared-rail@1.0.0.log").write_text("forged", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unexpected report files"):
                publish_reports(export, reports)
            self.assertEqual(
                durable.read_text(encoding="utf-8"), '{"status":"passed","exitCode":0}\n'
            )

    def test_report_generation_pointer_never_exposes_a_partial_copy(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            reports = root / "reports"
            old = root / "old"
            old.mkdir()
            (old / "clean-quality-results.json").write_text(
                '{"status":"passed","exitCode":0,"attempt":"old"}\n', encoding="utf-8"
            )
            publish_reports(old, reports)
            manifest_before = (reports / clean_quality_executor.REPORT_SET_MANIFEST).read_bytes()

            new = root / "new"
            new.mkdir()
            (new / "clean-quality-results.json").write_text(
                '{"status":"passed","exitCode":0,"attempt":"new"}\n', encoding="utf-8"
            )
            (new / "coverage.json").write_text('{"new":true}\n', encoding="utf-8")
            (new / "causal-failures.json").write_text(
                '{"schemaVersion":"python-causal-failures/v1"}\n',
                encoding="utf-8",
            )
            (new / "causal-failures.md").write_text(
                "# Causal failures\n",
                encoding="utf-8",
            )
            real_copy = clean_quality_executor.shutil.copyfile
            for fail_after in (1, 2):
                copies = 0

                def interrupted_copy(
                    source: Path,
                    target: Path,
                    fail_at: int = fail_after,
                ) -> Path:
                    nonlocal copies
                    copies += 1
                    if copies == fail_at:
                        raise OSError("injected publication failure")
                    return real_copy(source, target)

                with (
                    mock.patch.object(
                        clean_quality_executor.shutil,
                        "copyfile",
                        side_effect=interrupted_copy,
                    ),
                    self.assertRaisesRegex(OSError, "injected publication failure"),
                ):
                    publish_reports(new, reports)
                self.assertEqual(
                    (reports / clean_quality_executor.REPORT_SET_MANIFEST).read_bytes(),
                    manifest_before,
                )
                selected = clean_quality_executor.published_report_path(
                    reports, "clean-quality-results.json"
                )
                self.assertIn('"attempt":"old"', selected.read_text(encoding="utf-8"))

            with (
                mock.patch.object(
                    clean_quality_executor,
                    "_prune_report_generations",
                    side_effect=OSError("crash before pointer"),
                ),
                self.assertRaisesRegex(OSError, "crash before pointer"),
            ):
                publish_reports(new, reports)
            selected = clean_quality_executor.published_report_path(
                reports, "clean-quality-results.json"
            )
            self.assertIn('"attempt":"old"', selected.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
