"""Behavioural tests for provider runtime helpers that no test previously reached.

Every target here is either a thin adapter over docker/compose or a settings writer, so
each test drives the real function and asserts the value it returns, the file it writes
or the error it raises. The subprocess seam these helpers already use -- ``run_command``
and ``docker_command`` -- is replaced by a fake, so no docker daemon is ever contacted
and no subprocess or file handle is left open.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.providers import lifecycle, provider_setup
from agents_remember.providers.cgc import setup as cgc_setup
from agents_remember.providers.cgc.lifecycle import refresh as cgc_refresh_lifecycle
from agents_remember.providers.cgc.lifecycle.core import (
    cgc_all_layouts_from_settings,
    cgc_apply_settings,
    cgc_layout_from_args,
)
from agents_remember.providers.context import (
    CgcRepo,
    ContextProviderError,
    cgc_runtime_layout,
)
from agents_remember.providers.grepai.lifecycle import embedder as grepai_embedder
from agents_remember.providers.identity import stable_provider_id
from agents_remember.providers.lifecycle import compose_runtime, docker_runtime
from agents_remember.providers.lifecycle.command_runner import UNLIMITED_TIMEOUT

DOCKER = "/usr/bin/docker"


def _command_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> dict[str, Any]:
    """A ``run_command`` return value, in the exact shape the real runner produces."""

    return {
        "command": [DOCKER],
        "cwd": "/ws",
        "returncode": returncode,
        "durationSeconds": 0.01,
        "stdout": stdout,
        "stderr": stderr,
        "timedOut": False,
    }


class FakeClock:
    """Stand-in for the ``time`` module: sleeping is what advances the clock."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _ollama_embedder() -> dict[str, Any]:
    return {"containerName": "ar-ollama-ws", "model": "nomic-embed-text:v1.5"}


def _provider_instance(coordination_root: Path) -> dict[str, Any]:
    return {
        "id": "test",
        "scope": "workspace",
        "labels": {
            "agents-remember.provider": "codegraphcontext-code",
            "agents-remember.instance-id": "test",
            "agents-remember.scope": "workspace",
            "agents-remember.coordination-root": coordination_root.as_posix(),
        },
    }


def _cgc_provider_block(coordination_root: Path, roots: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "enabled": True,
        "instance": _provider_instance(coordination_root),
        "runtimeRoot": "<coordination_root>/providers/runners/codegraphcontext",
        "instanceRootTemplate": "<runtimeRoot>/<repoId>",
        "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
        "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
        "roots": roots,
        "backend": {
            "image": "falkordb/falkordb:v4.18.7",
            "runtimeRoot": "<coordination_root>/providers/data/codegraphcontext/falkordb",
            "dataRoot": "<backendRuntimeRoot>/data",
        },
    }


def _write_cgc_settings(root: Path, repo_ids: tuple[str, ...] = ("repo-a",)) -> tuple[Path, Path]:
    """Write a real CGC lifecycle settings file; return (coordination root, settings path)."""

    coordination_root = root / "workspace" / ".agents-remember"
    coordination_root.mkdir(parents=True)
    roots = []
    for repo_id in repo_ids:
        repo = root / "workspace" / repo_id
        repo.mkdir(parents=True, exist_ok=True)
        roots.append({"repoId": repo_id, "path": repo.as_posix()})
    settings_path = root / "lifecycle-settings.json"
    lifecycle.write_json(
        settings_path,
        {
            "contextProviders": {
                "enabled": True,
                "providers": {
                    "codegraphcontext-code": _cgc_provider_block(coordination_root, roots),
                },
            }
        },
    )
    return coordination_root, settings_path


def _parse_cgc(argv: list[str]) -> argparse.Namespace:
    args = lifecycle.build_parser().parse_args(["cgc", *argv])
    lifecycle.normalize_cgc_args(args)
    args.coordination_root = args.coordination_root.resolve()
    if args.repo_id is not None:
        args.repo_id = lifecycle.stable_provider_id(args.repo_id)
    return args


class RenderTextTests(unittest.TestCase):
    """provider_setup.render_text -- the human-readable summary of a setup run."""

    def test_full_payload_renders_header_summary_and_one_line_per_result(self) -> None:
        payload = {
            "action": "install",
            "ok": True,
            "coordinationRoot": "/ws/.agents-remember",
            "setupSummary": {"written": True, "last": "/ws/.agents-remember/setup-summary.json"},
            "results": [
                {"provider": "codegraphcontext", "action": "install-all", "ok": True},
                {
                    "provider": "grepai-memory",
                    "action": "seed",
                    "ok": False,
                    "reason": "bundle missing",
                },
                {"provider": "grepai-memory", "action": "index", "ok": True, "skipped": True},
                {
                    "provider": "codegraphcontext",
                    "action": "refresh",
                    "ok": False,
                    "stage": "compose-up",
                },
            ],
        }

        self.assertEqual(
            provider_setup.render_text(payload),
            "\n".join(
                [
                    "provider setup install: ok",
                    "coordination root: /ws/.agents-remember",
                    "summary: /ws/.agents-remember/setup-summary.json",
                    "- codegraphcontext install-all: ok",
                    "- grepai-memory seed: failed (bundle missing)",
                    "- grepai-memory index: skipped",
                    "- codegraphcontext refresh: failed (compose-up)",
                ]
            ),
        )

    def test_explicit_state_replaces_the_derived_ok_status(self) -> None:
        payload = {"action": "status", "state": "degraded", "ok": True, "coordinationRoot": "/ws"}

        self.assertEqual(
            provider_setup.render_text(payload),
            "provider setup status: degraded\ncoordination root: /ws",
        )

    def test_missing_state_and_falsy_ok_render_failed_with_no_result_lines(self) -> None:
        payload = {"action": "install", "ok": False, "coordinationRoot": "/ws"}

        self.assertEqual(
            provider_setup.render_text(payload),
            "provider setup install: failed\ncoordination root: /ws",
        )

    def test_empty_state_falls_back_to_the_derived_status(self) -> None:
        payload = {"action": "install", "state": "", "ok": True, "coordinationRoot": "/ws"}

        self.assertEqual(
            provider_setup.render_text(payload).splitlines()[0],
            "provider setup install: ok",
        )

    def test_unwritten_summary_is_omitted(self) -> None:
        payload = {
            "action": "install",
            "ok": True,
            "coordinationRoot": "/ws",
            "setupSummary": {"written": False, "last": "/ws/setup-summary.json"},
        }

        self.assertNotIn("summary:", provider_setup.render_text(payload))

    def test_non_dict_summary_is_ignored_rather_than_crashing(self) -> None:
        payload = {
            "action": "install",
            "ok": True,
            "coordinationRoot": "/ws",
            "setupSummary": "not-a-dict",
            "results": [],
        }

        self.assertEqual(
            provider_setup.render_text(payload),
            "provider setup install: ok\ncoordination root: /ws",
        )

    def test_result_without_provider_action_or_ok_falls_back_to_placeholders(self) -> None:
        payload = {"action": "install", "ok": True, "coordinationRoot": "/ws", "results": [{}]}

        self.assertEqual(
            provider_setup.render_text(payload).splitlines()[-1],
            "- provider action: failed",
        )

    def test_reason_wins_over_stage_in_the_detail_suffix(self) -> None:
        payload = {
            "action": "install",
            "ok": False,
            "coordinationRoot": "/ws",
            "results": [
                {
                    "provider": "grepai-memory",
                    "action": "seed",
                    "ok": False,
                    "reason": "source unavailable",
                    "stage": "copy",
                }
            ],
        }

        self.assertEqual(
            provider_setup.render_text(payload).splitlines()[-1],
            "- grepai-memory seed: failed (source unavailable)",
        )


class DockerRepoDigestTests(unittest.TestCase):
    """docker_runtime.docker_repo_digest -- read the pinned digest of a local image."""

    def _digest(self, result: dict[str, Any]) -> tuple[str | None, mock.Mock]:
        runner = mock.Mock(return_value=result)
        with (
            mock.patch.object(docker_runtime, "run_command", runner),
            mock.patch.object(docker_runtime, "docker_command", return_value=DOCKER),
        ):
            value = docker_runtime.docker_repo_digest(
                "falkordb/falkordb:v4.18.7", cwd=Path("/ws"), timeout=7
            )
        return value, runner

    def test_first_repo_digest_is_returned_and_the_inspect_command_is_exact(self) -> None:
        digest, runner = self._digest(
            _command_result(stdout='["falkordb/falkordb@sha256:aaa","mirror@sha256:bbb"]')
        )

        self.assertEqual(digest, "falkordb/falkordb@sha256:aaa")
        runner.assert_called_once_with(
            [
                DOCKER,
                "image",
                "inspect",
                "falkordb/falkordb:v4.18.7",
                "--format",
                "{{json .RepoDigests}}",
            ],
            cwd=Path("/ws"),
            timeout=7,
        )

    def test_missing_image_returns_none(self) -> None:
        digest, _ = self._digest(_command_result(returncode=1, stderr="No such image"))

        self.assertIsNone(digest)

    def test_unparseable_stdout_returns_none(self) -> None:
        digest, _ = self._digest(_command_result(stdout="Error: template failed"))

        self.assertIsNone(digest)

    def test_image_built_locally_without_digests_returns_none(self) -> None:
        digest, _ = self._digest(_command_result(stdout="[]\n"))

        self.assertIsNone(digest)

    def test_non_list_json_returns_none(self) -> None:
        digest, _ = self._digest(_command_result(stdout="null"))

        self.assertIsNone(digest)

    def test_non_string_digest_entry_is_coerced_to_text(self) -> None:
        digest, _ = self._digest(_command_result(stdout="[12345]"))

        self.assertEqual(digest, "12345")


class DockerInspectNetworkTests(unittest.TestCase):
    """compose_runtime.docker_inspect_network -- inspect a compose network, or admit
    it is not there."""

    def _inspect(self, result: dict[str, Any]) -> tuple[dict[str, Any] | None, mock.Mock]:
        runner = mock.Mock(return_value=result)
        with (
            mock.patch.object(compose_runtime, "run_command", runner),
            mock.patch.object(compose_runtime, "docker_command", return_value=DOCKER),
        ):
            value = compose_runtime.docker_inspect_network(
                "agents-remember-cgc", cwd=Path("/ws"), timeout=11
            )
        return value, runner

    def test_first_inspect_entry_is_returned_and_the_command_is_exact(self) -> None:
        payload = {"Name": "agents-remember-cgc", "Labels": {"com.docker.compose.project": "x"}}
        network, runner = self._inspect(
            _command_result(stdout=json.dumps([payload, {"Name": "b"}]))
        )

        self.assertEqual(network, payload)
        runner.assert_called_once_with(
            [DOCKER, "network", "inspect", "agents-remember-cgc"],
            cwd=Path("/ws"),
            timeout=11,
        )

    def test_absent_network_returns_none(self) -> None:
        network, _ = self._inspect(_command_result(returncode=1, stderr="No such network"))

        self.assertIsNone(network)

    def test_unparseable_stdout_returns_none(self) -> None:
        network, _ = self._inspect(_command_result(stdout="not json"))

        self.assertIsNone(network)

    def test_empty_inspect_list_returns_none(self) -> None:
        network, _ = self._inspect(_command_result(stdout="[]"))

        self.assertIsNone(network)

    def test_object_instead_of_list_returns_none(self) -> None:
        network, _ = self._inspect(_command_result(stdout='{"Name": "agents-remember-cgc"}'))

        self.assertIsNone(network)

    def test_result_feeds_the_ownership_check_that_guards_removal(self) -> None:
        owned = _command_result(
            stdout=json.dumps([{"Labels": {"com.docker.compose.project": "agents-remember-cgc"}}])
        )
        network, _ = self._inspect(owned)

        self.assertTrue(compose_runtime.network_managed_by_project(network, "agents-remember-cgc"))
        self.assertFalse(compose_runtime.network_managed_by_project(network, "other-project"))


class DockerWaitForOllamaTests(unittest.TestCase):
    """embedder.docker_wait_for_ollama -- poll ``ollama list`` until the model store
    answers."""

    def setUp(self) -> None:
        self.clock = FakeClock()

    def _wait(self, runner: mock.Mock, *, timeout: int) -> Any:
        with (
            mock.patch.object(grepai_embedder, "run_command", runner),
            mock.patch.object(grepai_embedder, "docker_command", return_value=DOCKER),
            mock.patch.object(grepai_embedder, "time", self.clock),
        ):
            return grepai_embedder.docker_wait_for_ollama(
                _ollama_embedder(), cwd=Path("/ws"), timeout=timeout
            )

    def test_healthy_container_returns_the_first_result_without_sleeping(self) -> None:
        ready = _command_result(stdout="NAME\nnomic-embed-text:v1.5\n")
        runner = mock.Mock(return_value=ready)

        value = self._wait(runner, timeout=300)

        self.assertIs(value, ready)
        self.assertEqual(self.clock.slept, [])
        runner.assert_called_once_with(
            [DOCKER, "exec", "ar-ollama-ws", "ollama", "list"],
            cwd=Path("/ws"),
            timeout=30,
        )

    def test_transient_failure_is_retried_after_a_two_second_sleep(self) -> None:
        ready = _command_result(stdout="nomic-embed-text:v1.5\n")
        runner = mock.Mock(side_effect=[_command_result(returncode=1, stderr="not ready"), ready])

        value = self._wait(runner, timeout=30)

        self.assertIs(value, ready)
        self.assertEqual(self.clock.slept, [2])
        self.assertEqual(runner.call_count, 2)

    def test_persistent_failure_raises_with_the_last_stderr(self) -> None:
        failure = _command_result(returncode=1, stderr="dial unix docker.sock: no such file")
        runner = mock.Mock(return_value=failure)

        with self.assertRaises(ContextProviderError) as caught:
            self._wait(runner, timeout=5)

        self.assertEqual(
            str(caught.exception),
            "Ollama health check failed: dial unix docker.sock: no such file",
        )
        # Polls at t=0, 2 and 4; t=6 is past the 5s deadline.
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(self.clock.slept, [2, 2, 2])

    def test_persistent_failure_without_stderr_reports_stdout_instead(self) -> None:
        failure = _command_result(returncode=1, stdout="container is restarting")

        with self.assertRaises(ContextProviderError) as caught:
            self._wait(mock.Mock(return_value=failure), timeout=5)

        self.assertEqual(
            str(caught.exception), "Ollama health check failed: container is restarting"
        )

    def test_zero_timeout_never_polls_and_reports_the_timeout(self) -> None:
        runner = mock.Mock(side_effect=AssertionError("must not poll"))

        with self.assertRaises(ContextProviderError) as caught:
            self._wait(runner, timeout=0)

        runner.assert_not_called()
        self.assertEqual(str(caught.exception), "timed out waiting for Ollama health check")


class OllamaModelPresentTests(unittest.TestCase):
    """embedder.ollama_model_present -- decide whether ``ollama list`` output already
    holds the wanted embedding model."""

    LIST_OUTPUT = (
        "NAME                       ID              SIZE      MODIFIED\n"
        "nomic-embed-text:v1.5      0a109f422b47    274 MB    2 hours ago\n"
        "qwen2.5-coder:7b           2b0496514337    4.7 GB    3 days ago\n"
    )

    def test_exact_tag_present(self) -> None:
        self.assertTrue(
            grepai_embedder.ollama_model_present(self.LIST_OUTPUT, "nomic-embed-text:v1.5")
        )

    def test_untagged_request_matches_the_latest_alias(self) -> None:
        listing = "nomic-embed-text:latest    0a109f422b47    274 MB    2 hours ago\n"

        self.assertTrue(grepai_embedder.ollama_model_present(listing, "nomic-embed-text"))

    def test_same_family_different_tag_counts_as_present(self) -> None:
        self.assertTrue(
            grepai_embedder.ollama_model_present(self.LIST_OUTPUT, "nomic-embed-text:v1.9")
        )

    def test_unrelated_models_do_not_match(self) -> None:
        self.assertFalse(
            grepai_embedder.ollama_model_present(self.LIST_OUTPUT, "mxbai-embed-large:latest")
        )

    def test_header_only_output_is_not_a_match(self) -> None:
        self.assertFalse(
            grepai_embedder.ollama_model_present(
                "NAME    ID    SIZE    MODIFIED\n", "nomic-embed-text:v1.5"
            )
        )

    def test_empty_output_is_not_a_match(self) -> None:
        self.assertFalse(grepai_embedder.ollama_model_present("", "nomic-embed-text:v1.5"))

    def test_blank_lines_are_skipped_rather_than_matching(self) -> None:
        self.assertFalse(
            grepai_embedder.ollama_model_present("\n   \n\t\n", "nomic-embed-text:v1.5")
        )


class CgcRefreshPreflightTests(unittest.TestCase):
    """refresh.cgc_refresh_preflight -- the gate that decides whether an index run may
    start."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        repo = root / "repo-a"
        repo.mkdir()
        self.layout = cgc_runtime_layout(
            CgcRepo(
                coordination_root=root / ".agents-remember",
                repo_id="repo-a",
                code_repo_root=repo,
            ),
        )
        self.command = {"command": [DOCKER, "compose", "run"], "cwd": root.as_posix()}
        self.settings_args = argparse.Namespace(
            dry_run=False,
            from_settings=root / "lifecycle-settings.json",
            repo_id="repo-a",
            code_repo_root=None,
        )

    def test_dry_run_returns_the_plan_and_never_touches_backend_or_doctor(self) -> None:
        self.settings_args.dry_run = True
        backend_start = mock.Mock()
        doctor = mock.Mock()

        with (
            mock.patch.object(cgc_refresh_lifecycle, "cgc_backend_start", backend_start),
            mock.patch.object(cgc_refresh_lifecycle, "cgc_doctor", doctor),
        ):
            early, backend = cgc_refresh_lifecycle.cgc_refresh_preflight(
                self.settings_args, self.layout, self.command
            )

        self.assertIsNone(backend)
        self.assertEqual(
            early,
            {
                "provider": "codegraphcontext",
                "action": "refresh",
                "ok": True,
                "repoId": "repo-a",
                "dryRun": True,
                "command": self.command,
                "cwd": self.layout.runtime_root.as_posix(),
                "env": self.layout.env(),
            },
        )
        backend_start.assert_not_called()
        doctor.assert_not_called()

    def test_failed_backend_short_circuits_before_the_doctor(self) -> None:
        doctor = mock.Mock()

        with (
            mock.patch.object(
                cgc_refresh_lifecycle,
                "cgc_backend_start",
                return_value={"provider": "codegraphcontext", "ok": False, "error": "no falkordb"},
            ),
            mock.patch.object(cgc_refresh_lifecycle, "cgc_doctor", doctor),
        ):
            early, backend = cgc_refresh_lifecycle.cgc_refresh_preflight(
                self.settings_args, self.layout, self.command
            )

        self.assertIs(early, backend)
        self.assertEqual(
            early,
            {
                "provider": "codegraphcontext",
                "ok": False,
                "error": "no falkordb",
                "action": "refresh",
                "repoId": "repo-a",
            },
        )
        doctor.assert_not_called()

    def test_failed_doctor_blocks_the_run_but_keeps_the_backend_result(self) -> None:
        backend_result = {"provider": "codegraphcontext", "action": "backend-start", "ok": True}

        with (
            mock.patch.object(
                cgc_refresh_lifecycle, "cgc_backend_start", return_value=backend_result
            ),
            mock.patch.object(
                cgc_refresh_lifecycle,
                "cgc_doctor",
                return_value={"ok": False, "checks": ["falkordb unreachable"]},
            ),
        ):
            early, backend = cgc_refresh_lifecycle.cgc_refresh_preflight(
                self.settings_args, self.layout, self.command
            )

        self.assertEqual(
            early, {"ok": False, "checks": ["falkordb unreachable"], "action": "refresh"}
        )
        self.assertEqual(backend, backend_result)

    def test_healthy_preflight_returns_no_early_result(self) -> None:
        backend_result = {"provider": "codegraphcontext", "action": "backend-start", "ok": True}

        with (
            mock.patch.object(
                cgc_refresh_lifecycle, "cgc_backend_start", return_value=backend_result
            ),
            mock.patch.object(cgc_refresh_lifecycle, "cgc_doctor", return_value={"ok": True}),
        ):
            early, backend = cgc_refresh_lifecycle.cgc_refresh_preflight(
                self.settings_args, self.layout, self.command
            )

        self.assertIsNone(early)
        self.assertEqual(backend, backend_result)

    def test_manual_override_mode_skips_the_managed_backend_entirely(self) -> None:
        manual_args = argparse.Namespace(
            dry_run=False,
            from_settings=None,
            repo_id="repo-a",
            code_repo_root=self.layout.code_repo_root,
        )
        backend_start = mock.Mock()

        with (
            mock.patch.object(cgc_refresh_lifecycle, "cgc_backend_start", backend_start),
            mock.patch.object(cgc_refresh_lifecycle, "cgc_doctor", return_value={"ok": True}),
        ):
            early, backend = cgc_refresh_lifecycle.cgc_refresh_preflight(
                manual_args, self.layout, self.command
            )

        self.assertIsNone(early)
        self.assertIsNone(backend)
        backend_start.assert_not_called()


class CgcRefreshTests(unittest.TestCase):
    """refresh.cgc_refresh -- one repo's forced reindex, end to end minus docker."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.coordination_root, self.settings_path = _write_cgc_settings(self.root)

    def _args(self, *, dry_run: bool = False, repo_id: str | None = "repo-a"):
        argv = [
            "refresh",
            "--coordination-root",
            str(self.coordination_root),
            "--from-settings",
            str(self.settings_path),
        ]
        if repo_id is not None:
            argv.extend(["--repo-id", repo_id])
        if dry_run:
            argv.append("--dry-run")
        return _parse_cgc(argv)

    def test_no_repo_id_in_settings_mode_fans_out_to_refresh_all(self) -> None:
        args = self._args(repo_id=None)
        all_result = {"provider": "codegraphcontext", "action": "refresh-all", "ok": True}

        with mock.patch.object(
            cgc_refresh_lifecycle, "cgc_refresh_all", return_value=all_result
        ) as refresh_all:
            result = cgc_refresh_lifecycle.cgc_refresh(args)

        self.assertIs(result, all_result)
        refresh_all.assert_called_once_with(args)

    def test_dry_run_plans_the_forced_index_and_writes_no_state(self) -> None:
        args = self._args(dry_run=True)
        layout = cgc_layout_from_args(args)

        with (
            mock.patch.object(compose_runtime, "docker_command", return_value=DOCKER),
            mock.patch.object(cgc_refresh_lifecycle, "run_compose") as run_compose,
        ):
            result = cgc_refresh_lifecycle.cgc_refresh(args)

        run_compose.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["repoId"], "repo-a")
        self.assertEqual(result["action"], "refresh")
        self.assertEqual(
            result["command"]["command"][-7:],
            [
                "run",
                "--rm",
                "--no-deps",
                "runner",
                "index",
                layout.container_code_repo_root,
                "--force",
            ],
        )
        self.assertEqual(result["command"]["overrideMode"], "stdin")
        self.assertFalse(layout.state_file.exists())

    def _run_refresh(self, args, compose_result: dict[str, Any]) -> dict[str, Any]:
        with (
            mock.patch.object(compose_runtime, "docker_command", return_value=DOCKER),
            mock.patch.object(
                cgc_refresh_lifecycle,
                "cgc_backend_start",
                return_value={
                    "provider": "codegraphcontext",
                    "action": "backend-start",
                    "ok": True,
                },
            ),
            mock.patch.object(cgc_refresh_lifecycle, "cgc_doctor", return_value={"ok": True}),
            mock.patch.object(
                cgc_refresh_lifecycle, "run_compose", return_value=compose_result
            ) as run_compose,
        ):
            result = cgc_refresh_lifecycle.cgc_refresh(args)
        self.run_compose = run_compose
        return result

    def test_successful_index_records_the_run_in_the_state_file(self) -> None:
        args = self._args()
        layout = cgc_layout_from_args(args)
        lifecycle.write_json(layout.state_file, {"lastAction": "install", "keepMe": "yes"})
        compose_result = _command_result(stdout="indexed 412 files") | {"durationSeconds": 12.5}

        result = self._run_refresh(args, compose_result)

        self.assertTrue(result["ok"])
        self.assertEqual(result["repoId"], "repo-a")
        self.assertIs(result["command"], compose_result)
        self.assertEqual(result["backend"]["action"], "backend-start")
        self.assertEqual(result["compose"]["overrideMode"], "stdin")
        state = json.loads(layout.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["lastAction"], "refresh")
        self.assertEqual(state["lastRefresh"]["returncode"], 0)
        self.assertEqual(state["lastRefresh"]["durationSeconds"], 12.5)
        # Pre-existing state survives the merge.
        self.assertEqual(state["keepMe"], "yes")

    def test_indexing_runs_without_a_command_timeout(self) -> None:
        args = self._args()

        self._run_refresh(args, _command_result())

        self.assertEqual(self.run_compose.call_args.kwargs["timeout"], UNLIMITED_TIMEOUT)
        self.assertEqual(self.run_compose.call_args.kwargs["cwd"], args.coordination_root)

    def test_failed_index_reports_not_ok_and_still_records_the_returncode(self) -> None:
        args = self._args()
        layout = cgc_layout_from_args(args)
        compose_result = _command_result(returncode=1, stderr="runner exited 1")

        result = self._run_refresh(args, compose_result)

        self.assertFalse(result["ok"])
        state = json.loads(layout.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["lastRefresh"]["returncode"], 1)

    def test_failed_doctor_aborts_before_compose_and_leaves_no_state(self) -> None:
        args = self._args()
        layout = cgc_layout_from_args(args)

        with (
            mock.patch.object(compose_runtime, "docker_command", return_value=DOCKER),
            mock.patch.object(
                cgc_refresh_lifecycle,
                "cgc_backend_start",
                return_value={"provider": "codegraphcontext", "ok": True},
            ),
            mock.patch.object(
                cgc_refresh_lifecycle,
                "cgc_doctor",
                return_value={"ok": False, "checks": ["runner image missing"]},
            ),
            mock.patch.object(cgc_refresh_lifecycle, "run_compose") as run_compose,
        ):
            result = cgc_refresh_lifecycle.cgc_refresh(args)

        run_compose.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "refresh")
        self.assertEqual(result["checks"], ["runner image missing"])
        self.assertFalse(layout.state_file.exists())


class CgcApplySettingsTests(unittest.TestCase):
    """core.cgc_apply_settings -- materialize every configured repo's runtime layout and
    prune the ones that are no longer configured."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.coordination_root, self.settings_path = _write_cgc_settings(
            self.root, repo_ids=("repo-a", "repo-b")
        )

    def _args(self, *, dry_run: bool = False):
        argv = [
            "apply-settings",
            "--coordination-root",
            str(self.coordination_root),
            "--from-settings",
            str(self.settings_path),
        ]
        if dry_run:
            argv.append("--dry-run")
        return _parse_cgc(argv)

    def _stale_instance(self) -> Path:
        stale = (
            self.coordination_root / "providers" / "runners" / "codegraphcontext" / "retired-repo"
        )
        stale.mkdir(parents=True)
        (stale / "provider-state.json").write_text("{}\n", encoding="utf-8")
        return stale

    def test_apply_materializes_state_for_every_configured_repo(self) -> None:
        args = self._args()
        _, _, layouts = cgc_all_layouts_from_settings(args)

        result = cgc_apply_settings(args)

        self.assertTrue(result["ok"])
        self.assertFalse(result["dryRun"])
        self.assertEqual(result["settingsFile"], self.settings_path.as_posix())
        self.assertEqual(
            [instance["repoId"] for instance in result["instances"]], ["repo-a", "repo-b"]
        )
        self.assertEqual(result["instances"][0]["graphName"], "cgc_repo_a")
        self.assertEqual(result["backendRuntimeRoot"], layouts[0].backend_root.as_posix())
        for layout in layouts:
            state = json.loads(layout.state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["lastAction"], "apply-settings")
            self.assertEqual(state["repoId"], layout.repo_id)
            self.assertEqual(state["codeRepoRoot"], layout.code_repo_root.as_posix())
            self.assertEqual(state["settingsFile"], self.settings_path.as_posix())
            self.assertTrue(layout.config_file.exists())

    def test_apply_records_the_configured_backend_once_for_the_workspace(self) -> None:
        args = self._args()
        _, _, layouts = cgc_all_layouts_from_settings(args)

        cgc_apply_settings(args)

        backend_state = json.loads(layouts[0].backend_state_file.read_text(encoding="utf-8"))
        self.assertEqual(backend_state["provider"], "codegraphcontext")
        self.assertEqual(backend_state["backend"]["image"], "falkordb/falkordb:v4.18.7")
        self.assertEqual(backend_state["backend"]["status"], "configured")
        self.assertEqual(
            backend_state["backend"]["runtimeRoot"], layouts[0].backend_root.as_posix()
        )
        self.assertEqual(backend_state["settingsFile"], self.settings_path.as_posix())

    def test_apply_removes_a_runtime_root_that_settings_no_longer_configure(self) -> None:
        args = self._args()
        stale = self._stale_instance()

        result = cgc_apply_settings(args)

        self.assertFalse(stale.exists())
        self.assertIn(
            {"path": stale.as_posix(), "reason": "unconfigured-cgc-instance"},
            result["removedArtifacts"],
        )

    def test_dry_run_reports_the_removal_without_deleting_or_writing_anything(self) -> None:
        args = self._args(dry_run=True)
        _, _, layouts = cgc_all_layouts_from_settings(args)
        stale = self._stale_instance()

        result = cgc_apply_settings(args)

        self.assertTrue(result["dryRun"])
        self.assertTrue(stale.exists())
        self.assertIn(
            {"path": stale.as_posix(), "reason": "unconfigured-cgc-instance"},
            result["removedArtifacts"],
        )
        for layout in layouts:
            self.assertFalse(layout.state_file.exists())
            self.assertFalse(layout.config_file.exists())
        self.assertFalse(layouts[0].backend_state_file.exists())
        self.assertEqual(
            [instance["repoId"] for instance in result["instances"]], ["repo-a", "repo-b"]
        )


class WriteIsolatedCgcSettingsTests(unittest.TestCase):
    """cgc_setup.write_isolated_cgc_settings -- point a worktree at its own CGC settings
    file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.coordination_root = self.root / "workspace" / ".agents-remember"
        self.coordination_root.mkdir(parents=True)
        self.target_repo = self.root / "worktrees" / "task-1"
        self.target_repo.mkdir(parents=True)
        self.isolated_root = self.root / "worktrees" / "task-1-ar"
        self.settings = {
            "contextProviders": {
                "enabled": True,
                "providers": {
                    "codegraphcontext-code": _cgc_provider_block(
                        self.coordination_root,
                        [{"repoId": "workspace", "path": self.root.as_posix()}],
                    )
                },
            }
        }

    def _args(self, **overrides: Any) -> SimpleNamespace:
        defaults = {
            "coordination_root": self.coordination_root,
            "cgc_isolated_runtime_root": self.isolated_root,
            "cgc_isolated_settings_path": None,
            "cgc_isolated_container_name": None,
            "cgc_seed_repo_id": None,
            "cgc_seed_target_repo_root": self.target_repo,
            "cgc_from_settings": None,
            "dry_run": False,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_written_settings_scope_cgc_to_the_worktree(self) -> None:
        args = self._args()

        result = cgc_setup.write_isolated_cgc_settings(args, self.settings)

        expected = self.isolated_root / "settings" / "codegraphcontext-provider-settings.json"
        self.assertEqual(args.cgc_from_settings, expected.resolve())
        self.assertEqual(
            result, {"path": expected.resolve().as_posix(), "dryRun": False, "settings": None}
        )
        written = json.loads(expected.read_text(encoding="utf-8"))
        self.assertEqual(written["version"], 1)
        self.assertTrue(written["contextProviders"]["enabled"])
        self.assertEqual(
            written["contextProviders"]["policy"],
            {"discoveryOnly": True, "sourceProofRequired": True},
        )
        provider = written["contextProviders"]["providers"]["codegraphcontext-code"]
        self.assertEqual(
            provider["roots"],
            [
                {
                    "repoId": stable_provider_id("task-1"),
                    "path": self.target_repo.resolve().as_posix(),
                }
            ],
        )
        self.assertEqual(provider["instance"]["scope"], "worktree")
        self.assertEqual(provider["runtime"]["mode"], "docker")
        self.assertTrue(provider["backend"]["containerName"].startswith("ar-cgc-falkordb-"))
        self.assertTrue(
            provider["runtimeRoot"].startswith(
                (self.isolated_root / "providers" / "runners").as_posix()
            )
        )

    def test_source_settings_are_not_mutated(self) -> None:
        original = json.loads(json.dumps(self.settings))

        cgc_setup.write_isolated_cgc_settings(self._args(), self.settings)

        self.assertEqual(self.settings, original)

    def test_explicit_settings_path_and_container_name_are_honoured(self) -> None:
        target = self.root / "elsewhere" / "cgc.json"
        args = self._args(
            cgc_isolated_settings_path=target,
            cgc_isolated_container_name="ar-cgc-falkordb-custom",
            cgc_seed_repo_id="explicit-repo",
        )

        result = cgc_setup.write_isolated_cgc_settings(args, self.settings)

        assert result is not None
        self.assertEqual(result["path"], target.resolve().as_posix())
        written = json.loads(target.read_text(encoding="utf-8"))
        provider = written["contextProviders"]["providers"]["codegraphcontext-code"]
        self.assertEqual(provider["backend"]["containerName"], "ar-cgc-falkordb-custom")
        self.assertEqual(provider["roots"][0]["repoId"], "explicit-repo")

    def test_dry_run_points_at_the_path_but_writes_nothing(self) -> None:
        args = self._args(dry_run=True)

        result = cgc_setup.write_isolated_cgc_settings(args, self.settings)

        expected = self.isolated_root / "settings" / "codegraphcontext-provider-settings.json"
        self.assertFalse(expected.exists())
        self.assertFalse(expected.parent.exists())
        assert result is not None
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["path"], expected.resolve().as_posix())
        self.assertEqual(result["settings"]["version"], 1)
        self.assertEqual(args.cgc_from_settings, expected.resolve())

    def test_no_isolated_root_clears_any_stale_settings_pointer(self) -> None:
        args = self._args(
            cgc_isolated_runtime_root=None, cgc_from_settings=self.root / "stale.json"
        )

        self.assertIsNone(cgc_setup.write_isolated_cgc_settings(args, self.settings))
        self.assertIsNone(args.cgc_from_settings)

    def test_settings_without_a_cgc_provider_produce_no_isolated_file(self) -> None:
        args = self._args()
        settings = {"contextProviders": {"enabled": True, "providers": {}}}

        self.assertIsNone(cgc_setup.write_isolated_cgc_settings(args, settings))
        self.assertIsNone(args.cgc_from_settings)
        self.assertFalse(self.isolated_root.exists())

    def test_isolated_root_without_a_target_repo_is_rejected(self) -> None:
        args = self._args(cgc_seed_target_repo_root=None)

        with self.assertRaises(RuntimeError) as caught:
            cgc_setup.write_isolated_cgc_settings(args, self.settings)

        self.assertIn("--cgc-seed-target-repo-root", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
