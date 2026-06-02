"""Tests for seeding the worktree ollama model from the workspace (#2: no 274MB re-pull).

Each worktree ollama starts empty; instead of pulling the embedding model over the network,
it copies the model from the already-running workspace ollama via a local tar pipe, and only
falls back to `ollama pull` when no source is configured or the copy did not land.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from agents_remember.providers.grepai.lifecycle import embedder as embedder_module
from agents_remember.providers.grepai.lifecycle.embedder import (
    _seed_ollama_model_from_source,
    docker_ensure_ollama_model,
)

_CWD = Path("/tmp")


def _embedder(*, seed_from: str | None = "ar-grepai-ollama-projects") -> dict[str, object]:
    e: dict[str, object] = {"containerName": "ar-grepai-ollama-wt", "model": "nomic-embed-text"}
    if seed_from is not None:
        e["seedFromContainer"] = seed_from
    return e


class SeedFromSourceTests(unittest.TestCase):
    def test_returns_none_without_source(self) -> None:
        self.assertIsNone(
            _seed_ollama_model_from_source(_embedder(seed_from=None), cwd=_CWD, timeout=60)
        )

    def test_returns_none_when_source_equals_target(self) -> None:
        e = _embedder(seed_from="ar-grepai-ollama-wt")  # same as containerName
        self.assertIsNone(_seed_ollama_model_from_source(e, cwd=_CWD, timeout=60))

    def test_streams_model_via_tar_pipe(self) -> None:
        with (
            mock.patch.object(embedder_module, "docker_command", return_value="docker"),
            mock.patch.object(
                embedder_module, "run_command", return_value={"returncode": 0}
            ) as run,
        ):
            result = _seed_ollama_model_from_source(_embedder(), cwd=_CWD, timeout=60)
        assert result is not None
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "ar-grepai-ollama-projects")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "sh")
        pipe = command[2]
        self.assertIn("exec ar-grepai-ollama-projects tar -C /root/.ollama -cf - models", pipe)
        self.assertIn("exec -i ar-grepai-ollama-wt tar -C /root/.ollama -xf -", pipe)


class EnsureModelTests(unittest.TestCase):
    def test_present_model_neither_seeds_nor_pulls(self) -> None:
        with (
            mock.patch.object(
                embedder_module, "docker_wait_for_ollama", return_value={"stdout": "x"}
            ),
            mock.patch.object(embedder_module, "ollama_model_present", return_value=True),
            mock.patch.object(embedder_module, "run_command") as run,
        ):
            result = docker_ensure_ollama_model(_embedder(), cwd=_CWD, timeout=60)
        self.assertTrue(result["alreadyPresent"])
        run.assert_not_called()

    def test_successful_seed_skips_pull(self) -> None:
        with (
            mock.patch.object(
                embedder_module, "docker_wait_for_ollama", return_value={"stdout": "x"}
            ),
            mock.patch.object(embedder_module, "docker_command", return_value="docker"),
            mock.patch.object(
                embedder_module, "ollama_model_present", side_effect=[False, True]
            ),
            mock.patch.object(
                embedder_module, "run_command", return_value={"returncode": 0}
            ) as run,
        ):
            result = docker_ensure_ollama_model(_embedder(), cwd=_CWD, timeout=60)
        self.assertTrue(result["ok"])
        self.assertEqual(result["seededFrom"], "ar-grepai-ollama-projects")
        self.assertNotIn("pull", result)
        self.assertEqual(run.call_count, 1)  # only the seed, never the pull

    def test_failed_seed_falls_back_to_pull(self) -> None:
        with (
            mock.patch.object(
                embedder_module, "docker_wait_for_ollama", return_value={"stdout": "x"}
            ),
            mock.patch.object(embedder_module, "docker_command", return_value="docker"),
            mock.patch.object(embedder_module, "ollama_model_present", side_effect=[False]),
            mock.patch.object(
                embedder_module,
                "run_command",
                side_effect=[{"returncode": 1}, {"returncode": 0}],
            ) as run,
        ):
            result = docker_ensure_ollama_model(_embedder(), cwd=_CWD, timeout=60)
        self.assertTrue(result["ok"])
        self.assertIn("pull", result)
        self.assertEqual(run.call_count, 2)  # failed seed, then pull
        pull_command = run.call_args.args[0]
        self.assertEqual(pull_command[-2:], ["pull", "nomic-embed-text"])

    def test_no_source_pulls_without_seeding(self) -> None:
        with (
            mock.patch.object(
                embedder_module, "docker_wait_for_ollama", return_value={"stdout": "x"}
            ),
            mock.patch.object(embedder_module, "docker_command", return_value="docker"),
            mock.patch.object(embedder_module, "ollama_model_present", side_effect=[False]),
            mock.patch.object(
                embedder_module, "run_command", return_value={"returncode": 0}
            ) as run,
        ):
            result = docker_ensure_ollama_model(_embedder(seed_from=None), cwd=_CWD, timeout=60)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["seedAttempt"])
        self.assertEqual(run.call_count, 1)  # only the pull


if __name__ == "__main__":
    unittest.main()
